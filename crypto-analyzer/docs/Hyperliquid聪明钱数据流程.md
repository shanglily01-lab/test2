# Hyperliquid 聪明钱活动 - 数据采集与展示逻辑全解析

本文档详细说明了系统中 Hyperliquid 聪明钱数据的完整流程，从数据采集到前端展示。

---

## 📊 一、数据采集层

### 1. 核心采集器：`HyperliquidCollector`

**位置：** `app/collectors/hyperliquid_collector.py`

**主要功能：**
- **API 端点：** `https://api.hyperliquid.xyz/info` (公开 API，无需密钥)
- **排行榜数据：** `https://stats-data.hyperliquid.xyz/Mainnet/leaderboard` (GET 请求)

**关键方法：**

```python
# 1. 发现聪明交易者（从排行榜）
async def discover_smart_traders(period='week', min_pnl=10000)
  - 获取排行榜数据
  - 筛选条件：周 PnL ≥ $10,000
  - 提取：address, pnl, roi, volume, account_value

# 2. 获取用户状态（持仓信息）
async def fetch_user_state(address)
  - 请求类型：clearinghouseState
  - 返回：当前持仓、余额等

# 3. 获取成交记录
async def fetch_user_fills(address, limit=100)
  - 请求类型：userFills
  - 返回：最近交易记录（价格、方向、数量、PnL）

# 4. 监控单个地址
async def monitor_address(address, hours=24)
  - 获取用户状态 + 成交记录
  - 分析交易方向（LONG/SHORT）
  - 统计：交易笔数、净流入、总PnL、持仓数

# 5. 批量监控所有地址（分级策略）
async def monitor_all_addresses(hours=24, priority='all', hyperliquid_db)
  - high: PnL>10K, ROI>50%, 7天内活跃, 限200个
  - medium: PnL>5K, ROI>30%, 30天内活跃, 限500个
  - low/all: 全部活跃钱包

# 6. 抓取聪明钱交易（用于 API 展示）
async def fetch_top_smart_money_trades_24h(top_n=100, min_trade_usd=50000, hours=24)
  - 从排行榜获取前100名交易者
  - 抓取每个地址的成交记录
  - 过滤：时间窗口 + 最小金额
```

---

## 💾 二、数据存储层

### 核心数据库：`HyperliquidDB`

**位置：** `app/database/hyperliquid_db.py`

**数据库表结构：**

```sql
-- 1. 交易者表
hyperliquid_traders
  - id, address, display_name, first_seen, last_updated

-- 2. 周度表现表
hyperliquid_weekly_performance
  - trader_id, week_start, week_end, pnl, roi, volume, account_value, pnl_rank

-- 3. 监控钱包表
hyperliquid_monitored_wallets
  - trader_id, address, label, monitor_type (auto/manual)
  - discovered_pnl, discovered_roi, is_monitoring
  - last_check_at, last_trade_at

-- 4. 钱包交易表
hyperliquid_wallet_trades
  - trader_id, address, coin, side (LONG/SHORT)
  - price, size, notional_usd, closed_pnl
  - trade_time, raw_data

-- 5. 钱包持仓表
hyperliquid_wallet_positions
  - trader_id, coin, side, size, entry_price
  - unrealized_pnl, snapshot_time
```

**关键方法：**

```python
# 保存周度表现
save_weekly_performance(address, pnl, roi, volume, account_value)

# 添加监控钱包
add_monitored_wallet(address, label, monitor_type, pnl, roi)

# 获取监控钱包（按优先级）
get_monitored_wallets_by_priority(min_pnl, min_roi, days_active, limit)

# 保存交易记录
save_wallet_trade(address, trade_data)

# 保存持仓快照
save_wallet_position(address, position_data, snapshot_time)
```

---

## ⚙️ 三、调度器任务

### 位置：`app/scheduler.py`

**定时任务配置：**

```python
# 1. 每天 02:00 - 采集排行榜
schedule.every().day.at("02:00").do(
    lambda: asyncio.run(self.collect_hyperliquid_leaderboard())
)
  ↓
  - 获取排行榜数据
  - 筛选聪明交易者（PnL ≥ $10K）
  - 保存周度表现到数据库
  - 自动添加到监控钱包列表

# 2. 分级监控策略
- 高优先级（200个）：每 5 分钟
- 中优先级（500个）：每 1 小时
- 全量扫描（8000+）：每 6 小时

schedule.every(5).minutes.do(
    lambda: asyncio.run(self.monitor_hyperliquid_wallets(priority='high'))
)
  ↓
  - 获取用户状态 + 成交记录
  - 保存交易到 wallet_trades
  - 保存持仓到 wallet_positions
  - 更新 last_check_at
```

---

## 📈 四、缓存聚合层

### 位置：`app/services/cache_update_service.py`

**缓存表：** `cache_hyperliquid_aggregation`

