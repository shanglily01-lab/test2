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
from app.services.volatility_calculator import get_volatility_calculator


class SmartEntryExecutor:
    """智能分批建仓执行器"""

    def __init__(self, db_config: dict, live_engine, price_service, account_id=None):
        """
        初始化执行器

        Args:
            db_config: 数据库配置
            live_engine: 交易引擎（用于同步等操作）
            price_service: 价格服务（WebSocket）
            account_id: 账户ID（可选，如果不提供则从live_engine获取或默认为2）
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.price_service = price_service
        # 优先使用传入的account_id，其次从live_engine获取，最后默认为2
        if account_id is not None:
            self.account_id = account_id
        else:
            self.account_id = getattr(live_engine, 'account_id', 2)

        # 分批配置
        self.batch_ratio = [0.3, 0.3, 0.4]  # 30%/30%/40%
        self.time_window = 30  # 30分钟建仓窗口 (配合K线强度评分: 15/30/45分钟)

    async def execute_entry(self, signal: Dict) -> Dict:
        """
        执行智能分批建仓

        流程：
        1. 根据K线强度调整建仓策略 (新增)
        2. 启动后台采样器（滚动5分钟窗口）
        3. 前5分钟：建立初始基线
        4. 动态入场：基于实时更新的基线动态入场

        Args:
            signal: 开仓信号 {
                'symbol': str,
                'direction': 'LONG'/'SHORT',
                'amount': float,
                'kline_strength': dict (可选 - K线强度评分结果)
            }

        Returns:
            建仓结果 {'success': bool, 'plan': dict, 'avg_price': float}
        """
        symbol = signal['symbol']
        direction = signal['direction']
        signal_time = datetime.now()

        # === 根据K线强度调整建仓策略 (新增) ===
        entry_strategy = signal.get('entry_strategy')
        kline_strength = signal.get('kline_strength')

        if entry_strategy:
            # 使用K线强度推荐的策略
            self.batch_ratio = entry_strategy['batch_ratio']
            self.time_window = entry_strategy['window_minutes']
            entry_mode = entry_strategy['mode']

            logger.info(f"🚀 {symbol} 开始智能建仓 | 方向: {direction} | 策略: {entry_mode}")
            logger.info(f"   K线强度: {kline_strength['total_score']}/40分 ({kline_strength['direction']}, {kline_strength['strength']})")
            logger.info(f"   建仓窗口: {self.time_window}分钟 | 分批比例: {self.batch_ratio}")
        else:
            # 使用默认策略
            self.batch_ratio = [0.3, 0.3, 0.4]
            self.time_window = 30
            logger.info(f"🚀 {symbol} 开始智能建仓流程 | 方向: {direction} (默认策略)")

        # 初始化建仓计划
        plan = {
            'symbol': symbol,
            'direction': direction,
            'signal_time': signal_time,
            'total_margin': signal.get('total_margin', 400),
            'leverage': signal.get('leverage', 5),
            'batches': [
                {'ratio': self.batch_ratio[0], 'filled': False, 'price': None, 'time': None, 'score': None, 'margin': None, 'quantity': None},
                {'ratio': self.batch_ratio[1], 'filled': False, 'price': None, 'time': None, 'score': None, 'margin': None, 'quantity': None},
                {'ratio': self.batch_ratio[2], 'filled': False, 'price': None, 'time': None, 'score': None, 'margin': None, 'quantity': None},
            ],
            'signal': signal,  # 保存原始信号用于创建持仓记录
            'kline_strength': kline_strength  # 保存K线强度数据
        }

        # 启动后台采样器（15分钟滚动窗口）
        sampler = PriceSampler(symbol, self.price_service, window_seconds=900)
        sampling_task = asyncio.create_task(sampler.start_background_sampling())

        logger.info(f"📊 等待15分钟建立初始价格基线（采集更全面的价格数据）...")

        # 等待初始基线建立（最多等待15分钟）
        wait_start = datetime.now()
        while not sampler.initial_baseline_built:
            await asyncio.sleep(1)
            if (datetime.now() - wait_start).total_seconds() > 900:  # 15分钟超时
                logger.warning(f"{symbol} 基线建立超时，使用当前样本")
                break

        if sampler.baseline:
            baseline = sampler.baseline
            logger.info(
                f"✅ 初始基线: 范围 {baseline['min_price']:.6f} - {baseline['max_price']:.6f}, "
                f"中位数 {baseline['p50']:.6f}, "
                f"趋势 {baseline['trend']['direction']} ({baseline['trend']['change_pct']:.2f}%)"
            )

        # 动态入场执行（根据策略调整时间窗口）
        max_window_seconds = self.time_window * 60
        logger.info(f"⚡ 开始动态入场执行（窗口{self.time_window}分钟，基线实时更新）...")

        try:
            while (datetime.now() - signal_time).total_seconds() < max_window_seconds:
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

        # 不再强制建仓，买了几批算几批
        filled_batches = [b for b in plan['batches'] if b['filled']]
        filled_count = len(filled_batches)

        if filled_count == 0:
            logger.error(f"❌ {symbol} 建仓窗口结束，没有完成任何批次")
            return {
                'success': False,
                'error': '没有完成任何批次',
                'position_id': None
            }

        # 计算平均成本和总数量
        avg_price = self._calculate_avg_price(plan)
        total_quantity = sum(b.get('quantity', 0) for b in filled_batches)

        # 标记持仓为完全开仓状态（无论完成几批）
        await self._finalize_position(plan)

        position_id = plan.get('position_id')
        logger.info(
            f"✅ [BATCH_ENTRY_COMPLETE] {symbol} {direction} | "
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

    async def _should_fill_batch1(
        self,
        plan: Dict,
        current_price: Decimal,
        baseline: Optional[Dict],
        sampler: PriceSampler,
        elapsed_minutes: float
    ) -> Tuple[bool, str]:
        """
        判断是否应该建仓第1批（基于90分位数阈值）

        Returns:
            (是否建仓, 原因)
        """
        if not baseline:
            # 基线未建立，不入场
            return False, "基线未建立"

        direction = plan['direction']

        if direction == 'LONG':
            # 做多：价格必须 <= p90
            evaluation = sampler.is_good_long_price(current_price)

            if evaluation['suitable']:
                return True, evaluation['reason']

        else:  # SHORT
            # 做空：价格必须 >= p90
            evaluation = sampler.is_good_short_price(current_price)

            if evaluation['suitable']:
                return True, evaluation['reason']

        return False, ""

    async def _should_fill_batch2(
        self,
        plan: Dict,
        current_price: Decimal,
        baseline: Optional[Dict],
        elapsed_minutes: float
    ) -> Tuple[bool, str]:
        """判断是否应该建仓第2批（基于90分位数阈值）"""
        if not baseline:
            return False, ""

        batch1_time = plan['batches'][0]['time']
        if not batch1_time:
            return False, ""

        time_since_batch1 = (datetime.now() - batch1_time).total_seconds() / 60

        # 至少等待1分钟
        if time_since_batch1 < 1:
            return False, ""

        direction = plan['direction']
        current_price_float = float(current_price)

        if direction == 'LONG':
            # 做多：价格 <= p90
            if current_price_float <= baseline['p90']:
                return True, f"价格{current_price_float:.6f} <= p90({baseline['p90']:.6f})"

        else:  # SHORT
            # 做空：价格 >= p90
            if current_price_float >= baseline['p90']:
                return True, f"价格{current_price_float:.6f} >= p90({baseline['p90']:.6f})"

        return False, ""

    async def _should_fill_batch3(
        self,
        plan: Dict,
        current_price: Decimal,
        baseline: Optional[Dict],
        elapsed_minutes: float
    ) -> Tuple[bool, str]:
        """判断是否应该建仓第3批（基于90分位数阈值）"""
        if not baseline:
            return False, ""

        batch2_time = plan['batches'][1]['time']
        if not batch2_time:
            return False, ""

        time_since_batch2 = (datetime.now() - batch2_time).total_seconds() / 60

        # 至少等待1分钟
        if time_since_batch2 < 1:
            return False, ""

        direction = plan['direction']
        current_price_float = float(current_price)

        if direction == 'LONG':
            # 做多：价格 <= p90
            if current_price_float <= baseline['p90']:
                return True, f"价格{current_price_float:.6f} <= p90({baseline['p90']:.6f})"

        else:  # SHORT
            # 做空：价格 >= p90
            if current_price_float >= baseline['p90']:
                return True, f"价格{current_price_float:.6f} >= p90({baseline['p90']:.6f})"

        return False, ""

    async def _execute_batch(self, plan: Dict, batch_num: int, price: Decimal, reason: str):
        """
        执行单批建仓（立即创建或更新持仓记录）

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

        # 🔥 每批都创建独立的持仓记录，不再更新同一个持仓
        position_id = await self._create_position_record(plan, batch_num)
        logger.info(f"📝 创建独立持仓记录 #{position_id} (第{batch_num+1}批)")

        # 计算当前平均成本
        filled_batches = [b for b in plan['batches'] if b['filled']]
        if len(filled_batches) > 0:
            total_weight = sum(b['ratio'] for b in filled_batches)
            avg_cost = sum(float(b['price']) * b['ratio'] for b in filled_batches) / total_weight
            logger.info(
                f"   当前平均成本: {avg_cost:.6f} | "
                f"已完成: {len(filled_batches)}/3批 ({total_weight*100:.0f}%)"
            )

    # 已移除强制完成逻辑 - 买了几批算几批，不再强制完成3批
    # async def _force_fill_remaining(self, plan: Dict):
    #     """超时强制建仓剩余部分"""
    #     for i, batch in enumerate(plan['batches']):
    #         if not batch['filled']:
    #             current_price = await self._get_current_price(plan['symbol'])
    #             logger.warning(f"⚠️ 超时强制建仓第{i+1}批")
    #             await self._execute_batch(plan, i, current_price, "超时强制建仓")

    async def _get_current_price(self, symbol: str) -> Decimal:
        """
        获取当前价格（多级降级策略）

        优先级:
        1. WebSocket实时价格
        2. REST API实时价格
        3. 数据库最新K线价格
        """
        # 第1级: 尝试从WebSocket获取
        try:
            ws_price = self.price_service.get_price(symbol)
            if ws_price and ws_price > 0:
                logger.debug(f"[价格获取] {symbol} 使用WebSocket价格: {ws_price}")
                return Decimal(str(ws_price))
        except Exception as e:
            logger.warning(f"[价格获取] {symbol} WebSocket获取失败: {e}")

        # 第2级: 降级到REST API实时价格
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            symbol_clean = symbol.replace('/', '').upper()

            session = requests.Session()
            retry_strategy = Retry(
                total=2,
                backoff_factor=0.1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)

            # 根据交易对类型选择API
            if symbol.endswith('/USD'):
                # 币本位合约使用dapi
                api_url = 'https://dapi.binance.com/dapi/v1/ticker/price'
                symbol_for_api = symbol_clean + '_PERP'
            else:
                # U本位合约使用fapi
                api_url = 'https://fapi.binance.com/fapi/v1/ticker/price'
                symbol_for_api = symbol_clean

            response = session.get(
                api_url,
                params={'symbol': symbol_for_api},
                timeout=3
            )

            if response.status_code == 200:
                data = response.json()
                # 币本位API返回数组，U本位返回对象
                if isinstance(data, list) and len(data) > 0:
                    rest_price = float(data[0]['price'])
                else:
                    rest_price = float(data['price'])

                if rest_price > 0:
                    logger.info(f"[价格获取] {symbol} 降级到REST API价格: {rest_price}")
                    return Decimal(str(rest_price))
        except Exception as e:
            logger.warning(f"[价格获取] {symbol} REST API获取失败: {e}")

        # 第3级: 最后降级到数据库K线价格
        try:
            import pymysql

            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            # 优先使用5m K线
            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '5m'
                ORDER BY open_time DESC
                LIMIT 1
            """, (symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result and result[0]:
                db_price = float(result[0])
                if db_price > 0:
                    logger.warning(f"[价格获取] {symbol} 降级到数据库K线价格: {db_price}")
                    return Decimal(str(db_price))
        except Exception as e:
            logger.error(f"[价格获取] {symbol} 数据库获取失败: {e}")

        # 所有方法都失败，返回0并记录错误
        logger.error(f"[价格获取] ❌ {symbol} 所有价格获取方法均失败，无法开仓！")
        return Decimal('0')

    def _calculate_avg_price(self, plan: Dict) -> float:
        """计算加权平均价格"""
        filled_batches = [b for b in plan['batches'] if b['filled'] and b['price']]
        if not filled_batches:
            return 0.0

        total_weight = sum(b['ratio'] for b in filled_batches)
        weighted_sum = sum(float(b['price']) * b['ratio'] for b in filled_batches)

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    async def _create_position_record(self, plan: Dict, batch_num: int = 0) -> int:
        """
        创建持仓记录 - 每批都创建独立的持仓记录

        Args:
            plan: 建仓计划
            batch_num: 批次序号（0/1/2）

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
            batch = plan['batches'][batch_num]  # 🔥 获取当前批次

            # ========== 🔥 已移除防重复检查，支持同一方向多个独立持仓 ==========
            # 每批建仓都创建独立的持仓记录，不再限制"同一方向只能1个持仓"
            # 这样分批建仓的逻辑更清晰，每个持仓独立计算盈亏，独立平仓
            # cursor.execute("""
            #     SELECT id, status, created_at
            #     FROM futures_positions
            #     WHERE symbol = %s
            #     AND position_side = %s
            #     AND status IN ('building', 'open')
            #     AND account_id = %s
            #     ORDER BY created_at DESC
            #     LIMIT 1
            # """, (symbol, direction, self.account_id))
            #
            # existing = cursor.fetchone()
            # if existing:
            #     existing_id = existing['id']
            #     existing_status = existing['status']
            #     existing_time = existing['created_at']
            #     logger.warning(
            #         f"⚠️ 跳过重复信号: {symbol} {direction} 已有持仓 "
            #         f"(ID:{existing_id}, 状态:{existing_status}, 创建于:{existing_time})"
            #     )
            #     cursor.close()
            #     conn.close()
            #     # 返回已存在的持仓ID，不创建新持仓
            #     return existing_id

            # 第1批的数据
            quantity = batch['quantity']
            price = batch['price']
            margin = batch['margin']

            # 准备 batch_plan JSON
            # 优化后的分批时间: 1小时内完成 (前15分钟采集样本, 然后30/45/60分钟执行)
            batch_plan_json = json.dumps({
                'batches': [
                    {'ratio': b['ratio'], 'timeout_minutes': [30, 45, 60][i]}
                    for i, b in enumerate(plan['batches'])
                ]
            })

            # 准备 batch_filled JSON (当前批次)
            batch_filled_json = json.dumps({
                'batches': [{
                    'batch_num': batch_num,
                    'ratio': batch['ratio'],
                    'price': batch['price'],
                    'time': batch['time'].isoformat(),
                    'margin': batch['margin'],
                    'quantity': batch['quantity']
                }]
            })

            # 计算止损止盈 (使用基于波动率的动态计算)
            volatility_calc = get_volatility_calculator()
            entry_score = signal.get('trade_params', {}).get('entry_score', 30)
            signal_components = list(signal.get('trade_params', {}).get('signal_components', {}).keys())

            stop_loss_pct, take_profit_pct, calc_reason = volatility_calc.get_sl_tp_for_position(
                symbol=symbol,
                position_side=direction,
                entry_score=entry_score,
                signal_components=signal_components
            )

            logger.info(f"[{symbol}] {direction} 止损止盈计算: SL={stop_loss_pct}% TP={take_profit_pct}% | {calc_reason}")

            # 转换为小数(百分比转为0.xx格式)
            stop_loss_pct_decimal = stop_loss_pct / 100
            take_profit_pct_decimal = take_profit_pct / 100

            if direction == 'LONG':
                stop_loss = price * (1 - stop_loss_pct_decimal)
                take_profit = price * (1 + take_profit_pct_decimal)
            else:
                stop_loss = price * (1 + stop_loss_pct_decimal)
                take_profit = price * (1 - take_profit_pct_decimal)

            # 🔥 插入持仓记录（每批都是独立持仓，直接设置为'open'）
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 stop_loss_pct, take_profit_pct,
                 entry_signal_type, entry_score, signal_components,
                 batch_plan, batch_filled, entry_signal_time,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'smart_trader_batch', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, direction, quantity, price, price,
                plan['leverage'], quantity * price, margin,
                stop_loss, take_profit,
                stop_loss_pct, take_profit_pct,
                signal.get('trade_params', {}).get('signal_combination_key', 'batch_entry'),
                signal.get('trade_params', {}).get('entry_score', 30),
                json.dumps(signal.get('trade_params', {}).get('signal_components', {})),
                batch_plan_json, batch_filled_json,
                plan['signal_time']
            ))

            position_id = cursor.lastrowid

            # 冻结当前批次保证金
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET current_balance = current_balance - %s,
                    frozen_balance = frozen_balance + %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (margin, margin, self.account_id))

            conn.commit()
            logger.info(f"✅ 第{batch_num+1}批建仓完成，创建独立持仓记录 | ID:{position_id}")
            return position_id

        except Exception as e:
            conn.rollback()
            logger.error(f"创建持仓记录失败（第{batch_num+1}批）: {e}")
            raise
        finally:
            cursor.close()
            conn.close()


    # 🔥 已废弃：_update_position 方法已移除
    # 不再更新同一个持仓，每批都创建独立持仓记录

    async def _finalize_position(self, plan: Dict):
        """🔥 已废弃：每批都直接创建为'open'状态，不需要从'building'转换"""
        # 保留空方法以保持向后兼容
        pass

    async def recover_building_positions(self):
        """
        恢复building状态的持仓,继续完成分批建仓
        在系统启动时调用,确保重启不会丢失建仓任务
        """
        import pymysql
        import json

        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 查询所有building状态的持仓（排除V2 K线回调策略的记录）
            cursor.execute("""
                SELECT
                    id, symbol, position_side, batch_plan, batch_filled,
                    created_at, entry_signal_time
                FROM futures_positions
                WHERE account_id = %s
                AND status = 'building'
                AND (entry_signal_type IS NULL OR entry_signal_type != 'kline_pullback_v2')
                ORDER BY created_at ASC
            """, (self.account_id,))

            building_positions = cursor.fetchall()
            cursor.close()
            conn.close()

            if not building_positions:
                logger.info("✅ 没有需要恢复的building状态持仓")
                return

            logger.info(f"🔄 发现 {len(building_positions)} 个building状态持仓,开始恢复...")

            for pos in building_positions:
                try:
                    await self._recover_single_position(pos)
                except Exception as e:
                    logger.error(f"恢复持仓 {pos['id']} 失败: {e}")

        except Exception as e:
            logger.error(f"恢复building状态持仓失败: {e}")

    async def _recover_single_position(self, pos: Dict):
        """恢复单个building状态的持仓"""
        import json

        position_id = pos['id']
        symbol = pos['symbol']
        direction = pos['position_side']

        batch_plan = json.loads(pos['batch_plan']) if pos['batch_plan'] else None
        batch_filled = json.loads(pos['batch_filled']) if pos['batch_filled'] else None

        if not batch_plan or not batch_filled:
            logger.warning(f"持仓 {position_id} 缺少batch数据,标记为open")
            await self._mark_position_as_open(position_id)
            return

        total_batches = len(batch_plan['batches'])
        filled_count = len(batch_filled['batches'])

        # 检查是否已经超时太久(超过1小时)
        from datetime import datetime, timedelta
        created_at = pos['created_at']
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        hours_since_created = (datetime.now() - created_at).total_seconds() / 3600

        if hours_since_created > 1:
            # 超过1小时,直接标记为open
            logger.info(
                f"持仓 {position_id} ({symbol} {direction}) 创建已超过{hours_since_created:.1f}小时, "
                f"完成度 {filled_count}/{total_batches}, 标记为open"
            )
            await self._mark_position_as_open(position_id)
            return

        # 如果还在合理时间范围内,继续完成建仓
        logger.info(
            f"🔄 恢复建仓任务: 持仓{position_id} ({symbol} {direction}) | "
            f"进度: {filled_count}/{total_batches}"
        )

        # 重建plan对象
        plan = {
            'position_id': position_id,
            'symbol': symbol,
            'direction': direction,
            'signal_time': pos.get('entry_signal_time') or created_at,
            'total_margin': 400,  # 默认值,实际已经在数据库中
            'leverage': 5,
            'batches': [],
            'signal': {}
        }

        # 重建batches结构
        for i, batch_plan_item in enumerate(batch_plan['batches']):
            batch = {
                'ratio': batch_plan_item['ratio'],
                'filled': False,
                'price': None,
                'time': None,
                'margin': None,
                'quantity': None
            }

            # 如果这个批次已完成,填充数据
            for filled_batch in batch_filled['batches']:
                if filled_batch['batch_num'] == i:
                    batch['filled'] = True
                    batch['price'] = filled_batch['price']
                    batch['time'] = datetime.fromisoformat(filled_batch['time'])
                    batch['margin'] = filled_batch.get('margin')
                    batch['quantity'] = filled_batch.get('quantity')
                    break

            plan['batches'].append(batch)

        # 启动后台任务继续建仓
        asyncio.create_task(self._continue_batch_entry(plan))
        logger.info(f"✅ 已启动持仓 {position_id} 的后台建仓任务")

    async def _continue_batch_entry(self, plan: Dict):
        """继续未完成的分批建仓"""
        symbol = plan['symbol']
        direction = plan['direction']
        position_id = plan['position_id']

        logger.info(f"🚀 继续建仓: {symbol} {direction} (持仓#{position_id})")

        # 启动价格采样器（15分钟滚动窗口）
        from app.services.price_sampler import PriceSampler
        sampler = PriceSampler(symbol, self.price_service, window_seconds=900)
        sampling_task = asyncio.create_task(sampler.start_background_sampling())

        # 等待基线建立（最多等待15分钟）
        wait_start = datetime.now()
        while not sampler.initial_baseline_built:
            await asyncio.sleep(1)
            if (datetime.now() - wait_start).total_seconds() > 900:  # 15分钟超时
                break

        try:
            # 最多继续尝试20分钟
            start_time = datetime.now()
            while (datetime.now() - start_time).total_seconds() < 1200:
                current_price = await self._get_current_price(symbol)
                elapsed_minutes = (datetime.now() - plan['signal_time']).total_seconds() / 60
                current_baseline = sampler.get_current_baseline()

                # 检查每个未完成的批次
                for batch_num, batch in enumerate(plan['batches']):
                    if batch['filled']:
                        continue

                    # 使用简化的判断逻辑:只要价格合理就建仓
                    should_fill = False
                    reason = ""

                    if batch_num == 0:
                        should_fill = True
                        reason = "恢复第1批建仓"
                    elif batch_num == 1 and plan['batches'][0]['filled']:
                        should_fill = True
                        reason = "恢复第2批建仓"
                    elif batch_num == 2 and plan['batches'][1]['filled']:
                        should_fill = True
                        reason = "恢复第3批建仓"

                    if should_fill:
                        await self._execute_batch(plan, batch_num, current_price, reason)

                        # 如果是最后一批,完成建仓
                        if batch_num == 2:
                            await self._finalize_position(plan)
                            logger.info(f"🎉 持仓 {position_id} 恢复建仓完成!")
                            return

                await asyncio.sleep(10)

            # 超时结束，买了几批算几批
            filled_count = len([b for b in plan['batches'] if b['filled']])
            logger.info(f"持仓 {position_id} 恢复建仓超时，已完成{filled_count}/3批，标记为open")
            await self._finalize_position(plan)

        finally:
            sampler.stop_sampling()
            sampling_task.cancel()

    async def _mark_position_as_open(self, position_id: int):
        """将持仓标记为open状态,并设置开仓时间和计划平仓时间"""
        import pymysql
        from datetime import timedelta
        import json

        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 查询持仓的entry_score和batch_filled以计算开仓时间
            cursor.execute("""
                SELECT entry_score, batch_filled
                FROM futures_positions
                WHERE id = %s
            """, (position_id,))

            result = cursor.fetchone()
            entry_score = result['entry_score'] if result else 30

            # 从batch_filled JSON中获取最晚一批的时间作为开仓时间
            open_time = datetime.now()  # 默认值
            try:
                batch_filled_json = result.get('batch_filled') if result else None
                if batch_filled_json:
                    batch_filled = json.loads(batch_filled_json)
                    batches = batch_filled.get('batches', [])
                    if batches:
                        # 最后一批的时间
                        last_batch = batches[-1]
                        time_str = last_batch.get('time')
                        if time_str:
                            open_time = datetime.fromisoformat(time_str)
            except Exception as e:
                logger.warning(f"解析batch_filled失败,使用当前时间作为开仓时间: {e}")

            # 根据entry_score计算持仓时长
            # 🔥 修改: 统一3小时强制平仓 - 边际收益递减
            max_hold_minutes = 180  # 3小时强制平仓

            planned_close_time = open_time + timedelta(minutes=max_hold_minutes)

            cursor.execute("""
                UPDATE futures_positions
                SET status = 'open',
                    open_time = %s,
                    planned_close_time = %s,
                    notes = CONCAT(COALESCE(notes, ''), ' [自动恢复] 系统重启后标记为open'),
                    updated_at = NOW()
                WHERE id = %s
            """, (open_time, planned_close_time, position_id))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ 持仓 {position_id} 已标记为open,计划平仓时间: {planned_close_time.strftime('%H:%M:%S')}")

        except Exception as e:
            logger.error(f"标记持仓 {position_id} 为open失败: {e}")
