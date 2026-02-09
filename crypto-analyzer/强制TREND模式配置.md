# 强制TREND模式配置说明

## 目标

只使用TREND模式交易，完全禁用RANGE震荡市策略。

## 原因

震荡市策略虽然胜率高(80%)，但：
1. 交易机会少
2. 不是核心盈利来源
3. 增加系统复杂度
4. **只要TREND模式就够了**

## 实施方法

### 方法1: 数据库配置(推荐)

直接在数据库设置固定模式：

```sql
-- 强制TREND模式
UPDATE trading_mode_control
SET mode_type = 'trend',
    auto_switch_enabled = 0
WHERE account_id = 2 AND trading_type = 'usdt_futures';

-- 如果记录不存在，插入
INSERT INTO trading_mode_control
(account_id, trading_type, mode_type, auto_switch_enabled, updated_at)
VALUES (2, 'usdt_futures', 'trend', 0, NOW())
ON DUPLICATE KEY UPDATE
    mode_type = 'trend',
    auto_switch_enabled = 0,
    updated_at = NOW();
```

### 方法2: 代码修改

修改 `smart_trader_service.py`:

```python
# 找到这段代码 (约2867行)
suggested_mode = self.mode_switcher.auto_switch_check(...)

# 改为
# suggested_mode = self.mode_switcher.auto_switch_check(...)  # 禁用自动切换
suggested_mode = None  # 🔥 强制不切换

# 找到这段代码 (约2888行)
current_mode_config = self.mode_switcher.get_current_mode(self.account_id, 'usdt_futures')
current_mode = current_mode_config['mode_type'] if current_mode_config else 'trend'

# 改为
# current_mode_config = self.mode_switcher.get_current_mode(self.account_id, 'usdt_futures')
current_mode = 'trend'  # 🔥 强制TREND模式
```

### 方法3: 环境变量

在 `.env` 文件添加：

```bash
# 强制TREND模式
FORCE_TREND_MODE=true
```

然后代码中检查：

```python
if os.getenv('FORCE_TREND_MODE', 'false').lower() == 'true':
    current_mode = 'trend'
    logger.info("🔥 强制TREND模式 - 已禁用震荡市策略")
```

---

## 推荐方案

**使用方法1 (数据库配置)**

原因：
- 不需要改代码
- 重启服务即可生效
- 可以随时通过SQL调整

执行：
```sql
UPDATE trading_mode_control
SET mode_type = 'trend',
    auto_switch_enabled = 0
WHERE account_id = 2 AND trading_type = 'usdt_futures';
```

---

## 验证

重启服务后，日志应该显示：

```
📊 [TRADING-MODE] 当前模式: trend | Big4: BULLISH(75.0)
```

并且不会再看到：
- ❌ "建议切换到range模式"
- ❌ "使用震荡市交易参数"

---

## 效果预期

**只用TREND模式** (基于2月1-4日数据):
- 交易: 约30-35笔/天
- 胜率: 51.5%
- 日盈利: +140U左右
- **简单、稳定、有效**

**vs 加上RANGE模式**:
- 交易: +2笔/天
- 额外盈利: +10-15U/天
- **提升有限，但增加复杂度**

**结论**: TREND模式足够，RANGE可有可无

---

**实施时间**: 立即
**生效时间**: 重启服务后
**预期效果**: 只在趋势明确时交易，其他时候休息
