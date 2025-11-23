# Crypto Analyzer - 加密货币分析系统

## 项目简介

一个功能完整的加密货币分析系统，提供实时数据采集、技术分析、策略执行和交易管理功能。

## 项目结构

```
crypto-analyzer/
├── app/                          # 主应用代码
│   ├── main.py                  # FastAPI主应用入口
│   ├── scheduler.py             # 数据采集调度器
│   ├── strategy_scheduler.py    # 策略执行调度器 ⭐
│   ├── hyperliquid_scheduler.py # Hyperliquid监控调度器
│   │
│   ├── api/                     # API路由
│   │   ├── routes.py           # 通用路由
│   │   ├── futures_api.py      # 合约交易API
│   │   ├── strategy_api.py     # 策略管理API
│   │   └── ...
│   │
│   ├── collectors/              # 数据采集器
│   │   ├── price_collector.py  # 价格采集
│   │   ├── binance_futures_collector.py  # 币安合约数据
│   │   ├── hyperliquid_collector.py      # Hyperliquid数据
│   │   └── ...
│   │
│   ├── analyzers/               # 分析器
│   │   ├── technical_indicators.py  # 技术指标计算
│   │   ├── signal_generator.py      # 信号生成
│   │   └── ...
│   │
│   ├── services/                # 业务服务
│   │   ├── strategy_executor.py     # 策略执行器
│   │   ├── strategy_hit_recorder.py # 策略命中记录
│   │   └── ...
│   │
│   ├── trading/                 # 交易引擎
│   │   ├── futures_trading_engine.py  # 合约交易引擎
│   │   └── ...
│   │
│   └── database/                # 数据库相关
│       ├── db_service.py        # 数据库服务
│       ├── models.py            # 数据模型
│       └── ...
│
├── config/                      # 配置文件
│   ├── config.yaml             # 主配置文件
│   └── strategies/             # 策略配置
│       └── futures_strategies.json
│
├── scripts/                     # 工具脚本
│   ├── migrations/             # 数据库迁移脚本
│   ├── init/                   # 初始化脚本
│   ├── corporate_treasury/     # 企业持仓管理
│   ├── etf/                    # ETF数据管理
│   └── hyperliquid/            # Hyperliquid监控
│
├── templates/                   # HTML模板
│   ├── index.html              # 首页
│   ├── dashboard.html          # 仪表板
│   ├── futures_trading.html    # 合约交易
│   ├── trading_strategies.html # 交易策略
│   └── ...
│
├── static/                      # 静态资源
│   ├── css/                    # 样式文件
│   ├── js/                     # JavaScript文件
│   └── images/                 # 图片资源
│
├── docs/                        # 文档
│   ├── guides/                 # 使用指南
│   └── features/               # 功能说明
│
├── logs/                        # 日志文件
├── data/                        # 数据文件
├── requirements.txt             # Python依赖
└── README.md                    # 本文件
```

## 核心功能

### 1. 数据采集
- 实时价格数据（Binance、Gate.io等）
- K线数据（1m、5m、15m、1h等）
- 合约数据（持仓量、资金费率、多空比）
- 新闻数据
- Hyperliquid聪明钱包监控

### 2. 技术分析
- 技术指标计算（EMA、MA、MACD、RSI、KDJ、BB等）
- 信号生成（买入/卖出信号）
- 趋势分析

### 3. 策略执行
- 自动策略执行（EMA交叉策略等）
- 策略命中记录
- 实时监控和执行

### 4. 交易管理
- 模拟合约交易
- 持仓管理
- 订单管理
- 止盈止损

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置数据库

编辑 `config.yaml`，配置MySQL数据库连接信息。

### 3. 运行数据库迁移

```bash
# 根据需要运行相应的迁移脚本
python scripts/migrations/xxx.sql
```

### 4. 启动服务

**数据采集器**（必须）：
```bash
python app/scheduler.py
```

**策略执行器**（必须，用于自动交易）：
```bash
python app/strategy_scheduler.py
```

**Web服务**（可选）：
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Hyperliquid监控**（可选）：
```bash
python app/hyperliquid_scheduler.py
```

### 5. 访问系统

浏览器打开：`http://localhost:8000`

## 重要服务说明

### 数据采集调度器 (`app/scheduler.py`)
- **功能**：采集市场数据（价格、K线、新闻等）
- **必须运行**：是（如果需要有数据）
- **日志**：`logs/scheduler_*.log`

### 策略执行调度器 (`app/strategy_scheduler.py`) ⭐
- **功能**：执行交易策略，检测信号，自动交易
- **必须运行**：是（如果需要自动交易）
- **日志**：`logs/scheduler_*.log`
- **说明**：每5秒检查一次策略，检测到信号时自动执行交易

### Web服务 (`app/main.py`)
- **功能**：提供Web界面和API
- **必须运行**：否（如果只需要自动交易，可以不运行）

### Hyperliquid监控 (`app/hyperliquid_scheduler.py`)
- **功能**：监控Hyperliquid聪明钱包
- **必须运行**：否（可选功能）

## 配置文件

### `config.yaml`
主配置文件，包含：
- 数据库配置
- API密钥配置
- 功能开关

### `config/strategies/futures_strategies.json`
策略配置文件，包含：
- 策略列表
- 策略参数（EMA周期、止损止盈等）
- 启用/禁用状态

## 数据库

主要数据表：
- `kline_data` - K线数据
- `price_data` - 价格数据
- `futures_positions` - 合约持仓
- `futures_orders` - 合约订单
- `strategy_hits` - 策略命中记录
- `smart_money_addresses` - 聪明钱包地址
- 等等...

## 日志

日志文件保存在 `logs/` 目录：
- `scheduler_YYYY-MM-DD.log` - 数据采集和策略执行日志
- `main_YYYY-MM-DD.log` - Web服务日志
- `hyperliquid_scheduler_YYYY-MM-DD.log` - Hyperliquid监控日志

## 开发

### 添加新功能
1. 在相应的模块中添加代码
2. 更新API路由（如需要）
3. 更新前端模板（如需要）
4. 更新文档

### 数据库迁移
1. 在 `scripts/migrations/` 创建SQL脚本
2. 执行迁移脚本
3. 更新 `models.py`（如需要）

## 常见问题

### Q: 策略执行器没有执行交易？
A: 检查：
1. 策略执行器是否在运行
2. 策略是否启用
3. 市场是否有EMA交叉信号
4. 信号是否被过滤条件过滤掉

### Q: 数据采集失败？
A: 检查：
1. 数据库连接是否正常
2. API密钥是否有效
3. 网络连接是否正常

### Q: Web页面无法访问？
A: 检查：
1. Web服务是否启动
2. 端口是否被占用
3. 防火墙设置

## 更多文档

- [快速启动指南](docs/guides/快速启动指南.md)
- [Hyperliquid调度器说明](docs/features/hyperliquid_scheduler.md)
- [监控机制说明](docs/features/监控机制.md)

## 许可证

[根据实际情况填写]

## 更新日志

### 2025-11-23
- 修复 strategy_id 字段类型问题（INT -> BIGINT）
- 添加策略命中记录功能
- 优化策略执行器

