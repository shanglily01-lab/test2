# 币本位合约交易功能

## 概述

已成功添加币本位合约交易功能，与现有的U本位合约交易并行运行。

## 新增文件

1. **前端页面**: `templates/coin_futures_trading.html`
   - 币本位合约交易界面
   - 页面标题: "币本位合约交易"
   - 访问路径: `/coin_futures_trading`

2. **后端API**: `app/api/coin_futures_api.py`
   - 币本位合约交易API endpoints
   - API路由前缀: `/api/coin-futures`
   - 数据源: Binance币本位合约API (dapi.binance.com)

## 修改的文件

1. **templates/futures_trading.html**
   - 页面标题改为: "U本位合约交易"
   - 导航栏增加币本位合约链接

2. **templates/coin_futures_trading.html**
   - 导航栏包含U本位和币本位合约链接
   - 所有API调用路径改为 `/api/coin-futures/*`

3. **app/main.py**
   - 添加币本位合约页面路由: `/coin_futures_trading`
   - 注册币本位合约API路由: `/api/coin-futures/*`

4. **app/api/coin_futures_api.py**
   - API前缀: `/api/coin-futures`
   - 币本位交易对配置: 从 `config.yaml` 的 `coin_futures_symbols` 读取
   - 价格API: 使用 `https://dapi.binance.com/dapi/v1/ticker/price`
   - 数据源标识: `binance_coin_futures`

## 功能特性

### 数据采集
- **交易对配置**: 8个币本位合约 (BTCUSD_PERP, ETHUSD_PERP, 等)
- **K线周期**: 5m, 15m, 1h, 1d
- **数据库标识**: exchange = 'binance_coin_futures'
- **采集服务**: `fast_collector_service.py` (使用 `SmartFuturesCollector`)

### API Endpoints

#### 币本位合约API (`/api/coin-futures/*`)
- `GET /api/coin-futures/symbols` - 获取币本位合约交易对列表
- `GET /api/coin-futures/price/{symbol}` - 获取单个币本位合约价格
- `POST /api/coin-futures/prices/batch` - 批量获取币本位合约价格
- `GET /api/coin-futures/positions` - 获取币本位合约持仓
- `POST /api/coin-futures/open` - 开仓
- `POST /api/coin-futures/close` - 平仓

#### U本位合约API (`/api/futures/*`) - 保持不变
- 所有原有endpoints继续使用U本位合约数据

### 数据库结构

使用同一个 `kline_data` 表，通过 `exchange` 字段区分:
- U本位合约: `exchange = 'binance_futures'`
- 币本位合约: `exchange = 'binance_coin_futures'`

### 页面导航

两个合约交易页面的导航栏都包含:
- 首页
- Dashboard
- 技术信号
- 现货交易
- **U本位合约** (链接到 `/futures_trading`)
- **币本位合约** (链接到 `/coin_futures_trading`)
- 复盘(24H)
- ETF数据
- 企业金库
- Gas统计
- 数据管理

## 使用方法

### 访问页面
1. **U本位合约交易**: http://localhost:8000/futures_trading
2. **币本位合约交易**: http://localhost:8000/coin_futures_trading

### 数据采集
数据采集服务已经在运行，每5分钟自动采集一次:
- U本位: 44个交易对
- 币本位: 8个交易对

### 配置交易对
编辑 `config.yaml`:
```yaml
# 币本位合约交易对 (Coin-M Futures)
coin_futures_symbols:
  - BTCUSD_PERP
  - ETHUSD_PERP
  - BNBUSD_PERP
  - SOLUSD_PERP
  - XRPUSD_PERP
  - ADAUSD_PERP
  - DOTUSD_PERP
  - LINKUSD_PERP
```

## 技术实现细节

### API数据源
- **U本位合约**: `https://fapi.binance.com` (USDT-M Futures)
- **币本位合约**: `https://dapi.binance.com` (Coin-M Futures)

### 符号格式转换
- **Binance格式**: BTCUSD_PERP
- **显示格式**: BTC/USD

### 价格获取优先级
1. Binance币本位合约API (dapi.binance.com)
2. 数据库最新K线数据 (fallback)

## 测试

### 验证数据采集
运行 `check_server_logs.py` 查看币本位数据:
```bash
python check_server_logs.py
```

预期输出:
- 币本位合约数据总数: 1282+ 条
- 包含所有配置的交易对
- 最近5分钟有新数据更新

### 验证API
```bash
# 获取币本位交易对列表
curl http://localhost:8000/api/coin-futures/symbols

# 获取BTC币本位价格
curl http://localhost:8000/api/coin-futures/price/BTC%2FUSD
```

## 未来扩展

1. 添加币本位合约特有的交易策略
2. 支持币本位合约的资金费率数据
3. 添加U本位和币本位的对比分析功能
4. 支持更多币本位交易对

## 注意事项

- 币本位合约使用的保证金是对应的加密货币(如BTC、ETH)，不是USDT
- 合约价值以USD计价，但保证金和盈亏以币计价
- 与U本位合约的风险管理策略可能不同
