# 🚀 加密货币智能分析系统

一个基于Python的多维度加密货币投资分析系统,整合技术指标、新闻情绪、资金费率和Hyperliquid聪明钱监控,提供全方位的投资决策支持。

## ⚠️ 免责声明

**本系统仅供学习和研究使用,不构成任何投资建议。加密货币交易具有极高风险,可能导致重大财务损失。使用本系统产生的任何交易决策和后果由用户自行承担。**

---

## 📋 目录

- [核心功能](#-核心功能)
- [系统架构](#-系统架构)
- [技术栈](#-技术栈)
- [快速开始](#-快速开始)
- [功能详解](#-功能详解)
- [API文档](#-api文档)
- [部署指南](#-部署指南)
- [配置说明](#-配置说明)
- [常见问题](#-常见问题)

---

## ✨ 核心功能

### 1. 5维度综合投资分析 🎯

系统整合5个关键维度进行综合评分(0-100分):

| 维度 | 权重 | 分析内容 |
|------|------|----------|
| 📊 技术指标 | **40%** | RSI、MACD、布林带、EMA、KDJ、成交量 |
| 📰 新闻情绪 | **15%** | 24小时新闻情绪分析、重大事件识别 |
| 💰 资金费率 | **15%** | 期货市场多空情绪、资金流向 |
| 🧠 Hyperliquid | **20%** | 聪明钱交易活动、净流入流出 |
| ⛓️ 以太坊链上 | **10%** | 链上聪明钱活动(预留) |

**输出结果:**
- ✅ 交易信号 (强烈买入/买入/持有/卖出/强烈卖出)
- ✅ 置信度 (0-100%)
- ✅ 各维度详细评分
- ✅ 价格目标 (入场价、止损价、止盈价)
- ✅ 详细分析依据列表
- ✅ 风险等级评估

### 2. Hyperliquid聪明钱监控 🧠

- 实时监控排行榜上的高盈利交易员
- 追踪聪明钱的持仓变化和交易行为
- 分析资金流向(净流入/流出)
- 识别热门交易币种
- 记录大额交易历史

### 3. 多数据源采集 📡

- **价格数据**: 支持币安、Gate.io等主流交易所
- **新闻数据**: 聚合CoinDesk、CoinTelegraph等多个来源
- **资金费率**: 实时监控期货市场情绪
- **Hyperliquid**: DEX交易数据和聪明钱活动

### 4. 现代化Web界面 💎

- 🎨 渐变紫色设计 + 毛玻璃效果
- 📊 5维度评分可视化进度条
- 📈 实时价格监控和K线图表
- 📰 新闻情绪分析展示
- 🧠 Hyperliquid聪明钱专区
- ⏰ 30秒自动刷新
- 📱 完全响应式设计

### 5. 智能信号生成 🎯

基于多维度综合评分自动生成交易信号:

| 综合评分 | 信号 | 含义 |
|----------|------|------|
| ≥75分 | 🚀 强烈买入 | 多个维度强烈看涨 |
| 60-75分 | 📈 买入 | 多数维度看涨 |
| 40-60分 | ➖ 持有 | 信号中性或分歧 |
| 25-40分 | 📉 卖出 | 多数维度看跌 |
| <25分 | 🔻 强烈卖出 | 多个维度强烈看跌 |

### 6. 自动化数据收集 ⏱️

通过调度器自动执行:
- ⏰ 每5分钟: 采集价格和K线数据
- ⏰ 每30分钟: 采集新闻、监控Hyperliquid钱包
- ⏰ 每1小时: 采集资金费率数据

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────┐
│                   数据收集层                          │
│              (Scheduler - 自动定期运行)              │
├─────────────────────────────────────────────────────┤
│  PriceCollector     → MySQL (klines表)              │
│  NewsCollector      → MySQL (crypto_news表)         │
│  FundingCollector   → MySQL (funding_rates表)       │
│  HyperliquidMonitor → MySQL (hyperliquid_*表)      │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│                   分析层                             │
│           (EnhancedInvestmentAnalyzer)              │
├─────────────────────────────────────────────────────┤
│  • 技术指标分析 (40%权重)                            │
│  • 新闻情绪分析 (15%权重)                            │
│  • 资金费率分析 (15%权重)                            │
│  • Hyperliquid分析 (20%权重)                        │
│  • 以太坊链上分析 (10%权重)                          │
│  → 综合评分 → 信号 + 置信度                         │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│                   API层                              │
│              (EnhancedDashboard)                    │
├─────────────────────────────────────────────────────┤
│  GET /api/dashboard                                 │
│  → 并行获取所有维度数据                              │
│  → 返回完整JSON                                      │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│                   前端层                             │
│           (dashboard.html + dashboard.js)           │
├─────────────────────────────────────────────────────┤
│  • 每30秒自动刷新                                    │
│  • 显示5维度评分进度条                               │
│  • Hyperliquid专区                                  │
│  • 价格目标和风险提示                                │
└─────────────────────────────────────────────────────┘
```

### 目录结构

```
crypto-analyzer/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI主应用
│   ├── scheduler.py                 # 数据收集调度器
│   ├── collectors/                  # 数据采集模块
│   │   ├── price_collector.py      # 价格数据采集
│   │   ├── news_collector.py       # 新闻数据采集
│   │   ├── enhanced_news_collector.py  # 增强新闻采集
│   │   ├── hyperliquid_collector.py     # Hyperliquid数据
│   │   ├── gate_collector.py       # Gate.io交易所
│   │   └── mock_price_collector.py # 模拟数据(测试用)
│   ├── analyzers/                   # 技术分析模块
│   │   ├── technical_indicators.py # 技术指标计算
│   │   ├── sentiment_analyzer.py   # 情绪分析
│   │   ├── signal_generator.py     # 信号生成
│   │   └── enhanced_investment_analyzer.py  # 5维度分析
│   ├── api/                         # API路由
│   │   └── enhanced_dashboard.py   # 仪表盘API
│   ├── database/                    # 数据库模块
│   │   ├── db_service.py           # 数据库服务
│   │   ├── hyperliquid_db.py       # Hyperliquid数据库
│   │   └── hyperliquid_schema.sql  # 数据库结构
│   ├── services/                    # 业务服务
│   │   └── analysis_service.py     # 分析服务
│   └── utils/                       # 工具函数
│       ├── config_loader.py        # 配置加载
│       └── logger.py               # 日志工具
├── templates/                       # HTML模板
│   └── dashboard.html              # 仪表盘页面
├── static/                          # 静态文件
│   ├── css/                        # 样式文件
│   └── js/
│       └── dashboard.js            # 仪表盘JS
├── config/                          # 配置文件
│   └── config.yaml                 # 主配置文件
├── hyperliquid_monitor.py          # Hyperliquid监控CLI
├── requirements.txt                # Python依赖
└── README.md                       # 本文档
```

---

## 🛠️ 技术栈

### 后端
- **Python 3.11+**
- **FastAPI** - 高性能Web框架
- **SQLAlchemy** - ORM数据库操作
- **MySQL** - 数据持久化
- **APScheduler** - 定时任务调度
- **aiohttp** - 异步HTTP客户端
- **pandas** - 数据处理
- **numpy** - 数值计算

### 数据采集
- **python-binance** - 币安API客户端
- **httpx/requests** - HTTP请求
- **feedparser** - RSS新闻解析
- **aiohttp** - 异步数据采集

### 技术分析
- 内置技术指标计算(RSI、MACD、布林带、EMA、KDJ等)
- 可选: pandas-ta (需要Python 3.12+)

### 前端
- **HTML5 + CSS3**
- **JavaScript (ES6+)**
- **Bootstrap 5** - UI框架
- **Chart.js** - 图表库(可选)

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.11+ (推荐3.12)
- MySQL 5.7+ 或 8.0+
- pip
- 4GB+ RAM (推荐)

### 2. 安装依赖

```bash
# 克隆项目
cd crypto-analyzer

# 创建虚拟环境(推荐)
python3 -m venv venv

# 激活虚拟环境
# Linux/Mac:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置数据库

```bash
# 创建MySQL数据库
mysql -u root -p -e "CREATE DATABASE crypto_analyzer CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 导入数据库结构
mysql -u root -p crypto_analyzer < app/database/hyperliquid_schema.sql
```

### 4. 配置系统

编辑 `config/config.yaml`:

```yaml
# 监控的币种
symbols:
  - BTC/USDT
  - ETH/USDT
  - BNB/USDT

# 数据库配置
database:
  type: mysql
  host: localhost
  port: 3306
  username: your_username
  password: your_password
  database: crypto_analyzer

# 代理配置(如需要)
proxy:
  enabled: false
  http: "http://127.0.0.1:7890"
  https: "http://127.0.0.1:7890"
```

### 5. 添加Hyperliquid监控钱包

```bash
# 扫描排行榜,自动添加前10名聪明钱钱包
python hyperliquid_monitor.py scan --period week --min-pnl 50000 --add 10

# 查看监控列表
python hyperliquid_monitor.py list
```

### 6. 启动系统

**方式1: 分别启动(推荐用于开发)**

```bash
# 窗口1: 启动数据收集调度器
python app/scheduler.py

# 窗口2: 启动Web服务器
python app/main.py
```

**方式2: 使用uvicorn启动Web服务**

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 7. 访问系统

- **仪表盘**: http://localhost:8000/dashboard
- **API文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/health

---

## 📊 功能详解

### 1. 5维度投资分析

#### 技术指标分析 (40%权重)

计算并分析以下技术指标:

- **RSI (相对强弱指标)**: 判断超买超卖
  - RSI < 30: 超卖,可能反弹
  - RSI > 70: 超买,可能回调

- **MACD (指数平滑移动平均线)**: 趋势和动量
  - 金叉(MACD上穿信号线): 看涨信号
  - 死叉(MACD下穿信号线): 看跌信号

- **布林带**: 波动率和压力支撑
  - 价格触及下轨: 可能反弹
  - 价格触及上轨: 可能回调

- **EMA (指数移动平均线)**: 趋势方向
  - 短期EMA上穿长期EMA: 看涨
  - 短期EMA下穿长期EMA: 看跌

- **成交量**: 确认信号强度
  - 放量上涨: 看涨确认
  - 放量下跌: 看跌确认

#### 新闻情绪分析 (15%权重)

- 分析24小时内的加密货币新闻
- 情绪分类: 利好(positive)、利空(negative)、中性(neutral)
- 识别重大事件和市场热点
- 关键词提取和主题分析

#### 资金费率分析 (15%权重)

监控期货市场资金费率:

- **正费率(多头支付空头)**: 市场多头过热
  - 费率 > 0.05%: 谨慎追高

- **负费率(空头支付多头)**: 市场空头过度
  - 费率 < -0.05%: 可能反弹机会

#### Hyperliquid聪明钱 (20%权重)

这是系统的核心优势之一!

**监控内容:**
- 排行榜上的高盈利交易员
- 聪明钱的持仓变化
- 交易行为(开仓/平仓)
- 资金流向(净流入/流出)

**评分规则:**
- 净流入 >$1M: 强烈看涨(85-100分)
- 净流入 $500K-$1M: 看涨(70-85分)
- 净流入/流出 <$500K: 中性(40-60分)
- 净流出 $500K-$1M: 看跌(25-40分)
- 净流出 >$1M: 强烈看跌(0-25分)

#### 以太坊链上分析 (10%权重)

预留功能,计划整合:
- 链上大额转账监控
- 巨鲸钱包行为分析
- Gas费用趋势

### 2. Hyperliquid监控工具

#### CLI命令

```bash
# 扫描排行榜发现聪明钱
python hyperliquid_monitor.py scan --period week --min-pnl 50000 --add 10

# 手动添加钱包
python hyperliquid_monitor.py add 0x1234... --label "我的聪明钱"

# 查看监控列表
python hyperliquid_monitor.py list

# 实时监控钱包
python hyperliquid_monitor.py watch 0x1234... --hours 24 --save

# 查看交易历史
python hyperliquid_monitor.py history --coin BTC --limit 20

# 移除钱包
python hyperliquid_monitor.py remove 0x1234...
```

#### 数据库结构

系统自动创建以下表:

- `hyperliquid_traders`: 交易员基本信息
- `hyperliquid_leaderboard`: 排行榜历史快照
- `hyperliquid_monitored_wallets`: 监控钱包列表
- `hyperliquid_wallet_trades`: 钱包交易记录
- `hyperliquid_wallet_positions`: 钱包持仓快照
- `hyperliquid_wallet_fund_changes`: 资金变化记录

### 3. 自动化数据收集

调度器(`app/scheduler.py`)自动运行以下任务:

| 任务 | 频率 | 说明 |
|------|------|------|
| 价格采集 | 每5分钟 | 采集实时价格和K线数据 |
| 新闻采集 | 每30分钟 | 聚合多个新闻源 |
| 资金费率 | 每1小时 | 监控期货市场情绪 |
| Hyperliquid监控 | 每30分钟 | 更新聪明钱活动 |

---

## 🔌 API文档

### 核心端点

#### 1. 获取仪表盘数据

```http
GET /api/dashboard
```

**响应示例:**

```json
{
  "success": true,
  "data": {
    "prices": [
      {
        "symbol": "BTC",
        "price": 95000.00,
        "change_24h": 2.5,
        "volume_24h": 1234567890.00
      }
    ],
    "recommendations": [
      {
        "symbol": "BTC",
        "signal": "STRONG_BUY",
        "confidence": 85.2,
        "current_price": 95000.00,
        "entry_price": 95000.00,
        "stop_loss": 92150.00,
        "take_profit": 104500.00,
        "scores": {
          "total": 85.2,
          "technical": 75.0,
          "news": 72.5,
          "funding": 65.0,
          "hyperliquid": 88.0,
          "ethereum": 50.0
        },
        "reasons": [
          "📊 技术指标看涨 (评分: 75/100)",
          "💰 资金费率看涨 (-0.080% - 空头过度)",
          "🧠 Hyperliquid聪明钱看涨 (净流入: $1,500,000)"
        ]
      }
    ],
    "hyperliquid": {
      "monitored_wallets": 15,
      "total_volume_24h": 5234567.89,
      "recent_trades": [...],
      "top_coins": [...]
    },
    "stats": {
      "total_symbols": 10,
      "bullish_signals": 6,
      "bearish_signals": 2
    }
  }
}
```

#### 2. 获取实时价格

```http
GET /api/price/{symbol}
```

#### 3. 获取技术分析

```http
GET /api/analysis/{symbol}?timeframe=1h
```

#### 4. 健康检查

```http
GET /health
```

完整API文档访问: http://localhost:8000/docs

---

## 🚢 部署指南

### 方式1: 直接部署(Linux)

```bash
# 1. 更新系统
sudo apt update && sudo apt upgrade -y

# 2. 安装依赖
sudo apt install python3.11 python3-pip python3-venv mysql-server -y

# 3. 配置MySQL
sudo mysql_secure_installation
mysql -u root -p -e "CREATE DATABASE crypto_analyzer CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 4. 克隆项目
git clone <your-repo>
cd crypto-analyzer

# 5. 安装Python依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 6. 配置
cp config/config.example.yaml config/config.yaml
vim config/config.yaml

# 7. 初始化数据库
mysql -u root -p crypto_analyzer < app/database/hyperliquid_schema.sql

# 8. 添加监控钱包
python hyperliquid_monitor.py scan --add 10

# 9. 使用systemd守护进程
sudo vim /etc/systemd/system/crypto-scheduler.service
```

**调度器服务配置:**

```ini
[Unit]
Description=Crypto Analyzer Scheduler
After=network.target mysql.service

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/crypto-analyzer
Environment="PATH=/path/to/crypto-analyzer/venv/bin"
ExecStart=/path/to/crypto-analyzer/venv/bin/python app/scheduler.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Web服务配置:**

```ini
[Unit]
Description=Crypto Analyzer Web Server
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/crypto-analyzer
Environment="PATH=/path/to/crypto-analyzer/venv/bin"
ExecStart=/path/to/crypto-analyzer/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**启动服务:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable crypto-scheduler crypto-web
sudo systemctl start crypto-scheduler crypto-web
sudo systemctl status crypto-scheduler crypto-web
```

### 方式2: Docker部署

```bash
# 构建镜像
docker build -t crypto-analyzer .

# 运行容器
docker run -d \
  --name crypto-analyzer \
  -p 8000:8000 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/data:/app/data \
  -e MYSQL_HOST=your-mysql-host \
  -e MYSQL_USER=your-user \
  -e MYSQL_PASSWORD=your-password \
  crypto-analyzer

# 使用docker-compose
docker-compose up -d
```

### 方式3: Windows部署

```bash
# 1. 安装Python 3.11+和MySQL

# 2. 安装依赖
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 3. 配置config/config.yaml

# 4. 创建启动脚本 start.bat
@echo off
start cmd /k "venv\Scripts\activate && python app\scheduler.py"
start cmd /k "venv\Scripts\activate && python app\main.py"

# 5. 双击start.bat启动
```

### Nginx反向代理(可选)

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket支持(如需要)
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## ⚙️ 配置说明

### config.yaml 完整配置

```yaml
# ==================== 监控币种 ====================
symbols:
  - BTC/USDT
  - ETH/USDT
  - BNB/USDT
  - SOL/USDT
  - XRP/USDT

# ==================== 数据库配置 ====================
database:
  type: mysql           # 数据库类型 (mysql/sqlite)
  host: localhost       # MySQL主机
  port: 3306           # MySQL端口
  username: root       # 用户名
  password: password   # 密码
  database: crypto_analyzer  # 数据库名

# ==================== 交易所配置 ====================
exchanges:
  binance:
    enabled: true
    api_key: ""        # API密钥(可选,公开数据无需)
    api_secret: ""

  gate:
    enabled: false
    api_key: ""
    api_secret: ""

# ==================== 代理配置 ====================
proxy:
  enabled: false       # 是否启用代理
  http: "http://127.0.0.1:7890"
  https: "http://127.0.0.1:7890"

# ==================== 数据采集配置 ====================
collector:
  interval: 300        # 采集间隔(秒) - 5分钟
  timeframes:
    - 1m
    - 5m
    - 15m
    - 1h
    - 4h
    - 1d

# ==================== 新闻配置 ====================
news:
  sources:
    - coindesk
    - cointelegraph
    - decrypt
  interval: 1800       # 采集间隔(秒) - 30分钟
  max_age: 86400       # 保留时长(秒) - 24小时

# ==================== Hyperliquid配置 ====================
hyperliquid:
  enabled: true
  monitor_interval: 1800  # 监控间隔(秒) - 30分钟
  leaderboard_periods:
    - day
    - week
  min_pnl: 10000       # 最低盈利(USD)

# ==================== 分析权重配置 ====================
analysis:
  weights:
    technical: 0.40    # 技术指标权重 40%
    news: 0.15         # 新闻情绪权重 15%
    funding: 0.15      # 资金费率权重 15%
    hyperliquid: 0.20  # Hyperliquid权重 20%
    ethereum: 0.10     # 以太坊链上权重 10%

# ==================== 技术指标参数 ====================
indicators:
  rsi:
    period: 14
    overbought: 70
    oversold: 30

  macd:
    fast: 12
    slow: 26
    signal: 9

  bollinger:
    period: 20
    std: 2

  ema:
    periods: [9, 21, 50, 200]

# ==================== API服务配置 ====================
api:
  host: 0.0.0.0
  port: 8000
  cors_origins:
    - "*"              # 生产环境应限制具体域名

# ==================== 通知配置(可选) ====================
notifications:
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""

  email:
    enabled: false
    smtp_server: ""
    smtp_port: 587
    sender: ""
    password: ""
    recipients: []

# ==================== 日志配置 ====================
logging:
  level: INFO          # DEBUG/INFO/WARNING/ERROR
  file: logs/app.log
  max_size: 10485760   # 10MB
  backup_count: 5

# ==================== 风险管理配置 ====================
risk:
  stop_loss_pct: 3     # 止损百分比 3%
  take_profit_pct: 10  # 止盈百分比 10%
  max_position_size: 0.1  # 最大仓位 10%
```

---

## ❓ 常见问题

### Q1: 首次启动后页面显示"加载中..."

**原因:** 数据库还没有足够的数据

**解决:**
1. 确保调度器正在运行: `ps aux | grep scheduler`
2. 等待5-10分钟让系统收集数据
3. 刷新页面查看

### Q2: 5维度评分都是50分(中性)

**原因:** 各维度缺少数据

**解决:**
```bash
# 1. 检查数据库连接
mysql -u root -p -e "USE crypto_analyzer; SELECT COUNT(*) FROM klines;"

# 2. 手动触发数据收集(测试)
python -c "from app.scheduler import Scheduler; import asyncio; s = Scheduler(); asyncio.run(s.collect_prices())"

# 3. 确保调度器持续运行
```

### Q3: Hyperliquid显示0钱包和0交易量

**原因:** 没有添加监控钱包

**解决:**
```bash
# 扫描并添加聪明钱钱包
python hyperliquid_monitor.py scan --period week --add 10

# 确认添加成功
python hyperliquid_monitor.py list

# 手动触发更新
python -c "from app.scheduler import Scheduler; import asyncio; s = Scheduler(); asyncio.run(s.monitor_hyperliquid_wallets())"
```

### Q4: 如何添加新的监控币种?

编辑 `config/config.yaml`:

```yaml
symbols:
  - BTC/USDT
  - ETH/USDT
  - YOUR_COIN/USDT   # 添加这里
```

重启服务后生效。

### Q5: 如何调整分析权重?

编辑 `config/config.yaml`:

```yaml
analysis:
  weights:
    technical: 0.50      # 提高技术指标权重到50%
    news: 0.10           # 降低新闻权重到10%
    funding: 0.10        # 降低资金费率权重到10%
    hyperliquid: 0.20    # 保持Hyperliquid 20%
    ethereum: 0.10       # 保持10%
```

### Q6: 系统资源占用如何?

**正常运行:**
- CPU: 5-15% (采集时可能短暂升高到30%)
- 内存: 300-800MB
- 磁盘: 约50-100MB/天 (视监控币种数量)
- 网络: 很少 (<100MB/天)

**推荐配置:**
- 1核2G: 监控≤10个币种
- 2核4G: 监控10-50个币种
- 4核8G: 监控>50个币种 + 历史回测

### Q7: 信号准确率如何?

**重要说明:**

加密货币市场极其复杂,**没有100%准确的预测系统**。

本系统的优势在于:
- ✅ 多维度综合分析,降低单一指标误判
- ✅ Hyperliquid聪明钱监控,跟随高手
- ✅ 完整的价格目标和风险提示
- ✅ 置信度评分,帮助判断信号强度

**建议:**
1. 先用小额资金测试
2. 结合自己的判断,不要盲目跟单
3. 严格执行止损策略
4. 分散投资,控制风险

### Q8: 如何查看系统日志?

```bash
# 查看调度器日志
tail -f logs/scheduler.log

# 查看Web服务器日志
tail -f logs/app.log

# 查看systemd服务日志
sudo journalctl -u crypto-scheduler -f
sudo journalctl -u crypto-web -f
```

### Q9: 数据库连接失败

```bash
# 检查MySQL是否运行
sudo systemctl status mysql

# 测试连接
mysql -h localhost -u your_user -p crypto_analyzer

# 检查配置文件
cat config/config.yaml | grep -A 5 database
```

### Q10: 如何备份数据?

```bash
# 备份数据库
mysqldump -u root -p crypto_analyzer > backup_$(date +%Y%m%d).sql

# 恢复数据库
mysql -u root -p crypto_analyzer < backup_20241020.sql

# 自动备份(添加到crontab)
0 2 * * * mysqldump -u root -p'password' crypto_analyzer > /backups/crypto_$(date +\%Y\%m\%d).sql
```

---

## 🔐 安全建议

1. **不要在代码中硬编码API密钥和密码**
2. **使用环境变量或配置文件管理敏感信息**
3. **配置文件权限设置为600**: `chmod 600 config/config.yaml`
4. **限制服务器SSH登录** (密钥认证、禁止root登录)
5. **配置防火墙**,仅开放必要端口(80/443/8000)
6. **定期更新系统和依赖包**
7. **不要使用具有交易权限的API Key**
8. **使用HTTPS** (配置SSL证书)
9. **定期备份数据库**
10. **设置强密码** (数据库、系统账户)

---

## 📈 使用技巧

### 技巧1: 重点关注Hyperliquid数据

Hyperliquid聪明钱占20%权重,是最重要的维度之一!

**查看方式:**
1. 打开仪表盘的"Hyperliquid聪明钱活动"区域
2. 关注"热门币种(24h净流入)"

**解读规则:**
- 净流入 >$1M: 🚀🚀🚀 强烈看涨信号
- 净流入 $500K-$1M: 📈📈 看涨信号
- 净流出 $500K-$1M: 📉📉 看跌信号
- 净流出 >$1M: 🔻🔻🔻 强烈看跌信号

### 技巧2: 多个维度一致时信号最强

当5个维度中有4个以上都指向同一方向时,信号可信度最高。

**示例:**
```
📊 技术指标  85/100  ← 看涨
📰 新闻情绪  78/100  ← 看涨
💰 资金费率  72/100  ← 看涨
🧠 Hyperliquid 90/100  ← 看涨
⛓️ 以太坊链上 65/100  ← 看涨
━━━━━━━━━━━━━━━━━━━━━━━━
📈 综合评分  82/100  ← 强烈看涨!
```

5个维度全部看涨 → 信号非常可靠!

### 技巧3: 结合资金费率判断

**负费率(<-0.05%)**: 空头过度
- 市场过于悲观
- 可能出现反弹
- 适合抄底

**正费率(>0.05%)**: 多头过热
- 市场过于乐观
- 可能出现回调
- 谨慎追高

### 技巧4: 使用价格目标

系统会自动计算:
- **入场价**: 建议买入价格
- **止损价**: 风险控制线(-3%)
- **止盈价**: 获利目标(+10%)

**严格执行止损止盈,不要贪心!**

### 技巧5: 查看详细分析依据

点击投资建议卡片的"分析依据",查看信号来源:

```
📋 分析依据 (5条)
• 📊 技术指标看涨 (评分: 75/100)
• RSI从超卖区域回升 (RSI=35)
• MACD金叉形成
• 💰 资金费率看涨 (-0.080% - 空头过度)
• 🧠 Hyperliquid聪明钱看涨
  (净流入: $1,500,000, 8个活跃钱包)
```

了解信号的具体原因,做出更明智的决策。

---

## 🗺️ 开发路线图

### 已完成 ✅
- [x] 多交易所数据采集
- [x] 技术指标分析
- [x] 新闻情绪分析
- [x] 资金费率监控
- [x] Hyperliquid聪明钱监控
- [x] 5维度综合投资分析
- [x] Web仪表盘
- [x] RESTful API
- [x] 自动化数据收集
- [x] MySQL数据持久化

### 进行中 🚧
- [ ] 以太坊链上数据分析
- [ ] 更多技术指标(KDJ完善、斐波那契等)
- [ ] WebSocket实时推送

### 计划中 📋
- [ ] 回测系统
- [ ] 策略评估和优化
- [ ] Telegram通知推送
- [ ] 多策略组合
- [ ] 风险管理优化
- [ ] 移动端App
- [ ] 机器学习预测模型

---

## 🤝 贡献

欢迎提交Issue和Pull Request!

### 开发环境设置

```bash
# 克隆项目
git clone <your-repo>
cd crypto-analyzer

# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试
pytest

# 代码格式化
black app/
isort app/

# 代码检查
flake8 app/
pylint app/
```

---

## 📄 许可证

MIT License

---

## 📞 联系方式

如有问题,请提交Issue或查看文档。

---

## ⚠️ 风险提示

**再次郑重提醒:**

1. **加密货币投资风险极高** - 可能导致重大财务损失
2. **本系统仅供学习研究** - 不构成任何投资建议
3. **历史数据不能预测未来** - 市场瞬息万变
4. **请做好风险管理** - 不要投入超过承受范围的资金
5. **建议小额测试** - 验证系统和策略
6. **结合基本面分析** - 技术分析有局限性
7. **保持独立思考** - 不要盲目跟随信号

**投资有风险,入市需谨慎!**

---

## 📊 系统截图

### 仪表盘主界面
- 4个统计卡片: 监控币种、看涨信号、看跌信号、聪明钱钱包
- 投资建议卡片: 带5维度评分进度条
- 实时价格表: 24h涨跌、成交量
- 新闻列表: 带情绪标签

### Hyperliquid专区
- 监控钱包统计
- 24小时交易量
- 热门交易币种
- 最近大额交易

### 5维度评分可视化
- 技术指标进度条
- 新闻情绪进度条
- 资金费率进度条
- Hyperliquid进度条
- 以太坊链上进度条
- 综合评分

---

**祝您使用愉快,投资顺利!** 🚀📈💰

---

**最后更新:** 2024-10-20
**版本:** v2.0 (增强版)
**状态:** ✅ 生产就绪
