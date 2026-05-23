# 超级大脑量化交易系统 — 项目上下文

## 项目概览

基于技术信号、市场趋势和智能风控的全自动加密货币量化交易平台,在 Binance 合约市场自动执行交易。支持 U 本位合约、币本位合约,提供 Web 管理界面。

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn (Port 9020) |
| 数据库 | MySQL 8.0 + pymysql (远程 AWS) |
| 调度 | schedule (app/scheduler.py) |
| 交易所 | Binance Futures |
| 前端 | Jinja2 + Tailwind CSS (服务端渲染) |
| AI | Google Gemini (google.genai SDK) |
| 通知 | Telegram Bot API |
| 配置 | dotenv_values 读 .env |

## 进程架构 (4 个独立进程)

1. **smart_trader_service.py** — U 本位合约主交易 (4100 行)
2. **coin_futures_trader_service.py** — 币本位合约交易
3. **app/scheduler.py** (crypto-scheduler, systemd) — 统一调度引擎
4. **app/main.py** (FastAPI) — Web 服务 + REST API

## 数据库

- `binance-data`: 主数据库 (100+ 表)
- `data_cache`: 预计算缓存库 (v3.5 新增,5 张缓存表)
- 连接通过 .env 配置,需显式在服务启动时连接

## scheduler 调度任务

| 任务 | 频率 |
|------|------|
| 价格采集 | 持续 (WS + REST) |
| data_cache.market_snapshot | 每 1 分钟 |
| data_cache.market_movers_snapshot | 每 5 分钟 |
| data_cache.position_stats_snapshot | 每 30 分钟 |
| data_cache.candidate_pool_snapshot | 每 30 分钟 |
| data_cache.settings_cache | 每 1 分钟 |
| Gemini 探索 | 每 6 小时 |
| Gemini 预测 | 每 12 小时 |
| 市场情绪分析 | 每 8 小时 |
| ETF 同步 | 每天 06:45 |
| 金库同步 | 每天 07:30 |

## Gemini AI 模块

### gemini_explore_worker (探索)
- 每 6h, kill switch: `gemini_explore_enabled`
- 检测红/黑天鹅,开模拟单 (SL=5%/TP=15%/3x/72h/500U)
- v3.5 接入实盘: `live_trading_enabled=1` 时同步到所有 active API Key

### gemini_predictor (预测)
- 每 12h, kill switch: `gemini_predict_enabled`
- 预测 TOP50 方向,不实盘接入

### gemini_sentiment_analyzer (情绪)
- 每 8h, kill switch: `gemini_sentiment_enabled` (默认 1)
- 市场情绪标签 + 川普分析

### S9 (multi_strategy_service 内)
- 每 6h, kill switch: `s9_gemini_ai_enabled`
- LONG-only 抄底反转

## 实盘控制

- **统一闸门**: `system_settings.live_trading_enabled` (1=开启)
- 实盘开仓需在 Top50 名单内
- 每账号 OPEN 上限 20 仓
- 各策略通过 `_sync_live()` / `_sync_to_live()` 同步到 BinanceFuturesEngine

## 代码规范

- 配置: `dotenv_values` 读 .env **不读系统环境变量**
- 日志: loguru → logs/ 目录
- 语法检查: `python -c "import ast; ast.parse(...)"`
- 代码改动后必须重启对应进程 (Python 不热重载)
- Gemini 使用新版 SDK: `import google.genai as genai`
- 非必要不要对已有逻辑进行过度封装,不要过度抽象
