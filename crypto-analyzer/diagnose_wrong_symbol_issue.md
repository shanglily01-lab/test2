# 币本位交易对错误开到U本位账户问题诊断

## 🔍 问题现象

错误日志显示:
```
2026-01-29 12:42:36 | ERROR | 获取价格失败: 无法获取DOT/USD的价格
2026-01-29 12:42:36 | WARNING | 更新持仓 DOT/USD 价格和盈亏失败: 无法获取DOT/USD的价格
2026-01-29 12:42:36 | ERROR | 获取价格失败: 无法获取ADA/USD的价格
2026-01-29 12:42:36 | WARNING | 更新持仓 ADA/USD 价格和盈亏失败: 无法获取ADA/USD的价格
```

## 📊 数据库调查结果

### 1. 持仓记录

查询 `futures_positions` 表发现:

| ID | symbol | account_id | source | position_side | 开仓时间 |
|---|---|---|---|---|---|
| 6600 | DOT/USD | 3 (币本位) | smart_trader_batch | SHORT | 2026-01-30 01:14:09 |
| 6590 | ADA/USD | 3 (币本位) | smart_trader_batch | SHORT | 2026-01-29 23:49:28 |

**问题**:
- ✅ 持仓在正确的账户 (account_id=3 币本位)
- ❌ 但source是 `smart_trader_batch` (U本位服务)
- ❌ 应该是 `coin_futures_trader` (币本位服务)

### 2. K线数据检查

查询 `futures_klines` 表:
```sql
SELECT DISTINCT symbol FROM futures_klines
WHERE symbol LIKE 'ADA%' OR symbol LIKE 'DOT%'
```

结果:
- ✅ ADA/USDT (有数据)
- ✅ DOT/USDT (有数据)
- ❌ ADA/USD (无数据)
- ❌ DOT/USD (无数据)

### 3. 配置文件检查

`config.yaml` 中:

**coin_futures_symbols** (第35-56行):
```yaml
coin_futures_symbols:
- BTCUSD_PERP
- ETHUSD_PERP
- ADAUSD_PERP  # ← 币本位格式
- DOTUSD_PERP  # ← 币本位格式
```

**symbols** (第409-498行):
```yaml
symbols:
- ADA/USDT  # ← U本位格式
- DOT/USDT  # ← U本位格式
```

## 🕵️ 根本原因分析

### 问题1: 币本位服务没有K线数据

币本位服务配置:
```python
# coin_futures_trader_service.py:69-71
coin_symbols = config.get('coin_futures_symbols', [])
# 转换: ADAUSD_PERP -> ADA/USD
all_symbols = [s.replace('USD_PERP', '/USD') for s in coin_symbols]
```

但是:
- ❌ `futures_klines` 表中没有 `ADA/USD`, `DOT/USD` 的K线数据
- ❌ 币本位服务无法获取K线数据进行分析
- ❌ **没有数据采集服务为币本位交易对收集K线**

### 问题2: Source字段显示U本位服务开的仓

所有 `ADA/USD`, `DOT/USD` 持仓的 `source` 都是 `smart_trader_batch`,说明:
- ❌ U本位服务(`smart_trader`)错误地开了币本位交易对的仓位
- ❌ 应该由币本位服务(`coin_futures_trader`)开仓

### 问题3: 币本位服务可能根本没在运行

需要检查:
```bash
pm2 list
# 查看是否有 coin_futures_trader 进程
```

## 💡 可能的原因

### 假设A: 币本位服务被禁用或未运行
- 币本位服务没有运行
- U本位服务的配置被错误地包含了币本位交易对
- 或者有bug导致U本位服务使用了币本位的whitelist

### 假设B: 配置被污染
- `smart_trader` 的whitelist意外包含了 `/USD` 交易对
- 可能是配置加载时的bug
- 或者数据库中有错误的whitelist配置

### 假设C: WebSocket服务共享问题
- 两个服务共享同一个WebSocket连接
- Symbol转换逻辑有bug
- `/USDT` 被错误地转换成了 `/USD`

