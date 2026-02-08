#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
超级大脑V3.0主控制器
负责集成: 评分系统 + 5M精准入场 + 移动止盈管理
"""

import asyncio
import json
import pymysql
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

# 导入V3模块
from app.services.smart_entry_executor_v3 import SmartEntryExecutorV3
from app.services.position_manager_v3 import PositionManagerV3
from app.strategies.signal_scorer_v3 import SignalScorerV3

load_dotenv()


class SmartTraderV3Controller:
    """超级大脑V3.0主控制器"""

    def __init__(self, config_path: str = 'config/v3_config.json'):
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        # 数据库配置
        self.db_config = {
            'host': os.getenv('DB_HOST'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'database': os.getenv('DB_NAME')
        }

        # 初始化V3模块
        self.entry_executor = SmartEntryExecutorV3(self.db_config)
        self.position_manager = PositionManagerV3(self.db_config)
        self.signal_scorer = SignalScorerV3(self.db_config)

        # 应用配置到各模块
        self._apply_config()

        # 持仓管理任务
        self.position_tasks = {}

        print(f"\n{'='*100}")
        print(f"超级大脑V3.0控制器初始化完成")
        print(f"版本: {self.config['version']}")
        print(f"状态: {'启用' if self.config['enabled'] else '禁用'}")
        print(f"{'='*100}\n")

    def _apply_config(self):
        """应用配置到各模块"""
        # 入场执行器配置
        if self.config['entry_config']['enabled']:
            self.entry_executor.entry_timeout = self.config['entry_config']['entry_timeout_minutes']
            self.entry_executor.check_interval = self.config['entry_config'].get('check_interval_seconds', 30)

        # 持仓管理器配置
        if self.config['position_management']['enabled']:
            pm_config = self.config['position_management']
            self.position_manager.trailing_threshold_usd = pm_config['trailing_stop']['threshold_usd']
            self.position_manager.trailing_step_usd = pm_config['trailing_stop']['step_usd']
            self.position_manager.fixed_stop_loss_pct = pm_config['fixed_stop_loss_pct']
            self.position_manager.fixed_take_profit_pct = pm_config['fixed_take_profit_pct']
            self.position_manager.max_holding_minutes = pm_config['max_holding_minutes']

        # 评分系统配置
        if self.config['scoring_system']['enabled']:
            self.signal_scorer.score_weights = self.config['scoring_system']['weights']
            self.signal_scorer.max_score = self.config['scoring_system']['max_score']
            self.signal_scorer.min_score_to_trade = self.config['scoring_system']['min_score_to_trade']

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            cursorclass=pymysql.cursors.DictCursor
        )

    async def detect_sudden_move(
        self,
        symbol: str,
        position_side: str,
        klines_15m: List[Dict]
    ) -> Dict:
        """
        🔥 突然拉升检测器 - 抓住价格突然拉起/下跌 + 放量

        检测条件:
        1. 最近5-15分钟价格变化 >= 1%
        2. 量能 >= 平均量的1.5倍
        3. 方向与position_side一致

        Args:
            symbol: 交易对
            position_side: LONG/SHORT
            klines_15m: 15M K线列表

        Returns:
            检测结果字典
        """
        if len(klines_15m) < 6:
            return {'detected': False, 'reason': 'K线数据不足'}

        # 获取最新K线和前5根的平均量
        latest = klines_15m[0]
        avg_volume = sum(k['volume'] for k in klines_15m[1:6]) / 5

        # 计算价格变化百分比
        price_change_pct = abs(latest['close'] - latest['open']) / latest['open']

        # 检查量能放大
        volume_ratio = latest['volume'] / avg_volume if avg_volume > 0 else 0

        # LONG: 检测突然上涨
        if position_side == 'LONG':
            if latest['close'] > latest['open']:  # 阳线
                if price_change_pct >= 0.01 and volume_ratio >= 1.5:
                    return {
                        'detected': True,
                        'reason': f"突然拉升: 涨幅{price_change_pct*100:.2f}%, 量能{volume_ratio:.2f}x",
                        'price_change_pct': price_change_pct,
                        'volume_ratio': volume_ratio,
                        'bypass_score': True  # 🔥 直接开仓，绕过评分
                    }

        # SHORT: 检测突然下跌
        elif position_side == 'SHORT':
            if latest['close'] < latest['open']:  # 阴线
                if price_change_pct >= 0.01 and volume_ratio >= 1.5:
                    return {
                        'detected': True,
                        'reason': f"突然下跌: 跌幅{price_change_pct*100:.2f}%, 量能{volume_ratio:.2f}x",
                        'price_change_pct': price_change_pct,
                        'volume_ratio': volume_ratio,
                        'bypass_score': True
                    }

        return {'detected': False, 'reason': '无突然拉升信号'}

    async def detect_pullback(
        self,
        symbol: str,
        position_side: str,
        klines_15m: List[Dict]
    ) -> Dict:
        """
        🔥 回调买入检测器 - 已经涨起来的等回调再次买入

        检测条件:
        1. 最近2小时(8根K线)有明显上涨/下跌 (累计变化 >= 2%)
        2. 最新K线出现回调 (1.5%-4%)
        3. 回调后有反弹确认 (最新K线方向与趋势一致)

        Args:
            symbol: 交易对
            position_side: LONG/SHORT
            klines_15m: 15M K线列表

        Returns:
            检测结果字典
        """
        if len(klines_15m) < 8:
            return {'detected': False, 'reason': 'K线数据不足'}

        # 计算最近8根K线的累计涨跌幅
        oldest_price = klines_15m[7]['open']
        current_price = klines_15m[0]['close']
        total_change_pct = (current_price - oldest_price) / oldest_price

        # 计算回调幅度 (从最近8根的最高点/最低点)
        if position_side == 'LONG':
            # 找最近8根的最高点
            highest_price = max(k['high'] for k in klines_15m[:8])
            pullback_pct = (highest_price - current_price) / highest_price

            # 检查: 1.整体上涨>=2% 2.回调1.5%-4% 3.最新K线是阳线(反弹确认)
            if total_change_pct >= 0.02 and 0.015 <= pullback_pct <= 0.04:
                if klines_15m[0]['close'] > klines_15m[0]['open']:  # 反弹确认
                    return {
                        'detected': True,
                        'reason': f"上涨回调买入: 累计涨幅{total_change_pct*100:.2f}%, 回调{pullback_pct*100:.2f}%",
                        'total_change_pct': total_change_pct,
                        'pullback_pct': pullback_pct,
                        'bypass_score': True
                    }

        elif position_side == 'SHORT':
            # 找最近8根的最低点
            lowest_price = min(k['low'] for k in klines_15m[:8])
            pullback_pct = (current_price - lowest_price) / lowest_price

            # 检查: 1.整体下跌>=2% 2.反弹1.5%-4% 3.最新K线是阴线(下跌确认)
            if total_change_pct <= -0.02 and 0.015 <= pullback_pct <= 0.04:
                if klines_15m[0]['close'] < klines_15m[0]['open']:  # 下跌确认
                    return {
                        'detected': True,
                        'reason': f"下跌反弹做空: 累计跌幅{abs(total_change_pct)*100:.2f}%, 反弹{pullback_pct*100:.2f}%",
                        'total_change_pct': total_change_pct,
                        'pullback_pct': pullback_pct,
                        'bypass_score': True
                    }

        return {'detected': False, 'reason': '无回调买入信号'}

    async def process_signal(
        self,
        symbol: str,
        position_side: str,
        big4_signal: str,
        big4_strength: int
    ) -> Optional[Dict]:
        """
        处理交易信号

        流程:
        1. 获取K线数据
        2. 🔥 优先检测突然拉升/回调买入 (直接开仓)
        3. 如果没有特殊信号，计算常规信号评分
        4. 如果评分达标，执行5M精准入场
        5. 启动持仓管理

        Args:
            symbol: 交易对
            position_side: LONG/SHORT
            big4_signal: Big4信号
            big4_strength: Big4强度

        Returns:
            处理结果
        """
        print(f"\n{'='*100}")
        print(f"[V3信号处理] {symbol} {position_side}")
        print(f"Big4: {big4_signal} (强度: {big4_strength})")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*100}\n")

        # 1. 检查是否已有持仓
        if await self._has_open_position(symbol, position_side):
            print(f"⚠️ {symbol} {position_side} 已有持仓，跳过")
            return None

        # 2. 检查交易对评级
        margin_amount = await self._get_margin_amount(symbol)
        if margin_amount == 0:
            print(f"⚠️ {symbol} 在黑名单3级，禁止交易")
            return None

        # 3. 获取K线数据
        klines_5h = await self._get_klines(symbol, '5h', 3)
        klines_15m = await self._get_klines(symbol, '15m', 20)

        if not klines_5h or not klines_15m:
            print(f"❌ 无法获取K线数据")
            return None

        # 4. 🔥 优先检测突然拉升
        sudden_move = await self.detect_sudden_move(symbol, position_side, klines_15m)
        if sudden_move['detected']:
            print(f"\n⚡ 检测到突然拉升信号!")
            print(f"   {sudden_move['reason']}")
            print(f"   🚀 直接开仓，绕过评分系统\n")

            # 直接执行入场
            entry_result = await self.entry_executor.execute_entry(
                signal={'signal_type': 'SUDDEN_MOVE', 'score': 999},
                symbol=symbol,
                position_side=position_side,
                total_margin=margin_amount,
                leverage=10
            )

            if entry_result:
                position = await self._save_position_to_db(
                    entry_result=entry_result,
                    score_result={'total_score': 999, 'breakdown': {'sudden_move': True}},
                    big4_signal=big4_signal,
                    big4_strength=big4_strength
                )
                await self._start_position_management(position)
                return position

        # 5. 🔥 检测回调买入
        pullback = await self.detect_pullback(symbol, position_side, klines_15m)
        if pullback['detected']:
            print(f"\n📉 检测到回调买入信号!")
            print(f"   {pullback['reason']}")
            print(f"   🚀 直接开仓，绕过评分系统\n")

            # 直接执行入场
            entry_result = await self.entry_executor.execute_entry(
                signal={'signal_type': 'PULLBACK', 'score': 999},
                symbol=symbol,
                position_side=position_side,
                total_margin=margin_amount,
                leverage=10
            )

            if entry_result:
                position = await self._save_position_to_db(
                    entry_result=entry_result,
                    score_result={'total_score': 999, 'breakdown': {'pullback': True}},
                    big4_signal=big4_signal,
                    big4_strength=big4_strength
                )
                await self._start_position_management(position)
                return position

        # 6. 🔥 Big4否决权检查 - 逆势不开仓
        if self._check_big4_veto(position_side, big4_signal, big4_strength):
            print(f"\n🚫 Big4否决: {big4_signal} 强度{big4_strength} 与 {position_side} 方向相反")
            print(f"   逆势风险过高，放弃开仓\n")
            return None

        # 7. 常规评分流程
        score_result = self.signal_scorer.calculate_total_score(
            symbol=symbol,
            position_side=position_side,
            klines_5h=klines_5h,
            klines_15m=klines_15m,
            big4_signal=big4_signal,
            big4_strength=big4_strength
        )

        # 8. 检查评分是否达标
        if not score_result['can_trade']:
            print(f"❌ 评分{score_result['total_score']:.1f}不达标 (阈值{score_result['max_score']})")
            return None

        print(f"\n✅ 评分达标，准备入场")

        # 6. 执行5M精准入场
        entry_result = await self.entry_executor.execute_entry(
            signal={'signal_type': 'V3_SIGNAL', 'score': score_result['total_score']},
            symbol=symbol,
            position_side=position_side,
            total_margin=margin_amount,
            leverage=10
        )

        if not entry_result:
            print(f"❌ 入场失败")
            return None

        # 7. 保存持仓到数据库
        position = await self._save_position_to_db(
            entry_result=entry_result,
            score_result=score_result,
            big4_signal=big4_signal,
            big4_strength=big4_strength
        )

        # 8. 启动持仓管理
        await self._start_position_management(position)

        print(f"\n{'='*100}")
        print(f"[V3信号处理完成] {symbol} {position_side}")
        print(f"持仓ID: {position['id']}")
        print(f"{'='*100}\n")

        return position

    def _check_big4_veto(
        self,
        position_side: str,
        big4_signal: str,
        big4_strength: int
    ) -> bool:
        """
        🔥 Big4否决权检查 - 逆势不开仓

        规则:
        1. LONG + Big4 BEAR强度>=10 → 否决
        2. SHORT + Big4 BULL强度>=10 → 否决

        Args:
            position_side: LONG/SHORT
            big4_signal: Big4信号
            big4_strength: Big4强度

        Returns:
            True=否决, False=通过
        """
        if not big4_signal or not big4_strength:
            return False

        # 标准化Big4信号
        normalized_signal = big4_signal.upper()
        if 'BULL' in normalized_signal:
            normalized_signal = 'BULL'
        elif 'BEAR' in normalized_signal:
            normalized_signal = 'BEAR'

        # 检查是否逆势
        if position_side == 'LONG' and normalized_signal == 'BEAR':
            if big4_strength >= 10:
                return True  # 否决LONG开仓
        elif position_side == 'SHORT' and normalized_signal == 'BULL':
            if big4_strength >= 10:
                return True  # 否决SHORT开仓

        return False

    async def _has_open_position(self, symbol: str, position_side: str) -> bool:
        """检查是否已有持仓"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE symbol = %s
                AND position_side = %s
                AND status = 'open'
            """, (symbol, position_side))

            result = cursor.fetchone()
            return result['count'] > 0

        finally:
            cursor.close()
            conn.close()

    async def _get_margin_amount(self, symbol: str) -> float:
        """获取交易对的保证金额度"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT rating_level
                FROM trading_symbol_rating
                WHERE symbol = %s
            """, (symbol,))

            result = cursor.fetchone()

            if not result:
                return self.config['margin_management']['default']

            rating = result['rating_level']
            margin_config = self.config['margin_management']

            if rating == 0:
                return margin_config['whitelist']
            elif rating == 1:
                return margin_config['blacklist_level_1']
            elif rating == 2:
                return margin_config['blacklist_level_2']
            elif rating == 3:
                return 0  # 禁止交易
            else:
                return margin_config['default']

        finally:
            cursor.close()
            conn.close()

    async def _get_klines(
        self,
        symbol: str,
        interval: str,
        count: int
    ) -> Optional[List[Dict]]:
        """
        从数据库获取K线数据

        Args:
            symbol: 交易对
            interval: 时间间隔 (5m, 15m, 5h等)
            count: K线数量

        Returns:
            K线列表 (最新的在前面)，获取失败返回None
        """
        try:
            conn = self.db_config
            import pymysql
            connection = pymysql.connect(
                host=conn['host'],
                user=conn['user'],
                password=conn['password'],
                database=conn['database'],
                cursorclass=pymysql.cursors.DictCursor
            )
            cursor = connection.cursor()

            # 获取K线数据
            cursor.execute("""
                SELECT open_price, close_price, high_price, low_price, volume, open_time
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                ORDER BY open_time DESC
                LIMIT %s
            """, (symbol, interval, count))

            rows = cursor.fetchall()
            cursor.close()
            connection.close()

            if not rows:
                return None

            # 转换为标准格式 (最新的在前面)
            klines = []
            for row in rows:
                klines.append({
                    'symbol': symbol,
                    'interval': interval,
                    'open': float(row['open_price']),
                    'close': float(row['close_price']),
                    'high': float(row['high_price']),
                    'low': float(row['low_price']),
                    'volume': float(row['volume']),
                    'timestamp': row['open_time']
                })

            return klines

        except Exception as e:
            print(f"[错误] 获取K线失败 {symbol} {interval}: {e}")
            return None

    async def _save_position_to_db(
        self,
        entry_result: Dict,
        score_result: Dict,
        big4_signal: str,
        big4_strength: int
    ) -> Dict:
        """保存持仓到数据库"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            # 插入持仓记录
            cursor.execute("""
                INSERT INTO futures_positions
                (symbol, position_side, entry_price, quantity, status,
                 entry_score, entry_signal_type, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'open', %s, %s, NOW(), NOW())
            """, (
                entry_result['symbol'],
                entry_result['position_side'],
                entry_result['avg_entry_price'],
                entry_result['total_quantity'],
                score_result['total_score'],
                f"V3_Big4_{big4_signal}_{big4_strength}"
            ))

            position_id = cursor.lastrowid
            conn.commit()

            print(f"✅ 持仓已保存到数据库，ID: {position_id}")

            return {
                'id': position_id,
                'symbol': entry_result['symbol'],
                'position_side': entry_result['position_side'],
                'entry_price': entry_result['avg_entry_price'],
                'quantity': entry_result['total_quantity'],
                'created_at': datetime.now()
            }

        except Exception as e:
            print(f"❌ 保存持仓失败: {e}")
            conn.rollback()
            return None

        finally:
            cursor.close()
            conn.close()

    async def _start_position_management(self, position: Dict) -> None:
        """启动持仓管理"""
        position_id = position['id']

        # 创建持仓管理任务
        task = asyncio.create_task(
            self.position_manager.manage_position(position)
        )

        self.position_tasks[position_id] = task
        print(f"✅ 持仓管理已启动 (ID: {position_id})")

    async def run_monitoring_loop(self):
        """主监控循环 - 持续监控信号"""
        print(f"\n{'='*100}")
        print(f"V3监控循环启动")
        print(f"{'='*100}\n")

        while True:
            try:
                # TODO: 从信号检测器获取信号
                # signals = await self._get_pending_signals()

                # 模拟信号
                signals = [
                    {
                        'symbol': 'BTC/USDT',
                        'position_side': 'LONG',
                        'big4_signal': 'BULL',
                        'big4_strength': 75
                    }
                ]

                for signal in signals:
                    await self.process_signal(
                        symbol=signal['symbol'],
                        position_side=signal['position_side'],
                        big4_signal=signal['big4_signal'],
                        big4_strength=signal['big4_strength']
                    )

                # 等待60秒再检查
                await asyncio.sleep(60)

            except Exception as e:
                print(f"❌ 监控循环异常: {e}")
                await asyncio.sleep(30)


# 测试代码
async def test_v3_controller():
    """测试V3控制器"""
    controller = SmartTraderV3Controller()

    # 处理一个信号
    result = await controller.process_signal(
        symbol='BTC/USDT',
        position_side='LONG',
        big4_signal='BULL',
        big4_strength=75
    )

    if result:
        print(f"\n✅ 信号处理成功")
        print(f"持仓ID: {result['id']}")
        print(f"交易对: {result['symbol']}")
        print(f"方向: {result['position_side']}")

        # 等待持仓管理完成 (或Ctrl+C退出)
        await asyncio.Future()
    else:
        print(f"\n❌ 信号处理失败")


if __name__ == '__main__':
    try:
        asyncio.run(test_v3_controller())
    except KeyboardInterrupt:
        print("\n用户中断")
