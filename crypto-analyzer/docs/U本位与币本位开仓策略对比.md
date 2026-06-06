# U 本位与币本位：开仓逻辑与策略对比

> 基于代码仓库梳理，2026-04-08  
> 说明：U 本位细节另见 [U本位开仓逻辑全解析.md](./U本位开仓逻辑全解析.md)；币本位组件说明见 [币本位超级大脑逻辑梳理.md](./币本位超级大脑逻辑梳理.md)。本文侧重**对照**与**差异**。

---

## 1. 总览对照

| 维度 | U 本位（USDⓈ-M） | 币本位（COIN-M） |
|------|------------------|-------------------|
| **计价/保证金** | USDT 计价，保证金为 U | 标的币计价，保证金为币 |
| **交易对格式** | `*/USDT` | `*/USD`（永续映射如 `BTCUSD_PERP`） |
| **模拟/主账户** | `account_id = 2` | `account_id = 3` |
| **总开关** | `system_settings.u_futures_trading_enabled`（主策略等） | `coin_futures_trading_enabled` |
| **引擎层** | `FuturesTradingEngine` + `BinanceFuturesEngine`（实盘） | `CoinFuturesTradingEngine` + 币本位 API |
| **主进程** | **多策略并行**：主策略、BTC 动量、预测器、U 破位等 | **单服务**：`coin_futures_trader_service.py` |
| **典型扫描间隔** | 主策略 300s；BTC 动量每分钟；破位 300s | 300s（`scan_interval`） |
| **V2 协同（5m 滚动评分）** | 主策略 **启用**（`SignalScoreV2Service`） | 币本位大脑内 **已移除**（注释：2026-02-21） |
| **羊群防护** | 主策略扫描后对 LONG 比例加权阈值 | 币本位 **无**该段逻辑 |
| **TOP50 实盘过滤** | 主策略在 `live_trading_enabled=1` 时检查 | 以代码为准：币本位 `open_position` 未体现同等 TOP50 分支（与主策略不同） |

---

## 2. U 本位：策略版图与职责

四类策略**共享 `account_id=2`**，通过不同 `source` / 服务进程区分（详见《U本位开仓逻辑全解析》）。

| 策略 | 入口文件 | 典型 source / 标记 | 核心逻辑摘要 |
|------|-----------|---------------------|-------------|
| **主策略（智能交易）** | `smart_trader_service.py` | `signal_confirm` / `trend_follow` | V1 多组件评分 + **V2 协同** + Big4 模式（强趋势 55 分 / 普通 20 分）+ 紧急干预 + 黑名单 + SmartEntryExecutor 采样入场 |
| **BTC 动量** | `app/services/btc_momentum_trader.py` | `BTC_MOMENTUM` | BTC 在 15–60 分钟内波动 ≥1.5% 触发，对 TOP50 同向批量意图开仓，独立冷却与 SL/TP |
| **市场预测器** | `app/services/market_predictor.py` | `PREDICTOR` | 长周期预测信号驱动（6 小时级），与主循环分离 |
| **U 本位破位** | `u_coin_style_trader_service.py` | `U_COIN_STYLE`（或文档中的 `u_coin_style`） | **复刻币本位式**评分与破位系统，标的为 `/USDT`；开关 `u_coin_style_enabled` |

**主策略（SmartTrader）开仓前硬条件（与币本位对齐的部分）**

- 仅允许 `*/USDT`；拒绝纯 `*/USD` 误入 U 服务。  
- `validate_signal_timeframe`、`validate_position_high_signal`、平仓后同向冷却、方向允许、防追高/追跌、评级 Level 3 禁止等。  
- 全局：`u_futures_trading_enabled`、盈利/亏损熔断、`max_positions`、Big4 紧急干预、（实盘）TOP50。

**与币本位差异（主策略独有）**

- **V2**：`coin_kline_scores` 方向与 V1 冲突时的软阻断（阈值 +25 分放行）。  
- **Big4 业务规则更丰富**：如 `NEUTRAL` 下主策略可整轮禁止开仓；币本位 run 循环里对 `NEUTRAL` 会**只保留做空机会**（见下节）。  
- **羊群防护**、**signal_confirm / trend_follow** 双模式阈值体系。

---

## 3. 币本位：单服务闭环

**文件**：`coin_futures_trader_service.py`  
**大脑**：`CoinFuturesDecisionBrain`（同文件内，约从第 145 行起）

### 3.1 主循环要点（`CoinFuturesTraderService.run`）

