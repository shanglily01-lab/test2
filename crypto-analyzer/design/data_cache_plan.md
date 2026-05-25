# 数据库预计算层设计方案

## 一、问题分析

经过全面审计，整个代码库有 **400+ 条 SQL 查询**，最突出的问题：

### 1.1 kline_data 被高频复杂 JOIN
`kline_data` 表是最大的表（数亿行），但在 `gemini_explore_worker.py` 中每 6h 就有：
- 4 层嵌套子查询的 movers 查询
- 跨 3 个子查询的 JOIN
- `ORDER BY` 全表扫描后再 LIMIT
这些查询在亿级数据量下会越来越慢。

### 1.2 重复的聚合查询
- 每个 Gemini 工人跑一次就查 6 次 `futures_positions` 做 COUNT/SUM
- 每次查都要全表扫描 closed 记录，即使只是要个总数
- dashboard 每次刷新也是同样的聚合

### 1.3 system_settings 高频低延迟读取
50+ 个查询分布在各个模块，每次都要走 MySQL 查询，但数据几乎不变（只有切换开关时改）

### 1.4 数据分散，多次查询
一个 Gemini 分析周期要顺序查：`kline_data` → `price_stats_24h` → `funding_rate_data` → `big4_trend_history` → `futures_positions`，每个查完再查下一个，总耗时 = 多次网络往返

---

## 二、预计算层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     定时调度 (scheduler.py)                        │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ 每 1 分钟    │  │ 每 5 分钟     │  │ 每 30 分钟              │  │
│  │ market_cache │  │ mover_cache  │  │ position_stats_cache   │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────────────┘  │
│         │                │                   │                    │
└─────────┼────────────────┼───────────────────┼────────────────────┘
          ▼                ▼                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                   data_cache 数据库 (独立 schema)                   │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ market_mv   │  │ movers_cache │  │ position_stats           │ │
│  │ _cache      │  │              │  │                          │ │
│  ├─────────────┤  ├──────────────┤  ├──────────────────────────┤ │
│  │ BTC/ETH/SOL │  │ 涨幅榜 Top20 │  │ 30d/7d/今日盈亏统计       │ │
│  │ 最新价格    │  │ 跌幅榜 Top20 │  │ 胜率/总交易数/多空分布    │ │
│  │ Big4 信号   │  │ 资金费率极端 │  │                          │ │
│  │ 恐惧贪婪    │  │ 成交量异动   │  │                          │ │
│  │ 川普新闻    │  │             │  │                          │ │
│  └──────┬──────┘  └──────┬───────┘  └───────────┬──────────────┘ │
└─────────┼────────────────┼──────────────────────┼────────────────┘
          ▼                ▼                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                     应用层直接读 data_cache                        │
│   gemini_explore_worker.py  │  dashboard  │  API 路由             │
│   gemini_predictor.py       │  sentiment  │  其他服务              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 三、新增缓存表定义

