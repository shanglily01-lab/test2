# 分批建仓集成计划

## 当前状态

### ✅ 已完成
1. **数据库Schema**: `app/database/smart_brain_schema.sql` 已创建（待执行）
2. **价格采样器**: `app/services/price_sampler.py` 已实现
3. **智能建仓执行器**: `app/services/smart_entry_executor.py` 已实现
4. **智能平仓优化器**: `app/services/smart_exit_optimizer.py` 已实现

### ❌ 待完成
1. **数据库迁移**: 需要在服务器上执行迁移脚本
2. **集成到 smart_trader_service.py**: 将分批建仓逻辑集成到现有开仓流程
3. **测试验证**: 小仓位实盘测试

---

## 数据库迁移

### 步骤1: 在服务器上执行迁移

```bash
# SSH登录到运行smart_trader_service.py的服务器
cd /path/to/crypto-analyzer

# 执行迁移（使用.env中的配置）
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data < app/database/smart_brain_schema.sql
```

### 验证迁移成功

```bash
# 检查新字段是否存在
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data -e "SHOW COLUMNS FROM futures_positions LIKE '%batch%';"
mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data -e "SHOW COLUMNS FROM futures_positions LIKE '%planned_close%';"
```

预期输出应该包含：
- `batch_plan`
- `batch_filled`
- `entry_signal_time`
- `avg_entry_price`
- `planned_close_time`
- `close_extended`
- `extended_close_time`
- `max_profit_pct`
- `max_profit_price`
- `max_profit_time`

---

## 集成方案

### 方案A: 渐进式集成（推荐）

**优点**：
- 风险低，可以逐步测试
- 可以通过配置开关控制启用/禁用
- 保留原有逻辑作为降级方案

**实施步骤**：

#### 1. 在 `config.yaml` 中添加开关

```yaml
# 分批建仓配置
batch_entry:
  enabled: false  # 初始设为false，测试后再启用
  whitelist_symbols: []  # 白名单：只有这些币种启用分批建仓（空=全部）
  batch_ratios: [0.3, 0.3, 0.4]  # 分批比例
  time_window_minutes: 30  # 建仓时间窗口
```

#### 2. 修改 `smart_trader_service.py`

在文件头部添加导入：

```python
from app.services.smart_entry_executor import SmartEntryExecutor
from app.services.smart_exit_optimizer import SmartExitOptimizer
```

在 `SmartTraderService.__init__()` 中初始化：

```python
def __init__(self, db_config, ws_service):
    # ...现有代码...

    # 加载分批建仓配置
    import yaml
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        self.batch_entry_config = config.get('batch_entry', {'enabled': False})

    # 初始化智能建仓执行器
    if self.batch_entry_config.get('enabled'):
        self.smart_entry_executor = SmartEntryExecutor(
            db_config=db_config,
            live_engine=self,  # 使用self作为live_engine
            price_service=ws_service
        )
        logger.info("✅ 智能分批建仓执行器已启动")
    else:
        self.smart_entry_executor = None
        logger.info("⚠️ 智能分批建仓未启用")
```

#### 3. 修改 `open_position()` 函数

在 `open_position()` 函数开始处添加分批建仓逻辑：

```python
def open_position(self, opp: dict):
    """开仓 - 支持做多和做空，支持分批建仓"""
    symbol = opp['symbol']
    side = opp['side']  # 'LONG' 或 'SHORT'

    # ========== 新增：分批建仓逻辑 ==========
    # 检查是否启用分批建仓
    if self.smart_entry_executor and self.batch_entry_config.get('enabled'):
        # 检查是否在白名单中（如果白名单为空，则对所有币种启用）
        whitelist = self.batch_entry_config.get('whitelist_symbols', [])
        should_use_batch = (not whitelist) or (symbol in whitelist)

        # 反转开仓不使用分批建仓（直接一次性开仓）
        is_reversal = 'reversal_from' in opp

        if should_use_batch and not is_reversal:
            logger.info(f"[BATCH_ENTRY] {symbol} {side} 使用智能分批建仓")
            return self._open_position_with_batch(opp)

    # ========== 原有逻辑（一次性开仓） ==========
    try:
        # ...原有代码保持不变...
```

#### 4. 实现 `_open_position_with_batch()` 函数

