# 现货交易策略诊断报告

## 问题总结

现货交易系统**完全没有产生任何交易信号**,导致错过了最近2天的大行情。

### 核心问题

1. **信号阈值过高**: 要求 ≥60分 才交易 (正常应该是 ≥40-45分)
2. **数据表不匹配**: 代码使用 `spot_positions`,但数据库只有 `paper_trading_positions`
3. **没有信号记录**: 不写入 `paper_trading_signal_executions`,导致无法追踪被拒绝的信号
4. **止盈止损过于保守**:
   - 止损: 10% (太大,应该5-8%)
   - 止盈: 50% (太大,应该15-30%)
5. **最大持仓数太少**: 只允许5个持仓,资金利用率不足

---

## 详细分析

### 1. 信号生成阈值问题

**代码位置**: [app/services/spot_trader_service.py:629-631](app/services/spot_trader_service.py#L629-L631)

```python
# 信号强度 >= 60 才考虑
if signal['signal_strength'] >= 60:
    opportunities.append(signal)
```

**问题**:
- 60分阈值过高,在震荡行情中很难达到
- 对比合约交易的入场阈值是 40-45分
- 导致大量有效信号被拒绝

**影响**:
- 最近2天行情中,可能有很多45-60分的优质信号被忽略
- 资金完全闲置,利用率0%

**建议修改**:
```python
# 信号强度 >= 45 才考虑 (降低15分)
if signal['signal_strength'] >= 45:
    opportunities.append(signal)
```

---

### 2. 数据表不匹配问题

**代码位置**: [app/services/spot_trader_service.py:379-384](app/services/spot_trader_service.py#L379-L384)

```python
cursor.execute("""
    SELECT *
    FROM spot_positions  # ← 表不存在
    WHERE status = 'active'
    ORDER BY created_at DESC
""")
```

**实际表名**: `paper_trading_positions`

**影响**:
- 如果服务运行,会抛出表不存在的错误
- 导致服务无法正常工作

**需要全局替换**:
- `spot_positions` → `paper_trading_positions`
- 检查所有SQL查询

---

### 3. 缺少信号记录功能

**问题**:
- `scan_opportunities()` 生成信号后,没有写入 `paper_trading_signal_executions` 表
- 无法追踪哪些信号被拒绝、为什么被拒绝
- 无法统计信号质量和策略表现

**建议添加**:
在 `scan_opportunities()` 中,为每个信号创建记录:

```python
def scan_opportunities(self) -> List[Dict]:
    opportunities = []
    all_signals = []

    for symbol in self.symbols:
        signal = self.signal_generator.generate_signal(symbol)
        all_signals.append((symbol, signal['signal_strength']))

        # 记录信号到数据库
        self._record_signal(signal, decision='SKIP' if signal['signal_strength'] < 45 else 'PENDING')

        # 信号强度 >= 45 才考虑
        if signal['signal_strength'] >= 45:
            opportunities.append(signal)

    # ... 其余代码
```

---

### 4. 风险参数过于保守

**代码位置**: [app/services/spot_trader_service.py:356-357](app/services/spot_trader_service.py#L356-L357)

```python
self.take_profit_pct = 0.50  # 50% 止盈
self.stop_loss_pct = 0.10    # 10% 止损
```

**问题**:
- **止盈50%**: 在正常行情中很难达到,导致持仓时间过长
- **止损10%**: 亏损容忍度太高,单笔亏损过大
- **风险回报比**: 5:1 过于激进,正常应该是 2:1 或 3:1

**建议修改**:
```python
self.take_profit_pct = 0.20  # 20% 止盈 (从50%降低到20%)
self.stop_loss_pct = 0.07    # 7% 止损 (从10%降低到7%)
```

**风险回报比**: 20% / 7% ≈ 2.86:1 (更合理)

---

### 5. 最大持仓数限制

**代码位置**: [app/services/spot_trader_service.py:353](app/services/spot_trader_service.py#L353)

```python
self.max_positions = 5  # 最多5个币种
```

**问题**:
- 总资金50,000 USDT,单币10,000 USDT
- 只开5个仓位 = 最多用50,000 USDT
- 但实际有20%现金储备 + 5批次建仓策略
- 实际资金利用率 ≈ 40% (每个币初始只建10%仓位)

**实际资金占用**:
- 5个币 × 10,000 × 10% (第1批) = 5,000 USDT
- 资金利用率: 5,000 / 50,000 = **10%**

**建议修改**:
```python
self.max_positions = 10  # 增加到10个币种
self.per_coin_capital = 5000  # 单币资金降低到5000
```

**优化后**:
- 10个币 × 5,000 × 10% = 5,000 USDT (初始)
- 分散风险,提高资金利用率

---

## 对比合约交易策略

| 参数 | 现货策略 | 合约策略 | 差异 |
|------|---------|---------|------|
| **入场阈值** | ≥60分 | ≥40-45分 | 现货太高 |
| **止盈** | 50% | 4-8% | 现货太高 |
| **止损** | 10% | 2-3% | 现货太高 |
| **最大持仓** | 5个 | 10-15个 | 现货太少 |
| **建仓策略** | 5批次 (10/10/20/20/40) | 3批次 | 现货更保守 |
| **资金利用率** | 10% | 60-80% | 现货太低 |

**结论**: 现货策略过于保守,导致:
- 信号通过率极低 (可能 <5%)
- 资金利用率极低 (10%)
- 错过绝大部分行情

---

## 建议的优化方案

### 方案A: 最小改动 (快速修复)

1. **降低入场阈值**: 60 → 45分
2. **修复数据表名**: `spot_positions` → `paper_trading_positions`
3. **调整止盈止损**: 止盈50%→20%, 止损10%→7%

**预期效果**:
- 信号通过率: <5% → 20-30%
- 资金利用率: 10% → 30-40%
- 能够捕捉中等强度的行情

---

### 方案B: 全面优化 (推荐)

1. **入场阈值**: 60 → 45分
2. **止盈止损**: 止盈50%→18%, 止损10%→6%
3. **增加持仓数**: 5 → 10个
4. **单币资金**: 10,000 → 5,000 USDT
5. **添加信号记录**: 记录所有信号到 `paper_trading_signal_executions`
6. **修复数据表**: 全局替换表名
7. **优化建仓策略**:
   - 第1批: 15% (从10%提高)
   - 第2批: 15%
   - 第3批: 25%
   - 第4批: 25%
   - 第5批: 20% (从40%降低)

**预期效果**:
- 信号通过率: <5% → 30-40%
- 资金利用率: 10% → 50-60%
- 风险回报比: 5:1 → 3:1 (更合理)
- 能够充分捕捉上涨行情

---

### 方案C: 激进优化 (抓住行情)

**仅适用于确定的上涨行情 (如最近2天)**

1. **入场阈值**: 60 → 40分 (降低20分)
2. **止盈止损**: 止盈50%→25%, 止损10%→8%
3. **增加持仓数**: 5 → 12个
4. **单币资金**: 10,000 → 4,000 USDT
5. **取消现金储备**: 20% → 10% (临时)
6. **加快建仓**: 前3批直接建60%仓位

**预期效果**:
- 信号通过率: <5% → 50-60%
- 资金利用率: 10% → 70-80%
- 在上涨行情中获取最大收益
- ⚠ 风险较高,需要及时调整

---

## SQL修复脚本

```sql
-- 检查现有表名
SHOW TABLES LIKE '%position%';

-- 如果需要,创建 spot_positions 表
CREATE TABLE IF NOT EXISTS spot_positions LIKE paper_trading_positions;

-- 或者在代码中全局替换表名
```

---

## 实施建议

### 立即执行 (紧急):

1. **修改入场阈值**: [spot_trader_service.py:630](app/services/spot_trader_service.py#L630)
   ```python
   if signal['signal_strength'] >= 45:  # 从60改为45
   ```

2. **修改止盈止损**: [spot_trader_service.py:356-357](app/services/spot_trader_service.py#L356-L357)
   ```python
   self.take_profit_pct = 0.20  # 从0.50改为0.20
   self.stop_loss_pct = 0.07    # 从0.10改为0.07
   ```

3. **重启服务**

### 后续优化:

1. 添加信号记录功能
2. 修复数据表名问题
3. 增加最大持仓数
4. 监控并调整参数

---

## 预期收益对比

**当前策略** (最近2天):
- 开仓: 0笔
- 收益: $0
- 资金利用率: 0%

**优化后策略** (方案B,假设):
- 开仓: 8-12笔
- 平均每笔: +8-15%
- 预期收益: $3,000-6,000 (假设平均10%收益,单笔5000资金)
- 资金利用率: 50-60%

**差距**: 错失了约 $3,000-6,000 的收益机会

---

## 下一步行动

1. [ ] 修改 [spot_trader_service.py](app/services/spot_trader_service.py) 的关键参数
2. [ ] 检查数据表名是否匹配
3. [ ] 在服务器上重启现货交易服务
4. [ ] 监控日志,确认信号开始生成
5. [ ] 24小时后评估效果,继续优化

**预计修改时间**: 15分钟
**预计生效时间**: 重启服务后立即生效 (下一个5分钟周期)
