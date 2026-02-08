# V2 和 V3 信号并行测试方案

## 📊 测试目的

同时运行V2和V3两个版本的信号评分系统，对比分析两者的开仓效果，找出最优策略。

---

## 🔥 V2 vs V3 核心差异

| 维度 | V2 | V3 |
|------|----|----|
| **主要时间周期** | 1H K线 (48根, 2天) | 5H + 15M + Big4(30H) |
| **评分总分** | 90分 | 42分 |
| **开仓阈值** | 35分 (38.9%) | 18分 (43%) |
| **核心优势** | 位置/动量/趋势全面分析 | 多时间周期协同 + 精准入场 |
| **建仓方式** | 一次性入场 | 一次性入场 (已取消分批) |

---

## 📋 V2评分系统详解

### 评分维度 (总分90分)

**1. 位置评分 (20分)**
- 72小时高低点位置
- 低位(< 30%) 做多: 20分
- 高位(> 70%) 做空: 20分
- 中位: 5分

**2. 24H动量 (15分)**
- 跌超3%: 做多 15分
- 涨超3%: 做空 15分

**3. 1H趋势 (20分)**
- 48根K线中 > 62.5%阳线: 做多 20分
- 48根K线中 > 62.5%阴线: 做空 20分

**4. 波动率 (10分)**
- 24H波动 > 5%: 双向 10分

**5. 连续趋势 (15分)**
- 10根1H中 ≥7根同向 + 涨跌幅适中: 15分

**6. 1D趋势确认 (10分)**
- 30天中 > 60%同向: 10分

---

## 📋 V3评分系统详解

### 评分维度 (总分42分)

**1. Big4 (5分)**
- 30H宏观趋势方向一致: 5分

**2. 5H趋势 (6分)**
- 5根中 ≥4根同向: 6分
- 5根中 ≥3根同向: 4分
- 5根中 ≥2根同向: 2分

**3. 15M信号 (14分)** - 主导权重
- 8根中 ≥6根同向: 14分
- 8根中 ≥5根同向: 10分
- 8根中 ≥4根同向: 7分
- 8根中 ≥3根同向: 4分

**4. 量价配合 (9分)**
- 实体大小 + 成交量匹配

**5. 技术指标 (8分)**
- RSI + MACD + 布林带

---

## 🚀 启用方法

### 1. 环境变量配置

在`.env`文件中设置：

```bash
# V2模式开关
USE_V2_MODE=true

# V3模式开关
USE_V3_MODE=true

# 两个都设为true即可并行运行
```

### 2. 数据库准备

执行数据库升级脚本添加`signal_version`字段：

```bash
mysql -u root -p binance-data < db_migrations/add_signal_version_field.sql
```

### 3. 启动服务

```bash
python smart_trader_service.py
```

---

## 📊 信号标识

开仓时会自动标记信号版本：

- **v2**: V2信号评分器产生的信号
- **v3**: V3信号评分器产生的信号
- **traditional**: 传统模式信号

信号版本保存在`futures_positions.signal_version`字段中。

---

## 📈 对比分析

### 查询V2信号的交易表现

```sql
SELECT
    signal_version,
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100 as win_rate,
    AVG(realized_pnl) as avg_pnl,
    SUM(realized_pnl) as total_pnl
FROM futures_positions
WHERE status = 'closed'
AND signal_version = 'v2'
AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY signal_version;
```

### 查询V3信号的交易表现

```sql
SELECT
    signal_version,
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100 as win_rate,
    AVG(realized_pnl) as avg_pnl,
    SUM(realized_pnl) as total_pnl
FROM futures_positions
WHERE status = 'closed'
AND signal_version = 'v3'
AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY signal_version;
```

### V2 vs V3 完整对比

```sql
SELECT
    signal_version,
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losses,
    ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate_pct,
    ROUND(AVG(realized_pnl), 2) as avg_pnl,
    ROUND(SUM(realized_pnl), 2) as total_pnl,
    ROUND(AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END), 2) as avg_win,
    ROUND(AVG(CASE WHEN realized_pnl <= 0 THEN realized_pnl END), 2) as avg_loss,
    ROUND(AVG(entry_score), 2) as avg_entry_score
FROM futures_positions
WHERE status = 'closed'
AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY signal_version
ORDER BY total_pnl DESC;
```

---

## 🎯 测试周期

建议测试时间: **3-7天**

**观察指标**:
1. ✅ 开仓频率 (V2预计更高)
2. ✅ 胜率对比 (V3目标 > 55%)
3. ✅ 平均盈亏 (V3有移动止盈)
4. ✅ 总盈亏 (最终指标)
5. ✅ 开仓质量 (信号评分分布)

---

## 🔍 日志观察

### V2信号示例

```
[V2-SCORE] BTC/USDT LONG 评分: 45/90
  - position_low: 20分 (72H位置: 25%)
  - momentum_down_3pct: 15分 (24H跌4.2%)
  - trend_1h_bull: 0分 (48H阳线45%)
  - volatility_high: 10分 (24H波动6.3%)
[SIGNAL_VERSION] BTC/USDT 信号版本: v2
```

### V3信号示例

```
[V3-SCORE] ETH/USDT SHORT 评分达标: 22/42
  - Big4: 5.0分
  - 5H: 4.0分
  - 15M: 10.0分
  - 量价: 2.0分
  - 技术: 1.0分
[SIGNAL_VERSION] ETH/USDT 信号版本: v3
```

---

## 📌 注意事项

1. **资金分配**: V2和V3共用同一个账户，注意总持仓数量限制
2. **信号冲突**: 同一币种可能同时产生V2和V3信号，会分别开仓
3. **数据收集**: 至少运行3天再做对比分析，1天数据样本太少
4. **参数调整**: 测试期间不要频繁调整参数，保持稳定观察

---

## 🎉 预期结果

### V2优势场景:
- ✅ 明显的位置优势 (底部/顶部)
- ✅ 大幅波动后的反转
- ✅ 中长期趋势启动

### V3优势场景:
- ✅ 多时间周期共振
- ✅ Big4方向明确
- ✅ 短期精准入场

### 最终目标:
**融合两者优势，打造最强评分系统！**

---

**测试开始时间**: 2026-02-08
**测试负责人**: 超级大脑系统
**数据分析**: 持续进行中...