顺序大致为：自适应优化检查 → 对冲检查 → SmartExitOptimizer 健康检查 → Big4 配置热更新 → 持仓数 → **拉 Big4** → **`brain.scan_all()`** 生成机会 → 盈利/亏损熔断 → **`coin_futures_trading_enabled`** → **Big4 NEUTRAL 时仅保留 SHORT 机会** → 对每条机会再做 Big4 加分/否决（强度 ≥70 禁逆势）→ 同品种同向持仓数 ≤1 → 冷却 → 无反向仓 → `open_position(opp)`。

### 3.2 信号生成（`CoinFuturesDecisionBrain.analyze`）

- 以 **1H 为主**、15m 为辅，含位置（24h 区间）、动量、趋势、波动、量能、突破/破位等，权重来自 `signal_scoring_weights`（与 U 主策略同源表结构，但**策略类型/配置可不同**）。  
- **多头阈值动态**：Big4 强多且强度≥50 时 `long_threshold=50`；否则随 `neutral_bias` 微调。空头仍用 `self.threshold`。  
- 清洗后至少 **2 个**信号；含 `position_mid` 时需 **≥3 个**信号。  
- 信号黑名单、方向矛盾、高位做多/低位做空禁止、**Big4 emergency_intervention** 的 block_long/block_short、**破位系统 BreakoutSystem** 加权或跳过。  
- **明确注释：已移除 V2 共振**。

### 3.3 执行层（`CoinFuturesTraderService.open_position`）

- 仅 **`*/USD`**；拒绝 `*/USDT`。  
- 价格：**DAPI** 标记/ ticker（`get_current_price`）。  
- 保证金：**按评级固定每档**（0→400U，1→100U，2→50U，3→禁止）；Big4 顺势 ×1.2；反转单可沿用 `original_margin`。  
- `quantity = margin * leverage / price`（币数量）。  
- SL/TP：来自 `system_settings` 等（与 U 类似读取链）。  
- 写入 `futures_positions`，`account_id=3`。  
- **注意**：插入 SQL 中 `source` 字段当前写死为 **`'smart_trader'`**（与 U 主策略字符串相同），**生产上区分请用 `account_id` 或后续改为专用 source 字符串**。

---

## 4. 核心差异小结

| 项目 | U 本位 | 币本位 |
|------|--------|--------|
| 策略数量 | 多进程/多模块并行 | 单服务主导 |
| V2 | 主策略使用 | 不使用 |
| Big4 NEUTRAL | 主策略常禁止开仓 | 运行中过滤为**仅做空**（若无做空机会则本轮跳过） |
| 同向多笔 | 主策略有分批/采样等复杂路径；需结合持仓表 | 代码中限制**同品种同向 1 个持仓**（防叠仓） |
| 破位子策略 | U 破位独立服务（`u_coin_style_trader_service.py`） | 内嵌于 `CoinFuturesDecisionBrain` + `BreakoutSystem` |
| 实盘 API | U 本位 fapi / 现货回补 | 币本位 **dapi** 为主 |

---

## 5. 关键源码索引

| 用途 | U 本位 | 币本位 |
|------|--------|--------|
| 主服务循环 | `smart_trader_service.py` | `coin_futures_trader_service.py`（`run` / `async_main`） |
| 评分/扫描 | `SmartTraderService.analyze`（同文件） | `CoinFuturesDecisionBrain.analyze` / `scan_all` |
| 开仓入口 | `SmartTraderService.open_position` | `CoinFuturesTraderService.open_position` |
| U 破位策略 | `u_coin_style_trader_service.py` | — |
| HTTP 模拟盘引擎 | `app/trading/futures_trading_engine.py` | `app/trading/coin_futures_trading_engine.py` |
| API 路由 | `app/api/futures_api.py` | `app/api/coin_futures_api.py` |

---

## 6. 文档与维护建议

1. **U 本位**深度以 [U本位开仓逻辑全解析.md](./U本位开仓逻辑全解析.md) 为准，本文不重复罗列 14 项权重与熔断数值。  
2. **币本位**仓位档位若调整，需同时核对 `_get_margin_per_batch` 与文档 [币本位超级大脑逻辑梳理.md](./币本位超级大脑逻辑梳理.md) 是否一致。  
3. 建议将币本位持仓 `source` 改为独立枚举（如 `coin_futures`），避免与 U 本位 `signal_confirm` 等混淆统计。

---

*本文仅描述当前代码行为；参数以 `config.yaml`、`system_settings` 及数据库为准。*
