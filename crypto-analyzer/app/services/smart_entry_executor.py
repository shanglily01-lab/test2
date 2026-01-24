"""
智能分批建仓执行器
基于动态价格评估体系和滚动窗口实现最优入场时机
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from loguru import logger

from app.services.price_sampler import PriceSampler


class SmartEntryExecutor:
    """智能分批建仓执行器"""

    def __init__(self, db_config: dict, live_engine, price_service):
        """
        初始化执行器

        Args:
            db_config: 数据库配置
            live_engine: 实盘交易引擎
            price_service: 价格服务（WebSocket）
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.price_service = price_service

        # 分批配置
        self.batch_ratio = [0.3, 0.3, 0.4]  # 30%/30%/40%
        self.time_window = 30  # 30分钟建仓窗口

    async def execute_entry(self, signal: Dict) -> Dict:
        """
        执行智能分批建仓

        流程：
        1. 启动后台采样器（滚动5分钟窗口）
        2. 前5分钟：建立初始基线
        3. 5-30分钟：基于实时更新的基线动态入场

        Args:
            signal: 开仓信号 {'symbol': str, 'direction': 'LONG'/'SHORT', 'amount': float}

        Returns:
            建仓结果 {'success': bool, 'plan': dict, 'avg_price': float}
        """
        symbol = signal['symbol']
        direction = signal['direction']
        signal_time = datetime.now()

        logger.info(f"🚀 {symbol} 开始智能建仓流程 | 方向: {direction}")

        # 初始化建仓计划
        plan = {
            'symbol': symbol,
            'direction': direction,
            'signal_time': signal_time,
            'batches': [
                {'ratio': 0.3, 'filled': False, 'price': None, 'time': None, 'score': None},
                {'ratio': 0.3, 'filled': False, 'price': None, 'time': None, 'score': None},
                {'ratio': 0.4, 'filled': False, 'price': None, 'time': None, 'score': None},
            ]
        }

        # 启动后台采样器（独立协程，持续运行30分钟）
        sampler = PriceSampler(symbol, self.price_service, window_seconds=300)
        sampling_task = asyncio.create_task(sampler.start_background_sampling())

        logger.info(f"📊 等待5分钟建立初始价格基线...")

        # 等待初始基线建立（最多等待6分钟）
        wait_start = datetime.now()
        while not sampler.initial_baseline_built:
            await asyncio.sleep(1)
            if (datetime.now() - wait_start).total_seconds() > 360:  # 6分钟超时
                logger.warning(f"{symbol} 基线建立超时，使用当前样本")
                break

        if sampler.baseline:
            baseline = sampler.baseline
            logger.info(
                f"✅ 初始基线: 范围 {baseline['min_price']:.6f} - {baseline['max_price']:.6f}, "
                f"中位数 {baseline['p50']:.6f}, "
                f"趋势 {baseline['trend']['direction']} ({baseline['trend']['change_pct']:.2f}%)"
            )

        # 动态入场执行（5-30分钟）
        logger.info(f"⚡ 开始动态入场执行（基线实时更新）...")

        try:
            while (datetime.now() - signal_time).total_seconds() < 1800:  # 总共30分钟
                current_price = await self._get_current_price(symbol)
                elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60

                # 获取实时更新的基线
                current_baseline = sampler.get_current_baseline()

                # 第1批建仓判断
                if not plan['batches'][0]['filled']:
                    should_fill, reason = await self._should_fill_batch1(
                        plan, current_price, current_baseline, sampler, elapsed_minutes
                    )
                    if should_fill:
                        await self._execute_batch(plan, 0, current_price, reason)

                # 第2批建仓判断
                elif not plan['batches'][1]['filled']:
                    should_fill, reason = await self._should_fill_batch2(
                        plan, current_price, current_baseline, elapsed_minutes
                    )
                    if should_fill:
                        await self._execute_batch(plan, 1, current_price, reason)

                # 第3批建仓判断
                elif not plan['batches'][2]['filled']:
                    should_fill, reason = await self._should_fill_batch3(
                        plan, current_price, current_baseline, elapsed_minutes
                    )
                    if should_fill:
                        await self._execute_batch(plan, 2, current_price, reason)
                        logger.info(f"🎉 {symbol} 全部建仓完成！")
                        break

                await asyncio.sleep(10)  # 每10秒检查一次

        finally:
            # 停止采样器
            sampler.stop_sampling()
            sampling_task.cancel()

        # 超时强制建仓剩余部分
        await self._force_fill_remaining(plan)

        # 计算平均成本
        avg_price = self._calculate_avg_price(plan)

        return {
            'success': True,
            'plan': plan,
            'avg_price': avg_price
        }

    async def _should_fill_batch1(
        self,
        plan: Dict,
        current_price: Decimal,
        baseline: Optional[Dict],
        sampler: PriceSampler,
        elapsed_minutes: float
    ) -> Tuple[bool, str]:
        """
        判断是否应该建仓第1批

        Returns:
            (是否建仓, 原因)
        """
        if not baseline:
            # 基线未建立，超过10分钟强制入场
            if elapsed_minutes >= 10:
                return True, f"基线未建立，超时入场(已{elapsed_minutes:.1f}分钟)"
            return False, ""

        direction = plan['direction']

        if direction == 'LONG':
            # 做多：评估当前价格
            evaluation = sampler.is_good_long_price(current_price)

            # 条件1: 价格评分>=80分（极优价格）
            if evaluation['score'] >= 80:
                return True, f"极优价格(评分{evaluation['score']}): {evaluation['reason']}"

            # 条件2: 价格评分>=60分 + 止跌信号
            if evaluation['score'] >= 60:
                signal_strength = sampler.detect_bottom_signal()
                if signal_strength >= 50:
                    return True, f"优秀价格(评分{evaluation['score']}) + 止跌信号({signal_strength}分)"

            # 条件3: 价格跌破基线最低价
            if float(current_price) <= baseline['min_price'] * 0.999:
                return True, f"突破基线最低价({baseline['min_price']:.6f})"

            # 条件4: 强上涨趋势 + 价格已升至p75以上（避免错过）
            if baseline['trend']['direction'] == 'up' and baseline['trend']['strength'] > 0.7:
                if float(current_price) >= baseline['p75']:
                    return True, f"强上涨趋势({baseline['trend']['change_pct']:.2f}%)，避免错过"

            # 条件5: 超时兜底（12分钟后价格合理即入场）
            if elapsed_minutes >= 12 and evaluation['score'] >= 40:
                return True, f"超时兜底(已{elapsed_minutes:.1f}分钟)，评分{evaluation['score']}"

            # 条件6: 强制超时（15分钟）
            if elapsed_minutes >= 15:
                return True, f"强制入场(已{elapsed_minutes:.1f}分钟)"

        else:  # SHORT
            # 做空：镜像逻辑
            evaluation = sampler.is_good_short_price(current_price)

            if evaluation['score'] >= 80:
                return True, f"极优价格(评分{evaluation['score']}): {evaluation['reason']}"

            if evaluation['score'] >= 60:
                signal_strength = sampler.detect_top_signal()
                if signal_strength >= 50:
                    return True, f"优秀价格(评分{evaluation['score']}) + 止涨信号({signal_strength}分)"

            if float(current_price) >= baseline['max_price'] * 1.001:
                return True, f"突破基线最高价({baseline['max_price']:.6f})"

            if baseline['trend']['direction'] == 'down' and baseline['trend']['strength'] > 0.7:
                if float(current_price) <= baseline['p25']:
                    return True, f"强下跌趋势({baseline['trend']['change_pct']:.2f}%)，避免错过"

            if elapsed_minutes >= 12 and evaluation['score'] >= 40:
                return True, f"超时兜底(已{elapsed_minutes:.1f}分钟)，评分{evaluation['score']}"

            if elapsed_minutes >= 15:
                return True, f"强制入场(已{elapsed_minutes:.1f}分钟)"

        return False, ""

    async def _should_fill_batch2(
        self,
        plan: Dict,
        current_price: Decimal,
        baseline: Optional[Dict],
        elapsed_minutes: float
    ) -> Tuple[bool, str]:
        """判断是否应该建仓第2批"""
        direction = plan['direction']
        batch1_price = plan['batches'][0]['price']
        batch1_time = plan['batches'][0]['time']

        if not batch1_price or not batch1_time:
            return False, ""

        time_since_batch1 = (datetime.now() - batch1_time).total_seconds() / 60

        # 至少等待3分钟
        if time_since_batch1 < 3:
            return False, ""

        batch1_price_float = float(batch1_price)
        current_price_float = float(current_price)

        if direction == 'LONG':
            # 条件1: 价格回调至第1批价格-0.3%（优质加仓点）
            if current_price_float <= batch1_price_float * 0.997:
                return True, f"回调加仓(第1批价{batch1_price:.6f}, 当前{current_price:.6f})"

            # 条件2: 价格仍低于p25分位数
            if baseline and current_price_float <= baseline['p25']:
                return True, f"价格仍在p25以下({baseline['p25']:.6f})"

            # 条件4: 超时兜底（距第1批10分钟）
            if time_since_batch1 >= 10:
                return True, f"超时建仓(距第1批{time_since_batch1:.1f}分钟)"

            # 条件5: 强制超时（距信号20分钟）
            if elapsed_minutes >= 20:
                return True, f"强制建仓(距信号{elapsed_minutes:.1f}分钟)"

        else:  # SHORT
            if current_price_float >= batch1_price_float * 1.003:
                return True, f"反弹加仓(第1批价{batch1_price:.6f}, 当前{current_price:.6f})"

            if baseline and current_price_float >= baseline['p75']:
                return True, f"价格仍在p75以上({baseline['p75']:.6f})"

            if time_since_batch1 >= 10:
                return True, f"超时建仓(距第1批{time_since_batch1:.1f}分钟)"

            if elapsed_minutes >= 20:
                return True, f"强制建仓(距信号{elapsed_minutes:.1f}分钟)"

        return False, ""

    async def _should_fill_batch3(
        self,
        plan: Dict,
        current_price: Decimal,
        baseline: Optional[Dict],
        elapsed_minutes: float
    ) -> Tuple[bool, str]:
        """判断是否应该建仓第3批"""
        direction = plan['direction']
        batch2_time = plan['batches'][1]['time']

        if not batch2_time:
            return False, ""

        time_since_batch2 = (datetime.now() - batch2_time).total_seconds() / 60

        # 至少等待3分钟
        if time_since_batch2 < 3:
            return False, ""

        # 计算前两批平均价
        avg_price = (float(plan['batches'][0]['price']) + float(plan['batches'][1]['price'])) / 2
        current_price_float = float(current_price)

        if direction == 'LONG':
            # 条件1: 价格不高于前两批平均价
            if current_price_float <= avg_price:
                return True, f"价格优于平均成本({avg_price:.6f})"

            # 条件2: 价格仍低于p50中位数
            if baseline and current_price_float <= baseline['p50']:
                return True, f"价格仍低于中位数({baseline['p50']:.6f})"

            # 条件3: 价格略高于平均价但在容忍范围（+0.3%）
            if current_price_float <= avg_price * 1.003:
                deviation = (current_price_float / avg_price - 1) * 100
                return True, f"价格接近平均成本(偏离{deviation:.2f}%)"

            # 条件4: 超时兜底（距第2批8分钟）
            if time_since_batch2 >= 8:
                return True, f"超时建仓(距第2批{time_since_batch2:.1f}分钟)"

            # 条件5: 强制超时（距信号28分钟）
            if elapsed_minutes >= 28:
                return True, f"强制完成建仓(距信号{elapsed_minutes:.1f}分钟)"

        else:  # SHORT
            if current_price_float >= avg_price:
                return True, f"价格优于平均成本({avg_price:.6f})"

            if baseline and current_price_float >= baseline['p50']:
                return True, f"价格仍高于中位数({baseline['p50']:.6f})"

            if current_price_float >= avg_price * 0.997:
                deviation = (1 - current_price_float / avg_price) * 100
                return True, f"价格接近平均成本(偏离{deviation:.2f}%)"

            if time_since_batch2 >= 8:
                return True, f"超时建仓(距第2批{time_since_batch2:.1f}分钟)"

            if elapsed_minutes >= 28:
                return True, f"强制完成建仓(距信号{elapsed_minutes:.1f}分钟)"

        return False, ""

    async def _execute_batch(self, plan: Dict, batch_num: int, price: Decimal, reason: str):
        """
        执行单批建仓

        Args:
            plan: 建仓计划
            batch_num: 批次编号（0,1,2）
            price: 入场价格
            reason: 入场原因
        """
        batch = plan['batches'][batch_num]

        # TODO: 调用实际开仓逻辑
        # await self.live_engine.open_position(
        #     symbol=plan['symbol'],
        #     direction=plan['direction'],
        #     size=batch['ratio'],
        #     price=price
        # )

        # 记录建仓信息
        batch['filled'] = True
        batch['price'] = price
        batch['time'] = datetime.now()

        logger.info(
            f"✅ {plan['symbol']} 第{batch_num+1}批建仓完成 | "
            f"价格: {price:.6f} | "
            f"比例: {batch['ratio']*100:.0f}% | "
            f"原因: {reason}"
        )

        # 计算当前平均成本
        filled_batches = [b for b in plan['batches'] if b['filled']]
        if len(filled_batches) > 0:
            total_weight = sum(b['ratio'] for b in filled_batches)
            avg_cost = sum(float(b['price']) * b['ratio'] for b in filled_batches) / total_weight
            logger.info(
                f"   当前平均成本: {avg_cost:.6f} | "
                f"已完成: {len(filled_batches)}/3批 ({total_weight*100:.0f}%)"
            )

    async def _force_fill_remaining(self, plan: Dict):
        """超时强制建仓剩余部分"""
        for i, batch in enumerate(plan['batches']):
            if not batch['filled']:
                current_price = await self._get_current_price(plan['symbol'])
                logger.warning(f"⚠️ 超时强制建仓第{i+1}批")
                await self._execute_batch(plan, i, current_price, "超时强制建仓")

    async def _get_current_price(self, symbol: str) -> Decimal:
        """获取当前价格"""
        try:
            price = self.price_service.get_price(symbol)
            if price:
                return Decimal(str(price))
            return Decimal('0')
        except Exception as e:
            logger.error(f"获取价格失败: {e}")
            return Decimal('0')

    def _calculate_avg_price(self, plan: Dict) -> float:
        """计算加权平均价格"""
        filled_batches = [b for b in plan['batches'] if b['filled'] and b['price']]
        if not filled_batches:
            return 0.0

        total_weight = sum(b['ratio'] for b in filled_batches)
        weighted_sum = sum(float(b['price']) * b['ratio'] for b in filled_batches)

        return weighted_sum / total_weight if total_weight > 0 else 0.0