```python
async def update_hyperliquid_aggregation(symbols: List[str])
  ↓
  对每个币种（如 BTC/USDT）：
    1. 遍历所有监控钱包
    2. 获取最近24小时交易
    3. 筛选该币种的交易
    4. 统计聚合数据：
       - long_trades / short_trades (笔数)
       - net_flow (净流入 USD)
       - inflow / outflow (流入/流出)
       - total_volume (总交易量)
       - total_pnl (总盈亏)
       - active_wallets (活跃钱包数)
       - avg_trade_size (平均交易规模)
       - largest_trade (最大单笔交易)
    5. 生成信号：
       - 多空比 > 2 且净流入 > $500K → STRONG_BUY
       - 多空比 > 1.5 且净流入 > $100K → BUY
       - 多空比 < 0.5 且净流出 < -$500K → STRONG_SELL
    6. 写入缓存表

定时更新：每 10 分钟
```

---

## 🌐 五、API 接口层

### 位置：`app/api/routes.py`

**核心接口：**

```python
# 1. 获取聪明钱交易（实时查询，不走缓存）
GET /api/hyperliquid/trades
参数:
  - hours: 时间窗口（默认24h）
  - min_usd: 最小交易金额（默认$50K）
  - limit: 返回数量（默认200）
  - coin: 币种过滤（可选）
  - side: 方向过滤（可选：LONG/SHORT）

实现逻辑:
  1. 创建 HyperliquidCollector 实例
  2. 调用 fetch_top_smart_money_trades_24h(top_n=100, min_usd, hours)
     ↓
     - 从排行榜获取前100名交易者
     - 并发抓取每个地址的成交记录
     - 过滤时间 + 金额
     - 补充交易者信息（PnL, ROI, account_value）
  3. 应用过滤条件（coin, side）
  4. 限制返回数量
  5. 统计数据：
     - 多空笔数 / 多空金额
     - 净流入
     - 独特钱包数 / 独特币种数
     - 多空比

返回格式:
{
  "success": true,
  "data": {
    "trades": [
      {
        "coin": "BTC",
        "side": "LONG",
        "price": 42000,
        "size": 1.5,
        "notional_usd": 63000,
        "closed_pnl": 1200,
        "timestamp": "2024-01-01T12:00:00",
        "trader_pnl": 50000,
        "trader_roi": 120
      }
    ],
    "statistics": {
      "total_count": 150,
      "long_count": 90,
      "short_count": 60,
      "total_long_usd": 3500000,
      "total_short_usd": 2000000,
      "net_flow_usd": 1500000,
      "unique_wallets": 85,
      "unique_coins": 15,
      "long_short_ratio": 1.5
    }
  }
}
```

---

## 🎨 六、前端展示层

### 位置：`templates/dashboard.html`

**展示逻辑：**

```javascript
// 1. 初始化加载
window.addEventListener('load', () => {
    loadHyperliquidTrades();  // 加载聪明钱交易
});

// 2. 定时刷新（每30秒）
async function refreshAllData() {
    await loadHyperliquidTrades();
}

// 3. 核心函数
async function loadHyperliquidTrades() {
    // 请求 API
    const response = await fetch('/api/hyperliquid/trades?hours=24&min_usd=50000&limit=200');
    const data = await response.json();

    // 渲染统计卡片
    document.getElementById('hlLongCount').textContent = stats.long_count;
    document.getElementById('hlShortCount').textContent = stats.short_count;
    document.getElementById('hlNetFlow').textContent = `$${(stats.net_flow_usd / 1e6).toFixed(2)}M`;
    document.getElementById('hlUniqueWallets').textContent = stats.unique_wallets;

    // 渲染交易表格
    trades.forEach(trade => {
        <tr>
          <td>${trade.coin}</td>
          <td><badge class="${trade.side === 'LONG' ? 'success' : 'danger'}">${sideText}</badge></td>
          <td>$${formatNumber(trade.notional_usd)}</td>
          <td class="${pnlClass}">$${formatNumber(trade.closed_pnl)}</td>
          <td>交易者PnL: $${formatNumber(trade.trader_pnl)}</td>
          <td>${timeStr}</td>
        </tr>
    });
}
```

**展示内容：**

1. **统计卡片**
   - 24h 做多笔数
   - 24h 做空笔数
   - 净流入（百万美元）
   - 活跃钱包数

2. **交易表格**
   - 币种 | 方向 | 金额 | PnL | 交易者信息 | 时间

---

