# 超级大脑量化交易系统 — 项目上下文

## 项目概览

基于技术信号、市场趋势和智能风控的全自动加密货币量化交易平台,在 Binance U 本位合约市场自动执行交易,提供 Web 管理界面。

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

## 进程架构 (3 个核心交易/调度进程 + Web)

1. **smart_trader_service.py** — U 本位合约主交易
2. **app/scheduler.py** (crypto-scheduler, systemd) — 统一调度引擎
3. **app/main.py** (FastAPI) — Web 服务 + REST API

辅助进程: fast_collector / ws_kline_collector / multi_strategy 等

## 数据库

- `binance-data`: 主数据库 (100+ 表)
- `data_cache`: 预计算缓存库 (v3.5 新增,5 张缓存表)
- 连接通过 .env 配置,需显式在服务启动时连接

## 重要规则

- **Big4RegimeMonitor / 熔断禁止改系统设置**: Big4 和盈亏熔断只能发通知，不允许修改 `system_settings` 的 `allow_long`/`allow_short`/`trading_enabled`。用户手动控制。
- **1h K线天然滞后**: 1h 最新收盘的 K 线永远是上一小时的，延迟约 60-65 分钟属正常。`BACKFILL_LAG_THRESHOLD_S['1h']=3900` (65min)，不要改小。
- **WS K线 backfill exchange name**: `_check_and_backfill` 中 exchange 必须用 `binance_futures`（匹配 `save_klines` 的存储名），不能用 `usdt_futures`。
- **关模拟仓不会自动同步实盘**: 没有后台监听服务。需要通过 `BinanceFuturesEngine.close_position_direct()` 主动在交易所平仓。
- **除 crypto-scheduler 外不要加心跳日志**: WS 采集器日志中有 `send_heartbeat` 日志是正常的 (每 20s)，不要把它当作 bug 去修。

## scheduler 调度任务

| 任务 | 频率 |
|------|------|
| 价格采集 | 持续 (WS + REST) |
| data_cache.market_snapshot | 每 1 分钟 |
| data_cache.market_movers_snapshot | 每 5 分钟 |
| data_cache.position_stats_snapshot | 每 30 分钟 |
| data_cache.candidate_pool_snapshot | 每 30 分钟 |
| data_cache.settings_cache | 每 1 分钟 |
| Gemini 探索 | 每 2 小时 |
| Gemini 预测 | 每 4 小时 |
| DeepSeek 探索 | 每 2 小时 |
| DeepSeek 预测 | 每 4 小时 |
| 市场情绪分析 | 每 6 小时 |
| ETF 同步 | 每天 06:45 |
| 金库同步 | 每天 07:30 |

## Gemini AI 模块

### gemini_explore_worker (探索)
- 每 2h, kill switch: `gemini_explore_enabled`
- 检测红/黑天鹅,开模拟单 (SL=3%/TP=8%/3x/6h/500U)
- v3.5 接入实盘: `live_trading_enabled=1` 时同步到所有 active API Key

### gemini_predictor (预测)
- 每 4h, kill switch: `gemini_predict_enabled`
- 预测 TOP50 方向,持仓 6h,SL=3%/TP=8%
- 不实盘接入

### gemini_sentiment_analyzer (情绪)
- 每 6h, kill switch: `gemini_sentiment_enabled` (默认 1)
- 市场情绪标签 + 川普分析

### GeminiPositionAdvisor (持仓顾问)
- 开关: `system_settings.gemini_position_advisor_enabled`
- 扫描 `futures_positions` (模拟仓, account_id=2), 持仓 >= 2h
- 问 Gemini 三选一: hold/observe/sell
- sell 时通过 `BinanceFuturesEngine.close_position_direct()` 平实盘，再同步关模拟仓
- 需 `live_close_enabled=1`（实盘平仓开关）才能操作实盘

## 多策略服务 (multi_strategy_service)

保留策略:
- **S1** 早期做多 (5x, RSI+MA20)
- **S5** 大币超卖 (5x, BTC/ETH/BNB/SOL/XRP, RSI<32)
- **S6** 小币量能异动 (5x, 量能先行)
- **S9** Gemini AI 抄底反转 (5x, LONG-only, 每6h)

已清理策略 (2026-05-28, 从未有效开仓, 移除代码+系统设置控件):
- S2 回调做多 / S3 顶部做空 / S4 反弹做空 / S7 MA支撑 / S8 顶部反转做空
- `run_fast()` 方法已删除, `run_slow()` 只调 S1/S5/S6/S9

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

## 进程间关系

- `smart_trader_service.py` (U本位) 负责主策略与多策略实盘
- `app/scheduler.py` (crypto-scheduler) 统一调度价格采集、Gemini 等
- `app/main.py` (FastAPI) 提供 Web 界面
- 核心进程需全部启动。某个挂了其他仍能运行，但对应功能会缺失
