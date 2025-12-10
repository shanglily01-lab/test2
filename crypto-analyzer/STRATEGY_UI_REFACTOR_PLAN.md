# 策略配置页面重构方案

## 目标
将当前分散的策略配置重新组织成3个清晰的大板块，提升用户体验。

## 三大板块结构

### 📊 板块1：开仓设置（Entry Settings）
**包含内容：**
- 基础配置（策略名称、交易对、交易方向、杠杆）
- 买入信号配置（EMA交叉、成交量过滤）
- 入场条件（趋势确认、信号强度过滤、MA10/EMA10同向过滤）
- 开仓控制（冷却时间、最大持仓数、价格偏离限制）
- 行情自适应（不同行情的开仓策略）

### 🎯 板块2：平仓设置（Exit Settings）
**包含内容：**
- 基础止损止盈（固定百分比）
- 趋势反转退出（MA反转、EMA过弱、早期止损）
- 动态止盈（根据趋势强度调整）
- 智能止损：
  - ATR动态止损
  - 移动止损（追踪止损）
  - 时间衰减止损
  - EMA支撑止损
  - **连续K线止损**（新增）
- 连续下跌K线止盈
- 价格穿越EMA平仓
- 平仓信号周期

### ⚙️ 板块3：订单管理（Order Management）
**包含内容：**
- 限价单设置（启用/禁用、价格偏离、超时转市价）
- 趋势转向时取消限价单
- 实盘同步配置（syncLive、数量百分比、最大保证金）
- 费率设置
- Telegram通知配置

## HTML结构示例

```html
<!-- ============ 板块1：开仓设置 ============ -->
<div class="major-block">
    <div class="major-block-header">
        <i class="bi bi-door-open"></i>
        开仓设置
        <span class="major-block-description">配置入场信号、条件和控制策略</span>
    </div>

    <!-- 基础配置 -->
    <div class="form-section">...</div>

    <!-- 买入信号 -->
    <div class="form-section">...</div>

    <!-- 入场条件 -->
    <div class="form-section">...</div>

    <!-- ... -->
</div>

<!-- ============ 板块2：平仓设置 ============ -->
<div class="major-block">
    <div class="major-block-header">
        <i class="bi bi-door-closed"></i>
        平仓设置
        <span class="major-block-description">配置止损、止盈和出场策略</span>
    </div>

    <!-- 基础止损止盈 -->
    <div class="form-section">...</div>

    <!-- 智能止损 -->
    <div class="form-section">...</div>

    <!-- ... -->
</div>

<!-- ============ 板块3：订单管理 ============ -->
<div class="major-block">
    <div class="major-block-header">
        <i class="bi bi-list-check"></i>
        订单管理
        <span class="major-block-description">配置订单执行、实盘同步和通知</span>
    </div>

    <!-- 限价单设置 -->
    <div class="form-section">...</div>

    <!-- 实盘同步 -->
    <div class="form-section">...</div>

    <!-- ... -->
</div>
```

## CSS样式（已添加）

```css
/* 大板块容器样式 */
.major-block {
    background: linear-gradient(135deg, rgba(43, 111, 237, 0.03), rgba(124, 58, 237, 0.03));
    border: 2px solid var(--border-default);
    border-radius: var(--radius-lg);
    padding: var(--spacing-xl);
    margin-bottom: var(--spacing-2xl);
    position: relative;
}
.major-block-header {
    font-size: 18px;
    font-weight: 700;
    color: var(--primary-blue);
    margin-bottom: var(--spacing-xl);
    padding-bottom: var(--spacing-md);
    border-bottom: 2px solid var(--primary-blue);
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
}
```

## 实施步骤

### 自动化重构（推荐）
由于文件有3000+行，建议使用脚本自动重构：

1. 备份当前文件
2. 运行重构脚本（待实现）
3. 测试所有功能
4. 部署到生产

### 手动重构
如果需要手动调整，按以下步骤：

1. 在 line 553 开始的第一个 form-section 之前添加：
   ```html
   <!-- ============ 板块1：开仓设置 ============ -->
   <div class="major-block">
       <div class="major-block-header">
           <i class="bi bi-door-open"></i>
           开仓设置
           <span class="major-block-description">配置入场信号、条件和控制策略</span>
       </div>
   ```

2. 在所有开仓相关的配置结束后（约 line 1200）添加：
   ```html
   </div> <!-- 结束板块1 -->
   ```

3. 在止损止盈配置开始前添加板块2的开始标记
4. 在限价单配置开始前添加板块3的开始标记
5. 在表单结束前添加最后一个 `</div>`

## 预期效果

重构后，用户将看到：
1. ✅ 清晰的三大板块，一目了然
2. ✅ 每个板块有明显的视觉分隔
3. ✅ 板块标题说明其用途
4. ✅ 逻辑相关的配置项聚集在一起
5. ✅ 减少用户的认知负担

## 注意事项

- 不改变任何JavaScript逻辑
- 不改变表单字段的name/id
- 只调整HTML结构和添加容器
- 保持所有现有功能不变
