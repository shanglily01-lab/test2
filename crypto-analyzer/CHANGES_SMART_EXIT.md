# SmartExitOptimizer 修改说明

## 修改日期
2026-01-26

## 修改原因
原有逻辑存在严重缺陷,导致66.7%的持仓延迟平仓:

1. **分批平仓逻辑复杂且不可靠**
   - 第1批平仓条件可能一直不满足
   - 第2批平仓的超时逻辑只有在第1批完成后才会检查
   - 导致超过planned_close_time后仍不平仓

2. **缺少强制平仓兜底**
   - `_check_exit_conditions()`没有检查超时
   - 超时逻辑只在`_should_exit_batch2()`中
   - 如果第1批没平,超时逻辑永远不会触发

## 新逻辑设计

### 核心原则
**类比分批建仓的确定性执行: 在规定时间内必须完成任务**

### 时间窗口
```
planned_close_time = 11:46

11:16 (T-30)  启动监控
11:21 (T-25)  完成5分钟价格基线
11:21-11:46   25分钟寻找最佳价格
11:46 (T+0)   必须强制执行平仓
```

### 平仓方式
- **不再分批**: 一次性平仓100%
- **简化逻辑**: 只有一个判断函数`_should_exit_single()`
- **强制兜底**: T+0必须执行,不再拖延

## 代码修改详情

### 1. 修改`_smart_batch_exit()`方法

**Before**: 分2批平仓,复杂的状态管理
```python
'batches': [
    {'ratio': 0.5, 'filled': False, ...},
    {'ratio': 0.5, 'filled': False, ...},
]
```

**After**: 一次性平仓,简单的状态
```python
'closed': False
```

### 2. 删除`_should_exit_batch1()`和`_should_exit_batch2()`

**Before**: 两个复杂的判断函数
- `_should_exit_batch1()`: 第1批50%平仓判断
- `_should_exit_batch2()`: 第2批50%平仓判断,包含超时逻辑

**After**: 一个简洁的判断函数
- `_should_exit_single()`: 100%平仓判断

### 3. 新增`_should_exit_single()`方法

```python
async def _should_exit_single(
    self,
    position: Dict,
    current_price: Decimal,
    baseline: Dict,
    entry_price: float,
    elapsed_minutes: float,
    planned_close_time: datetime
) -> tuple[bool, str]:
    """
    一次性平仓判断（100%）

    最高优先级：超时强制平仓
    if now >= planned_close_time:
        return True, "计划平仓时间已到，强制执行"

    其他条件：
    - 极佳卖点/买点 (评分 >= 95)
    - 优秀卖点/买点 + 有盈利 (评分 >= 85)
    - 突破基线最高/最低价
    - 高盈利 >= 2% + 价格在P50以上/以下
    - 强趋势预警
    - 时间压力 (T-10分钟,评分 >= 60)
    """
```

## 优势对比

| 维度 | 原逻辑 | 新逻辑 |
|------|--------|--------|
| 平仓批次 | 2批 (50% + 50%) | 1批 (100%) |
| 代码复杂度 | 高 | 低 |
| 状态管理 | 复杂 (batches数组) | 简单 (closed布尔值) |
| 超时保证 | ❌ 不可靠 | ✅ 可靠 |
| 强制执行时间 | T+30 (第2批) | T+0 (计划时间) |
| 延迟风险 | 高 (66.7%延迟) | 低 (必须准时) |

## 预期效果

1. **准时率显著提升**
   - 原: 33.3%准时
   - 预期: >90%准时

2. **不再需要手动干预**
   - 原: 7个持仓手动平仓
   - 预期: 0个手动平仓

3. **逻辑更清晰可维护**
   - 代码行数减少约40%
   - 状态管理简化
   - 更易于理解和调试

## 测试建议

1. 监控下一个交易日的平仓情况
2. 检查日志中"计划平仓时间已到"的触发频率
3. 统计延迟平仓的比例
4. 对比平仓价格质量(是否仍能找到好价格)

## 风险评估

**低风险修改**:
- 保留了所有价格评分逻辑
- 保留了止损/止盈检查
- 只是简化了平仓批次和强化了超时保障

## 回滚方案

如果新逻辑有问题,可以通过git恢复到修改前的版本:
```bash
git diff HEAD app/services/smart_exit_optimizer.py
git checkout HEAD -- app/services/smart_exit_optimizer.py
```