```python
async def _open_position_with_batch(self, opp: dict):
    """使用智能分批建仓执行器开仓"""
    symbol = opp['symbol']
    side = opp['side']

    try:
        # 验证信号（复用原有验证逻辑）
        signal_components = opp.get('signal_components', {})
        is_valid, reason = self.validate_signal_timeframe(signal_components, side, symbol)
        if not is_valid:
            logger.warning(f"[SIGNAL_REJECT] {symbol} {side} - {reason}")
            return False

        is_valid, reason = self.validate_position_high_signal(symbol, signal_components, side)
        if not is_valid:
            logger.warning(f"[SIGNAL_REJECT] {symbol} {side} - {reason}")
            return False

        # 计算保证金（复用原有逻辑）
        rating_level = self.opt_config.get_symbol_rating_level(symbol)
        rating_config = self.opt_config.get_blacklist_config(rating_level)

        if rating_level == 3:
            logger.warning(f"[BLACKLIST_LEVEL3] {symbol} 已被永久禁止交易")
            return False

        rating_margin_multiplier = rating_config['margin_multiplier']
        base_position_size = self.position_size_usdt * rating_margin_multiplier

        # 使用自适应参数调整仓位大小
        if side == 'LONG':
            position_multiplier = self.brain.adaptive_long.get('position_size_multiplier', 1.0)
            adaptive_params = self.brain.adaptive_long
        else:
            position_multiplier = self.brain.adaptive_short.get('position_size_multiplier', 1.0)
            adaptive_params = self.brain.adaptive_short

        adjusted_position_size = base_position_size * position_multiplier

        # 检查对冲
        opposite_side = 'SHORT' if side == 'LONG' else 'LONG'
        is_hedge = self.has_position(symbol, opposite_side)
        if is_hedge:
            hedge_multiplier = self.opt_config.get_hedge_margin_multiplier()
            adjusted_position_size = adjusted_position_size * hedge_multiplier
            logger.info(f"[HEDGE_MARGIN] {symbol} 对冲开仓, 保证金缩减到{hedge_multiplier*100:.0f}%")

        # 调用智能建仓执行器
        entry_result = await self.smart_entry_executor.execute_entry({
            'symbol': symbol,
            'direction': side,
            'total_margin': adjusted_position_size,
            'leverage': self.leverage,
            'strategy_id': 'smart_trader',
            'trade_params': {
                'entry_score': opp.get('score', 0),
                'signal_components': signal_components,
                'adaptive_params': adaptive_params
            }
        })

        if entry_result['success']:
            position_id = entry_result['position_id']
            logger.info(
                f"✅ [BATCH_ENTRY_COMPLETE] {symbol} {side} | "
                f"持仓ID: {position_id} | "
                f"平均价格: ${entry_result['avg_price']:.4f} | "
                f"总数量: {entry_result['total_quantity']:.2f}"
            )
            return True
        else:
            logger.error(f"❌ [BATCH_ENTRY_FAILED] {symbol} {side} | {entry_result.get('error')}")
            return False

    except Exception as e:
        logger.error(f"❌ [BATCH_ENTRY_ERROR] {symbol} {side} | {e}")
        return False
```

### 方案B: 完全替换（不推荐初期）

直接将所有开仓逻辑改为分批建仓，风险较高。

---

## 智能平仓集成

### 在 `SmartTraderService.__init__()` 中初始化

```python
# 初始化智能平仓优化器
self.smart_exit_optimizer = SmartExitOptimizer(
    db_config=db_config,
    live_engine=self,
    price_service=ws_service
)

# 在服务启动时，为所有开仓持仓启动智能平仓监控
self._start_smart_exit_monitoring()
```

### 实现 `_start_smart_exit_monitoring()` 函数

```python
async def _start_smart_exit_monitoring(self):
    """为所有开仓持仓启动智能平仓监控"""
    try:
        conn = self._get_connection()
        cursor = conn.cursor()

        # 查询所有开仓持仓（有batch_plan的为分批建仓持仓）
        cursor.execute("""
            SELECT id, symbol, position_side
            FROM futures_positions
            WHERE status = 'open'
            AND account_id = %s
            AND batch_plan IS NOT NULL
        """, (self.account_id,))

        positions = cursor.fetchall()
        cursor.close()

        for pos in positions:
            position_id, symbol, side = pos
            await self.smart_exit_optimizer.start_monitoring_position(position_id)
            logger.info(f"✅ 启动智能平仓监控: 持仓{position_id} {symbol} {side}")

        logger.info(f"✅ 智能平仓监控已启动，监控 {len(positions)} 个持仓")

    except Exception as e:
        logger.error(f"❌ 启动智能平仓监控失败: {e}")
```

