# 修复超级大脑止盈缩水问题

## 问题根因

**价格来源不一致导致止盈缩水:**

### 检查止盈时 (SmartExitOptimizer)
```python
# smart_exit_optimizer.py:388
current_price = self.price_service.get_price(symbol)  # WebSocket实时价格
if current_price >= take_profit_price:
    await self._execute_close(position_id, current_price, reason)
```

### 实际平仓时 (SmartTraderService)
```python
# smart_trader_service.py:1905
current_price = self.get_current_price(symbol)  # 5分钟K线收盘价(有延迟!)

# smart_trader_service.py:670-674
cursor.execute("""
    SELECT close_price FROM kline_data
    WHERE symbol = %s AND timeframe = '5m'  # ← 问题: 5分钟K线延迟
    ORDER BY open_time DESC LIMIT 1
""", (symbol,))
```

**时间差**: WebSocket实时价 vs 5分钟K线价 = 可能相差几秒到5分钟

**结果**: 以更低的价格平仓，损失0.5%-4% ROI

## 具体案例

| 环节 | 价格来源 | KAIA价格 | 说明 |
|------|----------|----------|------|
| 触发检查 | WebSocket实时 | $0.069762 | 达到止盈价,触发平仓 |
| 实际平仓 | 5分钟K线 | $0.069220 | 延迟数据,价格已回落 |
| 损失 | - | -$18.85 | 理论$26.15 - 实际$7.30 |

## 解决方案

### 方案1: 优先使用WebSocket价格 (推荐)

修改 `smart_trader_service.py` 第665-695行:

```python
def get_current_price(self, symbol: str):
    """获取当前价格 - 优先WebSocket实时价,回退到K线"""
    try:
        # 优先从WebSocket获取实时价格
        if self.ws_service:
            ws_price = self.ws_service.get_price(symbol)
            if ws_price and ws_price > 0:
                logger.debug(f"[PRICE] {symbol} WebSocket实时价: {ws_price}")
                return ws_price
            else:
                logger.debug(f"[PRICE] {symbol} WebSocket价格无效,回退到K线")

        # 回退到5分钟K线
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT close_price, open_time
            FROM kline_data
            WHERE symbol = %s AND timeframe = '5m'
            ORDER BY open_time DESC LIMIT 1
        """, (symbol,))
        result = cursor.fetchone()
        cursor.close()

        if not result:
            logger.warning(f"[PRICE] {symbol} K线数据不存在")
            return None

        close_price, open_time = result

        # 检查数据新鲜度: 5m K线数据不能超过10分钟前
        import time
        current_timestamp_ms = int(time.time() * 1000)
        data_age_minutes = (current_timestamp_ms - open_time) / 1000 / 60

        if data_age_minutes > 10:
            logger.warning(
                f"[DATA_STALE] {symbol} K线数据过时! "
                f"最新K线时间: {data_age_minutes:.1f}分钟前"
            )
            return None

        logger.debug(f"[PRICE] {symbol} K线价格: {close_price} (数据年龄: {data_age_minutes:.1f}分钟)")
        return float(close_price)

    except Exception as e:
        logger.error(f"获取{symbol}价格失败: {e}")
        return None
```

**优点**:
- 使用与触发检查相同的价格来源
- 消除价格来源不一致导致的缩水
- WebSocket价格更实时，延迟<1秒

**缺点**:
- 依赖WebSocket服务稳定性
- 需要确保WebSocket连接正常

### 方案2: 传递触发价格

修改 `smart_exit_optimizer.py` 第857行:

```python
# 调用实盘引擎执行平仓，传递触发时的价格
close_result = await self.live_engine.close_position(
    symbol=position['symbol'],
    direction=position['direction'],
    position_size=float(position['position_size']),
    reason=reason,
    trigger_price=current_price  # ✅ 新增: 传递触发价格
)
```

修改 `smart_trader_service.py` 第1811行:

```python
async def close_position(self, symbol: str, direction: str, position_size: float,
                        reason: str = "smart_exit", trigger_price: float = None):
    """
    异步平仓方法（供SmartExitOptimizer调用）

    Args:
        trigger_price: 触发平仓时的价格(优先使用这个价格)
    """
    try:
        # 如果提供了触发价格,优先使用
        if trigger_price:
            success = self.close_position_by_side(symbol, direction, reason, trigger_price=trigger_price)
        else:
            success = self.close_position_by_side(symbol, direction, reason)

        ...
```

**优点**:
- 保证使用触发时的价格
- 不改变价格获取逻辑

**缺点**:
- 需要修改多个方法签名
- 传递价格可能不是真实市场价

## 推荐实施

**采用方案1**: 优先使用WebSocket实时价格

### 实施步骤

1. 修改 `smart_trader_service.py` 的 `get_current_price()` 方法
2. 添加WebSocket价格优先逻辑
3. 保留K线价格作为回退
4. 测试验证价格一致性

### 验证方法

添加日志对比:
```python
ws_price = self.ws_service.get_price(symbol)
kline_price = self._get_kline_price(symbol)
logger.info(f"[PRICE比较] {symbol} WS:{ws_price} vs K线:{kline_price} 差距:{abs(ws_price-kline_price)/kline_price*100:.2f}%")
```

## 预期效果

修复前:
- KAIA: 理论+$26.15, 实际+$7.30, 缩水72%
- 总损失: ~$30 USDT/天

修复后:
- 使用WebSocket实时价,与触发价格一致
- 预计止盈收益提升 20%-70%
- 月收益增加: ~$900 USDT
