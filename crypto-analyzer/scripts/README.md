# Scripts 目录说明

本目录包含各种临时脚本和工具，用于分析、测试和数据库维护。

## 📁 目录结构

### `analysis/` - 交易分析脚本
用于分析超级大脑的交易表现和策略效果。

- `analyze_brain_trading.py` - 分析最近24小时的超级大脑交易
- `analyze_brain_trading_extended.py` - 分析最近7天的超级大脑交易（扩展版）
- `analyze_last_night_trades.py` - 分析指定时间段的交易（如昨晚8点到现在）
- `check_account2_brain.py` - 检查账户2（模拟账户）的超级大脑交易情况

**使用示例：**
```bash
# 查看最近24小时交易表现
python scripts/analysis/analyze_brain_trading.py

# 查看最近7天的详细分析
python scripts/analysis/analyze_brain_trading_extended.py

# 查看昨晚的交易
python scripts/analysis/analyze_last_night_trades.py
```

---

### `database_tools/` - 数据库检查和维护工具
用于检查数据库结构、字段、优化记录等。

- `check_schema_and_add_entry_score.py` - 检查并添加entry_score字段
- `check_server_optimization.py` - 查询服务器端的权重优化历史
- `check_server_optimization_v2.py` - 优化历史查询（改进版）
- `update_entry_score_field.py` - 更新entry_score字段类型和属性
- `check_optimization.py` - 检查优化配置
- `check_reasons.py` - 检查开仓和平仓原因字段

**使用示例：**
```bash
# 检查服务器端的权重优化记录
python scripts/database_tools/check_server_optimization_v2.py

# 检查entry_score字段状态
python scripts/database_tools/update_entry_score_field.py

# 检查平仓原因字段
python scripts/database_tools/check_reasons.py
```

---

### `tests/` - 测试脚本
用于测试特定功能的正确性。

- `test_reason_parsing.py` - 测试开仓和平仓原因的解析函数

**使用示例：**
```bash
# 测试原因解析
python scripts/tests/test_reason_parsing.py
```

---

## 🗑️ 清理说明

这些脚本大多是临时调试和分析用的，如果不再需要可以安全删除。

**保留推荐：**
- `analyze_brain_trading_extended.py` - 日常分析使用
- `check_server_optimization_v2.py` - 检查优化记录
- `update_entry_score_field.py` - 数据库维护

**可以删除：**
- 其他临时调试脚本

---

## 📝 注意事项

1. 所有脚本都使用服务器数据库配置（13.212.252.171）
2. 默认查询账户2（模拟账户）的数据
3. 运行前确保数据库连接正常