---

## 测试计划

### 阶段1: 单币种小仓位测试（1-2天）

```yaml
batch_entry:
  enabled: true
  whitelist_symbols: ['BTC/USDT']  # 只测试BTC
  batch_ratios: [0.3, 0.3, 0.4]
  time_window_minutes: 30
```

**测试指标**：
- 是否成功完成3批次建仓？
- 平均入场价格是否优于一次性开仓？
- 30分钟超时是否正常工作？
- 智能平仓是否正常触发？

### 阶段2: 多币种测试（3-5天）

```yaml
batch_entry:
  enabled: true
  whitelist_symbols: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
  batch_ratios: [0.3, 0.3, 0.4]
  time_window_minutes: 30
```

### 阶段3: 全面启用（7天后）

```yaml
batch_entry:
  enabled: true
  whitelist_symbols: []  # 空=全部启用
  batch_ratios: [0.3, 0.3, 0.4]
  time_window_minutes: 30
```

---

## 回滚方案

如果出现问题，立即回滚：

```yaml
batch_entry:
  enabled: false  # 关闭分批建仓
```

重启服务：

```bash
# 停止服务
pkill -f smart_trader_service.py

# 重启服务
nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &
```

---

## 监控指标

### 需要监控的关键指标

1. **建仓效率**
   - 平均建仓用时（是否在30分钟内完成）
   - 各批次价格评分分布
   - 平均入场价格与信号价格的偏差

2. **平仓效果**
   - 各层级平仓触发次数（高盈利/中盈利/低盈利/微亏损）
   - 延长平仓触发次数
   - 平均持仓时间

3. **整体表现**
   - 胜率变化（与一次性开仓对比）
   - 平均盈利变化
   - 最大回撤变化

### 查询SQL

```sql
-- 分批建仓成功率
SELECT
    COUNT(*) as total_batch_positions,
    SUM(CASE WHEN JSON_LENGTH(batch_filled) = 3 THEN 1 ELSE 0 END) as completed_batches,
    SUM(CASE WHEN JSON_LENGTH(batch_filled) = 3 THEN 1 ELSE 0 END) / COUNT(*) * 100 as completion_rate
FROM futures_positions
WHERE batch_plan IS NOT NULL
AND DATE(open_time) = CURDATE();

-- 平均入场价格优势
SELECT
    symbol,
    AVG(avg_entry_price) as avg_batch_entry,
    AVG(entry_price) as avg_direct_entry,
    (AVG(entry_price) - AVG(avg_entry_price)) / AVG(entry_price) * 100 as price_advantage_pct
FROM futures_positions
WHERE DATE(open_time) = CURDATE()
GROUP BY symbol;

-- 平仓层级分布
SELECT
    close_reason,
    COUNT(*) as count,
    AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes
FROM futures_positions
WHERE status = 'closed'
AND close_reason LIKE '%盈利%'
AND DATE(close_time) = CURDATE()
GROUP BY close_reason;
```

---

## 下一步操作

### ✅ 立即执行

1. **数据库迁移**（在服务器上执行）
   ```bash
   mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data < app/database/smart_brain_schema.sql
   ```

2. **验证迁移成功**
   ```bash
   mysql -h 13.212.252.171 -u admin -p'Tonny@1000' binance-data -e "SHOW COLUMNS FROM futures_positions LIKE '%batch%';"
   ```

### ⏳ 等待确认后执行

3. **修改 smart_trader_service.py**（渐进式集成方案）
4. **修改 config.yaml**（添加分批建仓配置，初始设为disabled）
5. **重启服务**
6. **单币种小仓位测试**

---

## 风险提示

⚠️ **注意事项**：

1. **数据库迁移不可逆**：虽然使用了 `ADD COLUMN IF NOT EXISTS`，但执行前建议备份数据库
2. **代码修改需谨慎**：涉及开仓逻辑，建议在非高峰时段进行
3. **小规模测试**：建议从单币种、小仓位开始测试
4. **监控日志**：密切关注日志输出，发现异常立即回滚
5. **保留降级方案**：通过配置开关可以快速切换回原有逻辑