```sql
-- ============================================================
-- 3.1 市场概览快照 (每 1 分钟更新)
-- 所有价格、行情、情绪数据的单行汇总
-- ============================================================
CREATE TABLE data_cache.market_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    
    -- 核心币种价格
    btc_price       DECIMAL(20,8)  DEFAULT NULL,
    btc_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    eth_price       DECIMAL(20,8)  DEFAULT NULL,
    eth_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    sol_price       DECIMAL(20,8)  DEFAULT NULL,
    sol_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    bnb_price       DECIMAL(20,8)  DEFAULT NULL,
    bnb_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    xrp_price       DECIMAL(20,8)  DEFAULT NULL,
    xrp_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    
    -- 市场情绪
    big4_signal            VARCHAR(20)  DEFAULT NULL COMMENT 'BEARISH/BULLISH/NEUTRAL',
    fear_greed_value       INT          DEFAULT NULL,
    fear_greed_label       VARCHAR(20)  DEFAULT NULL,
    
    -- Gemini 自身分析缓存
    last_explore_summary   TEXT         DEFAULT NULL,
    last_predict_summary   TEXT         DEFAULT NULL,
    last_sentiment_summary TEXT         DEFAULT NULL,
    
    -- 元数据
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    compute_ms      INT          DEFAULT 0,
    
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='市场概览快照 — 每 1min 更新, 供 Gemini/API/dashboard 直接读';


-- ============================================================
-- 3.2 24h 市场异动快照 (每 5 分钟更新)
-- 涨幅榜、跌幅榜、资金费率极端、成交量异动
-- 替代 gemini_explore_worker 中复杂的 4 层嵌套 JOIN
-- ============================================================
CREATE TABLE data_cache.market_movers_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    category        VARCHAR(20)  NOT NULL COMMENT 'gainers/losers/funding_high/funding_low/volume_spike',
    symbol          VARCHAR(32)  NOT NULL,
    value           DECIMAL(20,8) DEFAULT NULL COMMENT '涨跌幅%/费率%/成交额',
    rank_no         INT          DEFAULT 0,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_category (category, rank_no),
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='24h 市场异动 — 每 5min 更新, 替代 kline_data 复杂 JOIN';


-- ============================================================
-- 3.3 持仓统计快照 (每 30 分钟更新)
-- 各 source 的盈亏统计、持仓数、胜率
-- 替代 100+ 条 futures_positions 聚合查询
-- ============================================================
CREATE TABLE data_cache.position_stats_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    source          VARCHAR(32)  NOT NULL COMMENT 'gemini_explore/gemini_predict/PREDICTOR/all',
    account_id      INT          NOT NULL DEFAULT 2,
    
    -- 总览
    open_count      INT          DEFAULT 0 COMMENT '当前持仓数',
    closed_24h      INT          DEFAULT 0,
    closed_7d       INT          DEFAULT 0,
    closed_30d      INT          DEFAULT 0,
    
    -- 盈亏
    pnl_24h         DECIMAL(20,4) DEFAULT 0,
    pnl_7d          DECIMAL(20,4) DEFAULT 0,
    pnl_30d         DECIMAL(20,4) DEFAULT 0,
    total_pnl       DECIMAL(20,4) DEFAULT 0,
    
    -- 胜率
    wins_30d        INT          DEFAULT 0,
    losses_30d      INT          DEFAULT 0,
    win_rate_30d    DECIMAL(5,2) DEFAULT 0,
    
    -- 多空分布
    long_count      INT          DEFAULT 0,
    short_count     INT          DEFAULT 0,
    long_pnl        DECIMAL(20,4) DEFAULT 0,
    short_pnl       DECIMAL(20,4) DEFAULT 0,
    
    -- 元数据
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE INDEX idx_source_account (source, account_id),
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='持仓统计快照 — 每 30min 更新, 替代重复聚合查询';


-- ============================================================
-- 3.4 候选交易对池快照 (每 6 分钟更新)
-- 预先算好 Gemini explore/predict 需要的候选池
-- (24h 涨跌幅 + 成交额 + 资金费率 + 最新 K 线行情)
-- 替代 gemini_explore_worker 的 _build_universe
-- ============================================================
CREATE TABLE data_cache.candidate_pool_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    symbol          VARCHAR(32)  NOT NULL,
    exchange        VARCHAR(20)  NOT NULL DEFAULT 'binance_futures',
    
    -- 24h 行情
    current_price   DECIMAL(20,8) DEFAULT NULL,
    change_24h      DECIMAL(10,2) DEFAULT NULL,
    quote_volume_24h DECIMAL(20,2) DEFAULT NULL,
    
    -- 资金费率
    funding_rate    DECIMAL(10,6) DEFAULT NULL,
    
    -- 技术指标 (预先算好)
    rsi_14          DECIMAL(8,2)  DEFAULT NULL,
    ema_9           DECIMAL(20,8) DEFAULT NULL,
    ema_21          DECIMAL(20,8) DEFAULT NULL,
    
    -- K 线当前 bars 的 JSON 摘要 (供 Gemini prompt 用)
    kline_1h_json   MEDIUMTEXT   DEFAULT NULL COMMENT '最近 12 根 1h K 线 JSON',
    kline_15m_json  MEDIUMTEXT   DEFAULT NULL COMMENT '最近 8 根 15m K 线 JSON',
    kline_1d_json   MEDIUMTEXT   DEFAULT NULL COMMENT '最近 7 根 1d K 线 JSON',
    
    -- 元数据
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE INDEX idx_symbol (symbol, exchange),
    INDEX idx_change_24h (change_24h),
    INDEX idx_volume (quote_volume_24h),
    INDEX idx_funding (funding_rate)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='候选交易对池 — 每 6min 更新, 替代 kline_data 四层子查询';


-- ============================================================
-- 3.5 系统设置缓存 (写时更新, 无需定时)
-- 高频读取的配置项，减少 system_settings 的 50+ 查询
-- ============================================================
CREATE TABLE data_cache.settings_cache (
    setting_key     VARCHAR(64)  PRIMARY KEY,
    setting_value   VARCHAR(255) NOT NULL DEFAULT '',
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='系统设置缓存 — 写 system_settings 时同步更新, 应用层读这里';
```

