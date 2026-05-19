# S8 / S9 重构规划 (strategy_live / strategy_bigmid → multi_strategy_service)

**状态**: 规划阶段,未实施
**预计时机**: 2026-06-15 之后 (test2 整合 1 个月后)
**预估工作量**: 2-3 个 focused day

---

## 为什么不在 2026-05-15 做

1. **strategy_live + strategy_bigmid 已经能跑** (commit 75282b0 改 API_BASE 后)
2. **风险高**: 1261 + 1002 = 2263 行,涉及状态机重写、SL/TP 逻辑统一
3. **优先级低**: 进程隔离 (现状) 比代码内聚 (目标) 影响小
4. **数据需求**: 重构后行为可能有微妙差异,需要先用 1 个月数据建 baseline 对比

---

## 重构目标

把 test3 迁来的两个独立进程,合并进 [`app/services/multi_strategy_service.py`](../app/services/multi_strategy_service.py) 当成 S8/S9:

| 当前 (Phase 1) | 重构后 (Phase ∞) |
|---------------|------------------|
| strategy_live.py (独立进程) | scan_s8_topshort() in multi_strategy_service.py |
| strategy_bigmid.py (独立进程) | scan_s9_gemini_ai() in multi_strategy_service.py |
| HTTP /api/futures/open | self._open_position() helper |
| strategy_state 表 | futures_positions 表 (用 source 区分) |
| strategy_live.py 进程 | run_fast() 调度 |
| strategy_bigmid.py 进程 (6h) | run_slow() 内的 6h 限速 |

好处:
- 进程数 6 → 4 (砍掉 strategy_live + strategy_bigmid)
- 统一 SmartExitOptimizer 处理平仓 (代替 strategy_live 自己的 trail 逻辑)
- 统一 13 道关卡过滤 (Big4 / blacklist / 评级 / 熔断 等)

风险:
- strategy_live 的 trail/early-sl/breakeven 比 SmartExitOptimizer 更精细
- strategy_live 有自己的品种黑名单,迁移要合并
- Gemini API 调用要确保线程安全

---

## Phase ∞.1: S8 (topshort 顶部反转做空)

### 当前 strategy_live.py 核心逻辑提取

入场条件 (line 100-113 + 信号检测函数):
- `TOP_PUMP_THRESH = 0.80` (48h 涨 ≥ 80%)
- `TOP_NO_NEW_H = 6` (近 6 小时无新高)
- `TOP_LOOKBACK_H = 48` (lookback window)
- `TOPSHORT_MIN_HISTORY_DAYS = 12` (上市 ≥ 12 天)
- `TOP_MIN_24H_CHANGE_PCT = -15.0` (24h 已跌 15%+ 不再做空)
- 入场位置守卫: 3h 15m K 线区间内不在 10% 分位以下

出场逻辑 (统一):
- 硬 TP 20%
- 硬 SL 12%
- 持仓 6h
- 4h 平仓后冷却

### 加到 multi_strategy_service.py 的方案

```python
class MultiStrategyService:
    # ... 已有 S1-S7 常量 ...

    # 策略8: 激进顶部反转做空 (from test3 strategy_live topshort)
    S8_LEVERAGE = 5
    S8_MARGIN = 500
    S8_MAX_POSITIONS = 3
    S8_TP_PCT = 0.20
    S8_SL_PCT = 0.12
    S8_HOLD_HOURS = 6
    S8_SOURCE = 's8_topshort'
    S8_PUMP_THRESH = 0.80
    S8_NO_NEW_H = 6
    S8_LOOKBACK_H = 48
    S8_MIN_HISTORY_DAYS = 12
    S8_MAX_24H_DROP = -15.0
    S8_COOLDOWN_HOURS = 4

    def scan_s8_topshort(self):
        """S8: 48h涨>=80% + N根无新高 + 12天上市历史 + 24h未跌15% 做空"""
        if self._strategy_position_count(self.S8_SOURCE) >= self.S8_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 == 'STRONG_BULLISH':
            logger.info("[S8] Big4 强牛市,跳过顶部反转做空")
            return

        symbols = self._get_candidate_symbols(min_abs_change=50.0)  # 24h 变化 >= 50% 才检查
        opened = 0

        for symbol in symbols:
            if self._strategy_position_count(self.S8_SOURCE) + opened >= self.S8_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                # 1. 检查上市历史 ≥ 12 天 (from _topshort_has_min_listed_history)
                # 2. 检查 24h 跌幅 < 15% (from 24h_change check)
                # 3. 计算 48h 涨幅 (1h K线 48 根)
                # 4. 检查近 6h 无新高
                # 5. 检查 3h 入场位置不在低位
                # 6. 满足全部 → _open_position(SHORT)
                pass  # TODO: 从 strategy_live.py 提取信号检测代码

            except Exception as e:
                logger.warning(f"[S8] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S8] 本轮新开 {opened} 单")
```

