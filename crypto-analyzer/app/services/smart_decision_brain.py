#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能决策大脑 - 基于K线多维度分析的交易决策系统
纯粹基于K线数据,不依赖任何外部指标
"""

from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
from loguru import logger
import pymysql
from app.services.breakout_system import BreakoutSystem


class SmartDecisionBrain:
    """智能决策大脑"""

    def __init__(self, db_config: dict, exchange=None):
        """
        初始化决策大脑

        Args:
            db_config: 数据库配置
            exchange: 交易所接口（用于破位系统）
        """
        self.db_config = db_config
        self.connection = None
        self.exchange = exchange

        # 初始化破位系统
        if exchange:
            self.breakout_system = BreakoutSystem(exchange)
            logger.info("✅ 破位系统已初始化")
        else:
            self.breakout_system = None
            logger.warning("⚠️ 未提供exchange，破位系统未初始化")

        # 黑名单 - 表现较差不再交易的交易对 (2026-01-20更新)
        self.blacklist = [
            'IP/USDT',        # 亏损 $79.34 (2笔订单, 0%胜率)
            'VIRTUAL/USDT',   # 亏损 $35.65 (4笔订单, 0%胜率) - 从白名单移除
            'LDO/USDT',       # 亏损 $35.88 (5笔订单, 0%胜率) - 从白名单移除
            'ATOM/USDT',      # 亏损 $27.56 (5笔订单, 20%胜率)
            'ADA/USDT',       # 亏损 $22.87 (6笔订单, 0%胜率) - 从白名单移除
        ]

        # 白名单 - 只做LONG方向(基于回测数据,已移除黑名单币种)
        self.whitelist_long = [
            'BCH/USDT',    # 4笔 +1.28%, 100%胜率
            # 'LDO/USDT',  # 已加入黑名单 (实盘表现差)
            'ENA/USDT',    # 3笔 +1.26%, 100%胜率
            'WIF/USDT',    # 3笔 +0.84%, 100%胜率
            'TAO/USDT',    # 3笔 +0.80%, 100%胜率
            'DASH/USDT',   # 1笔 +2.10%
            'ETC/USDT',    # 2笔 +1.36%, 100%胜率
            # 'VIRTUAL/USDT', # 已加入黑名单 (实盘表现差)
            'NEAR/USDT',   # 1笔 +1.04%
            'AAVE/USDT',   # 1笔 +0.92%
            'SUI/USDT',    # 1笔 +0.88%
            'UNI/USDT',    # 3笔 +0.88%
            # 'ADA/USDT',  # 已加入黑名单 (实盘表现差)
            'SOL/USDT',    # 2笔 +0.47%
        ]

        # 决策阈值
        self.threshold = 30  # 最低30分才开仓

        logger.info(f"✅ 智能决策大脑已初始化 | 白名单币种: {len(self.whitelist_long)}个 | 黑名单币种: {len(self.blacklist)}个 | 阈值: {self.threshold}分")

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

        # 只读取合约K线数据 (exchange='binance_futures')
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

        # 反转顺序 (从旧到新)
        klines.reverse()

        # 转换数据类型
        for k in klines:
            k['open'] = float(k['open'])
            k['high'] = float(k['high'])
            k['low'] = float(k['low'])
            k['close'] = float(k['close'])
            k['volume'] = float(k['volume'])

        return klines

    def analyze_position(self, klines_1d: List[Dict]) -> Tuple[int, List[str]]:
        """
        分析价格位置 (30分)

        Args:
            klines_1d: 1日K线数据

        Returns:
            (评分, 理由列表)
        """
        if len(klines_1d) < 30:
            return 0, ['数据不足']

        # 计算30日高低点
        high_30d = max(k['high'] for k in klines_1d[-30:])
        low_30d = min(k['low'] for k in klines_1d[-30:])
        current = klines_1d[-1]['close']

        # 位置百分比
        if high_30d == low_30d:
            position_pct = 50.0
        else:
            position_pct = (current - low_30d) / (high_30d - low_30d) * 100

        # 7日涨幅
        if len(klines_1d) >= 7:
            price_7d_ago = klines_1d[-7]['close']
            gain_7d = (current - price_7d_ago) / price_7d_ago * 100
        else:
            gain_7d = 0

        score = 0
        reasons = []

        # LONG方向评分
        if position_pct < 30:
            score += 20
            reasons.append(f"✅ 底部区域({position_pct:.0f}%)")
        elif position_pct > 70:
            score -= 20
            reasons.append(f"❌ 顶部区域({position_pct:.0f}%)")
        else:
            score += 5
            reasons.append(f"⚠ 中部区域({position_pct:.0f}%)")

        # 涨幅评分
        if gain_7d < 10:
            score += 10
            reasons.append(f"✅ 7日涨幅适中({gain_7d:.1f}%)")
        elif gain_7d > 20:
            score -= 10
            reasons.append(f"❌ 7日涨幅过大({gain_7d:.1f}%)")

        return score, reasons

    def analyze_trend(self, klines_1d: List[Dict], klines_1h: List[Dict]) -> Tuple[int, List[str]]:
        """
        分析趋势强度 (20分)

        Args:
            klines_1d: 1日K线
            klines_1h: 1小时K线

        Returns:
            (评分, 理由列表)
        """
        score = 0
        reasons = []

        # 1D趋势
        if len(klines_1d) >= 30:
            bullish_1d = sum(1 for k in klines_1d[-30:] if k['close'] > k['open'])
            bullish_ratio = bullish_1d / 30

            if bullish_ratio > 0.6:  # 超过60%阳线
                score += 20
                reasons.append(f"✅ 1D强势({bullish_1d}/30阳线)")
            elif bullish_ratio < 0.4:
                score -= 5
                reasons.append(f"❌ 1D弱势({30-bullish_1d}/30阴线)")

        return score, reasons

    def analyze_support_resistance(self, klines_1h: List[Dict]) -> Tuple[int, List[str], Dict]:
        """
        分析支撑阻力和盈亏比 (30分)

        Args:
            klines_1h: 1小时K线

        Returns:
            (评分, 理由列表, 支撑阻力数据)
        """
        if len(klines_1h) < 50:
            return 0, ['数据不足'], {}

        recent = klines_1h[-100:] if len(klines_1h) >= 100 else klines_1h
        current = klines_1h[-1]['close']

        highs = [k['high'] for k in recent]
        lows = [k['low'] for k in recent]

        # 找阻力位和支撑位
        resistance_candidates = [h for h in highs if h > current]
        resistance = min(resistance_candidates) if resistance_candidates else max(highs)

        support_candidates = [l for l in lows if l < current]
        support = max(support_candidates) if support_candidates else min(lows)

        # 计算空间
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

    def check_breakout(self, current_positions: Dict = None) -> Dict:
        """
        检查Big4破位

        Args:
            current_positions: 当前持仓

        Returns:
            破位检测结果
        """
        if not self.breakout_system:
            return {
                'has_breakout': False,
                'message': '破位系统未初始化'
            }

        try:
            result = self.breakout_system.check_and_handle_breakout(current_positions)

            if result['has_breakout']:
                market = result['market']
                logger.warning(f"🔥 Big4破位检测: {market['direction']} | "
                             f"强度{market['strength']:.1f} | "
                             f"置信度{market['confidence']:.2f}")

                # 显示持仓处理结果
                if result.get('position_result'):
                    pos_result = result['position_result']
                    if pos_result['closed']:
                        logger.warning(f"⚠️ 已平仓 {len(pos_result['closed'])} 个反向仓位")
                    if pos_result['adjusted']:
                        logger.info(f"📊 已调整 {len(pos_result['adjusted'])} 个同向止损")

            return result

        except Exception as e:
            logger.error(f"❌ 破位检测失败: {e}")
            return {
                'has_breakout': False,
                'error': str(e)
            }

    def should_trade(self, symbol: str, base_score: int = None, signal_direction: str = None) -> Dict:
        """
        决策是否交易（含破位系统加权）

        Args:
            symbol: 交易对
            base_score: 外部提供的基础评分（如果为None则进行完整分析）
            signal_direction: 信号方向（'LONG' 或 'SHORT'，如果为None则只做LONG）

        Returns:
            决策结果字典
        """
        # 黑名单检查 (优先级最高)
        if symbol in self.blacklist:
            return {
                'decision': False,
                'direction': None,
                'score': 0,
                'reasons': [f'🚫 {symbol} 在黑名单中 (实盘表现较差)'],
                'trade_params': {}
            }

        # 白名单检查
        if symbol not in self.whitelist_long:
            return {
                'decision': False,
                'direction': None,
                'score': 0,
                'reasons': [f'❌ {symbol} 不在白名单中'],
                'trade_params': {}
            }

        # 🔥 V5.1优化: Big4强势反向时暂停白名单交易
        # 检查Big4破位状态，如果强度>=12且方向为BEARISH，禁止白名单做多
        if self.breakout_system:
            booster_status = self.breakout_system.booster.get_status()
            if booster_status['active']:
                big4_direction = booster_status['direction']
                big4_strength = booster_status['strength']

                # 白名单只做多，检查Big4是否为BEARISH且强度>=12
                if big4_direction == 'SHORT' and big4_strength >= 12:
                    return {
                        'decision': False,
                        'direction': None,
                        'score': 0,
                        'reasons': [f'🚫 Big4强势下跌(强度{big4_strength:.0f})，暂停白名单做多交易'],
                        'trade_params': {}
                    }

        try:
            # 如果没有提供基础评分，进行完整分析
            if base_score is None:
                # 加载K线
                klines_1d = self.load_klines(symbol, '1d', 50)
                klines_1h = self.load_klines(symbol, '1h', 100)

                if len(klines_1d) < 30 or len(klines_1h) < 50:
                    return {
                        'decision': False,
                        'direction': None,
                        'score': 0,
                        'reasons': ['数据不足'],
                        'trade_params': {}
                    }

                # 多维度分析
                pos_score, pos_reasons = self.analyze_position(klines_1d)
                trend_score, trend_reasons = self.analyze_trend(klines_1d, klines_1h)
                sr_score, sr_reasons, sr_data = self.analyze_support_resistance(klines_1h)

                # 综合评分（基础分）
                base_score = pos_score + trend_score + sr_score
                signal_direction = 'LONG'  # 白名单只做LONG
            else:
                # 使用提供的评分，但仍需加载数据获取支撑阻力
                klines_1h = self.load_klines(symbol, '1h', 100)
                if len(klines_1h) < 50:
                    return {
                        'decision': False,
                        'direction': None,
                        'score': 0,
                        'reasons': ['数据不足'],
                        'trade_params': {}
                    }

                sr_score, sr_reasons, sr_data = self.analyze_support_resistance(klines_1h)
                pos_reasons = []
                trend_reasons = []

            # 应用破位系统加权
            total_score = base_score
            breakout_boost = 0
            breakout_reasons = []

            if self.breakout_system:
                current_price = klines_1h[-1]['close']
                score_result = self.breakout_system.calculate_signal_score(
                    symbol=symbol,
                    base_score=base_score,
                    signal_direction=signal_direction,
                    current_price=current_price
                )

                breakout_boost = score_result.get('boost_score', 0)
                total_score = score_result.get('total_score', base_score)

                # 检查是否应该跳过（反向信号）
                if score_result.get('should_skip'):
                    return {
                        'decision': False,
                        'direction': None,
                        'score': total_score,
                        'reasons': [f"🚫 {score_result.get('skip_reason', '反向信号被过滤')}"],
                        'trade_params': {}
                    }

                # 检查是否应该生成（同向信号）
                if not score_result.get('should_generate'):
                    return {
                        'decision': False,
                        'direction': None,
                        'score': total_score,
                        'reasons': [f"❌ {score_result.get('generate_reason', '不满足开仓条件')}"],
                        'trade_params': {}
                    }

                # 添加破位加权说明
                if breakout_boost != 0:
                    breakout_reasons.append(
                        f"🔥 破位加权: {breakout_boost:+d}分 "
                        f"({score_result.get('generate_reason', '')})"
                    )

            # 汇总理由
            all_reasons = []
            all_reasons.extend(pos_reasons)
            all_reasons.extend(trend_reasons)
            all_reasons.extend(sr_reasons)
            all_reasons.extend(breakout_reasons)

            # 决策
            decision = total_score >= self.threshold

            result = {
                'decision': decision,
                'direction': signal_direction if decision else None,
                'score': total_score,
                'base_score': base_score,
                'breakout_boost': breakout_boost,
                'threshold': self.threshold,
                'reasons': all_reasons,
                'trade_params': {}
            }

            if decision:
                # 根据评分动态调整持仓时间
                if total_score >= 45:
                    max_hold_minutes = 360  # 6小时 (高置信度，45+分)
                elif total_score >= 30:
                    max_hold_minutes = 240  # 4小时 (中等置信度，30-44分)
                else:
                    max_hold_minutes = 120  # 2小时 (低置信度，<30分)

                # 添加交易参数
                result['trade_params'] = {
                    'stop_loss': sr_data['support'],
                    'take_profit': sr_data['resistance'],
                    'risk_reward': sr_data['risk_reward'],
                    'max_hold_minutes': max_hold_minutes,
                    'entry_score': total_score  # 存储开仓评分用于后续重评分
                }

                # 如果是破位信号生成，记录开仓
                if self.breakout_system and breakout_boost > 0:
                    self.breakout_system.record_opening(symbol)
                    logger.info(f"📝 记录破位开仓: {symbol}")

            return result

        except Exception as e:
            logger.error(f"❌ {symbol} 决策分析失败: {e}")
            return {
                'decision': False,
                'direction': None,
                'score': 0,
                'reasons': [f'分析失败: {str(e)}'],
                'trade_params': {}
            }

    def get_breakout_status(self) -> Dict:
        """
        获取破位系统状态

        Returns:
            破位系统状态信息
        """
        if not self.breakout_system:
            return {
                'active': False,
                'message': '破位系统未初始化'
            }

        try:
            status = self.breakout_system.get_system_status()

            # 添加active标志，基于booster和convergence状态
            booster_status = status.get('booster', {})
            convergence_status = status.get('convergence', {})

            return {
                'active': booster_status.get('active', False) and convergence_status.get('active', False),
                'market': booster_status,
                'convergence': convergence_status,
                'last_detection': status.get('last_detection')
            }
        except Exception as e:
            logger.error(f"❌ 获取破位状态失败: {e}")
            return {
                'active': False,
                'error': str(e)
            }

    def scan_all_symbols(self) -> List[Dict]:
        """
        扫描所有白名单币种,找出符合条件的交易机会

        Returns:
            符合条件的交易机会列表
        """
        opportunities = []

        logger.info(f"🔍 开始扫描 {len(self.whitelist_long)} 个币种...")

        for symbol in self.whitelist_long:
            try:
                result = self.should_trade(symbol)

                if result['decision']:
                    opportunities.append({
                        'symbol': symbol,
                        'direction': result['direction'],
                        'score': result['score'],
                        'base_score': result.get('base_score', result['score']),
                        'breakout_boost': result.get('breakout_boost', 0),
                        'reasons': result['reasons'],
                        'trade_params': result['trade_params']
                    })

                    boost_info = f" (+{result.get('breakout_boost', 0)}破位加权)" if result.get('breakout_boost', 0) > 0 else ""
                    logger.info(f"✅ {symbol} | 评分{result['score']}{boost_info} | "
                              f"盈亏比{result['trade_params']['risk_reward']:.1f}:1")

            except Exception as e:
                logger.error(f"❌ {symbol} 扫描失败: {e}")
                continue

        logger.info(f"📊 扫描完成 | 找到 {len(opportunities)} 个交易机会")

        # 按评分排序
        opportunities.sort(key=lambda x: x['score'], reverse=True)

        return opportunities