## 🔧 解决方案

### 立即修复 (已完成)

✅ 将有问题的持仓标记为closed:
```python
# delete_dgb_position.py 已执行
# DOT/USD (ID:6600) -> closed
# ADA/USD (ID:6590) -> closed
```

### 根本修复方案

#### 方案1: 检查并修复服务配置

1. **检查pm2进程列表**:
```bash
pm2 list
```

2. **如果币本位服务未运行,启动它**:
```bash
pm2 start coin_futures_trader_service.py --name coin_futures_trader
```

3. **确保两个服务使用不同的account_id**:
- U本位: account_id=2
- 币本位: account_id=3

#### 方案2: 为币本位添加数据采集

币本位交易对需要K线数据,需要:

1. **添加币本位数据采集脚本** (或修改现有采集器):
```python
# 在 binance_futures_collector.py 或创建新的 coin_futures_collector.py
# 采集 ADA/USD, DOT/USD 等币本位交易对的K线
```

2. **确保K线数据写入 `futures_klines` 表**

#### 方案3: 添加交易对过滤验证

在 `smart_trader_service.py` 中添加验证:

```python
# 在 open_position() 方法开头
def open_position(self, opp: dict):
    symbol = opp['symbol']

    # 🔥 新增: 验证symbol格式
    if symbol.endswith('/USD'):
        logger.error(f"[SYMBOL_ERROR] {symbol} 是币本位交易对,不应在U本位服务开仓")
        return False

    if not symbol.endswith('/USDT'):
        logger.error(f"[SYMBOL_ERROR] {symbol} 格式错误,U本位只支持/USDT交易对")
        return False

    # ... 原有代码
```

在 `coin_futures_trader_service.py` 中添加:

```python
def open_position(self, opp: dict):
    symbol = opp['symbol']

    # 🔥 新增: 验证symbol格式
    if symbol.endswith('/USDT'):
        logger.error(f"[SYMBOL_ERROR] {symbol} 是U本位交易对,不应在币本位服务开仓")
        return False

    if not symbol.endswith('/USD'):
        logger.error(f"[SYMBOL_ERROR] {symbol} 格式错误,币本位只支持/USD交易对")
        return False

    # ... 原有代码
```

#### 方案4: 禁用没有数据的交易对

如果不想交易币本位合约,可以:

1. **停止币本位服务**:
```bash
pm2 stop coin_futures_trader
pm2 delete coin_futures_trader
```

2. **从 `config.yaml` 中移除或注释 `coin_futures_symbols`**:
```yaml
# coin_futures_symbols:  # 暂时禁用币本位
# - BTCUSD_PERP
# - ETHUSD_PERP
```

## 📋 行动清单

- [x] 诊断问题根源
- [x] 关闭有问题的持仓 (DOT/USD, ADA/USD)
- [ ] **检查pm2进程**: `pm2 list` 查看币本位服务是否在运行
- [ ] **添加symbol格式验证** 到两个服务
- [ ] **决定**: 是否继续使用币本位合约?
  - 如果是 → 添加数据采集 + 启动服务
  - 如果否 → 禁用配置 + 停止服务
- [ ] **重启服务**使修复生效
- [ ] **监控日志**确认不再出现错误

## 🎯 推荐操作

建议先检查 `pm2 list`,然后根据需求选择:

### 选项A: 继续使用币本位 (需要工作量)
1. 为币本位添加数据采集
2. 确保服务正常运行
3. 添加symbol验证

### 选项B: 暂时禁用币本位 (快速解决)
1. `pm2 stop coin_futures_trader`
2. 注释掉 `config.yaml` 中的 `coin_futures_symbols`
3. 添加symbol验证防止再次发生
4. 重启U本位服务

**推荐选项B**,因为:
- 币本位交易对没有K线数据
- 无法进行技术分析
- 风险较高
- 可以专注做好U本位合约
