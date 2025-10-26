# 📚 项目文档索引

欢迎来到 Crypto Analyzer 项目文档中心！

---

## 🚀 快速开始

### 新用户必读
1. **[快速更新指南](quick/快速更新指南.md)** - 5分钟快速部署最新修复
2. **[最新修复说明](quick/最新修复说明.md)** - 了解最近的更新内容
3. **[文档索引](quick/文档索引.md)** - 完整的文档导航

---

## 📖 使用指南

### 基础指南
- **[快速启动指南](guides/快速启动指南.md)** - 如何启动项目
- **[Windows部署指南](guides/Windows部署指南.md)** - Windows 本地部署详细步骤
- **[安装检查清单](guides/安装检查清单.md)** - 安装验证步骤

---

## 💡 功能说明

### 核心功能
- **[监控机制](features/监控机制.md)** - 了解数据监控机制
- **[Hyperliquid指南](features/Hyperliquid指南.md)** - Hyperliquid 功能完整指南
- **[Hyperliquid数据库指南](features/Hyperliquid数据库指南.md)** - 数据库结构和操作
- **[Hyperliquid指标说明](features/Hyperliquid指标说明.md)** - 各项指标详细解释
- **[技术指标说明](features/技术指标说明.md)** - RSI、MACD 等技术指标
- **[Web界面说明](features/Web界面说明.md)** - 仪表盘使用指南

---

## 🔧 技术文档

### 架构和实现
- **[项目结构](technical/项目结构.md)** - 代码组织结构
- **[调度器说明](technical/调度器说明.md)** - 调度任务机制

---

## 🆕 更新和修复

### 最新更新
- **[文件更新清单](updates/文件更新清单.md)** - v2.0 完整更新清单
- **[今日修复总结](updates/今日修复总结.md)** - 2025-10-24 所有修复
- **[K线修复说明](updates/K线修复说明.md)** - K线多交易所支持修复
- **[Hyperliquid更新](updates/Hyperliquid更新.md)** - Hyperliquid 功能优化
- **[HYPE价格修复](updates/HYPE价格修复.md)** - HYPE 价格问题解决

---

## 🎯 快速查找

### 我想知道...

**"如何快速部署最新修复？"**
→ [快速更新指南](quick/快速更新指南.md)

**"需要更新哪些文件？"**
→ [文件更新清单](updates/文件更新清单.md)

**"Hyperliquid 指标是什么意思？"**
→ [Hyperliquid指标说明](features/Hyperliquid指标说明.md)

**"如何在 Windows 上部署？"**
→ [Windows部署指南](guides/Windows部署指南.md)

**"HYPE 价格为什么不更新？"**
→ [HYPE价格修复](updates/HYPE价格修复.md)

**"项目代码结构是怎样的？"**
→ [项目结构](technical/项目结构.md)

---

## 📂 文档组织

```
docs/
├── README.md                    # 本文档（文档索引）
│
├── quick/                       # 快速参考
│   ├── 快速更新指南.md          # 5分钟部署指南
│   ├── 最新修复说明.md          # 最新更新说明
│   └── 文档索引.md              # 完整导航
│
├── guides/                      # 使用指南
│   ├── 快速启动指南.md
│   ├── Windows部署指南.md
│   └── 安装检查清单.md
│
├── features/                    # 功能说明
│   ├── 监控机制.md
│   ├── Hyperliquid指南.md
│   ├── Hyperliquid数据库指南.md
│   ├── Hyperliquid指标说明.md
│   ├── 技术指标说明.md
│   └── Web界面说明.md
│
├── technical/                   # 技术文档
│   ├── 项目结构.md
│   └── 调度器说明.md
│
└── updates/                     # 更新记录
    ├── 文件更新清单.md
    ├── 今日修复总结.md
    ├── K线修复说明.md
    ├── Hyperliquid更新.md
    └── HYPE价格修复.md
```

---

## 🛠️ 工具和脚本

### 诊断工具（tools/ 目录）
- `diagnose_hyperliquid_dashboard.py` - 诊断 Hyperliquid 仪表盘问题
- `verify_kline_fix.py` - 验证 K线修复
- `check_hype_in_db.py` - 检查数据库数据
- `test_hype_price.py` - 测试 HYPE 价格采集

### 工具脚本（scripts/ 目录）
- `scripts/init/` - 初始化脚本
- `scripts/hyperliquid/` - Hyperliquid 工具
- `scripts/etf/` - ETF 数据工具
- `scripts/collectors/` - 数据采集工具
- `scripts/utils/` - 通用工具

---

## 📞 需要帮助？

1. **查看相关文档** - 使用上面的"快速查找"
2. **运行诊断工具** - 位于 `tools/` 目录
3. **查看日志** - `logs/` 目录

---

**最后更新：** 2025-10-24
**文档版本：** v1.0
