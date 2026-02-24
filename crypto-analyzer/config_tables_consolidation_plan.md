# 配置表整合方案

## 当前状态分析

### 1. `system_settings` 表
**用途**: 系统级配置存储
**结构**:
```sql
- setting_key VARCHAR - 配置键
- setting_value VARCHAR - 配置值
- description TEXT - 描述
- updated_by VARCHAR - 更新人
- updated_at DATETIME - 更新时间
```

**当前配置项**:
- `big4_filter_enabled`: Big4过滤器开关
- `batch_entry_strategy`: 分批建仓策略
- `allow_long`: 允许做多
- `allow_short`: 允许做空
- `u_futures_trading_enabled`: U本位开仓开关 ✅
- `coin_futures_trading_enabled`: 币本位开仓开关 ✅
- `live_trading_enabled`: 实盘交易开关

### 2. `trading_control` 表
**用途**: 交易开关控制（**已废弃**）
**结构**:
```sql
- account_id INT - 账户ID (2=U本位, 3=币本位)
- trading_type VARCHAR - 交易类型 (usdt_futures/coin_futures)
- trading_enabled BOOLEAN - 交易开关
- updated_by VARCHAR - 更新人
- updated_at DATETIME - 更新时间
```

**问题**:
- 功能与 `system_settings` 重复
- 代码已改为查询 `system_settings` (提交 56ffd7d)
- 但 API 代码 `app/api/trading_control_api.py` 仍在使用这个表

### 3. `system_status`
**结论**: 不是数据库表，是代码中的方法名，无需处理

---

## 整合方案

### 阶段1: 清理废弃的 `trading_control` 表

#### 步骤1: 删除废弃的API
```bash
rm app/api/trading_control_api.py
```

#### 步骤2: 确认 `system_settings` 已有必要配置
运行以下SQL确认：
```sql
SELECT setting_key, setting_value
FROM system_settings
WHERE setting_key IN (
    'u_futures_trading_enabled',
    'coin_futures_trading_enabled'
);
```

如果没有，插入：
```sql
INSERT INTO system_settings (setting_key, setting_value, description) VALUES
('u_futures_trading_enabled', '1', 'U本位合约开仓开关 (1=启用, 0=禁用)'),
('coin_futures_trading_enabled', '1', '币本位合约开仓开关 (1=启用, 0=禁用)')
ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
```

#### 步骤3: 删除 `trading_control` 表
```sql
DROP TABLE IF EXISTS trading_control;
```

### 阶段2: 统一配置管理

#### 标准化配置命名规范
所有配置键使用下划线命名，遵循格式：`<服务>_<功能>_<开关>`

**建议的配置结构**:
```sql
-- 交易开关
u_futures_trading_enabled           -- U本位开仓开关
coin_futures_trading_enabled        -- 币本位开仓开关
live_trading_enabled                -- 实盘交易总开关

-- 交易方向
allow_long                          -- 允许做多
allow_short                         -- 允许做空

-- 策略开关
big4_filter_enabled                 -- Big4过滤器
batch_entry_strategy                -- 分批建仓策略 (kline_pullback/price_percentile)

-- 风控参数
max_positions                       -- 最大持仓数
position_size_usdt                  -- 默认仓位大小
```

#### 统一配置读取方法
在 `app/services/system_settings_loader.py` 中提供统一接口：
```python
def get_setting(key: str, default: any = None) -> any:
    """统一的配置读取方法"""
    # ... 从 system_settings 表读取
```

### 阶段3: 前端配置页面统一

#### 当前问题
- `/api/system/settings` - 管理 system_settings
- `/api/trading-control` - 管理 trading_control (废弃)

#### 整合后
只保留一个配置API：
- `GET /api/system/settings` - 获取所有配置
- `PUT /api/system/settings/{key}` - 更新单个配置
- `POST /api/system/settings/batch` - 批量更新配置

---

## 迁移脚本

### migration_consolidate_config_tables.sql
```sql
-- ==========================================
-- 配置表整合迁移脚本
-- ==========================================

-- 1. 确保 system_settings 有必要的配置
INSERT INTO system_settings (setting_key, setting_value, description, updated_by)
SELECT
    CASE
        WHEN trading_type = 'usdt_futures' THEN 'u_futures_trading_enabled'
        WHEN trading_type = 'coin_futures' THEN 'coin_futures_trading_enabled'
    END as setting_key,
    CAST(trading_enabled AS CHAR) as setting_value,
    CONCAT(trading_type, ' 交易开关 (来自 trading_control)') as description,
    COALESCE(updated_by, 'migration') as updated_by
FROM trading_control
WHERE trading_type IN ('usdt_futures', 'coin_futures')
ON DUPLICATE KEY UPDATE
    setting_value = VALUES(setting_value),
    updated_by = VALUES(updated_by),
    updated_at = NOW();

-- 2. 备份 trading_control 表（以防万一）
CREATE TABLE IF NOT EXISTS trading_control_backup AS SELECT * FROM trading_control;

-- 3. 删除 trading_control 表
DROP TABLE IF EXISTS trading_control;

-- 4. 显示结果
SELECT '配置表整合完成！' as status;
SELECT setting_key, setting_value, description
FROM system_settings
WHERE setting_key LIKE '%trading_enabled%';
```

---

## 执行清单

- [ ] 1. 备份数据库
- [ ] 2. 确认当前配置值
- [ ] 3. 运行迁移脚本
- [ ] 4. 删除 `app/api/trading_control_api.py`
- [ ] 5. 测试配置读取是否正常
- [ ] 6. 重启服务验证
- [ ] 7. 删除备份表 `trading_control_backup`

---

## 预期效果

✅ **整合后的优势**:
1. 只有一个配置表 `system_settings`
2. 配置管理统一、清晰
3. 减少代码复杂度
4. 避免配置不一致的问题
5. API接口更简洁

✅ **风险控制**:
1. 已备份原表数据
2. 配置迁移有数据验证
3. 代码已适配新表（提交 56ffd7d）
