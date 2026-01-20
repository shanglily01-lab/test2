# 信号评分体系自适应优化方案

## 🎯 当前问题分析

### 现有评分逻辑（硬编码）

当前SmartDecisionBrain的评分权重是固定的：

| 信号组件 | LONG权重 | SHORT权重 | 说明 |
|----------|----------|-----------|------|
| 位置评分 | +20 | +20 | 低位做多，高位做空 |
| 短期动量 | +15 | +15 | 24h跌3%做多，涨3%做空 |
| 1小时趋势 | +20 | +20 | 62.5%阳线做多，阴线做空 |
| 波动率 | +10 | +10 | >5%波动率加分 |
| 连续趋势 | +15 | +15 | 7根连续K线 |
| 大趋势确认 | +10 | +10 | 30天趋势一致 |

**总分阈值**: 通常20-30分开仓

### ❌ 存在的问题

1. **无法自适应**: 所有权重固定，无法根据实际表现调整
2. **无效指标无法剔除**: 不知道哪些指标真正有效
3. **市场环境变化**: 不同市场环境下，有效指标可能不同
4. **得分高低无反馈**: SMART_BRAIN_15 vs SMART_BRAIN_60 的表现差异无法反馈到评分系统

---

## 💡 解决方案：信号评分权重自适应系统

### 核心思路

1. 将评分权重存储到数据库
2. 根据历史表现动态调整权重
3. 不同得分区间使用不同的风控参数

---

## 📊 数据库设计

### 表1: signal_scoring_weights (信号评分权重表)

```sql
CREATE TABLE signal_scoring_weights (
    id INT AUTO_INCREMENT PRIMARY KEY,
    signal_component VARCHAR(50) NOT NULL UNIQUE COMMENT '信号组件名',
    weight_long DECIMAL(5,2) NOT NULL COMMENT 'LONG权重',
    weight_short DECIMAL(5,2) NOT NULL COMMENT 'SHORT权重',
    base_weight DECIMAL(5,2) NOT NULL COMMENT '基准权重（初始值）',
    performance_score DECIMAL(5,2) COMMENT '表现评分（-100到100）',
    last_adjusted TIMESTAMP COMMENT '上次调整时间',
    adjustment_count INT DEFAULT 0 COMMENT '调整次数',
    description VARCHAR(255) COMMENT '描述',
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_component (signal_component),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信号评分权重表';
```

**初始数据**:

```sql
INSERT INTO signal_scoring_weights
(signal_component, weight_long, weight_short, base_weight, description) VALUES
('position_low', 20, 0, 20, '低位置评分'),
('position_high', 0, 20, 20, '高位置评分'),
('momentum_down', 15, 0, 15, '24h下跌动量'),
('momentum_up', 0, 15, 15, '24h上涨动量'),
('trend_1h_bull', 20, 0, 20, '1h看涨趋势'),
('trend_1h_bear', 0, 20, 20, '1h看跌趋势'),
('volatility_high', 10, 10, 10, '高波动率'),
('consecutive_bull', 15, 0, 15, '连续看涨'),
('consecutive_bear', 0, 15, 15, '连续看跌'),
('trend_1d_bull', 10, 0, 10, '1d看涨趋势'),
('trend_1d_bear', 0, 10, 10, '1d看跌趋势');
```

### 表2: signal_score_performance (信号得分表现表)

```sql
CREATE TABLE signal_score_performance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    score_range VARCHAR(20) NOT NULL COMMENT '得分区间（如 "15-20"）',
    position_side VARCHAR(10) NOT NULL COMMENT 'LONG/SHORT',
    total_orders INT DEFAULT 0 COMMENT '总订单数',
    win_orders INT DEFAULT 0 COMMENT '盈利订单数',
    total_pnl DECIMAL(15,2) DEFAULT 0 COMMENT '总盈亏',
    avg_pnl DECIMAL(10,2) COMMENT '平均盈亏',
    win_rate DECIMAL(5,4) COMMENT '胜率',
    avg_hold_minutes INT COMMENT '平均持仓时间',
    last_analyzed TIMESTAMP COMMENT '上次分析时间',
    recommended_action VARCHAR(50) COMMENT '建议操作',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_score_side (score_range, position_side),
    INDEX idx_side (position_side),
    INDEX idx_analyzed (last_analyzed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信号得分表现表';
```

**示例数据分析结果**:

| score_range | side  | orders | win_rate | avg_pnl | recommended_action |
|-------------|-------|--------|----------|---------|-------------------|
| 15-20       | LONG  | 45     | 12%      | -$8.50  | DISABLE           |
| 20-30       | LONG  | 89     | 28%      | -$2.30  | REDUCE_SIZE       |
| 30-40       | LONG  | 124    | 45%      | +$3.80  | NORMAL            |
| 40-50       | LONG  | 78     | 58%      | +$8.20  | INCREASE_SIZE     |
| 50+         | LONG  | 34     | 72%      | +$15.50 | AGGRESSIVE        |

---

## 🔧 实施方案

### 选项A: 完整实施（推荐，但工作量大）

