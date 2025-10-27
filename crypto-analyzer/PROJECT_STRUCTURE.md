# Crypto Analyzer - 项目结构文档

加密货币数据分析和交易系统

## 📁 项目目录结构

```
crypto-analyzer/
├── app/                          # 应用核心代码
│   ├── analyzers/                # 数据分析模块
│   ├── api/                      # API接口
│   │   ├── enhanced_dashboard.py           # 标准Dashboard API
│   │   ├── enhanced_dashboard_cached.py    # 缓存优化版Dashboard API
│   │   ├── paper_trading_api.py           # 模拟交易API
│   │   └── strategy_api.py                # 策略API
│   ├── collectors/               # 数据采集器
│   │   ├── price_collector.py             # Binance价格数据采集
│   │   ├── gate_collector.py              # Gate.io数据采集
│   │   ├── binance_futures_collector.py   # Binance合约数据采集
│   │   ├── hyperliquid_collector.py       # Hyperliquid数据采集
│   │   ├── news_collector.py              # 新闻数据采集
│   │   └── smart_money_collector.py       # 聪明钱数据采集
│   ├── database/                 # 数据库模型和服务
│   │   ├── models.py              # SQLAlchemy数据模型
│   │   └── db_service.py          # 数据库服务类
│   ├── services/                 # 业务服务
│   │   ├── cache_update_service.py        # 缓存更新服务
│   │   └── ...
│   ├── strategies/               # 交易策略
│   ├── trading/                  # 交易模块
│   └── web/                      # Web模块
│       └── templates/
├── config/                       # 配置文件
│   ├── strategies/               # 策略配置
│   └── ...
├── data/                         # 数据文件存储
├── docs/                         # 项目文档
│   ├── features/                 # 功能文档
│   ├── guides/                   # 使用指南
│   ├── quick/                    # 快速开始
│   └── updates/                  # 更新日志
├── logs/                         # 日志文件
├── scripts/                      # 脚本工具
│   ├── collectors/               # 采集器脚本
│   ├── etf/                      # ETF相关脚本
│   ├── hyperliquid/              # Hyperliquid脚本
│   ├── init/                     # 初始化脚本
│   ├── migrations/               # 数据库迁移
│   │   └── 001_create_cache_tables.sql    # 缓存表创建SQL
│   ├── utils/                    # 工具脚本
│   └── 管理/                      # 管理脚本
│       └── update_cache_manual.py         # 手动更新缓存
├── static/                       # 静态资源
│   ├── css/                      # 样式文件
│   └── js/                       # JavaScript文件
│       ├── dashboard.js          # Dashboard前端逻辑
│       └── ...
├── templates/                    # HTML模板
│   └── dashboard.html            # Dashboard页面
├── tools/                        # 工具集
├── .gitignore                    # Git忽略配置
├── config.yaml                   # 主配置文件
├── requirements.txt              # Python依赖
├── run.py                        # 应用启动入口
├── paper_trading_api.py          # 模拟交易API入口
├── README.md                     # 项目说明
├── PROJECT_STRUCTURE.md          # 本文档
├── GIT_COMMIT_GUIDE.md           # Git提交指南
├── STATUS_AND_NEXT_STEPS.md      # 当前状态和下一步
├── QUICK_START_OPTIMIZATION.md   # 性能优化快速开始
├── OPTIMIZATION_COMPLETE.md      # 性能优化说明
├── NEXT_STEPS.md                 # 下一步计划
└── 性能优化完成说明.md            # 性能优化完整说明（中文）
```

## 🔧 核心组件说明

### 1. 数据采集层 (app/collectors/)

负责从各个交易所和数据源采集数据：

- **price_collector.py**: Binance现货市场数据（价格、K线、订单簿）
- **gate_collector.py**: Gate.io交易所数据
- **binance_futures_collector.py**: Binance合约市场数据（资金费率、持仓量、多空比）
- **hyperliquid_collector.py**: Hyperliquid DEX数据
- **news_collector.py**: 加密货币新闻和情绪分析
- **smart_money_collector.py**: 大户资金流向追踪

**关键功能**：
- 实时价格数据采集
- K线数据采集（1m, 5m, 1h, 1d）
- 成交量数据（volume, quote_volume）
- 合约数据（资金费率、持仓量）

### 2. 数据存储层 (app/database/)

使用MySQL存储所有数据：

**主要数据表**：
- `price_data`: 实时价格数据
- `kline_data`: K线历史数据
- `news_data`: 新闻和情绪数据
- `binance_futures_*`: 合约相关数据表
- `hyperliquid_*`: Hyperliquid数据表

