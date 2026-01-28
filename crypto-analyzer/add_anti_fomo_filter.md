# 防追高过滤机制

## 问题案例

**SOMI/USDT** 01-28 19:03开仓:
- 开仓价: $0.3129
- 19分钟后止损: $0.305 (-12.72%)
- 入场评分: $0.94 (异常低)

**典型追高特征**:
1. 开仓后立即下跌
2. 极短时间内止损
3. 评分异常

## 优化方案

### 1. 24H价格位置过滤

```python
def check_price_position(symbol, current_price):
    """检查当前价格在24H区间的位置"""
    # 获取24H最高最低价
    high_24h = get_24h_high(symbol)
    low_24h = get_24h_low(symbol)

    # 计算位置百分比
    position_pct = (current_price - low_24h) / (high_24h - low_24h) * 100

    # 做多过滤: 不在高于75%位置开多
    if position_pct > 75:
        return False, f"价格位于24H区间{position_pct:.1f}%位置,拒绝做多(防追高)"

    # 做空过滤: 不在低于25%位置开空
    if position_pct < 25:
        return False, f"价格位于24H区间{position_pct:.1f}%位置,拒绝做空(防杀跌)"

    return True, "位置合理"
```

### 2. 短期波动过滤

```python
def check_recent_volatility(symbol):
    """检查最近1小时波动"""
    # 获取最近1小时K线
    klines_1h = get_klines(symbol, '5m', 12)

    # 计算1小时涨跌幅
    change_1h = (klines_1h[-1]['close'] - klines_1h[0]['open']) / klines_1h[0]['open'] * 100

    # 做多过滤: 1小时涨幅>5%时拒绝(可能已冲高)
    if change_1h > 5:
        return False, f"1H涨幅{change_1h:+.2f}%,拒绝做多(防追涨)"

    # 做空过滤: 1小时跌幅>5%时拒绝(可能已杀跌)
    if change_1h < -5:
        return False, f"1H跌幅{change_1h:+.2f}%,拒绝做空(防追跌)"

    return True, "波动正常"
```

### 3. 评分最低阈值

```python
# 在smart_trader_service.py中修改

# 当前: threshold = 27
# 建议: threshold = 35  # 提高到35分,过滤低质量信号

# 并添加严格检查
if score < 35:
    logger.warning(f"评分{score}低于35分,拒绝开仓")
    continue
```

### 4. 集成到现有代码

在 `smart_trader_service.py` 的 `scan_opportunities()` 函数中添加:

```python
def scan_opportunities(self):
    opportunities = []

    for symbol in self.symbols:
        # ... 现有信号生成代码 ...

        # 新增: 防追高过滤
        current_price = self.ws_price_service.get_price(symbol)

        # 检查24H价格位置
        position_ok, position_msg = self.check_price_position(symbol, current_price, new_side)
        if not position_ok:
            logger.info(f"[ANTI-FOMO] {symbol} {position_msg}")
            continue

        # 检查短期波动
        volatility_ok, volatility_msg = self.check_recent_volatility(symbol, new_side)
        if not volatility_ok:
            logger.info(f"[ANTI-FOMO] {symbol} {volatility_msg}")
            continue

        # 通过过滤,添加到机会列表
        opportunities.append(...)
```

## 实施步骤

1. 在 `smart_trader_service.py` 中添加两个检查方法
2. 提高最低评分阈值到35-40分
3. 在扫描机会时调用防追高过滤
4. 重启服务测试

## 预期效果

- 减少追高被套的交易
- 提高做多胜率 (当前42% → 55%+)
- 避免极短时间内止损的情况
- 过滤掉SOMI这类异常信号

## 需要修改的文件

- `smart_trader_service.py` (添加防追高逻辑)
- 测试后观察效果
