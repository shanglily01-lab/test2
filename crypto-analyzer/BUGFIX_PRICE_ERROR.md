# 修复报告: 开单价格错误问题

## 🐛 问题描述

用户报告开单的价格"全是错的"。经检查发现:

- **STRAX/USDT**: 市场价 1.417，入场价 0.02031 ❌ (错70倍)
- **MKR/USDT**: 市场价 1650.1，入场价 1818.5 ❌
- **SXP/USDT**: 市场价 0.0656，入场价 0.0610 ❌
- **TROY/USDT**, **HIFI/USDT**, **SNT/USDT** 等多个交易对价格异常

## 🔍 根本原因

### 1. 配置文件包含已下架的交易对

`config.yaml` 中有 **26个交易对**在币安合约已经下架(状态=SETTLING):

```
ALPACA/USDT, BNX/USDT, ALPHA/USDT, PORT3/USDT, UXLINK/USDT,
VIDT/USDT, SXP/USDT, AGIX/USDT, LINA/USDT, MEMEFI/USDT,
LEVER/USDT, NEIROETH/USDT, FTM/USDT, WAVES/USDT, OMNI/USDT,
AMB/USDT, BSW/USDT, OCEAN/USDT, STRAX/USDT, REN/USDT,
UNFI/USDT, DGB/USDT, TROY/USDT, HIFI/USDT, SNT/USDT, MKR/USDT
```

### 2. WebSocket返回错误的标记价格

对于SETTLING状态的合约:
- 币安 WebSocket 仍然推送数据
- 但价格是**结算前的旧价格**或**错误的价格**
- 系统没有过滤这些下架合约,导致开仓时使用了错误价格

### 3. 分批建仓被卡住

因为价格错误导致:
- 持仓状态一直停留在 `building` (分批建仓中)
- 第2批、第3批永远无法触发
- 占用保证金但无法正常交易

## ✅ 修复措施

### 1. 更新 config.yaml ✅

- **删除**: 26个已下架的交易对
- **保留**: 81个正常交易的交易对
- **备份**: 原配置已备份到 `config.yaml.backup_20260125_210531`

### 2. 清理无效持仓 ✅

删除了 4个 building 状态的无效持仓:
- ID:5794 SXP/USDT SHORT
- ID:5792 DGB/USDT SHORT
- ID:5790 STRAX/USDT SHORT
- ID:5789 SXP/USDT SHORT

### 3. 更新交易黑名单 ✅

将26个下架交易对加入 `trading_blacklist` 表:
- 原因: "币安合约已下架(SETTLING)"
- 状态: `is_active = 1`
- 系统将使用100U小仓位或完全跳过这些交易对

## 📊 修复结果

### 配置文件变化
```
原有交易对: 107 个
更新后:     81 个
已移除:     26 个
```

### 数据库变化
```
删除无效持仓: 4 个
新增黑名单:   26 条
```

## 🔧 需要执行的操作

### 1. 重启交易服务 ⚠️ 必须

```bash
# 停止正在运行的服务
pkill -f smart_trader_service.py

# 重新启动
nohup python smart_trader_service.py > logs/trader.log 2>&1 &
```

**为什么必须重启**:
- 配置文件已更新,但内存中还是旧的107个交易对
- 重启后会重新加载配置,只监控81个有效交易对
- WebSocket 订阅也会更新,不再订阅下架的交易对

### 2. 验证系统正常

重启后检查日志:
```bash
tail -f logs/smart_trader_service.py logs/trader.log

# 应该看到:
# ✅ 从数据库加载配置:
#    总交易对: 81
#    可交易: 81 个
```

### 3. 定期检查合约状态

创建定时任务(每周一次):
```bash
# 添加到 crontab
0 2 * * 1 cd /path/to/crypto-analyzer && python check_symbol_status.py >> logs/symbol_check.log 2>&1
```

## 🛡️ 预防措施

### 1. 添加价格合理性检查

建议在 `smart_entry_executor.py` 的 `_get_current_price` 函数中添加:

```python
# 价格合理性检查
if price > 0:
    # 与数据库K线价格对比,差异>50%则使用K线价格
    db_price = self._get_price_from_kline(symbol)
    if db_price and abs(price - db_price) / db_price > 0.5:
        logger.warning(f"{symbol} WebSocket价格异常 ({price}), 使用K线价格 ({db_price})")
        return Decimal(str(db_price))
```

### 2. 过滤SETTLING状态的合约

在 WebSocket 订阅前检查合约状态:

```python
def _filter_valid_symbols(symbols: List[str]) -> List[str]:
    """过滤出可交易的合约"""
    response = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo')
    exchange_info = response.json()

    valid_symbols = []
    for symbol_info in exchange_info['symbols']:
        if symbol_info['status'] == 'TRADING':
            symbol = symbol_info['symbol']
            formatted = symbol[:-4] + '/USDT' if symbol.endswith('USDT') else symbol
            if formatted in symbols:
                valid_symbols.append(formatted)

    return valid_symbols
```

### 3. 监控building持仓

添加告警,如果持仓超过1小时仍在building状态:

```python
# 每小时检查一次
SELECT id, symbol, position_side, created_at
FROM futures_positions
WHERE status = 'building'
AND TIMESTAMPDIFF(HOUR, created_at, NOW()) > 1
```

## 📈 影响范围

### 受影响的功能
- ✅ 开仓价格获取
- ✅ 分批建仓执行
- ✅ WebSocket价格监控
- ✅ 智能入场时机判断

### 未受影响的功能
- ✅ 平仓逻辑 (使用REST API实时价格)
- ✅ 止损止盈 (基于持仓表中的价格)
- ✅ K线数据采集 (独立进程)
- ✅ 评分系统 (基于历史K线)

## 📝 相关文件

### 修复脚本
1. `check_symbol_status.py` - 检查交易对状态
2. `fix_bad_positions_and_config.py` - 修复无效持仓和配置
3. `BUGFIX_PRICE_ERROR.md` - 本报告

### 涉及的代码
1. `smart_trader_service.py:59` - 加载配置
2. `app/services/smart_entry_executor.py:432` - 获取价格
3. `app/services/binance_ws_price.py:243` - WebSocket消息处理

### 修改的配置
1. `config.yaml` - 交易对列表 (107→81)
2. `trading_blacklist` 表 - 新增26条黑名单

## ⏰ 时间线

- **2026-01-25 12:57-13:05**: 系统开了多个building持仓(价格错误)
- **2026-01-25 21:00**: 用户发现问题并报告
- **2026-01-25 21:04**: 诊断出根本原因(下架合约)
- **2026-01-25 21:05**: 执行修复脚本,清理无效数据
- **2026-01-25 21:06**: 修复完成,等待重启服务

---

**修复人员**: Claude (AI Assistant)
**修复时间**: 2026-01-25 21:05
**状态**: ✅ 已完成 (需重启服务生效)
**严重级别**: 🔴 高 (影响所有开仓操作)
