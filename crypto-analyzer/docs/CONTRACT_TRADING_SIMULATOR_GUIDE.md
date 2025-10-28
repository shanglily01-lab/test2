# 模拟合约交易系统使用指南
## Contract Trading Simulator Guide

---

## 📖 目录 / Table of Contents

1. [系统概述](#系统概述)
2. [核心功能](#核心功能)
3. [快速开始](#快速开始)
4. [API接口文档](#api接口文档)
5. [代码示例](#代码示例)
6. [风险参数说明](#风险参数说明)
7. [常见问题](#常见问题)

---

## 系统概述 / System Overview

模拟合约交易系统是一个完整的加密货币永续合约模拟器，支持：

### ✨ 核心特性

- **双向交易** - 支持做多（LONG）和做空（SHORT）
- **杠杆交易** - 1-125倍杠杆自由选择
- **风险控制** - 自动计算强平价格和保证金率
- **止盈止损** - 支持设置止盈和止损价格
- **实时盈亏** - 已实现/未实现盈亏实时计算
- **爆仓检测** - 自动触发强制平仓机制
- **完整记录** - 所有订单和交易历史记录
- **统计分析** - 胜率、ROI等交易统计

---

## 核心功能 / Core Features

### 1. 账户管理 / Account Management

```python
account = {
    'balance': 10000.00,          # 账户余额（USDT）
    'equity': 10200.00,           # 权益 = 余额 + 未实现盈亏
    'margin_used': 1000.00,       # 已用保证金
    'margin_available': 9200.00,  # 可用保证金
    'margin_ratio': 10.20,        # 保证金率
    'total_pnl': 200.00,          # 总盈亏
    'total_fee': 50.00            # 总手续费
}
```

### 2. 订单类型 / Order Types

| 订单类型 | 说明 | 使用场景 |
|---------|------|----------|
| **MARKET** | 市价单 | 立即成交，按当前市价 |
| **LIMIT** | 限价单 | 指定价格成交 |

### 3. 持仓方向 / Position Sides

| 方向 | 说明 | 盈利条件 |
|------|------|----------|
| **LONG** | 做多 | 价格上涨 |
| **SHORT** | 做空 | 价格下跌 |

### 4. 风险参数 / Risk Parameters

```python
# 默认参数
initial_balance = 10000          # 初始资金 10,000 USDT
maker_fee = 0.0002               # Maker手续费 0.02%
taker_fee = 0.0004               # Taker手续费 0.04%
funding_rate = 0.0001            # 资金费率 0.01%
max_leverage = 125               # 最大杠杆 125x
maintenance_margin_rate = 0.004  # 维持保证金率 0.4%
```

---

## 快速开始 / Quick Start

### 方法1：直接使用Python

```python
import asyncio
from app.trading.contract_trading_simulator import (
    ContractTradingSimulator,
    OrderSide,
    OrderType
)

async def main():
    # 1. 初始化模拟器
    simulator = ContractTradingSimulator(
        initial_balance=10000,  # 初始资金
        max_leverage=125        # 最大杠杆
    )

    # 2. 创建开多订单
    order = simulator.create_order(
        symbol="BTC/USDT",
        side=OrderSide.LONG,     # 做多
        quantity=1,              # 1张
        order_type=OrderType.MARKET,
        leverage=10,             # 10倍杠杆
        stop_loss=95000,         # 止损价
        take_profit=105000       # 止盈价
    )

    # 3. 执行订单
    await simulator.execute_order(
        order_id=order.order_id,
        current_price=100000
    )

    # 4. 查看持仓
    positions = simulator.get_positions()
    print(positions)

    # 5. 更新价格（检查盈亏）
    simulator._update_account_equity({"BTC/USDT": 102000})

    # 6. 检查风控
    liquidated = simulator.check_liquidation({"BTC/USDT": 102000})
    triggered = simulator.check_stop_loss_take_profit({"BTC/USDT": 102000})

    # 7. 平仓
    await simulator._close_position("BTC/USDT", 102000)

    # 8. 查看统计
    stats = simulator.get_statistics()
    print(stats)

asyncio.run(main())
```

### 方法2：运行测试脚本

```bash
# 在项目根目录执行
python scripts/test_contract_trading.py
```

测试脚本包含5个测试场景：
1. ✅ 基本交易功能
2. 💥 爆仓机制
3. 🎯 止盈止损
4. 📊 多个持仓管理
5. 📈 交易统计

### 方法3：使用API接口

```bash
# 1. 启动服务器
python app/main.py

# 2. 访问API文档
http://localhost:8000/docs

# 3. 初始化模拟器
curl -X POST "http://localhost:8000/api/contract-trading/init?initial_balance=10000"

# 4. 创建订单
curl -X POST "http://localhost:8000/api/contract-trading/order" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC/USDT",
    "side": "LONG",
    "quantity": 1,
    "leverage": 10,
    "stop_loss": 95000,
    "take_profit": 105000
  }'
```

---

## API接口文档 / API Documentation

### 1. 初始化模拟器

**POST** `/api/contract-trading/init`

```json
// Query参数
{
  "initial_balance": 10000
}

// 响应
{
  "success": true,
  "message": "交易模拟器初始化成功",
  "data": {
    "account_id": "SIMULATOR_001",
    "balance": 10000.00,
    "equity": 10000.00,
    "margin_available": 10000.00
  }
}
```

### 2. 创建订单

**POST** `/api/contract-trading/order`

```json
// 请求体
{
  "symbol": "BTC/USDT",
  "side": "LONG",           // LONG 或 SHORT
  "quantity": 1,
  "order_type": "MARKET",   // MARKET 或 LIMIT
  "price": null,            // 限价单价格（市价单为null）
  "leverage": 10,
  "stop_loss": 95000,       // 可选
  "take_profit": 105000     // 可选
}

// 响应
{
  "success": true,
  "message": "订单创建成功",
  "data": {
    "order_id": "ORDER_20251028_000001",
    "symbol": "BTC/USDT",
    "side": "LONG",
    "type": "MARKET",
    "quantity": 1,
    "leverage": 10,
    "status": "PENDING"
  }
}
```

### 3. 执行订单

**POST** `/api/contract-trading/order/execute`

```json
// 请求体
{
  "order_id": "ORDER_20251028_000001",
  "current_price": 100000
}

// 响应
{
  "success": true,
  "message": "订单执行成功",
  "data": {
    "order_id": "ORDER_20251028_000001",
    "execution_price": 100000,
    "account": { /* 账户信息 */ }
  }
}
```

### 4. 获取持仓

**GET** `/api/contract-trading/positions`

```json
// 响应
{
  "success": true,
  "data": {
    "positions": [
      {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "quantity": 1,
        "entry_price": 100000,
        "leverage": 10,
        "liquidation_price": 90400,
        "unrealized_pnl": 2000,
        "margin": 10000,
        "pnl_percentage": 20.00
      }
    ],
    "count": 1
  }
}
```

### 5. 平仓

**POST** `/api/contract-trading/position/close`

```json
// 请求体
{
  "symbol": "BTC/USDT",
  "current_price": 102000
}

// 响应
{
  "success": true,
  "message": "平仓成功",
  "data": { /* 账户信息 */ }
}
```

### 6. 更新价格

**POST** `/api/contract-trading/prices/update`

```json
// 请求体
{
  "prices": {
    "BTC/USDT": 102000,
    "ETH/USDT": 3100
  }
}

// 响应
{
  "success": true,
  "data": {
    "account": { /* 账户信息 */ },
    "liquidated_positions": [],
    "triggered_orders": [
      {"symbol": "BTC/USDT", "type": "TAKE_PROFIT"}
    ]
  }
}
```

### 7. 获取交易统计

**GET** `/api/contract-trading/statistics`

```json
// 响应
{
  "success": true,
  "data": {
    "total_trades": 10,
    "winning_trades": 6,
    "losing_trades": 4,
    "win_rate": 60.00,
    "total_profit": 5000,
    "total_loss": -2000,
    "net_pnl": 3000,
    "total_fee": 100,
    "roi": 30.00
  }
}
```

---

## 代码示例 / Code Examples

### 示例1：基本开多单

```python
async def example_long_position():
    simulator = ContractTradingSimulator(initial_balance=10000)

    # 开10倍杠杆多单
    order = simulator.create_order(
        symbol="BTC/USDT",
        side=OrderSide.LONG,
        quantity=0.5,
        leverage=10
    )

    # 以$100,000成交
    await simulator.execute_order(order.order_id, 100000)

    # 价格上涨到$105,000
    simulator._update_account_equity({"BTC/USDT": 105000})

    # 平仓获利
    await simulator._close_position("BTC/USDT", 105000)

    # 盈亏 = (105000 - 100000) * 0.5 = $2,500
```

### 示例2：开空单

```python
async def example_short_position():
    simulator = ContractTradingSimulator(initial_balance=10000)

    # 开20倍杠杆空单
    order = simulator.create_order(
        symbol="ETH/USDT",
        side=OrderSide.SHORT,
        quantity=10,
        leverage=20
    )

    # 以$3,000成交
    await simulator.execute_order(order.order_id, 3000)

    # 价格下跌到$2,800
    simulator._update_account_equity({"ETH/USDT": 2800})

    # 平仓获利
    await simulator._close_position("ETH/USDT", 2800)

    # 盈亏 = (3000 - 2800) * 10 = $2,000
```

### 示例3：止盈止损

```python
async def example_stop_loss_take_profit():
    simulator = ContractTradingSimulator(initial_balance=10000)

    # 开单设置止盈止损
    order = simulator.create_order(
        symbol="BTC/USDT",
        side=OrderSide.LONG,
        quantity=1,
        leverage=10,
        stop_loss=95000,      # 止损：跌到$95,000平仓
        take_profit=110000    # 止盈：涨到$110,000平仓
    )

    await simulator.execute_order(order.order_id, 100000)

    # 模拟价格变动
    while True:
        current_price = get_realtime_price("BTC/USDT")

        # 检查止盈止损
        triggered = simulator.check_stop_loss_take_profit(
            {"BTC/USDT": current_price}
        )

        if triggered:
            print(f"触发: {triggered[0][1]}")
            break

        await asyncio.sleep(60)
```

### 示例4：爆仓场景

```python
async def example_liquidation():
    simulator = ContractTradingSimulator(initial_balance=1000)

    # 开高杠杆（危险）
    order = simulator.create_order(
        symbol="BTC/USDT",
        side=OrderSide.LONG,
        quantity=1,
        leverage=100  # 100倍杠杆！
    )

    await simulator.execute_order(order.order_id, 100000)

    # 查看强平价
    positions = simulator.get_positions()
    liquidation_price = positions[0]['liquidation_price']
    print(f"强平价: ${liquidation_price:,.2f}")  # 约 $99,040

    # 价格下跌触发爆仓
    simulator._update_account_equity({"BTC/USDT": 99000})
    liquidated = simulator.check_liquidation({"BTC/USDT": 99000})

    if liquidated:
        print("💥 爆仓！")
```

---

## 风险参数说明 / Risk Parameters

### 1. 强平价格计算 / Liquidation Price

#### 多头强平价：
```
强平价 = 开仓价 × (1 - 1/杠杆 + 维持保证金率)
```

**示例**：
- 开仓价：$100,000
- 杠杆：10x
- 维持保证金率：0.4%
- 强平价 = $100,000 × (1 - 0.1 + 0.004) = **$90,400**

#### 空头强平价：
```
强平价 = 开仓价 × (1 + 1/杠杆 - 维持保证金率)
```

**示例**：
- 开仓价：$100,000
- 杠杆：10x
- 维持保证金率：0.4%
- 强平价 = $100,000 × (1 + 0.1 - 0.004) = **$109,600**

### 2. 保证金计算 / Margin Calculation

```
所需保证金 = 持仓价值 / 杠杆
持仓价值 = 数量 × 价格
```

**示例**：
- 数量：1 BTC
- 价格：$100,000
- 杠杆：10x
- 保证金 = ($100,000 × 1) / 10 = **$10,000**

### 3. 盈亏计算 / PnL Calculation

#### 多头盈亏：
```
盈亏 = (当前价 - 开仓价) × 数量
```

#### 空头盈亏：
```
盈亏 = (开仓价 - 当前价) × 数量
```

**示例（多头）**：
- 开仓价：$100,000
- 当前价：$105,000
- 数量：0.5 BTC
- 盈亏 = ($105,000 - $100,000) × 0.5 = **+$2,500**

### 4. 保证金率 / Margin Ratio

```
保证金率 = 权益 / 已用保证金
权益 = 余额 + 未实现盈亏
```

**风险等级**：
- 保证金率 > 10: 安全 ✅
- 5 < 保证金率 ≤ 10: 警告 ⚠️
- 保证金率 ≤ 5: 危险 🔴
- 保证金率 < 1: 触发强平 💥

---

## 常见问题 / FAQ

### Q1: 如何选择合适的杠杆？

**A:** 杠杆越高，风险越大：

| 杠杆 | 风险等级 | 适用场景 |
|------|---------|----------|
| 1-5x | 低风险 | 新手、长期持仓 |
| 5-20x | 中风险 | 有经验的交易者 |
| 20-50x | 高风险 | 短期交易 |
| 50x+ | 极高风险 | 专业交易者 |

### Q2: 止损应该设置在哪里？

**A:** 常见止损策略：
- **固定比例**: 开仓价的 3-5%
- **支撑位**: 关键支撑位下方
- **ATR指标**: 基于波动率设置

**示例**：
```python
entry_price = 100000
stop_loss = entry_price * 0.97  # 3% 止损 = $97,000
```

### Q3: 如何避免爆仓？

**A:** 5个关键原则：
1. ✅ 使用低杠杆（≤10x）
2. ✅ 始终设置止损
3. ✅ 保持充足保证金率（>5）
4. ✅ 分散持仓，不要满仓
5. ✅ 监控保证金率变化

### Q4: 手续费如何计算？

**A:**
- **Maker费率**: 0.02% (限价单)
- **Taker费率**: 0.04% (市价单)

```python
# 示例
position_value = 100000 * 1  # $100,000
maker_fee = position_value * 0.0002  # $20
taker_fee = position_value * 0.0004  # $40
```

### Q5: 如何测试策略？

**A:** 使用模拟器测试：

```python
async def backtest_strategy():
    simulator = ContractTradingSimulator(initial_balance=10000)

    # 加载历史数据
    historical_data = load_klines("BTC/USDT", "1h", days=30)

    for candle in historical_data:
        # 策略逻辑
        if buy_signal(candle):
            order = simulator.create_order(...)
            await simulator.execute_order(...)

        # 更新价格
        simulator._update_account_equity({
            "BTC/USDT": candle['close']
        })

    # 查看结果
    stats = simulator.get_statistics()
    print(f"回测收益率: {stats['roi']:.2f}%")
```

---

## 技术支持 / Support

如有问题，请查看：
- 📖 [API文档](http://localhost:8000/docs)
- 🧪 [测试脚本](../scripts/test_contract_trading.py)
- 💻 [源代码](../app/trading/contract_trading_simulator.py)

---

**最后更新**: 2025-10-28
**版本**: v1.0.0
