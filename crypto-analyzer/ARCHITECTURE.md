# 系统架构说明

## 核心服务

### 1. smart_trader_service.py (超级大脑 - 核心交易服务)
**职责**: 智能交易决策和执行
**运行方式**: `python smart_trader_service.py` (独立后台运行)

**功能**:
- 🔍 扫描交易机会 (每30秒)
- 📊 信号评分系统 (12个信号组合,30分阈值)
- 📈 开仓/平仓管理
- 🎯 止盈止损监控
- 🔄 对冲持仓管理
- 🔁 反向信号处理
- ⚙️ **自适应优化** (每天凌晨2点):
  - 交易对评级更新 (3级黑名单制度)
  - 波动率配置更新 (15M K线动态止盈)
  - 自适应参数调整

**黑名单机制**:
- **Level 0** (白名单): 正常仓位 (100%)
- **Level 1**: 仓位限制25% (亏损≥$100 或 hard_stop_loss≥3次)
- **Level 2**: 仓位限制12.5% (亏损≥$200 或 hard_stop_loss≥5次)
- **Level 3**: 禁止交易 (亏损≥$400 或 hard_stop_loss≥8次)

**升级条件**: 盈利≥$50 且 胜率≥60%

---

### 2. app/scheduler.py (数据采集调度器)
**职责**: 数据采集和缓存更新
**运行方式**: `python app/scheduler.py` 或通过main.py启动

**功能**:
- 📊 现货数据采集 (Binance)
  - 5m K线 (每5分钟)
  - 15m K线 (每15分钟)
  - 1h K线 (每1小时)
  - 1d K线 (每天00:05)
- 💰 资金费率采集 (每5分钟)
- 📰 新闻数据采集 (每15分钟)
- ⛓️ Ethereum链上数据 (5m/1h/1d)
- 💎 Hyperliquid排行榜 (每天02:00)
- 🚀 缓存更新:
  - 分析缓存 (技术指标+新闻+资金费率+投资建议, 每5分钟)
  - Hyperliquid聚合缓存 (每10分钟)

**不再包含**:
- ❌ 自动合约交易 (已移至smart_trader_service.py)
- ❌ 交易对评级更新 (已移至smart_trader_service.py)

---

### 3. app/hyperliquid_scheduler.py (Hyperliquid独立调度器)
**职责**: Hyperliquid聪明钱包监控
**运行方式**: `python app/hyperliquid_scheduler.py` (独立运行)

**功能**:
- 👛 钱包资金动态监控
- 📊 交易记录跟踪
- 💼 持仓快照保存

---

## 已废弃服务

### ~~app/strategy_scheduler.py~~ (已废弃)
**状态**: 可以删除
**原因**: 策略执行功能已整合到smart_trader_service.py

---

## 运行指南

### 启动顺序

1. **启动数据采集服务**:
```bash
python app/scheduler.py
```

2. **启动超级大脑交易服务**:
```bash
python smart_trader_service.py
```

3. **启动Hyperliquid监控** (可选):
```bash
python app/hyperliquid_scheduler.py
```

4. **启动Web界面** (可选):
```bash
python main.py
# 访问 http://localhost:8000
```

---

## 数据流

```
数据采集 (scheduler.py)
    ↓
数据库 (MySQL)
    ↓
超级大脑 (smart_trader_service.py)
    ↓
交易执行 (开仓/平仓)
    ↓
每日优化 (凌晨2点)
    - 评级更新
    - 参数调整
    - 波动率配置
```

---

## 配置文件

- `config.yaml`: 系统主配置
- `.env`: 环境变量 (API密钥、数据库密码等)
- 数据库表:
  - `trading_symbol_rating`: 交易对评级
  - `signal_scoring_weights`: 信号评分权重
  - `adaptive_params`: 自适应参数
  - `futures_positions`: 持仓记录

---

## 日志位置

- `logs/smart_trader_YYYY-MM-DD.log`: 超级大脑日志
- `logs/scheduler_YYYY-MM-DD.log`: 数据采集日志
- `logs/hyperliquid_scheduler_YYYY-MM-DD.log`: Hyperliquid监控日志

---

## 关键特性

### 信号系统
- 12个交易信号组合
- 30分阈值开仓
- 时间框架一致性验证
- position_high增强验证
- 波动率自适应止损

### 风险控制
- 3级黑名单制度
- 仓位动态调整
- 10分钟开仓冷却期
- K线数据新鲜度检查
- 对冲持仓管理

### 自动优化
- 每日凌晨2点自动运行
- 评级自动升降级
- 参数自适应调整
- 波动率配置更新

---

最后更新: 2026-01-24
