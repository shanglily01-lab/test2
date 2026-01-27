#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版智能决策大脑 - 集成高级信号检测器
在原有白名单+多维度分析基础上,增加高级信号检测
"""

from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
from loguru import logger
import pymysql

from app.services.advanced_signal_detector import AdvancedSignalDetector
from app.analyzers.kline_strength_scorer import KlineStrengthScorer
from app.services.signal_analysis_service import SignalAnalysisService


class SmartDecisionBrainEnhanced:
    """增强版智能决策大脑 (集成高级信号检测 + K线强度评分)"""

    def __init__(self, db_config: dict):
        """
        初始化决策大脑

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None

        # 黑名单白名单保持不变
        self.blacklist = [
            'IP/USDT',
            'VIRTUAL/USDT',
            'LDO/USDT',
            'ATOM/USDT',
            'ADA/USDT',
        ]

        # 白名单 - 主流币 (永不被黑名单拦截)
        self.whitelist_main = [
            'BTC/USDT',
            'ETH/USDT',
            'SOL/USDT',
            'BNB/USDT',
        ]

        # 白名单 - 只做LONG方向
        self.whitelist_long = [
            'BCH/USDT',
            'ENA/USDT',
            'WIF/USDT',
            'TAO/USDT',
            'DASH/USDT',
            'ETC/USDT',
            'NEAR/USDT',
            'AAVE/USDT',
            'SUI/USDT',
            'UNI/USDT',
            'SOL/USDT',
        ]

        # 合并所有可交易币种
        self.all_tradable = list(set(self.whitelist_main + self.whitelist_long))

        # 决策阈值 (优化后提升到45分)
        self.threshold = 45  # 最低45分才开仓 (原30分)
        self.min_kline_score = 15  # K线强度最低15分

        # === 新增: 高级信号检测器 ===
        self.advanced_detector = AdvancedSignalDetector(db_config)
        self.enable_advanced_signals = True  # 启用高级信号检测

        # === 新增: K线强度评分器 ===
        self.kline_scorer = KlineStrengthScorer()
        self.signal_analyzer = SignalAnalysisService(db_config)
        self.enable_kline_scoring = True  # 启用K线强度评分

        logger.info(f"✅ 增强版智能决策大脑已初始化")
        logger.info(f"   主流币: {len(self.whitelist_main)}个 | 白名单: {len(self.whitelist_long)}个 | 黑名单: {len(self.blacklist)}个")
        logger.info(f"   决策阈值: {self.threshold}分 (K线强度>={self.min_kline_score}分) | 高级信号: {'启用' if self.enable_advanced_signals else '禁用'}")
        logger.info(f"   K线强度评分: {'启用' if self.enable_kline_scoring else '禁用'}")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            try:
                self.connection = pymysql.connect(
                    host=self.db_config.get('host', 'localhost'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    database=self.db_config.get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10,
                    read_timeout=30
                )
            except Exception as e:
                logger.error(f"❌ 数据库连接失败: {e}")
                raise
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(
                    host=self.db_config.get('host', 'localhost'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    database=self.db_config.get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10,
                    read_timeout=30
                )

        return self.connection

    def load_klines(self, symbol: str, timeframe: str, limit: int = 100) -> List[Dict]:
        """
        加载合约K线数据

        Args:
            symbol: 交易对 (如 BTC/USDT)
            timeframe: 时间周期 (1d, 1h, 15m)
            limit: 返回数量

        Returns:
            K线数据列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
            SELECT open_time,
                   open_price as open,
                   high_price as high,
                   low_price as low,
                   close_price as close,
                   volume
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            AND exchange = 'binance_futures'
            AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 60 DAY)) * 1000
            ORDER BY open_time DESC
            LIMIT %s
        """

        cursor.execute(query, (symbol, timeframe, limit))
        klines = cursor.fetchall()
        cursor.close()

        klines.reverse()

        for k in klines:
            k['open'] = float(k['open'])
            k['high'] = float(k['high'])
            k['low'] = float(k['low'])
            k['close'] = float(k['close'])
            k['volume'] = float(k['volume'])

        return klines

    def analyze_position(self, klines_1d: List[Dict]) -> Tuple[int, List[str]]:
        """分析价格位置 (30分)"""
        if len(klines_1d) < 30:
            return 0, ['数据不足']

        high_30d = max(k['high'] for k in klines_1d[-30:])
        low_30d = min(k['low'] for k in klines_1d[-30:])
        current = klines_1d[-1]['close']

        if high_30d == low_30d:
            position_pct = 50.0
        else:
            position_pct = (current - low_30d) / (high_30d - low_30d) * 100

        if len(klines_1d) >= 7:
            price_7d_ago = klines_1d[-7]['close']
            gain_7d = (current - price_7d_ago) / price_7d_ago * 100
        else:
            gain_7d = 0

        score = 0
        reasons = []

        if position_pct < 30:
            score += 20
            reasons.append(f"✅ 底部区域({position_pct:.0f}%)")
        elif position_pct > 70:
            score -= 20
            reasons.append(f"❌ 顶部区域({position_pct:.0f}%)")
        else:
            score += 5
            reasons.append(f"⚠ 中部区域({position_pct:.0f}%)")

        if gain_7d < 10:
            score += 10
            reasons.append(f"✅ 7日涨幅适中({gain_7d:.1f}%)")
        elif gain_7d > 20:
            score -= 10
            reasons.append(f"❌ 7日涨幅过大({gain_7d:.1f}%)")

        return score, reasons

    def analyze_trend(self, klines_1d: List[Dict], klines_1h: List[Dict]) -> Tuple[int, List[str]]:
        """分析趋势强度 (20分)"""
        score = 0
        reasons = []

        if len(klines_1d) >= 30:
            bullish_1d = sum(1 for k in klines_1d[-30:] if k['close'] > k['open'])
            bullish_ratio = bullish_1d / 30

            if bullish_ratio > 0.6:
                score += 20
                reasons.append(f"✅ 1D强势({bullish_1d}/30阳线)")
            elif bullish_ratio < 0.4:
                score -= 5
                reasons.append(f"❌ 1D弱势({30-bullish_1d}/30阴线)")

        return score, reasons

    def analyze_support_resistance(self, klines_1h: List[Dict]) -> Tuple[int, List[str], Dict]:
        """分析支撑阻力和盈亏比 (30分)"""
        if len(klines_1h) < 50:
            return 0, ['数据不足'], {}

        recent = klines_1h[-100:] if len(klines_1h) >= 100 else klines_1h
        current = klines_1h[-1]['close']

        highs = [k['high'] for k in recent]
        lows = [k['low'] for k in recent]

        resistance_candidates = [h for h in highs if h > current]
        resistance = min(resistance_candidates) if resistance_candidates else max(highs)

        support_candidates = [l for l in lows if l < current]
        support = max(support_candidates) if support_candidates else min(lows)

        upside = (resistance - current) / current * 100
        downside = (current - support) / current * 100

        if downside > 0:
            risk_reward = upside / downside
        else:
            risk_reward = 0

        score = 0
        reasons = []

        if risk_reward >= 2:
            score += 30
            reasons.append(f"✅ 极佳盈亏比{risk_reward:.1f}:1 (上{upside:.1f}%/下{downside:.1f}%)")
        elif risk_reward >= 1.5:
            score += 15
            reasons.append(f"✅ 良好盈亏比{risk_reward:.1f}:1")
        else:
            score -= 10
            reasons.append(f"❌ 盈亏比不足{risk_reward:.1f}:1")

        sr_data = {
            'resistance': resistance,
            'support': support,
            'upside': upside,
            'downside': downside,
            'risk_reward': risk_reward
        }

        return score, reasons, sr_data

    # === 新增: 高级信号检测方法 ===
    def check_advanced_signals(self, symbol: str) -> Optional[Dict]:
        """
        检测高级信号

        Args:
            symbol: 交易对

        Returns:
            高级信号结果或 None
        """
        if not self.enable_advanced_signals:
            return None

        try:
            signal = self.advanced_detector.detect_signals(symbol)

            if signal:
                logger.info(f"🎯 {symbol} 检测到高级信号: {signal.signal_type} ({signal.strength}, {signal.score}分)")
                logger.info(f"   入场: ${signal.entry_price:.2f} | 止损: ${signal.stop_loss:.2f}")
                logger.info(f"   原因: {signal.reason}")

                return {
                    'signal_type': signal.signal_type,
                    'direction': signal.direction,
                    'strength': signal.strength,
                    'score': signal.score,
                    'entry_price': signal.entry_price,
                    'stop_loss': signal.stop_loss,
                    'reason': signal.reason,
                    'details': signal.details
                }

            return None

        except Exception as e:
            logger.error(f"❌ {symbol} 高级信号检测失败: {e}")
            return None

    def should_trade(self, symbol: str) -> Dict:
        """
        决策是否交易 (集成高级信号)

        优先级:
        1. 高级信号 (最高优先级)
        2. 原有的白名单+多维度分析

        Args:
            symbol: 交易对

        Returns:
            决策结果字典
        """
        # 黑名单检查 (优先级最高,但主流币例外)
        if symbol in self.blacklist and symbol not in self.whitelist_main:
            return {
                'decision': False,
                'direction': None,
                'score': 0,
                'signal_source': 'blacklist',
                'reasons': [f'🚫 {symbol} 在黑名单中 (实盘表现较差)'],
                'trade_params': {}
            }

        try:
            # === 优先检查高级信号 (主流币优先) ===
            if symbol in self.whitelist_main:
                advanced_signal = self.check_advanced_signals(symbol)

                if advanced_signal:
                    # 高级信号评分映射到决策评分
                    # STRONG (70-105分) -> 开仓
                    # MEDIUM (50-69分) -> 小仓位开仓
                    signal_score = advanced_signal['score']

                    if signal_score >= 70:
                        decision_score = 80  # 高分
                        max_hold_minutes = 360  # 6小时
                    elif signal_score >= 50:
                        decision_score = 50  # 中等分
                        max_hold_minutes = 240  # 4小时
                    else:
                        decision_score = 30  # 低分
                        max_hold_minutes = 120  # 2小时

                    return {
                        'decision': True,
                        'direction': advanced_signal['direction'],
                        'score': decision_score,
                        'signal_source': 'advanced_signal',
                        'signal_type': advanced_signal['signal_type'],
                        'signal_strength': advanced_signal['strength'],
                        'reasons': [
                            f"🎯 高级信号: {advanced_signal['signal_type']}",
                            f"📊 信号评分: {signal_score}分 ({advanced_signal['strength']})",
                            f"💡 {advanced_signal['reason']}"
                        ],
                        'trade_params': {
                            'stop_loss': advanced_signal['stop_loss'],
                            'take_profit': None,  # 高级信号暂不设置止盈
                            'risk_reward': 0,
                            'max_hold_minutes': max_hold_minutes,
                            'entry_score': decision_score,
                            'signal_details': advanced_signal['details']
                        }
                    }

            # === 白名单检查 ===
            if symbol not in self.whitelist_long and symbol not in self.whitelist_main:
                return {
                    'decision': False,
                    'direction': None,
                    'score': 0,
                    'signal_source': 'whitelist',
                    'reasons': [f'❌ {symbol} 不在白名单中'],
                    'trade_params': {}
                }

            # === K线强度评分 (新增) ===
            kline_score_result = None
            kline_score = 0

            if self.enable_kline_scoring:
                try:
                    # 获取三周期K线强度
                    strength_1h = self.signal_analyzer.analyze_kline_strength(symbol, '1h', 24)
                    strength_15m = self.signal_analyzer.analyze_kline_strength(symbol, '15m', 24)
                    strength_5m = self.signal_analyzer.analyze_kline_strength(symbol, '5m', 24)

                    if all([strength_1h, strength_15m, strength_5m]):
                        # 计算K线强度评分
                        kline_score_result = self.kline_scorer.calculate_strength_score(
                            strength_1h, strength_15m, strength_5m
                        )
                        kline_score = kline_score_result['total_score']

                        logger.debug(f"{symbol} K线强度: {kline_score}/40分 ({kline_score_result['direction']}, {kline_score_result['strength']})")
                except Exception as e:
                    logger.warning(f"{symbol} K线强度评分失败: {e}")

            # === 传统多维度分析 ===
            # 加载K线
            klines_1d = self.load_klines(symbol, '1d', 50)
            klines_1h = self.load_klines(symbol, '1h', 100)

            if len(klines_1d) < 30 or len(klines_1h) < 50:
                return {
                    'decision': False,
                    'direction': None,
                    'score': 0,
                    'signal_source': 'analysis',
                    'reasons': ['数据不足'],
                    'trade_params': {}
                }

            # 多维度分析 (总分60分)
            pos_score, pos_reasons = self.analyze_position(klines_1d)
            trend_score, trend_reasons = self.analyze_trend(klines_1d, klines_1h)
            sr_score, sr_reasons, sr_data = self.analyze_support_resistance(klines_1h)

            traditional_score = pos_score + trend_score + sr_score

            # === 综合评分 = K线强度分(40) + 传统分析分(60) ===
            total_score = kline_score + traditional_score

            # 汇总理由
            all_reasons = []

            # K线强度原因 (优先显示)
            if kline_score_result:
                all_reasons.extend([f"【K线强度{kline_score}/40】"] + kline_score_result['reasons'])

            # 传统分析原因
            all_reasons.append(f"【传统分析{traditional_score}/60】")
            all_reasons.extend(pos_reasons)
            all_reasons.extend(trend_reasons)
            all_reasons.extend(sr_reasons)

            # === 决策判断 ===
            # 总分>=45分 且 K线强度>=15分
            decision = (total_score >= self.threshold) and (kline_score >= self.min_kline_score)

            # 确定方向
            if kline_score_result and kline_score_result['direction'] != 'NEUTRAL':
                # 优先使用K线强度方向
                direction = kline_score_result['direction']
            elif decision:
                # 回退到LONG (白名单默认)
                direction = 'LONG'
            else:
                direction = None

            # 只做LONG方向检查 (whitelist_long)
            if symbol in self.whitelist_long and symbol not in self.whitelist_main:
                if direction == 'SHORT':
                    decision = False
                    all_reasons.insert(0, f"⚠️ {symbol}仅允许LONG方向")

            result = {
                'decision': decision,
                'direction': direction if decision else None,
                'score': total_score,
                'kline_score': kline_score,
                'traditional_score': traditional_score,
                'threshold': self.threshold,
                'signal_source': 'kline_enhanced_analysis',
                'reasons': all_reasons,
                'trade_params': {},
                'kline_strength': kline_score_result  # 保存完整K线强度数据
            }

            if decision:
                # 根据K线强度确定持仓时长
                if kline_score_result:
                    entry_strategy = self.kline_scorer.get_entry_strategy(kline_score)
                    max_hold_minutes = entry_strategy['max_hold_minutes']
                else:
                    # 回退到传统评分
                    if total_score >= 60:
                        max_hold_minutes = 360
                    elif total_score >= 45:
                        max_hold_minutes = 240
                    else:
                        max_hold_minutes = 180

                result['trade_params'] = {
                    'stop_loss': sr_data['support'],
                    'take_profit': sr_data['resistance'],
                    'risk_reward': sr_data['risk_reward'],
                    'max_hold_minutes': max_hold_minutes,
                    'entry_score': total_score,
                    'kline_strength_score': kline_score
                }

                # 如果有K线强度数据,添加入场策略
                if kline_score_result:
                    entry_strategy = self.kline_scorer.get_entry_strategy(kline_score)
                    result['trade_params']['entry_strategy'] = entry_strategy

            return result

        except Exception as e:
            logger.error(f"❌ {symbol} 决策分析失败: {e}")
            return {
                'decision': False,
                'direction': None,
                'score': 0,
                'signal_source': 'error',
                'reasons': [f'分析失败: {str(e)}'],
                'trade_params': {}
            }

    def scan_all_symbols(self) -> List[Dict]:
        """
        扫描所有可交易币种,找出符合条件的交易机会

        优先扫描主流币 (可能有高级信号),再扫描白名单币种

        Returns:
            符合条件的交易机会列表
        """
        opportunities = []

        # 去重
        all_symbols = list(set(self.whitelist_main + self.whitelist_long))

        logger.info(f"🔍 开始扫描 {len(all_symbols)} 个币种 (主流币{len(self.whitelist_main)}个 + 白名单{len(self.whitelist_long)}个)...")

        # 优先扫描主流币
        priority_symbols = self.whitelist_main
        normal_symbols = [s for s in all_symbols if s not in priority_symbols]

        for symbol in priority_symbols + normal_symbols:
            try:
                result = self.should_trade(symbol)

                if result['decision']:
                    opportunity = {
                        'symbol': symbol,
                        'direction': result['direction'],
                        'score': result['score'],
                        'signal_source': result['signal_source'],
                        'reasons': result['reasons'],
                        'trade_params': result['trade_params']
                    }

                    # 如果是高级信号,添加额外信息
                    if result['signal_source'] == 'advanced_signal':
                        opportunity['signal_type'] = result['signal_type']
                        opportunity['signal_strength'] = result['signal_strength']

                    opportunities.append(opportunity)

                    # 日志输出
                    source_label = {
                        'advanced_signal': '🎯高级信号',
                        'kline_enhanced_analysis': '📊K线增强',
                        'analysis': '📊传统分析',
                        'whitelist': '⭐白名单'
                    }.get(result['signal_source'], '❓未知')

                    if result['signal_source'] == 'advanced_signal':
                        logger.info(f"✅ {symbol} | {source_label} | {result.get('signal_type', '')} | 评分{result['score']}")
                    elif result['signal_source'] == 'kline_enhanced_analysis':
                        kline_s = result.get('kline_score', 0)
                        trad_s = result.get('traditional_score', 0)
                        direction = result.get('direction', 'N/A')
                        logger.info(f"✅ {symbol} | {source_label} | {direction} | 总分{result['score']} (K线{kline_s}+分析{trad_s})")
                    else:
                        rr = result['trade_params'].get('risk_reward', 0)
                        logger.info(f"✅ {symbol} | {source_label} | 评分{result['score']} | 盈亏比{rr:.1f}:1")

            except Exception as e:
                logger.error(f"❌ {symbol} 扫描失败: {e}")
                continue

        logger.info(f"📊 扫描完成 | 找到 {len(opportunities)} 个交易机会")

        # 按评分排序 (高级信号优先)
        def sort_key(x):
            if x['signal_source'] == 'advanced_signal':
                return (1, x['score'])  # 高级信号优先
            else:
                return (0, x['score'])  # 普通信号其次

        opportunities.sort(key=sort_key, reverse=True)

        return opportunities
