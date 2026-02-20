"""
K线回调分批建仓执行器 V2
基于K线形态回调确认实现最优入场时机

核心策略：
- 做多：等待1根反向阴线作为回调确认
- 做空：等待1根反向阳线作为反弹确认
- 两级降级：15M（0-30分钟）→ 5M（30-60分钟）
- 纪律严明：宁愿错过，不追涨杀跌
"""
import asyncio
import json
import pymysql
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from loguru import logger
import pymysql


class KlinePullbackEntryExecutor:
    """K线回调分批建仓执行器"""

    def __init__(self, db_config: dict, live_engine, price_service, account_id=None):
        """
        初始化执行器

        Args:
            db_config: 数据库配置
            live_engine: 交易引擎
            price_service: 价格服务（WebSocket）
            account_id: 账户ID
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.price_service = price_service
        if account_id is not None:
            self.account_id = account_id
        else:
            self.account_id = getattr(live_engine, 'account_id', 2)

        # 分批配置
        self.batch_ratio = [0.3, 0.3, 0.4]  # 30%/30%/40%
        self.total_window_minutes = 60  # 总时间窗口60分钟
        self.primary_window_minutes = 30  # 第一阶段30分钟（15M）
        self.check_interval_seconds = 60  # 每60秒检查一次（K线更新频率）

    async def execute_entry(self, signal: Dict) -> Dict:
        """
        执行K线回调分批建仓

        流程：
        1. 阶段1（0-30分钟）：监控15M K线，等待1根反向K线
        2. 阶段2（30-60分钟）：切换到5M K线，等待1根反向K线
        3. 60分钟截止，能完成几批算几批

        Args:
            signal: 开仓信号 {
                'symbol': str,
                'direction': 'LONG'/'SHORT',
                'amount': float,
                'total_margin': float,
                'leverage': int
            }

        Returns:
            建仓结果 {'success': bool, 'plan': dict, 'avg_price': float}
        """
        symbol = signal['symbol']
        direction = signal['direction']

        # 🔥 关键修复：使用真实的信号触发时间，而不是重启时间
        # 如果signal中有signal_time，使用它；否则使用当前时间（新信号）
        signal_time = signal.get('signal_time', datetime.now())

        # 如果signal_time是字符串，转换为datetime
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time)

        logger.info(f"🚀 {symbol} 开始K线回调分批建仓 V2 | 方向: {direction}")
        logger.info(f"   信号时间: {signal_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"   策略: 1根反向K线确认 | 15M(0-30min) → 5M(30-60min)")

        # 🔥 确保symbol已订阅到WebSocket价格服务
        if self.price_service and hasattr(self.price_service, 'subscribe'):
            try:
                await self.price_service.subscribe([symbol])
                logger.debug(f"✅ {symbol} 已订阅到WebSocket价格服务")
            except Exception as e:
                logger.warning(f"⚠️ {symbol} WebSocket订阅失败: {e}，将使用数据库价格")

        # 初始化建仓计划
        plan = {
            'symbol': symbol,
            'direction': direction,
            'signal_time': signal_time,
            'total_margin': signal.get('total_margin', 400),
            'leverage': signal.get('leverage', 5),
            'batches': [
                {'ratio': self.batch_ratio[0], 'filled': False, 'price': None, 'time': None, 'reason': None, 'margin': None, 'quantity': None},
                {'ratio': self.batch_ratio[1], 'filled': False, 'price': None, 'time': None, 'reason': None, 'margin': None, 'quantity': None},
                {'ratio': self.batch_ratio[2], 'filled': False, 'price': None, 'time': None, 'reason': None, 'margin': None, 'quantity': None},
            ],
            'signal': signal,
            'phase': 'primary',  # primary=15M阶段, fallback=5M阶段
            'consecutive_reverse_count': 0  # 连续反向K线计数
        }

        # 🔥 立即创建数据库记录，持久化signal_time
        # 这样重启后可以继续基于原始signal_time执行，而不是重新开始
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 stop_loss_pct, take_profit_pct,
                 entry_signal_type, entry_score, signal_components,
                 batch_plan, batch_filled, entry_signal_time,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'smart_trader_batch', 'building', NOW(), NOW())
            """, (
                self.account_id,
                symbol,
                direction,
                0,  # quantity初始为0
                0,  # entry_price初始为0
                0,  # avg_entry_price初始为0
                plan['leverage'],
                0,  # notional_value初始为0
                0,  # margin初始为0
                None,  # stop_loss_price
                None,  # take_profit_price
                None,  # stop_loss_pct
                None,  # take_profit_pct
                'kline_pullback_v2',
                signal.get('trade_params', {}).get('entry_score', 0),
                json.dumps(signal.get('trade_params', {}).get('signal_components', {})),
                json.dumps(plan['batches']),
                json.dumps([]),  # batch_filled初始为空
                signal_time  # entry_signal_time
            ))

            position_id = cursor.lastrowid
            plan['position_id'] = position_id

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ {symbol} 创建V2持仓记录 | ID:{position_id} | 信号时间:{signal_time.strftime('%H:%M:%S')}")

        except Exception as e:
            logger.error(f"❌ {symbol} 创建持仓记录失败: {e}")
            return {
                'success': False,
                'error': f'创建持仓记录失败: {e}',
                'position_id': None
            }

        try:
            # 检查信号是否已过期
            elapsed_seconds = (datetime.now() - signal_time).total_seconds()
            if elapsed_seconds >= self.total_window_minutes * 60:
                logger.warning(f"⚠️ {symbol} 信号已过期 | 信号时间: {signal_time.strftime('%H:%M:%S')} | 已过: {elapsed_seconds/60:.1f}分钟 > {self.total_window_minutes}分钟窗口")
                return {
                    'success': False,
                    'error': f'信号已过期({elapsed_seconds/60:.0f}分钟)',
                    'position_id': None
                }

            # 执行分批建仓主循环
            logger.info(f"🔄 {symbol} 进入主循环，窗口时长: {self.total_window_minutes}分钟")
            while (datetime.now() - signal_time).total_seconds() < self.total_window_minutes * 60:
                elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60
                logger.debug(f"🔄 {symbol} 循环开始 | 已用时: {elapsed_minutes:.1f}分钟")
                current_price = await self._get_current_price(symbol)

                if not current_price:
                    logger.warning(f"⚠️ {symbol} 无法获取当前价格，等待{self.check_interval_seconds}秒后重试...")
                    await asyncio.sleep(self.check_interval_seconds)
                    continue

                logger.debug(f"🔄 {symbol} 当前价格: ${current_price} | 已用时: {elapsed_minutes:.1f}分钟")

                # 判断当前阶段（15M或5M）
                if elapsed_minutes < self.primary_window_minutes:
                    # 阶段1: 15M K线回调
                    timeframe = '15m'
                    plan['phase'] = 'primary'
                else:
                    # 阶段2: 30分钟后统一切换到5M（无论第1批是否完成）
                    timeframe = '5m'
                    plan['phase'] = 'fallback'
                    if plan.get('fallback_logged') != True:
                        completed = sum(1 for b in plan['batches'] if b['filled'])
                        logger.info(f"⏰ {symbol} 30分钟后切换到5M精准监控 | 已完成{completed}/3批")
                        plan['fallback_logged'] = True

                # 获取最近2根K线，判断是否连续反向
                # 根据阶段确定检测基准时间
                if plan['phase'] == 'primary':
                    # 15M阶段：从信号时间开始检测
                    detection_base_time = signal_time
                else:
                    # 5M阶段：从30分钟时刻开始检测
                    detection_base_time = signal_time + timedelta(minutes=self.primary_window_minutes)

                reverse_confirmed = await self._check_consecutive_reverse_klines(
                    symbol, direction, timeframe, count=1, signal_time=detection_base_time
                )

                if reverse_confirmed:
                    # 找到第一个未完成的批次
                    for batch_idx, batch in enumerate(plan['batches']):
                        if not batch['filled']:
                            reason = f"{timeframe.upper()}反向K线回调确认"
                            await self._execute_batch(plan, batch_idx, current_price, reason)
                            break

                # 检查是否全部完成
                if all(b['filled'] for b in plan['batches']):
                    logger.info(f"🎉 {symbol} 全部3批建仓完成！")
                    break

                await asyncio.sleep(self.check_interval_seconds)

        except Exception as e:
            logger.error(f"❌ {symbol} 分批建仓执行出错: {e}")

        # 建仓结束，统计结果
        filled_batches = [b for b in plan['batches'] if b['filled']]
        filled_count = len(filled_batches)

        if filled_count == 0:
            logger.warning(f"⚠️ {symbol} 建仓窗口结束，未完成任何批次（无回调机会，遵守纪律）")
            return {
                'success': False,
                'error': '无回调机会，未完成任何批次',
                'position_id': None
            }

        # 计算平均成本和总数量
        avg_price = self._calculate_avg_price(plan)
        total_quantity = sum(b.get('quantity', 0) for b in filled_batches)

        # 标记持仓为完全开仓状态
        await self._finalize_position(plan)

        position_id = plan.get('position_id')
        logger.info(
            f"✅ [KLINE_PULLBACK_COMPLETE] {symbol} {direction} | "
            f"持仓ID: {position_id} | "
            f"完成批次: {filled_count}/3 | "
            f"平均价格: ${avg_price:.4f} | "
            f"总数量: {total_quantity:.2f}"
        )

        return {
            'success': True,
            'position_id': position_id,
            'avg_price': avg_price,
            'total_quantity': total_quantity,
            'filled_batches': filled_count,
            'plan': plan
        }

    async def _check_consecutive_reverse_klines(
        self,
        symbol: str,
        direction: str,
        timeframe: str,
        count: int = 1,
        signal_time: datetime = None
    ) -> bool:
        """
        检查信号时间之后是否有反向K线

        Args:
            symbol: 交易对
            direction: 方向（LONG/SHORT）
            timeframe: 时间周期（15m/5m）
            count: 需要的K线数量（默认1根）
            signal_time: 信号时间（只检查此时间之后的K线）

        Returns:
            是否确认反向回调
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 🔥 数据库中symbol格式为 'RAY/USDT'（带斜杠），不需要转换

            # 🔥 关键逻辑：查询信号时间之后的**固定前N根**K线（非滑动窗口）
            # 例如：信号14:42触发，等待的是14:45和15:00这固定的2根15M K线
            # 而不是每次都取最近的2根（那样永远等不到）
            if signal_time:
                # 🔥 将Python datetime转换为Unix毫秒时间戳（数据库存储格式）
                signal_timestamp = int(signal_time.timestamp() * 1000)

                # 🔥 关键逻辑：查询信号后的前N根K线（包括当前进行中的K线）
                # K线数据是实时更新的，当前K线虽未完成但也有当前开盘价和收盘价
                # 不排除当前K线，直接取前N根进行判断
                cursor.execute("""
                    SELECT open_price, close_price, open_time
                    FROM kline_data
                    WHERE symbol = %s
                      AND timeframe = %s
                      AND exchange = 'binance_futures'
                      AND open_time > %s
                    ORDER BY open_time ASC
                    LIMIT %s
                """, (symbol, timeframe, signal_timestamp, count))
            else:
                # 兼容旧逻辑（无signal_time时）
                cursor.execute("""
                    SELECT open_price, close_price, open_time
                    FROM kline_data
                    WHERE symbol = %s
                      AND timeframe = %s
                      AND exchange = 'binance_futures'
                    ORDER BY open_time DESC
                    LIMIT %s
                """, (symbol, timeframe, count))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(klines) < count:
                return False

            # 判断K线方向
            reverse_count = 0
            kline_times = []
            for kline in klines:
                open_price = float(kline['open_price'])
                close_price = float(kline['close_price'])
                kline_times.append(kline['open_time'])

                if direction == 'LONG':
                    # 做多：需要阴线回调（close < open）
                    if close_price < open_price:
                        reverse_count += 1
                else:  # SHORT
                    # 做空：需要阳线反弹（close > open）
                    if close_price > open_price:
                        reverse_count += 1

            # 必须全部是反向K线
            is_confirmed = reverse_count == count

            # 调试日志
            if signal_time:
                kline_times_str = ', '.join([
                    datetime.fromtimestamp(kt / 1000).strftime('%H:%M') for kt in kline_times
                ]) if kline_times else '无'

                logger.info(
                    f"🔍 [{symbol}] {direction} {timeframe} K线检测 | "
                    f"信号时间: {signal_time.strftime('%H:%M:%S')} | "
                    f"检测到 {len(klines)}/{count} 根K线 [{kline_times_str}] | "
                    f"反向数: {reverse_count} | "
                    f"结果: {'✅确认' if is_confirmed else '❌未确认'}"
                )

            return is_confirmed

        except Exception as e:
            logger.error(f"❌ {symbol} 检查K线形态失败: {e}")
            import traceback
            traceback.print_exc()
            return False

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
        symbol = plan['symbol']
        direction = plan['direction']

        # 计算这一批的保证金和数量
        batch_margin = plan['total_margin'] * batch['ratio']
        batch_quantity = (batch_margin * plan['leverage']) / float(price)

        # 记录批次信息
        batch['filled'] = True
        batch['price'] = float(price)
        batch['time'] = datetime.now()
        batch['reason'] = reason
        batch['margin'] = batch_margin
        batch['quantity'] = batch_quantity

        logger.success(
            f"📈 [{batch_num + 1}/3批] {symbol} {direction} | "
            f"价格: ${price:.4f} | "
            f"数量: {batch_quantity:.2f} | "
            f"原因: {reason}"
        )

        # 第1批时创建持仓记录，后续批次更新持仓
        if batch_num == 0:
            await self._create_position_record(plan, price)
        else:
            await self._update_position_record(plan)

    async def _create_position_record(self, plan: Dict, entry_price: Decimal):
        """创建持仓记录（第1批）"""
        import json

        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            symbol = plan['symbol']
            direction = plan['direction']
            batch1 = plan['batches'][0]

            # 准备batch_plan JSON（保存完整的建仓计划）
            batch_plan_json = json.dumps({
                'batches': [
                    {'ratio': b['ratio']} for b in plan['batches']
                ],
                'total_margin': plan['total_margin'],
                'leverage': plan['leverage'],
                'signal_time': plan['signal_time'].isoformat(),
                'strategy': 'kline_pullback_v2'
            })

            # 准备batch_filled JSON（目前只有第1批）
            batch_filled_json = json.dumps({
                'batches': [{
                    'batch_num': 0,
                    'ratio': batch1['ratio'],
                    'price': batch1['price'],
                    'time': batch1['time'].isoformat(),
                    'margin': batch1['margin'],
                    'quantity': batch1['quantity'],
                    'reason': batch1['reason']
                }]
            })

            # 计算名义价值（quantity * entry_price）
            notional_value = batch1['quantity'] * float(entry_price)

            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type,
                 batch_plan, batch_filled, entry_signal_time,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                self.account_id,
                symbol,
                direction,  # LONG/SHORT
                batch1['quantity'],
                float(entry_price),
                float(entry_price),  # avg_entry_price（第1批时与entry_price相同）
                plan['leverage'],
                notional_value,
                batch1['margin'],
                batch1['time'],
                None,  # 止损后续设置
                None,  # 止盈后续设置
                'kline_pullback_v2',  # entry_signal_type存储策略类型
                batch_plan_json,
                batch_filled_json,
                plan['signal_time'],
                'smart_trader_batch',  # source
                'building'  # status = building（分批建仓中）
            ))

            position_id = cursor.lastrowid
            plan['position_id'] = position_id

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ {symbol} 创建持仓记录 | ID: {position_id}")

        except Exception as e:
            logger.error(f"❌ {symbol} 创建持仓记录失败: {e}")

    async def _update_position_record(self, plan: Dict):
        """更新持仓记录（第2、3批）"""
        import json

        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            position_id = plan.get('position_id')
            if not position_id:
                return

            # 计算新的平均成本和总数量
            filled_batches = [b for b in plan['batches'] if b['filled']]
            total_quantity = sum(b['quantity'] for b in filled_batches)
            total_cost = sum(b['price'] * b['quantity'] for b in filled_batches)
            avg_price = total_cost / total_quantity if total_quantity > 0 else 0
            total_margin = sum(b['margin'] for b in filled_batches)

            # 更新batch_filled JSON
            batch_filled_json = json.dumps({
                'batches': [
                    {
                        'batch_num': i,
                        'ratio': b['ratio'],
                        'price': b['price'],
                        'time': b['time'].isoformat(),
                        'margin': b['margin'],
                        'quantity': b['quantity'],
                        'reason': b['reason']
                    }
                    for i, b in enumerate(plan['batches']) if b['filled']
                ]
            })

            cursor.execute("""
                UPDATE futures_positions
                SET entry_price = %s,
                    quantity = %s,
                    margin = %s,
                    batch_filled = %s
                WHERE id = %s
            """, (avg_price, total_quantity, total_margin, batch_filled_json, position_id))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"❌ 更新持仓记录失败: {e}")

    async def _finalize_position(self, plan: Dict):
        """完成建仓，标记持仓为完全开仓状态"""
        try:
            position_id = plan.get('position_id')
            if not position_id:
                return

            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            # 从building（分批建仓中）改为open（正式持仓）
            cursor.execute("""
                UPDATE futures_positions
                SET status = 'open', updated_at = NOW()
                WHERE id = %s
            """, (position_id,))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"❌ 完成建仓标记失败: {e}")

    async def _get_current_price(self, symbol: str) -> Optional[Decimal]:
        """获取当前价格"""
        try:
            if self.price_service:
                price = self.price_service.get_price(symbol)
                if price and price > 0:
                    return Decimal(str(price))

            # 回退到数据库
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 数据库中symbol格式为 'ENSO/USDT'（带斜杠），直接使用
            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '5m' AND exchange = 'binance_futures'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return Decimal(str(result['close_price']))

        except Exception as e:
            logger.error(f"❌ 获取价格失败: {e}")

        return None

    def _calculate_avg_price(self, plan: Dict) -> float:
        """计算平均成本"""
        filled_batches = [b for b in plan['batches'] if b['filled']]
        if not filled_batches:
            return 0

        total_cost = sum(b['price'] * b['quantity'] for b in filled_batches)
        total_quantity = sum(b['quantity'] for b in filled_batches)

        return total_cost / total_quantity if total_quantity > 0 else 0

    async def recover_building_positions(self):
        """
        恢复未完成的分批建仓任务

        系统重启后，继续完成未完成的批次（如果还在60分钟窗口内）
        """
        import json

        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询所有building状态的持仓（分批建仓中）
            cursor.execute("""
                SELECT id, symbol, position_side, batch_plan, batch_filled, entry_signal_time
                FROM futures_positions
                WHERE account_id = %s
                AND status = 'building'
                AND entry_signal_type = 'kline_pullback_v2'
                ORDER BY entry_signal_time ASC
            """, (self.account_id,))

            partial_positions = cursor.fetchall()
            cursor.close()
            conn.close()

            if not partial_positions:
                logger.info("✅ [V2-RECOVERY] 没有需要恢复的building状态持仓")
                return

            logger.info(f"🔄 [V2-RECOVERY] 发现 {len(partial_positions)} 个building状态持仓，开始恢复...")

            for pos in partial_positions:
                try:
                    await self._recover_single_position(pos)
                except Exception as e:
                    logger.error(f"❌ [V2-RECOVERY] 恢复持仓 {pos['id']} 失败: {e}")

            logger.info(f"✅ [V2-RECOVERY] 恢复任务完成")

        except Exception as e:
            logger.error(f"❌ [V2-RECOVERY] 恢复任务失败: {e}")

    async def _recover_single_position(self, pos: Dict):
        """恢复单个未完成的持仓"""
        import json

        position_id = pos['id']
        symbol = pos['symbol']
        direction = pos['position_side']  # 使用正确的字段名

        # 解析batch_plan和batch_filled
        try:
            batch_plan = json.loads(pos['batch_plan']) if pos['batch_plan'] else None
            batch_filled = json.loads(pos['batch_filled']) if pos['batch_filled'] else None
        except:
            logger.warning(f"⚠️ [V2-RECOVERY] 持仓 {position_id} batch数据解析失败，标记为completed")
            await self._mark_position_completed(position_id)
            return

        if not batch_plan or not batch_filled:
            logger.warning(f"⚠️ [V2-RECOVERY] 持仓 {position_id} 缺少batch数据，标记为completed")
            await self._mark_position_completed(position_id)
            return

        # 解析信号时间
        signal_time = pos['entry_signal_time']
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time)

        # 计算已过去时间和剩余时间
        elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60
        remaining_minutes = self.total_window_minutes - elapsed_minutes

        if remaining_minutes <= 0:
            # 超时，标记为completed
            logger.info(
                f"⏰ [V2-RECOVERY] 持仓 {position_id} ({symbol} {direction}) "
                f"已超过60分钟窗口 (已过{elapsed_minutes:.1f}分钟)，标记为completed"
            )
            await self._mark_position_completed(position_id)
            return

        # 重建plan对象
        filled_count = len(batch_filled['batches'])
        total_batches = len(batch_plan['batches'])

        logger.info(
            f"🔄 [V2-RECOVERY] 恢复持仓 {position_id} ({symbol} {direction}) | "
            f"已完成 {filled_count}/{total_batches} 批次 | "
            f"剩余时间 {remaining_minutes:.1f}分钟"
        )

        # 重建plan
        plan = {
            'position_id': position_id,
            'symbol': symbol,
            'direction': direction,
            'signal_time': signal_time,
            'total_margin': batch_plan['total_margin'],
            'leverage': batch_plan['leverage'],
            'batches': [],
            'phase': 'primary' if elapsed_minutes < self.primary_window_minutes else 'fallback'
        }

        # 重建batches数组
        for i, batch_spec in enumerate(batch_plan['batches']):
            # 检查这一批是否已完成
            filled_batch = next((b for b in batch_filled['batches'] if b['batch_num'] == i), None)

            if filled_batch:
                # 已完成的批次
                plan['batches'].append({
                    'ratio': batch_spec['ratio'],
                    'filled': True,
                    'price': filled_batch['price'],
                    'time': datetime.fromisoformat(filled_batch['time']),
                    'reason': filled_batch['reason'],
                    'margin': filled_batch['margin'],
                    'quantity': filled_batch['quantity']
                })
            else:
                # 未完成的批次
                plan['batches'].append({
                    'ratio': batch_spec['ratio'],
                    'filled': False,
                    'price': None,
                    'time': None,
                    'reason': None,
                    'margin': None,
                    'quantity': None
                })

        # 继续执行建仓流程（从当前时间点继续）
        try:
            while (datetime.now() - signal_time).total_seconds() < self.total_window_minutes * 60:
                elapsed_minutes = (datetime.now() - signal_time).total_seconds() / 60
                current_price = await self._get_current_price(symbol)

                if not current_price:
                    await asyncio.sleep(self.check_interval_seconds)
                    continue

                # 判断当前阶段（15M或5M）
                if elapsed_minutes < self.primary_window_minutes:
                    timeframe = '15m'
                    plan['phase'] = 'primary'
                else:
                    # 阶段2: 如果第1批未完成，切换到5M
                    if not plan['batches'][0]['filled']:
                        timeframe = '5m'
                        plan['phase'] = 'fallback'
                        logger.info(f"⏰ [V2-RECOVERY] {symbol} 切换到5M监控")
                    else:
                        timeframe = '15m'

                # 判断是否有反向K线
                reverse_confirmed = await self._check_consecutive_reverse_klines(
                    symbol, direction, timeframe, count=1, signal_time=signal_time
                )

                if reverse_confirmed:
                    # 找到第一个未完成的批次
                    for batch_idx, batch in enumerate(plan['batches']):
                        if not batch['filled']:
                            reason = f"{timeframe.upper()}反向K线回调确认(恢复)"
                            await self._execute_batch(plan, batch_idx, current_price, reason)
                            break

                # 检查是否全部完成
                if all(b['filled'] for b in plan['batches']):
                    logger.info(f"🎉 [V2-RECOVERY] {symbol} 全部批次建仓完成！")
                    await self._finalize_position(plan)
                    return

                await asyncio.sleep(self.check_interval_seconds)

            # 时间窗口结束
            logger.info(f"⏰ [V2-RECOVERY] {symbol} 建仓窗口结束，标记为completed")
            await self._finalize_position(plan)

        except Exception as e:
            logger.error(f"❌ [V2-RECOVERY] {symbol} 恢复执行失败: {e}")
            await self._mark_position_completed(position_id)

    async def _mark_position_completed(self, position_id: int):
        """标记持仓为open（完成分批建仓）"""
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE futures_positions
                SET status = 'open', updated_at = NOW()
                WHERE id = %s
            """, (position_id,))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"❌ 标记持仓 {position_id} 为open失败: {e}")