**缓存表**（性能优化）：
- `price_stats_24h`: 24小时价格统计缓存
- `technical_indicators_cache`: 技术指标缓存
- `investment_recommendations_cache`: 投资建议缓存
- `news_sentiment_aggregation`: 新闻情绪聚合
- `funding_rate_stats`: 资金费率统计
- `hyperliquid_symbol_aggregation`: Hyperliquid聚合数据

### 3. 业务服务层 (app/services/)

**cache_update_service.py** - 缓存更新服务：
- 定期更新所有缓存表
- 预计算统计数据
- 智能评分算法
- 性能优化（API响应 <500ms）

### 4. API接口层 (app/api/)

**enhanced_dashboard_cached.py** - 缓存版Dashboard API（推荐）：
- 从缓存表读取数据
- 响应时间 <500ms
- 支持高并发

**enhanced_dashboard.py** - 标准Dashboard API：
- 实时查询数据库
- 响应时间 5-15秒
- 适合调试

### 5. 前端展示层 (static/, templates/)

**dashboard.html** - 主仪表板页面：
- 实时价格展示
- 技术指标图表
- 智能投资建议
- 新闻情绪分析
- 合约数据监控

**dashboard.js** - 前端逻辑：
- 数据刷新（每30秒）
- 图表渲染（ECharts）
- 交互功能

## 🚀 主要功能

### 1. 实时价格监控
- 多交易所价格对比
- 24小时价格变化
- 成交量统计
- 买卖压力分析

### 2. 技术分析
- RSI、MACD、布林带等技术指标
- K线图表分析
- 趋势识别

### 3. 智能投资建议
- 基于多维度评分（技术面、资金面、市场情绪）
- 买入/卖出/观望建议
- 风险评估

### 4. 合约市场分析
- 资金费率监控
- 持仓量变化
- 多空比分析
- 清算数据

### 5. 新闻情绪分析
- 实时新闻采集
- AI情绪分析
- 市场热度监控

### 6. 模拟交易
- 纸上交易功能
- 策略回测
- 性能统计

## ⚙️ 配置文件

### config.yaml
主配置文件，包含：
- 数据库连接配置
- 交易所API密钥
- 监控币种列表
- 采集频率设置
- 日志配置

## 🔄 定时任务 (Scheduler)

**app/scheduler.py** 负责定时执行：

- **每1分钟**：采集实时价格、1分钟K线、合约数据
- **每5分钟**：采集5分钟K线、更新缓存
- **每1小时**：采集1小时K线、新闻数据
- **每24小时**：采集日线数据、数据清理

## 📊 性能优化

通过缓存表机制实现：

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| API响应时间 | 5-15秒 | <500ms | **30倍** ⚡ |
| 数据库查询 | ~200次 | ~5次 | **40倍** |
| CPU占用 | 30-50% | <5% | **10倍** |
| 并发能力 | 2请求/秒 | 50+请求/秒 | **25倍** |

详见：[QUICK_START_OPTIMIZATION.md](QUICK_START_OPTIMIZATION.md)

## 🔧 开发工具

### 调试脚本（scripts/管理/）
- `update_cache_manual.py`: 手动更新缓存

### 数据库迁移（scripts/migrations/）
- `001_create_cache_tables.sql`: 创建所有缓存表

## 📝 文档索引

- **README.md**: 项目总览
- **QUICK_START_OPTIMIZATION.md**: 5分钟快速部署性能优化
- **OPTIMIZATION_COMPLETE.md**: 性能优化技术详解
- **GIT_COMMIT_GUIDE.md**: Git提交规范和指南
- **STATUS_AND_NEXT_STEPS.md**: 当前问题状态和解决步骤
- **NEXT_STEPS.md**: 项目后续开发计划

## 🛠️ 技术栈

- **后端**: Python 3.9+, FastAPI, SQLAlchemy
- **数据库**: MySQL 8.0+
- **前端**: HTML5, JavaScript, Bootstrap, ECharts
- **数据采集**: ccxt, requests, pandas
- **任务调度**: APScheduler
- **日志**: loguru

## 🚦 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置数据库
编辑 `config.yaml`，配置MySQL连接信息

### 3. 创建数据库表
```bash
python scripts/init/init_database.py
```

### 4. 执行缓存表迁移
```sql
-- 在MySQL中执行
source scripts/migrations/001_create_cache_tables.sql
```

### 5. 启动服务
```bash
# 终端1: 启动API服务
python run.py

# 终端2: 启动调度器
python app/scheduler.py
```

### 6. 访问Dashboard
打开浏览器访问：`http://localhost:8000/dashboard.html`

## 📮 联系方式

如有问题，请提交 Issue 或查看文档。

## 📄 许可证

MIT License