**步骤**:
1. 创建2张新表
2. 修改SmartDecisionBrain从数据库读取权重
3. 记录每笔订单的信号组成（JSON格式）
4. 每日分析权重调整
5. 动态调整阈值和仓位倍数

**优势**: 最智能，完全自适应
**工作量**: 约4-6小时开发 + 测试

### 选项B: 简化版（快速见效）

**步骤**:
1. 只创建signal_score_performance表
2. 分析不同得分区间的表现
3. 根据得分动态调整仓位倍数（已在adaptive_params中）
4. 禁用表现差的得分区间

**优势**: 快速实施，立即见效
**工作量**: 约1-2小时开发

### 选项C: 先观察再决定（最稳健）⭐

**立即修改**:
1. 在futures_positions表添加entry_score字段（如果没有）
2. 记录每笔订单的开仓得分
3. 运行1周

**1周后分析**:
```sql
SELECT
    CASE
        WHEN entry_score < 20 THEN '15-20'
        WHEN entry_score < 30 THEN '20-30'
        WHEN entry_score < 40 THEN '30-40'
        WHEN entry_score < 50 THEN '40-50'
        ELSE '50+'
    END as score_range,
    position_side,
    COUNT(*) as orders,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) as win_rate,
    AVG(realized_pnl) as avg_pnl
FROM futures_positions
WHERE status = 'closed'
AND close_time > DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY score_range, position_side
ORDER BY position_side, score_range;
```

**根据结果决定**:
- 如果高分信号明显更好 → 提高阈值
- 如果某些得分区间特别差 → 禁用该区间
- 如果得分高低无明显差异 → 说明评分体系需要重构

**优势**: 数据驱动决策
**工作量**: 30分钟修改代码 + 1周观察

---

## 📈 优化算法

### 权重调整公式

```python
def adjust_weight(component_name, current_weight, performance_data):
    """
    根据表现调整权重

    performance_data = {
        'win_rate': 0.45,           # 胜率45%
        'avg_pnl': 5.20,            # 平均盈利$5.20
        'contribution': 0.32        # 对总得分的贡献度
    }
    """
    # 基准胜率50%, 基准盈利$0
    win_rate_diff = (performance_data['win_rate'] - 0.50) * 100
    pnl_score = performance_data['avg_pnl'] / 5  # 归一化

    # 综合评分
    component_score = (win_rate_diff * 0.6) + (pnl_score * 0.4)

    # 调整幅度
    if component_score > 10:
        adjustment = +3
    elif component_score > 5:
        adjustment = +2
    elif component_score < -10:
        adjustment = -3
    elif component_score < -5:
        adjustment = -2
    else:
        adjustment = 0

    new_weight = max(5, min(30, current_weight + adjustment))
    return new_weight
```

### 得分阈值动态调整

```python
def adjust_threshold(score_performance):
    """
    根据不同得分区间的表现，调整最低开仓阈值
    """
    # 找到胜率>50%的最低得分区间
    profitable_scores = [
        score for score, data in score_performance.items()
        if data['win_rate'] > 0.50
    ]

    if profitable_scores:
        new_threshold = min(profitable_scores)
    else:
        # 找到盈亏平衡点
        breakeven_scores = [
            score for score, data in score_performance.items()
            if data['avg_pnl'] > 0
        ]
        new_threshold = min(breakeven_scores) if breakeven_scores else 40

    return new_threshold
```

### 得分区间策略

根据得分区间表现，动态调整策略:

| 得分 | 胜率 | 策略调整 |
|------|------|----------|
| <20  | <20% | 禁用此得分区间的信号 |
| 20-30| 20-35%| 仓位×0.5 |
| 30-40| 35-50%| 仓位×0.8 |
| 40-50| 50-65%| 正常仓位 |
| 50+  | >65% | 仓位×1.2 |

---

## 🎯 实施建议

### 推荐路径：选项C（先观察）

**第1周**:
1. 检查futures_positions表是否有entry_score字段
2. 如果没有，添加该字段
3. 修改开仓逻辑，记录得分
4. 运行1周，收集数据

**第2周**:
1. 运行SQL分析得分区间表现
2. 手动分析数据
3. 根据结果决定：
   - 提高/降低阈值
   - 禁用低分区间
   - 或实施完整的权重优化系统

**为什么推荐选项C**:
- ✅ 最小改动，风险最低
- ✅ 数据驱动决策
- ✅ 1周后有真实数据支撑
- ✅ 避免过早优化

---

## 📊 预期效果

### 短期（1-2周）
- 识别哪些得分区间表现最好
- 提高开仓阈值，过滤低质量信号
- 预期胜率提升5-10%

### 中期（1个月）
- 得分区间策略生效
- 高分信号增加仓位
- 低分信号减少仓位或禁用
- 预期盈利因子提升20-30%

### 长期（3个月）
- 权重完全自适应
- 评分体系持续优化
- 系统达到最优状态

---

**创建时间**: 2026-01-20
**版本**: 1.0
**状态**: 方案设计
**下一步**: 检查entry_score字段 → 记录得分 → 观察1周