### 从 strategy_live.py 要提取的函数

| 当前位置 | 函数 | 用途 | 迁移方案 |
|---------|------|------|---------|
| line ~459 | `_topshort_has_min_listed_history` | 12 天上市检查 | 完整移植 |
| line ~277 | `get_price` (HTTP) | 取价 | 改用 `self._get_current_price` |
| line ~430 | `_get_24h_stats` | 24h 高低 | 改用 `self._get_klines` 计算 |
| line ~439 | `_get_4h_stats` | 4h 高低 | 改用 `self._get_klines` 计算 |
| line ~383 | `open_order` (HTTP) | 开仓 | 改用 `self._open_position` |
| line ~700-900 (估) | `topshort_tick` 主逻辑 | 信号生成 | 提取核心 if 条件 |

### 限价单支持

strategy_live 支持限价单 (`limit_price` 参数)。multi_strategy_service 当前**只支持市价单**。迁移时:
- 选项 A: S8 也用市价单 (简化,但可能滑点大)
- 选项 B: 给 multi_strategy_service._open_position 加 limit_price 支持 (改 INSERT 语句 + futures_orders 表写 PENDING)

推荐 A,因为 S8 触发条件本来就是 reactive (价格已大幅 pump),滑点能接受。

---

## Phase ∞.2: S9 (Gemini AI 决策)

### 当前 strategy_bigmid.py 核心逻辑

每 6 小时:
1. 取 top-30 大币种 (24h 成交额排序)
2. 对每个 symbol 准备结构化数据:
   - 15 天日线 OHLC
   - 4 天 1h K 线
   - 8h 15m + 1h K 线
   - RSI / 成交量统计
3. 把数据塞给 Gemini API,prompt 让它返回 JSON:
   `{"direction": "long|short|skip", "expected_pnl_pct": float, "reasoning": "..."}`
4. 过滤 `expected_pnl_pct >= 0.01` 的 → 限价 (current ± 0.5%) 开仓
5. TP 3% / SL 2% / 持仓 6h

### 加到 multi_strategy_service.py 的方案

```python
class MultiStrategyService:
    S9_INTERVAL_HOURS = 6
    S9_MIN_EXPECTED_PNL = 0.01
    S9_MAX_POSITIONS = 5
    S9_MARGIN = 300
    S9_LEVERAGE = 5
    S9_TP_PCT = 0.03
    S9_SL_PCT = 0.02
    S9_HOLD_HOURS = 6
    S9_SOURCE = 's9_gemini_ai'
    _last_s9_run = None

    def scan_s9_gemini_ai(self):
        """S9: 每 6h 让 Gemini AI 决策 top30 大币,expected_pnl>=1% 即开仓"""
        from datetime import datetime, timedelta
        now = datetime.utcnow()

        # 6h 限速
        if self._last_s9_run and (now - self._last_s9_run).total_seconds() < self.S9_INTERVAL_HOURS * 3600:
            return
        self._last_s9_run = now

        if self._strategy_position_count(self.S9_SOURCE) >= self.S9_MAX_POSITIONS:
            return

        # 1. 取 top30 (按 24h 成交额, 排除证券类)
        from app.services.securities_filter import is_security
        symbols = self._get_top30_by_quote_volume()
        symbols = [s for s in symbols if not is_security(s)]

        # 2. 准备数据 + 调 Gemini
        for symbol in symbols:
            try:
                payload = self._prepare_gemini_payload(symbol)
                verdict = self._call_gemini(payload)
                if not verdict or verdict.get('direction') == 'skip':
                    continue
                if verdict.get('expected_pnl_pct', 0) < self.S9_MIN_EXPECTED_PNL:
                    continue

                side = 'LONG' if verdict['direction'] == 'long' else 'SHORT'
                reason = f"S9_Gemini: {verdict.get('reasoning', '')[:100]}"
                self._open_position(
                    symbol, side, self.S9_MARGIN, self.S9_LEVERAGE,
                    self.S9_TP_PCT, self.S9_SL_PCT, self.S9_HOLD_HOURS,
                    self.S9_SOURCE, reason
                )
            except Exception as e:
                logger.warning(f"[S9] {symbol} 异常: {e}")
```