---

## 四、调度方案

在 `scheduler.py` 中添加新的缓存刷新任务：

| 任务 | 频率 | 数据来源 | 写入目标 |
|------|------|----------|----------|
| `refresh_market_snapshot` | 每 1 分钟 | `price_stats_24h`, `big4_trend_history`, `fear_greed_index` | `market_snapshot` |
| `refresh_market_movers` | 每 5 分钟 | `price_stats_24h`, `funding_rate_data` (用简单查询替代 4 层 JOIN) | `market_movers_snapshot` |
| `refresh_candidate_pool` | 每 6 分钟 | `price_stats_24h`, `funding_rate_data`, `kline_data` (一次取完) | `candidate_pool_snapshot` |
| `refresh_position_stats` | 每 30 分钟 | `futures_positions` (一次聚合) | `position_stats_snapshot` |
| `sync_settings_cache` | 写时触发 | `system_settings` (用触发器或代码双写) | `settings_cache` |

### 关键调度代码示例

```python
# scheduler.py 中的新任务注册

# ── 数据缓存层 ──────────────────────────────────────────
# 市场快照 (1min)
def refresh_market_snapshot():
    """汇总 BTC/ETH/SOL 价格、Big4 信号等, 写入 market_snapshot"""
    sql = """
        REPLACE INTO data_cache.market_snapshot
            (id, btc_price, btc_change_24h, eth_price, eth_change_24h,
             sol_price, sol_change_24h, big4_signal, ...)
        SELECT
            1,
            (SELECT current_price FROM price_stats_24h WHERE symbol='BTCUSDT'),
            (SELECT change_24h FROM price_stats_24h WHERE symbol='BTCUSDT'),
            (SELECT current_price FROM price_stats_24h WHERE symbol='ETHUSDT'),
            (SELECT change_24h FROM price_stats_24h WHERE symbol='ETHUSDT'),
            ...
    """
    # 执行即可

# 市场异动 (5min)
def refresh_market_movers():
    """简单查询替代 4 层 JOIN, 写入 market_movers_snapshot"""
    # SELECT symbol, price_change_pct_24h FROM price_stats_24h
    #   WHERE ... ORDER BY ... LIMIT 20
    # 分别写入 gainers / losers 等

# 候选池 (6min)
def refresh_candidate_pool():
    """一次性取所有候选交易对的数据, 写入 candidate_pool_snapshot"""
    # 用一次批量查询替代 N 次独立查询
```

---

## 五、现有代码改法

### 5.1 gemini_explore_worker.py (最重要的优化点)

**改前** — 每次 6h 跑一轮时就做亿级 JOIN：
```python
# 4 层嵌套子查询
SELECT k.symbol, ... FROM kline_data k
INNER JOIN (SELECT symbol, MAX(open_time) ... FROM kline_data ...) latest ON ...
INNER JOIN (SELECT ... FROM kline_data ...) p24 ON ...
INNER JOIN (SELECT ... FROM kline_data ...) vol ON ...
```

**改后** — 直接读预计算池：
```python
# 一次简单查询
SELECT symbol, current_price, change_24h, quote_volume_24h,
       funding_rate, rsi_14, kline_1h_json, kline_15m_json
FROM data_cache.candidate_pool_snapshot
WHERE quote_volume_24h >= 10000000
  AND symbol LIKE '%/USDT'
ORDER BY ABS(change_24h) DESC
```

**改动收益**：`亿级 JOIN + 排序` → `单表扫描 2000 行`

