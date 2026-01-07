"""
实时持仓监控服务

基于 WebSocket 实时价格推送，实现毫秒级移动止盈/止损监控
不再依赖轮询，价格变动即时触发检测
"""

import asyncio
from typing import Dict, List, Optional, Callable
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from loguru import logger

from app.services.binance_ws_price import get_ws_price_service, BinanceWSPriceService


class RealtimePositionMonitor:
    """实时持仓监控服务"""

    def __init__(self, db_config: dict, strategy_executor=None, fallback_callback=None):
        """
        初始化实时监控服务

        Args:
            db_config: 数据库配置
            strategy_executor: 策略执行器（用于平仓）
            fallback_callback: 降级回调函数，当 WebSocket 不健康时调用
        """
        self.db_config = db_config
        self.strategy_executor = strategy_executor
        self.fallback_callback = fallback_callback  # 降级回调
        self.ws_service: BinanceWSPriceService = get_ws_price_service()
        self.running = False
        self.LOCAL_TZ = timezone(timedelta(hours=8))

        # 持仓缓存
        self.positions: Dict[int, dict] = {}  # position_id -> position_data
        self.symbol_positions: Dict[str, List[int]] = {}  # symbol -> [position_ids]

        # 策略参数缓存
        self.strategy_params: Dict[int, dict] = {}  # strategy_id -> params

        # 降级状态
        self._fallback_mode = False  # 是否处于降级模式
        self._fallback_poll_interval = 2  # 降级时的轮询间隔（秒）
        self._fallback_task = None  # 降级轮询任务

    def get_db_connection(self):
        """获取数据库连接"""
        import pymysql
        return pymysql.connect(
            host=self.db_config['host'],
            port=self.db_config.get('port', 3306),
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            cursorclass=pymysql.cursors.DictCursor,
            charset='utf8mb4'
        )

    def load_open_positions(self, account_id: int = 2) -> List[dict]:
        """加载所有开放持仓"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, symbol, position_side, entry_price, quantity, leverage,
                       max_profit_pct, trailing_stop_activated, trailing_stop_price,
                       stop_loss_price, take_profit_price, strategy_id, open_time
                FROM futures_positions
                WHERE account_id = %s AND status = 'open'
            """, (account_id,))

            positions = cursor.fetchall()

            # 更新缓存
            self.positions.clear()
            self.symbol_positions.clear()

            for pos in positions:
                pos_id = pos['id']
                symbol = pos['symbol']

                self.positions[pos_id] = pos

                if symbol not in self.symbol_positions:
                    self.symbol_positions[symbol] = []
                self.symbol_positions[symbol].append(pos_id)

            return positions

        finally:
            cursor.close()
            conn.close()

    def load_strategy_params(self, strategy_id: int) -> dict:
        """加载策略参数（每次从数据库读取，确保配置更新后立即生效）"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT config FROM trading_strategies WHERE id = %s
            """, (strategy_id,))

            row = cursor.fetchone()
            if row:
                import json
                config = json.loads(row['config']) if isinstance(row['config'], str) else row['config']
                params = {
                    'trailing_activate': config.get('trailingActivate', 1.5),
                    'trailing_callback': config.get('trailingCallback', 0.5),
                    'stop_loss_pct': config.get('stopLossPercent') or config.get('stopLoss', 2.5),
                    'take_profit_pct': config.get('takeProfitPercent') or config.get('takeProfit', 8.0),
                    'trailing_cooldown_minutes': config.get('trailingCooldownMinutes', 15),
                    'sync_live': config.get('syncLive', False)
                }
                return params

            # 默认参数
            return {
                'trailing_activate': 1.5,
                'trailing_callback': 0.5,
                'stop_loss_pct': 2.5,
                'take_profit_pct': 8.0,
                'trailing_cooldown_minutes': 15,
                'sync_live': False
            }

        finally:
            cursor.close()
            conn.close()

    def update_position_db(self, position_id: int, updates: dict):
        """更新持仓数据库"""
        if not updates:
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            set_clauses = []
            values = []
            for key, value in updates.items():
                set_clauses.append(f"{key} = %s")
                values.append(value)

            values.append(position_id)
            sql = f"UPDATE futures_positions SET {', '.join(set_clauses)} WHERE id = %s"
            cursor.execute(sql, values)
            conn.commit()

            # 同步更新缓存
            if position_id in self.positions:
                self.positions[position_id].update(updates)

        finally:
            cursor.close()
            conn.close()

    async def on_price_update(self, symbol: str, price: float):
        """
        价格更新回调 - 核心逻辑

        每次价格更新时触发，检测所有该交易对的持仓是否需要平仓
        """
        if symbol not in self.symbol_positions:
            return

        position_ids = self.symbol_positions.get(symbol, [])
        if not position_ids:
            return

        for pos_id in position_ids[:]:  # 使用切片避免迭代时修改
            position = self.positions.get(pos_id)
            if not position:
                continue

            try:
                await self._check_position(position, price)
            except Exception as e:
                logger.error(f"检查持仓 {pos_id} 异常: {e}")

    async def _check_position(self, position: dict, current_price: float):
        """检查单个持仓的止盈止损"""
        pos_id = position['id']
        symbol = position['symbol']
        position_side = position['position_side']
        entry_price = float(position['entry_price'])
        max_profit_pct = float(position.get('max_profit_pct') or 0)
        trailing_activated = position.get('trailing_stop_activated') or False
        strategy_id = position.get('strategy_id')

        # 获取策略参数
        params = self.load_strategy_params(strategy_id) if strategy_id else {}
        trailing_activate = params.get('trailing_activate', 1.5)
        trailing_callback = params.get('trailing_callback', 0.5)
        stop_loss_pct = params.get('stop_loss_pct', 2.5)

        # 计算当前盈亏
        if position_side == 'LONG':
            current_pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            current_pnl_pct = (entry_price - current_price) / entry_price * 100

        updates = {}

        # 1. 检查硬止损（不受冷却时间限制）
        if current_pnl_pct <= -stop_loss_pct:
            close_reason = f"hard_stop_loss|loss:{abs(current_pnl_pct):.2f}%"
            logger.info(f"🚨 [实时监控] {symbol} 硬止损平仓(亏损{abs(current_pnl_pct):.2f}% >= {stop_loss_pct}%)")
            await self._close_position(position, close_reason)
            return

        # 2. 检查开仓冷却时间（冷却期内不检查移动止盈/止损）
        trailing_cooldown_minutes = params.get('trailing_cooldown_minutes', 15)
        open_time = position.get('open_time')
        if open_time:
            now = datetime.now(self.LOCAL_TZ).replace(tzinfo=None)
            # 确保 open_time 是 datetime 对象
            if isinstance(open_time, datetime):
                elapsed_minutes = (now - open_time).total_seconds() / 60
                if elapsed_minutes < trailing_cooldown_minutes:
                    # 冷却期内只更新最高盈利，不触发移动止盈/止损
                    if current_pnl_pct > max_profit_pct + 0.01:
                        updates['max_profit_pct'] = current_pnl_pct
                        updates['max_profit_price'] = current_price
                        position['max_profit_pct'] = current_pnl_pct
                        self.update_position_db(pos_id, updates)
                    return  # 冷却期内跳过后续移动止盈/止损检查

        # 3. 更新最高盈利
        if current_pnl_pct > max_profit_pct + 0.01:
            updates['max_profit_pct'] = current_pnl_pct
            updates['max_profit_price'] = current_price
            logger.info(f"[实时更新] {symbol} 最高盈利: {max_profit_pct:.2f}% -> {current_pnl_pct:.2f}%")
            max_profit_pct = current_pnl_pct

            # 同步更新缓存
            position['max_profit_pct'] = current_pnl_pct

        # 4. 检查是否激活移动止盈
        if not trailing_activated and max_profit_pct >= trailing_activate:
            updates['trailing_stop_activated'] = True
            trailing_activated = True

            if position_side == 'LONG':
                trailing_stop_price = current_price * (1 - trailing_callback / 100)
            else:
                trailing_stop_price = current_price * (1 + trailing_callback / 100)
            updates['trailing_stop_price'] = trailing_stop_price

            logger.info(f"🎯 [实时监控] {symbol} 移动止盈激活! 盈利={max_profit_pct:.2f}%, 止损价={trailing_stop_price:.6f}")

            # 同步更新缓存
            position['trailing_stop_activated'] = True
            position['trailing_stop_price'] = trailing_stop_price

        # 5. 检查移动止盈回撤
        if trailing_activated:
            callback_pct = max_profit_pct - current_pnl_pct
            if callback_pct >= trailing_callback:
                close_reason = f"移动止盈平仓(从最高{max_profit_pct:.2f}%回撤{callback_pct:.2f}% >= {trailing_callback}%)"
                logger.info(f"💰 [实时监控] {symbol} {close_reason}")
                await self._close_position(position, close_reason)
                return

            # 更新移动止损价格
            if position_side == 'LONG':
                new_trailing_price = current_price * (1 - trailing_callback / 100)
                current_trailing_price = float(position.get('trailing_stop_price') or 0)
                if new_trailing_price > current_trailing_price:
                    updates['trailing_stop_price'] = new_trailing_price
                    position['trailing_stop_price'] = new_trailing_price
                    logger.debug(f"[移动止盈] {symbol} 做多 止损价上移: {current_trailing_price:.6f} -> {new_trailing_price:.6f}")
            else:
                new_trailing_price = current_price * (1 + trailing_callback / 100)
                current_trailing_price = float(position.get('trailing_stop_price') or float('inf'))
                if new_trailing_price < current_trailing_price:
                    updates['trailing_stop_price'] = new_trailing_price
                    position['trailing_stop_price'] = new_trailing_price
                    logger.debug(f"[移动止盈] {symbol} 做空 止损价下移: {current_trailing_price:.6f} -> {new_trailing_price:.6f}")

        # 保存更新
        if updates:
            self.update_position_db(pos_id, updates)

    async def _close_position(self, position: dict, reason: str):
        """执行平仓"""
        pos_id = position['id']
        symbol = position['symbol']

        # 从缓存中移除
        if pos_id in self.positions:
            del self.positions[pos_id]
        if symbol in self.symbol_positions and pos_id in self.symbol_positions[symbol]:
            self.symbol_positions[symbol].remove(pos_id)

        # 调用策略执行器平仓
        if self.strategy_executor:
            try:
                # 获取完整的策略配置（需要用于syncLive判断）
                strategy_id = position.get('strategy_id')
                strategy = {}

                if strategy_id:
                    conn = self.db_connection
                    cursor = conn.cursor()
                    cursor.execute("SELECT config FROM trading_strategies WHERE id = %s", (strategy_id,))
                    result = cursor.fetchone()
                    cursor.close()

                    if result:
                        import json
                        strategy = json.loads(result['config'])
                        strategy['id'] = strategy_id

                result = await self.strategy_executor.execute_close_position(position, reason, strategy)

                # 平仓成功后，检查紧急停止（硬止损时）
                if result.get('success') and 'hard_stop_loss' in reason:
                    account_id = position.get('account_id', 2)
                    await self.strategy_executor._check_circuit_breaker(account_id)

            except Exception as e:
                logger.error(f"平仓执行失败: {e}")
        else:
            logger.warning(f"strategy_executor 未设置，无法执行平仓")

    def _on_ws_health_change(self, is_healthy: bool, reason: str):
        """WebSocket 健康状态变化回调"""
        if not is_healthy and not self._fallback_mode:
            # WebSocket 不健康，启动降级模式
            logger.warning(f"⚠️ WebSocket 不健康，启动降级轮询模式: {reason}")
            self._fallback_mode = True
            if self.fallback_callback:
                self.fallback_callback(True, reason)
            # 启动降级轮询任务
            if self._fallback_task is None or self._fallback_task.done():
                self._fallback_task = asyncio.create_task(self._fallback_poll_loop())
        elif is_healthy and self._fallback_mode:
            # WebSocket 恢复健康，退出降级模式
            logger.info("✅ WebSocket 恢复健康，退出降级轮询模式")
            self._fallback_mode = False
            if self.fallback_callback:
                self.fallback_callback(False, "WebSocket 恢复正常")
            # 取消降级轮询任务
            if self._fallback_task and not self._fallback_task.done():
                self._fallback_task.cancel()
                self._fallback_task = None

    async def _fallback_poll_loop(self):
        """降级轮询循环 - 当 WebSocket 不健康时使用 REST API 获取价格"""
        logger.info(f"📡 降级轮询模式启动（间隔: {self._fallback_poll_interval}秒）")

        while self.running and self._fallback_mode:
            try:
                # 获取所有需要监控的交易对
                symbols = list(self.symbol_positions.keys())
                if not symbols:
                    await asyncio.sleep(self._fallback_poll_interval)
                    continue

                # 使用 REST API 获取价格
                for symbol in symbols:
                    try:
                        price = await self._get_price_from_api(symbol)
                        if price:
                            await self.on_price_update(symbol, price)
                    except Exception as e:
                        logger.debug(f"降级模式获取 {symbol} 价格失败: {e}")

            except Exception as e:
                logger.error(f"降级轮询循环出错: {e}")

            await asyncio.sleep(self._fallback_poll_interval)

        logger.info("📡 降级轮询模式已停止")

    async def _get_price_from_api(self, symbol: str) -> Optional[float]:
        """从 REST API 获取价格（降级时使用）"""
        try:
            # 如果有 live_engine，使用它获取价格
            if self.strategy_executor and hasattr(self.strategy_executor, 'live_engine'):
                live_engine = self.strategy_executor.live_engine
                if live_engine:
                    price = live_engine.get_current_price(symbol)
                    if price and price > 0:
                        return float(price)

            # 回退：从数据库获取最新价格
            conn = self.get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT price FROM price_data
                    WHERE symbol = %s
                    ORDER BY timestamp DESC LIMIT 1
                """, (symbol,))
                row = cursor.fetchone()
                if row:
                    return float(row['price'])
            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.debug(f"获取 {symbol} 价格失败: {e}")

        return None

    def is_in_fallback_mode(self) -> bool:
        """检查是否处于降级模式"""
        return self._fallback_mode

    async def start(self, account_id: int = 2):
        """启动实时监控服务"""
        if self.running:
            logger.warning("实时监控服务已在运行")
            return

        self.running = True
        logger.info("🚀 启动实时持仓监控服务...")

        # 加载持仓
        positions = self.load_open_positions(account_id)
        symbols = list(self.symbol_positions.keys())

        if not symbols:
            logger.info("当前无开放持仓，等待新持仓...")

        # 启动 WebSocket 并订阅
        if symbols:
            await self.ws_service.subscribe(symbols)

        # 注册价格回调
        self.ws_service.add_callback(
            lambda symbol, price: asyncio.create_task(self.on_price_update(symbol, price))
        )

        # 注册健康状态回调（用于自动降级）
        self.ws_service.add_health_callback(self._on_ws_health_change)

        # 启动 WebSocket
        if not self.ws_service.is_running():
            asyncio.create_task(self.ws_service.start(symbols))

        logger.info(f"✅ 实时监控服务已启动，监控 {len(positions)} 个持仓，{len(symbols)} 个交易对")
        logger.info(f"🔄 自动降级保护已启用（{self.ws_service._stale_threshold}秒无数据将自动切换轮询模式）")

        # 定期刷新持仓列表（处理新开仓和外部平仓）
        while self.running:
            await asyncio.sleep(10)  # 每10秒刷新一次持仓列表
            try:
                await self._refresh_positions(account_id)
            except Exception as e:
                logger.error(f"刷新持仓列表异常: {e}")

    async def _refresh_positions(self, account_id: int):
        """刷新持仓列表"""
        old_symbols = set(self.symbol_positions.keys())

        # 重新加载持仓
        self.load_open_positions(account_id)

        new_symbols = set(self.symbol_positions.keys())

        # 订阅新交易对
        symbols_to_add = new_symbols - old_symbols
        if symbols_to_add:
            await self.ws_service.subscribe(list(symbols_to_add))
            logger.info(f"新增订阅: {symbols_to_add}")

        # 取消旧订阅
        symbols_to_remove = old_symbols - new_symbols
        if symbols_to_remove:
            await self.ws_service.unsubscribe(list(symbols_to_remove))
            logger.info(f"取消订阅: {symbols_to_remove}")

    async def stop(self):
        """停止实时监控服务"""
        logger.info("正在停止实时监控服务...")
        self.running = False
        self._fallback_mode = False

        # 停止降级轮询任务
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
            self._fallback_task = None

        await self.ws_service.stop()
        logger.info("实时监控服务已停止")


# 全局单例
_realtime_monitor: Optional[RealtimePositionMonitor] = None


def get_realtime_monitor() -> Optional[RealtimePositionMonitor]:
    """获取实时监控服务单例"""
    return _realtime_monitor


def init_realtime_monitor(db_config: dict, strategy_executor=None, fallback_callback=None) -> RealtimePositionMonitor:
    """初始化实时监控服务"""
    global _realtime_monitor
    if _realtime_monitor is None:
        _realtime_monitor = RealtimePositionMonitor(db_config, strategy_executor, fallback_callback)
    return _realtime_monitor