### 关键挑战

1. **Gemini API 配额**: 免费版日限 ~1500 次,top30 × 4 轮/天 = 120 次,远未触顶,OK
2. **Gemini API 失败重试**: strategy_bigmid 有完整的重试逻辑,要移植
3. **Prompt 工程**: bigmid 的 prompt 是核心,绝不能简化
4. **JSON 解析**: Gemini 输出格式不稳定,bigmid 有大量 cleanup 逻辑

### 从 strategy_bigmid.py 要提取的函数

| 函数 | 用途 | 行数估算 |
|------|------|---------|
| `_collect_market_data` | 准备 Gemini 输入 | ~100 行 |
| `_call_gemini` | API 调用 + 重试 | ~50 行 |
| `_parse_gemini_verdict` | JSON 清洗 + 验证 | ~80 行 |
| Prompt 模板 | core decision logic | ~50 行 |

---

## Phase ∞.3: 迁移后行为差异 + baseline 对比

重构后必须验证 S8/S9 vs strategy_live/bigmid 的行为差异:

### 对比指标

| 维度 | 重构前 (strategy_live) | 重构后 (S8) | 差异原因 |
|------|----------------------|-------------|---------|
| 平均日开仓数 | ? | ? | SmartExitOptimizer 接管平仓后会不会少开 |
| 平均胜率 | ? | ? | 13 道关卡过滤 vs strategy_live 自己的过滤 |
| 平均 R:R | ? | ? | 是否仍然能跑出 20%/12% R:R |
| 单笔最大盈/亏 | ? | ? | trail 逻辑是否被 SmartExit 替代 |
| Big4 BEARISH 期间表现 | ? | ? | strategy_live 不看 Big4 |

需要**先收集 1 个月数据**作为 baseline。

### 验证方法

灰度迁移:
1. 同时跑 strategy_live (旧) + scan_s8 (新),source 不同
2. 跑 7 天对比,如果新版差距 < 20%,切换
3. 旧 strategy_live.py 改为 disable (env var 控制)

---

## Phase ∞.4: 删除原文件 + 进程

完成 S8/S9 验证后:

```bash
# 1. 停 strategy_live + strategy_bigmid 进程
sudo systemctl stop crypto-strategy-live crypto-strategy-bigmid
sudo systemctl disable crypto-strategy-live crypto-strategy-bigmid

# 2. 删 systemd 文件
sudo rm /etc/systemd/system/crypto-strategy-{live,bigmid}.service
sudo systemctl daemon-reload

# 3. 归档 Python 文件 (不立即删,防回滚)
mkdir -p deprecated/
git mv strategy_live.py strategy_bigmid.py deprecated/
git mv strategy_state_db.py deprecated/   # 如果 S8/S9 不再用

git commit -m "deprecate: strategy_live/bigmid 已整合为 S8/S9"
```

---

## 验收 checklist

完成 S8/S9 后:

- [ ] multi_strategy_service.py 包含 scan_s8 + scan_s9
- [ ] run_slow() 调度 S8 (复用 SLOW_SCAN_INTERVAL_SEC)
- [ ] run_slow() 调度 S9 (6h 限速)
- [ ] futures_positions 有 source='s8_topshort' / 's9_gemini_ai'
- [ ] SmartExitOptimizer 能正常关闭 S8/S9 持仓 (它们不在 _MULTI_STRATEGY_SOURCES 里?)
- [ ] 与 strategy_live/bigmid 数据对比 baseline 误差 < 20%
- [ ] 旧 strategy_live.py + strategy_bigmid.py 已移到 deprecated/
- [ ] systemd 服务停掉
- [ ] 进程数从 6 降到 4

---

## 不重构的替代方案

如果以后觉得 S8/S9 重构成本高于收益,可以:
- **保留现状** (6 个进程,各自独立)
- 接受多进程的简单性 (一个挂不影响其他)
- 集中精力优化每个独立策略的胜率

事实上,微服务/单体的取舍没有绝对答案。**当前的多进程方案已经足够好**,只是不"优雅"。

---

*文档版本: v1.0 | 创建: 2026-05-15*
