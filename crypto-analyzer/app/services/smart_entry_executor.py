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
        self.account_id = 2  # 模拟盘账户ID

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
            'total_margin': signal.get('total_margin', 400),
            'leverage': signal.get('leverage', 5),
            'batches': [
                {'ratio': 0.3, 'filled': False, 'price': None, 'time': None, 'score': None, 'margin': None, 'quantity': None},
                {'ratio': 0.3, 'filled': False, 'price': None, 'time': None, 'score': None, 'margin': None, 'quantity': None},
                {'ratio': 0.4, 'filled': False, 'price': None, 'time': None, 'score': None, 'margin': None, 'quantity': None},
            ],
            'signal': signal  # 保存原始信号用于创建持仓记录
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

        # 计算平均成本和总数量
        avg_price = self._calculate_avg_price(plan)
        total_quantity = sum(b.get('quantity', 0) for b in plan['batches'] if b.get('filled'))

        # 检查是否所有批次都完成
        if all(b['filled'] for b in plan['batches']):
            try:
                # 创建持仓记录
                position_id = await self._create_position_record(plan)

                logger.info(
                    f"✅ [BATCH_ENTRY_COMPLETE] {symbol} {direction} | "
                    f"持仓ID: {position_id} | "
                    f"平均价格: ${avg_price:.4f} | "
                    f"总数量: {total_quantity:.2f}"
                )

                return {
                    'success': True,
                    'position_id': position_id,
                    'avg_price': avg_price,
                    'total_quantity': total_quantity,
                    'plan': plan
                }
            except Exception as e:
                logger.error(f"❌ 创建持仓记录失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return {
                    'success': False,
                    'error': f'创建持仓记录失败: {e}'
                }
        else:
            logger.error(f"❌ {symbol} 建仓未完成，部分批次失败")
            return {
                'success': False,
                'error': '建仓未完成'
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

        # 计算这一批的保证金和数量（模拟盘，不调用交易所API）
        batch_margin = plan['total_margin'] * batch['ratio']
        batch_quantity = (batch_margin * plan['leverage']) / float(price)

        # 记录建仓信息
        batch['filled'] = True
        batch['price'] = float(price)
        batch['time'] = datetime.now()
        batch['margin'] = batch_margin
        batch['quantity'] = batch_quantity

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

    async def _create_position_record(self, plan: Dict) -> int:
        """
        创建分批建仓持仓记录（模拟盘，不调用交易所API）

        Args:
            plan: 建仓计划

        Returns:
            position_id: 持仓ID
        """
        import pymysql
        import json

        conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        try:
            symbol = plan['symbol']
            direction = plan['direction']
            signal = plan['signal']

            # 计算汇总数据
            total_quantity = sum(b['quantity'] for b in plan['batches'] if b['filled'])
            avg_price = self._calculate_avg_price(plan)
            total_margin = sum(b['margin'] for b in plan['batches'] if b['filled'])

            # 准备 batch_plan 和 batch_filled JSON
            batch_plan_json = json.dumps({
                'batches': [
                    {
                        'ratio': b['ratio'],
                        'timeout_minutes': [15, 20, 28][i]
                    }
                    for i, b in enumerate(plan['batches'])
                ]
            })

            batch_filled_json = json.dumps({
                'batches': [
                    {
                        'ratio': b['ratio'],
                        'price': b['price'],
                        'time': b['time'].isoformat() if b['time'] else None,
                        'margin': b['margin'],
                        'quantity': b['quantity']
                    }
                    for b in plan['batches'] if b['filled']
                ]
            })

            # 计算计划平仓时间（基于 entry_score）
            entry_score = signal.get('trade_params', {}).get('entry_score', 30)
            if entry_score >= 45:
                max_hold_minutes = 360  # 6小时
            elif entry_score >= 30:
                max_hold_minutes = 240  # 4小时
            else:
                max_hold_minutes = 120  # 2小时

            from datetime import timedelta
            planned_close_time = datetime.now() + timedelta(minutes=max_hold_minutes)

            # 计算止损止盈价格
            adaptive_params = signal.get('trade_params', {}).get('adaptive_params', {})
            stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)
            take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)

            if direction == 'LONG':
                stop_loss = avg_price * (1 - stop_loss_pct)
                take_profit = avg_price * (1 + take_profit_pct)
            else:  # SHORT
                stop_loss = avg_price * (1 + stop_loss_pct)
                take_profit = avg_price * (1 - take_profit_pct)

            # 插入持仓记录
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, entry_score, signal_components,
                 batch_plan, batch_filled, entry_signal_time, planned_close_time,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, 'smart_trader_batch', 'open', NOW(), NOW())
            """, (
                self.account_id,
                symbol,
                direction,
                total_quantity,
                avg_price,  # entry_price 使用平均价
                avg_price,  # avg_entry_price
                plan['leverage'],
                total_quantity * avg_price,  # notional_value
                total_margin,
                stop_loss,
                take_profit,
                signal.get('trade_params', {}).get('signal_combination_key', 'batch_entry'),
                entry_score,
                json.dumps(signal.get('trade_params', {}).get('signal_components', {})),
                batch_plan_json,
                batch_filled_json,
                plan['signal_time'],
                planned_close_time
            ))

            position_id = cursor.lastrowid

            # 冻结保证金
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET current_balance = current_balance - %s,
                    frozen_balance = frozen_balance + %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (total_margin, total_margin, self.account_id))

            conn.commit()

            logger.info(
                f"📝 持仓记录已创建: ID={position_id} | "
                f"{symbol} {direction} | "
                f"数量: {total_quantity:.2f} | "
                f"平均价: ${avg_price:.4f} | "
                f"保证金: ${total_margin:.0f}"
            )

            return position_id

        except Exception as e:
            conn.rollback()
            logger.error(f"创建持仓记录失败: {e}")
            raise
        finally:
            cursor.close()
            conn.close()
