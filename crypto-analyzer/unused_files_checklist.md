# 未使用文件清单

## 📋 待删除文件列表

### 1. 震荡模式相关策略文件（已废弃）
```
app/strategies/bollinger_mean_reversion.py
app/strategies/range_market_detector.py
app/strategies/range_reversal_strategy.py
app/strategies/mode_switcher.py
```
**原因**: 已移除所有震荡模式交易，只保留趋势模式

---

### 2. 震荡模式API（已废弃）
```
app/api/trading_mode_api.py
```
**原因**: 该API依赖已删除的震荡模式策略文件

---

### 3. 临时分析脚本
```
app/analyze_24h_signals.py
app/simple_disaster_check.py
app/12h_retrospective_analysis.py
```
**原因**: 临时调试脚本，现已不再使用

---

### 4. 已删除的根目录脚本
**状态**: 已在上次commit中删除 ✅

---

### 5. 文档文件（震荡模式相关）
**状态**: 已在上次commit中删除 ✅

---

### 6. 脚本目录下可能未使用的文件
需要进一步确认的文件：
```
app/execute_brain_optimization.py
app/emergency_circuit_breaker.py
scripts/analysis/analyze_brain_trading.py
scripts/analysis/analyze_brain_trading_extended.py
scripts/analysis/check_account2_brain.py
scripts/analysis/analyze_last_night_trades.py
scripts/database_tools/check_optimization.py
scripts/database_tools/check_reasons.py
scripts/database_tools/check_schema_and_add_entry_score.py
scripts/database_tools/check_server_optimization.py
scripts/database_tools/check_server_optimization_v2.py
scripts/database_tools/update_entry_score_field.py
```
**说明**: 这些可能是一次性运行的工具脚本，需要确认是否仍需保留

---

## ✅ 确认保留的核心文件

### 主服务
- `smart_trader_service.py` - U本位交易服务 ✅
- `coin_futures_trader_service.py` - 币本位交易服务 ✅
- `fast_collector_service.py` - 快速数据采集服务 ✅
- `check_big4_trend.py` - Big4趋势检测 ✅
- `reset_weights.py` - 权重重置工具 ✅

### 核心策略
- `app/strategies/safe_mode_switcher.py` - 安全模式切换器 ✅
- `app/services/smart_entry_executor.py` - 智能分批建仓 ✅
- `app/services/smart_exit_optimizer.py` - 智能出场优化 ✅
- `app/services/big4_trend_detector.py` - Big4趋势检测器 ✅
- `app/services/signal_quality_manager.py` - 信号质量管理器 ✅

---

## 🗑️ 建议的删除步骤

### 第一批：震荡模式相关（高优先级）
```bash
rm app/strategies/bollinger_mean_reversion.py
rm app/strategies/range_market_detector.py
rm app/strategies/range_reversal_strategy.py
rm app/strategies/mode_switcher.py
rm app/api/trading_mode_api.py
```

### 第二批：临时分析脚本
```bash
rm app/analyze_24h_signals.py
rm app/simple_disaster_check.py
rm app/12h_retrospective_analysis.py
```


---

## ⚠️ 需要用户确认的文件

以下文件可能是工具脚本，建议用户确认后再删除：

1. **脑优化相关**
   - `app/execute_brain_optimization.py` - 未在main.py中被引用，可能是独立脚本

2. **紧急熔断**
   - `app/emergency_circuit_breaker.py` - 未在main.py中被引用，可能已废弃

3. **分析脚本**
   - `scripts/analysis/*` - 这些分析脚本是否还需要？

4. **数据库工具**
   - `scripts/database_tools/*` - 这些是一次性运行的脚本吗？

---

## 📊 文件统计

- **确认删除**: 7个文件（震荡模式+临时脚本）
- **待确认**: 约15个文件（工具脚本）
- **核心保留**: 主服务和核心策略模块

---

**请确认后我将执行删除操作**
