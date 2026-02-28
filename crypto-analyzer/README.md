# 超级大脑交易系统 (Super Brain Trading System)

> 基于 Binance 的全自动加密货币合约交易平台，集信号评分、Big4趋势研判、风险管理于一体，支持 U 本位与币本位双引擎 24 小时无间断运行。

---

## 目录

- [系统概述](#系统概述)
- [核心功能](#核心功能)
- [技术架构](#技术架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [服务说明](#服务说明)
- [信号评分系统](#信号评分系统)
- [风险管理](#风险管理)
- [Web 管理界面](#web-管理界面)
- [数据库说明](#数据库说明)
- [API 接口](#api-接口)
- [配置说明](#配置说明)

---

## 系统概述

超级大脑交易系统是一套多维度、全自动的加密货币量化交易系统，主要特点：

- **双引擎交易**：U 本位合约（USDT 结算）+ 币本位合约（BTC/ETH 结算）独立运行
- **信号评分驱动**：24 个信号分量加权评分，多空各有独立权重体系
- **Big4 宏观过滤**：实时监控 BTC/ETH/BNB/SOL 四大主力，宏观方向不明时暂停开仓
- **8 层风控体系**：从止损止盈到全局熔断，层层防护
- **模拟 + 实盘联动**：模拟盘验证策略，一键切换实盘同步
- **完整 Web 后台**：17 个管理页面，涵盖持仓监控、信号分析、配置管理

---

## 核心功能

### 交易策略

| 功能 | U 本位 | 币本位 |
|------|--------|--------|
| 杠杆倍数 | 5x | 5x |
| 止损 | 2% | 3% |
| 止盈 | 6% | 2% |
| 最大持仓时间 | ~4 小时 | ~60 分钟 |
| 分批建仓 | ✅ 3 批（30%-30%-40%） | ❌ 一次性建仓 |
| Deep V 反转保护 | ✅ 45 分钟保护窗口 | ✅ |
| 移动止盈 | ✅ | ✅ |

### 信号体系

- **24 个信号分量**，覆盖趋势、动量、成交量、位置等维度
- **LONG 满分**：217 分 / **SHORT 满分**：243 分（SHORT 约 12% 优势，合理范围）
- **入场阈值**：55 分（可配置）
- 信号权重存储于数据库，支持人工调整（已禁用自动优化器）

### Big4 宏观研判

- **监控标的**：BTC（50%权重）、ETH（30%）、BNB（10%）、SOL（10%）
- **时间维度**：1H（30根）× 15M（30根）× 5M（3根）三层研判
- **输出信号**：BULLISH / BEARISH / NEUTRAL
- **紧急干预**：6H 涨跌 >5% 时触发全局禁空/禁多，保护 2 小时

### 数据采集

| 数据源 | 频率 | 内容 |
|--------|------|------|
| Binance 现货 | 5m/15m/1h/1d | K 线 OHLCV |
| Binance 合约 | 实时 + 5m | 价格 + K 线 |
| 资金费率 | 每 5 分钟 | Binance 期货资金费率 |
| 新闻 | 每 15 分钟 | RSS 多源聚合 |
| Hyperliquid | 每日 + 实时 | 排行榜 + 钱包监控 |
| 以太坊链上 | 5m/1h/1d | Gas + 链上数据 |
| 价格统计缓存 | 每 1 分钟 | price_stats_24h 刷新 |
| 技术指标缓存 | 每 5 分钟 | technical_indicators_cache |

---

## 技术架构

### 技术栈

| 层级 | 技术 |
|------|------|
| **后端框架** | Python 3.9+ · FastAPI 0.104 · Uvicorn |
| **数据库** | MySQL 8.0 · SQLAlchemy 2.0 · pymysql |
| **异步** | asyncio · aiohttp · WebSocket |
| **调度** | schedule · APScheduler |
| **数据处理** | pandas · numpy |
| **前端** | Jinja2 模板 · HTML/CSS/JavaScript |
| **日志** | loguru |
| **配置** | PyYAML · python-dotenv |
| **认证** | PyJWT · bcrypt |

### 系统架构图

```
┌─────────────────────────────────────────────────────────┐
│                     Web 管理界面 (Port 9020)              │
│           FastAPI + Jinja2 (app/main.py)                 │
└───────────────────┬─────────────────────────────────────┘
                    │ REST API
    ┌───────────────┼────────────────┐
    ▼               ▼                ▼
┌────────┐   ┌───────────┐   ┌──────────────┐
│U本位   │   │币本位     │   │数据采集      │
│交易引擎│   │交易引擎   │   │调度器        │
│(账户2) │   │(账户3)    │   │(scheduler.py)│
└────┬───┘   └─────┬─────┘   └──────┬───────┘
     │              │                │
     └──────────────┴────────────────┘
                    │
            ┌───────▼────────┐
            │   MySQL 数据库  │
            │  (binance-data) │
            └───────┬────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   ┌─────────┐ ┌─────────┐ ┌──────────┐
   │Binance  │ │Hyperliq.│ │ 新闻/链上 │
   │Spot/Fut │ │ Wallets │ │  数据源   │
   └─────────┘ └─────────┘ └──────────┘
```

---

## 目录结构

```
crypto-analyzer/
├── smart_trader_service.py          # U 本位自动交易主进程
├── coin_futures_trader_service.py   # 币本位自动交易主进程
├── app/
│   ├── main.py                      # FastAPI 服务器入口
│   ├── scheduler.py                 # 统一数据采集调度器
│   ├── hyperliquid_scheduler.py     # Hyperliquid 钱包监控
│   ├── api/                         # REST API 模块（17个）
│   │   ├── futures_api.py
│   │   ├── coin_futures_api.py
│   │   ├── live_trading_api.py
│   │   ├── paper_trading_api.py
│   │   ├── system_settings_api.py
│   │   ├── rating_api.py
│   │   └── ...
│   ├── services/                    # 核心业务服务（40+个）
│   │   ├── smart_decision_brain.py      # 智能决策大脑
│   │   ├── signal_score_v2_service.py   # 信号评分 V2
│   │   ├── big4_trend_detector.py       # Big4 趋势检测
│   │   ├── smart_entry_executor.py      # 智能入场执行器
│   │   ├── smart_exit_optimizer.py      # 智能出场优化器
│   │   ├── signal_blacklist_checker.py  # 信号黑名单检查
│   │   ├── breakout_system.py           # 突破系统
│   │   ├── binance_ws_price.py          # WebSocket 实时价格
│   │   ├── adaptive_optimizer.py        # 参数自适应优化器
│   │   ├── cache_update_service.py      # 缓存更新服务
│   │   └── ...
│   ├── trading/                     # 交易引擎
│   │   ├── futures_trading_engine.py        # U 本位交易引擎
│   │   ├── coin_futures_trading_engine.py   # 币本位交易引擎
│   │   ├── binance_futures_engine.py        # Binance 实盘接口
│   │   └── paper_trading_engine.py          # 模拟盘引擎
│   ├── collectors/                  # 数据采集器（9个）
│   │   ├── price_collector.py
│   │   ├── binance_futures_collector.py
│   │   ├── hyperliquid_collector.py
│   │   ├── news_collector.py
│   │   └── ...
│   ├── analyzers/                   # 分析模块（5个）
│   │   ├── technical_indicators.py
│   │   ├── signal_generator.py
│   │   └── ...
│   ├── strategies/                  # 策略模块
│   │   ├── bollinger_mean_reversion.py
│   │   ├── range_market_detector.py
│   │   └── ...
│   └── database/
│       ├── db_service.py
│       └── connection_pool.py
├── templates/                       # 17 个 HTML 管理页面
├── static/                          # 前端静态资源
├── docs/                            # 详细设计文档
├── scripts/                         # 数据库迁移脚本
├── config.yaml                      # 主配置文件
├── requirements.txt                 # Python 依赖
└── .env                             # 环境变量（不提交 Git）
```

---

## 快速开始

### 环境要求

- Python 3.9+
- MySQL 8.0+
- Binance 账户（已开通合约权限并创建 API Key）

### 安装步骤

```bash
# 1. 克隆仓库
git clone <repo-url>
cd crypto-analyzer

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填写数据库和 API 信息
```

### 环境变量 (.env)

```env
DB_HOST=your_mysql_host
DB_PORT=3306
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=binance-data

JWT_SECRET_KEY=your_jwt_secret
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

### 初始化数据库

```bash
# 执行建表脚本
mysql -u your_user -p binance-data < table_schemas.txt
```

### 启动服务

各服务可独立启动，建议按以下顺序：

```bash
# 1. 启动 FastAPI 后台服务器（必须）
uvicorn app.main:app --host 0.0.0.0 --port 9020

# 2. 启动数据采集调度器（必须，为交易引擎提供数据）
python app/scheduler.py

# 3. 启动 U 本位自动交易（可选）
python smart_trader_service.py

# 4. 启动币本位自动交易（可选）
python coin_futures_trader_service.py
```

启动后访问：**http://localhost:9020**

---

## 服务说明

### smart_trader_service.py — U 本位自动交易

负责 USDT 结算的合约自动交易，账户 ID = 2。

**核心流程**：
1. 每 5 秒扫描全部交易对
2. Big4 宏观研判（过滤方向不明行情）
3. 信号评分（24 个分量，阈值 55 分）
4. 信号黑名单检查
5. 盈利熔断 / 亏损熔断检查（每 4/3 小时）
6. 触底反弹 / 触顶回调紧急拦截
7. 分 3 批建仓（30%-30%-40%）
8. 持续监控止损 / 止盈 / 超时平仓

**熔断规则**：
- 盈利熔断：过去 6H 盈利 > 1000U → 禁止 U 本位开仓
- 亏损熔断：过去 3H 亏损 > 300U → 禁止 U 本位开仓

### coin_futures_trader_service.py — 币本位自动交易

负责以 BTC/ETH 为保证金的合约自动交易，账户 ID = 3。

**熔断规则**：
- 盈利熔断：过去 6H 盈利 > 200U → 禁止币本位开仓
- 亏损熔断：过去 3H 亏损 > 100U → 禁止币本位开仓

### app/scheduler.py — 数据采集调度器

统一管理所有外部数据的采集与缓存更新：

| 任务 | 频率 |
|------|------|
| 价格统计缓存（price_stats_24h） | 每 1 分钟 |
| 技术指标 + 投资建议缓存 | 每 5 分钟 |
| K 线数据（5m/15m/1h/1d） | 随频率触发 |
| 资金费率 | 每 5 分钟 |
| 新闻聚合 | 每 15 分钟 |
| Hyperliquid 聚合缓存 | 每 10 分钟 |
| K 线评分（update_all_coin_scores） | 每 5 分钟 |

---

## 信号评分系统

### 信号分量（24个）

| 信号 | 描述 | LONG权重 | SHORT权重 |
|------|------|----------|-----------|
| `position_low` | 价格处于低位 | 30 | 0 |
| `position_mid` | 价格处于中位 | 5 | 5 |
| `position_high` | 价格处于高位 | 0 | 20 |
| `momentum_up_3pct` | 24H涨幅 >3% | 15 | 0 |
| `momentum_down_3pct` | 24H跌幅 >3% | 0 | 15 |
| `trend_1h_bull` | 1H看涨（≥13根阳线） | 30 | 0 |
| `trend_1h_bear` | 1H看跌（≥13根阴线） | 0 | 20 |
| `consecutive_bull` | 连续上涨（7/10根阳线） | 27 | 0 |
| `consecutive_bear` | 连续下跌（7/10根阴线） | 0 | 25 |
| `volume_power_bull` | 强势成交量看涨 | 25 | 0 |
| `volume_power_bear` | 强势成交量看跌 | 0 | 25 |
| `volume_power_1h_bull` | 1H成交量看涨 | 27 | 0 |
| `volume_power_1h_bear` | 1H成交量看跌 | 0 | 25 |
| `breakout_long` | 高位突破看涨 | 30 | 0 |
| `breakdown_short` | 低位跌破看跌 | 0 | 20 |
| `volatility_high` | 高波动率 | 10 | 10 |

**总满分**：LONG = 217 分 / SHORT = 243 分

### 信号组合黑名单

高亏损信号组合会自动加入 `signal_blacklist` 表，系统扫描时跳过，如：
- `consecutive_bull + position_mid + trend_1h_bull`（LONG，46单 10.9%胜率 -405U）

---

## 风险管理

### 交易对评级系统

`trading_symbol_rating` 表按历史表现对交易对分级：

| 等级 | 限额 | 说明 |
|------|------|------|
| Level 0 | 400U | 正常交易 |
| Level 1 | 100U | 限制仓位 |
| Level 2 | 50U | 严格限制 |
| Level 3 | 0U | 完全禁止 |

### 全局开关（system_settings 表）

| 配置键 | 说明 |
|--------|------|
| `u_futures_trading_enabled` | U 本位总开关 |
| `coin_futures_trading_enabled` | 币本位总开关 |
| `live_trading_enabled` | 模拟盘与实盘联动开关 |
| `allow_long` | 允许做多 |
| `allow_short` | 允许做空 |

### 熔断机制

- **盈利熔断**：短期盈利过大（市场可能反转），自动暂停开仓
- **亏损熔断**：短期亏损超限，自动暂停开仓
- **Big4 紧急拦截**：触底反弹写入 `allow_short=0`，触顶回调写入 `allow_long=0`

---

## Web 管理界面

访问 `http://your-server:9020` 后可访问以下页面：

| 页面 | 路径 | 说明 |
|------|------|------|
| 仪表盘 | `/dashboard` | 价格概览、投资建议、Hyperliquid 数据 |
| 技术信号 | `/technical-signals` | 多时间框架技术指标（RSI/MACD/KDJ/BB） |
| U 本位持仓 | `/futures-trading` | 模拟盘 U 本位持仓与历史 |
| 币本位持仓 | `/coin-futures-trading` | 模拟盘币本位持仓与历史 |
| 实盘监控 | `/live-trading` | 真实 Binance 持仓监控 |
| 模拟盘 | `/paper-trading` | 多账户模拟交易 |
| 复盘分析 | `/futures-review` | 交易复盘与统计 |
| 行情识别 | `/market-regime` | 市场状态检测（趋势/震荡/突破） |
| 系统配置 | `/system-settings` | 全局开关与参数配置 |
| 数据管理 | `/data-management` | 采集任务状态与管理 |
| API 密钥 | `/api-keys` | 交易所 API Key 管理 |
| ETF 数据 | `/etf-data` | 加密货币 ETF 资金流向 |
| 企业持仓 | `/corporate-treasury` | MicroStrategy 等企业持币追踪 |
| Gas 监控 | `/blockchain-gas` | 以太坊 Gas 分析 |

---

## 数据库说明

主数据库：`binance-data`（MySQL 8.0）

### 核心表

| 表名 | 说明 |
|------|------|
| `futures_positions` | 模拟盘持仓（含已平仓历史） |
| `futures_trades` | 交易执行记录 |
| `kline_data` | K 线 OHLCV 数据（14M+ 行） |
| `coin_kline_scores` | K 线评分（每 5 分钟更新） |
| `big4_trend_history` | Big4 趋势快照 |
| `trading_symbol_rating` | 交易对评级（Level 0-3） |
| `signal_scoring_weights` | 信号权重配置（24 个分量） |
| `signal_blacklist` | 高亏损信号组合黑名单 |
| `system_settings` | 全局配置键值对 |
| `price_stats_24h` | 24H 价格统计缓存 |
| `technical_indicators_cache` | 技术指标缓存（15m/1h/1d） |
| `investment_recommendations_cache` | 投资建议缓存 |
| `funding_rate_stats` | 资金费率统计 |
| `live_futures_positions` | 实盘 Binance 持仓 |
| `hyperliquid_monitored_wallets` | Hyperliquid 监控钱包 |
| `corporate_treasury_companies` | 企业持币信息（108+ 家） |

### 重要注意事项

> ⚠️ `smart_trader_service.py` 中 `_get_connection()` 返回的连接**必须手动 `close()`**，否则导致 MySQL `Too many connections` 错误。

---

## API 接口

所有接口基础路径：`http://your-server:9020`

### 交易接口

```
GET  /api/futures/positions          # 获取 U 本位持仓
GET  /api/futures/trades             # 获取 U 本位交易记录
GET  /api/coin-futures/positions     # 获取币本位持仓
POST /api/futures/close/{id}         # 手动平仓
```

### 分析接口

```
GET  /api/technical-signals          # 技术信号（batch query + 60s 缓存）
GET  /api/dashboard                  # 仪表盘数据
GET  /api/trend-analysis             # 趋势分析
GET  /api/market-regime              # 市场状态
```

### 配置接口

```
GET  /api/system/settings            # 获取系统配置
POST /api/system/settings            # 更新系统配置
GET  /api/rating                     # 交易对评级列表
POST /api/rating/{symbol}            # 更新交易对评级
```

---

## 配置说明

### config.yaml 主要参数

```yaml
# 服务配置
api:
  host: 0.0.0.0
  port: 9020

# 数据库
database:
  mysql:
    host: your_host
    port: 3306
    database: binance-data

# 交易参数（U本位）
trading:
  leverage: 5
  entry_threshold: 55        # 信号入场阈值
  stop_loss_pct: 0.02        # 止损 2%
  take_profit_pct: 0.06      # 止盈 6%
  max_hold_hours: 4          # 最大持仓时间

# 监控交易对列表
symbols:
  - BTC/USDT
  - ETH/USDT
  - BNB/USDT
  - SOL/USDT
  # ...
```

---

## 注意事项

1. **数据库连接**：所有手动创建的 pymysql 连接必须在使用后调用 `.close()`，否则会耗尽连接池（默认上限 151）。

2. **启动顺序**：先启动 `scheduler.py` 采集数据，再启动交易服务；否则交易引擎因无数据而无法正常评分。

3. **信号权重**：`signal_scoring_weights` 表由人工维护，已禁用 `ScoringWeightOptimizer` 自动调整，如需修改请直接更新数据库。

4. **实盘联动**：`live_trading_enabled = false` 时，模拟盘平仓不会同步到 Binance 实盘，两者完全独立运行。

5. **熔断后恢复**：盈利/亏损熔断触发后，需手动在系统配置页面重新开启对应开关。

6. **代码修改**：修改交易服务代码后，需重启对应进程才能生效；修改配置文件同理。

---

## 文档

详细设计文档位于 `docs/` 目录：

- `docs/超级大脑U本位合约交易方案.md` — U 本位完整交易逻辑说明
- `docs/超级大脑币本位合约交易方案.md` — 币本位完整交易逻辑说明
- `docs/超级大脑现货交易方案.md` — 现货交易方案（V1.5）

---

*版本：V3.0 | 最后更新：2026-02*