### 5.2 gemini_explore_worker.py 中的 K 线获取

**改前** — 对每个候选 symbol(40-60个) 分别查 3 次 `kline_data`：
```python
for symbol in universe:
    klines_1h = fetch_klines(symbol, '1h', 12)
    klines_15m = fetch_klines(symbol, '15m', 8)
    klines_1d = fetch_klines(symbol, '1d', 7)
```

**改后**：
```python
rows = cur.execute("SELECT symbol, kline_1h_json, kline_15m_json, kline_1d_json "
                   "FROM data_cache.candidate_pool_snapshot")
```

**改动收益**：`40-60 * 3 = 120-180 次查询` → `1 次查询`

### 5.3 gemini_explore_worker.py 的全局上下文

**改前** — 多次独立查询：
```python
btc_stats = query("SELECT ... FROM kline_data ... WHERE symbol='BTC/USDT'")
eth_stats = query("SELECT ... FROM kline_data ... WHERE symbol='ETH/USDT'")
sol_stats = query("SELECT ... FROM kline_data ... WHERE symbol='SOL/USDT'")
funding = query("SELECT ... FROM funding_rate_data ...")
big4 = query("SELECT ... FROM big4_trend_history ...")
```

**改后**：
```python
snapshot = cur.execute_one("SELECT * FROM data_cache.market_snapshot WHERE id=1")
movers = cur.execute("SELECT * FROM data_cache.market_movers_snapshot ORDER BY rank_no")
```

**改动收益**：`5-8 次查询` → `2 次查询`

### 5.4 dashboard / API 端点的持仓统计

**改前** — 每次前端刷新就查一次聚合：
```python
SELECT COUNT(*), SUM(realized_pnl), ... FROM futures_positions
WHERE account_id=2 AND status='closed' ...
```

**改后**：
```python
SELECT * FROM data_cache.position_stats_snapshot WHERE source='all' AND account_id=2
```

**改动收益**：全表扫描聚合 → 1 行直接读

### 5.5 system_settings 高频读取

**改前** — 每个模块自行 `SELECT setting_value FROM system_settings WHERE setting_key=%s`

**改后** — 写一个全局缓存函数：
```python
_settings_cache = {}
_SETTINGS_CACHE_TTL = 60  # 60 秒过期

def get_setting(key: str, default: str = '') -> str:
    """带本地缓存的 setting 读取, 避免频繁查 MySQL"""
    cached = _settings_cache.get(key)
    if cached and time.time() - cached['time'] < _SETTINGS_CACHE_TTL:
        return cached['value']
    # 从 data_cache.settings_cache 查
    cur.execute("SELECT setting_value FROM data_cache.settings_cache WHERE setting_key=%s", (key,))
    row = cur.fetchone()
    value = str(row['setting_value']) if row else default
    _settings_cache[key] = {'value': value, 'time': time.time()}
    return value
```

---

## 六、预期收益

| 指标 | 优化前 | 优化后 | 提升比例 |
|------|--------|--------|----------|
| gemini explore 一轮的查询次数 | ~200 次 | ~5 次 | **97.5% ↓** |
| 每次 explore 查询耗时 | 10-30s | 0.5-1s | **95% ↓** |
| dashboard 持仓统计 API 延迟 | 1-3s | 10ms | **99% ↓** |
| kline_data 表读负载 | 极高(亿级扫描) | 极低(仅缓存更新) | **99% ↓** |
| system_settings 重复查询 | 50+/次/轮 | 0(内存缓存) | **100% ↓** |

---

## 七、实施路径

**Phase 1** — 创建 data_cache schema + 5 张缓存表 (1 天)
**Phase 2** — 在 scheduler.py 注册 4 个缓存刷新任务 (1 天)
**Phase 3** — 改造 `gemini_explore_worker.py` 读缓存 (2 天)
**Phase 4** — 改造 `gemini_predictor.py`、`dashboard_snapshot_service.py` 读缓存 (1 天)
**Phase 5** — 改造所有 `system_settings` 读取为本地缓存 (1 天)
**Phase 6** — 观察运行，下掉旧的复杂查询 (滚动)

---

请确认方案，同意后我开始编码实施。