## 🔄 七、完整数据流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                      1. 数据采集（每天02:00）                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
    HyperliquidCollector.discover_smart_traders()
                              ↓
              Hyperliquid API 排行榜 (周度 PnL)
                              ↓
            筛选：PnL ≥ $10K, ROI > 50%
                              ↓
    ┌──────────────────────────────────────────────────┐
    │         保存到数据库（HyperliquidDB）              │
    │  - hyperliquid_weekly_performance               │
    │  - hyperliquid_monitored_wallets (auto)         │
    └──────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  2. 分级监控（高频/中频/低频）                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
    monitor_hyperliquid_wallets(priority='high/medium/all')
                              ↓
         ┌──────────────────────────────────┐
         │  并发监控200/500/8000+个钱包      │
         │  - fetch_user_state()            │
         │  - fetch_user_fills()             │
         └──────────────────────────────────┘
                              ↓
    ┌──────────────────────────────────────────────────┐
    │         实时写入数据库                             │
    │  - hyperliquid_wallet_trades（交易记录）          │
    │  - hyperliquid_wallet_positions（持仓快照）       │
    └──────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  3. 缓存聚合（每10分钟）                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
        CacheUpdateService.update_hyperliquid_aggregation()
                              ↓
         ┌──────────────────────────────────┐
         │  对每个币种：                      │
         │  - 遍历所有监控钱包                │
         │  - 统计24h交易数据                │
         │  - 计算多空比、净流入、PnL         │
         │  - 生成信号（BUY/SELL）           │
         └──────────────────────────────────┘
                              ↓
    ┌──────────────────────────────────────────────────┐
    │         写入缓存表                                 │
    │  - cache_hyperliquid_aggregation                │
    └──────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  4. API 查询（实时/缓存混合）                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
         GET /api/hyperliquid/trades
                              ↓
         ┌──────────────────────────────────┐
         │  实时查询（不走缓存）：             │
         │  1. 获取排行榜前100名              │
         │  2. 抓取成交记录                   │
         │  3. 过滤 + 统计                   │
         └──────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  5. 前端展示（每30秒刷新）                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
              loadHyperliquidTrades()
                              ↓
         ┌──────────────────────────────────┐
         │  渲染统计卡片 + 交易表格           │
         │  - 24h多空笔数                    │
         │  - 净流入                         │
         │  - 活跃钱包数                     │
         │  - 实时交易列表                   │
         └──────────────────────────────────┘
```

---

## 🎯 八、关键特性总结

### 1. 分级监控策略

- **高优先级**（200个）��PnL>10K, ROI>50%, 7天内活跃 → 每5分钟
- **中优先级**（500个）：PnL>5K, ROI>30%, 30天内活跃 → 每1小时
- **全量扫描**（8000+）：所有活跃钱包 → 每6小时

### 2. 数据更新频率

- **排行榜采集**：每天1次（02:00）
- **钱包监控**：分级（5min/1h/6h）
- **缓存聚合**：每10分钟
- **前端刷新**：每30秒

### 3. API 查询模式

- **实时查询**：`/api/hyperliquid/trades` 不走缓存，直接调用 API
- **缓存查询**：Dashboard 主页面使用缓存聚合数据
- **混合模式**：统计用缓存，详细交易用实时

### 4. 性能优化

- **并发采集**：使用 `asyncio.gather` 并发监控多个钱包
- **API 限流**：每个地址间隔1.5秒
- **缓存机制**：聚合数据缓存10分钟
- **重试机制**：带指数退避的3次重试

---

## 📋 相关文件列表

### 核心文件

- **数据采集：** `app/collectors/hyperliquid_collector.py`
- **数据库：** `app/database/hyperliquid_db.py`
- **数据库Schema：** `app/database/hyperliquid_schema.sql`
- **调度器：** `app/scheduler.py`
- **缓存服务：** `app/services/cache_update_service.py`
- **API接口：** `app/api/routes.py`
- **前端展示：** `templates/dashboard.html`

### 辅助工具

- **Token映射：** `app/services/hyperliquid_token_mapper.py`
- **管理脚本：** `scripts/hyperliquid/manage_wallets.py`
- **监控脚本：** `scripts/hyperliquid/monitor.py`
- **排行榜查看：** `scripts/hyperliquid/view_leaderboard.py`

---

## 🔍 技术要点

### API 特点

1. **公开访问**：无需 API Key
2. **速率限制**：需要控制请求频率（建议1.5秒间隔）
3. **数据格式**：JSON，包含完整的交易和持仓信息

### 数据处理

1. **币种标识符映射**：Hyperliquid 使用数字索引，需要映射到标准符号
2. **交易方向**：`side='B'` 表示做多，`side='A'` 表示做空
3. **金额单位**：统一使用 USD 计价

### 监控策略

1. **自动发现**：从排行榜自动添加高 PnL 交易者
2. **手动添加**：支持手动添加特定钱包地址
3. **优先级管理**：根据表现动态调整监控频率
4. **历史追踪**：保存完整的交易和持仓历史

---

## 📝 未来优化方向

1. **实时 WebSocket**：替代轮询，降低延迟
2. **机器学习预测**：基于历史数据预测交易者行为
3. **社交网络分析**：识别交易者之间的关联
4. **风险评估**：评估跟单风险
5. **多链支持**：扩展到其他链上 DEX

---

*文档生成时间：2025-11-08*
*版本：1.0*
