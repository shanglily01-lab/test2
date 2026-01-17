# 前端反向操作策略集成指南

## 需要修改的文件

`templates/trading_strategies.html`

## 修改位置

在**第650行**（V3策略说明 `</div>` 之后）插入反向操作策略配置UI

## 插入的HTML代码

代码已保存在: `contrarian_ui_snippet.html`

将该文件的内容插入到：
```html
<!-- V3策略说明 -->
<div id="v3Description"...>
    ...
</div>

<!-- 在这里插入 contrarian_ui_snippet.html 的内容 -->

<!-- 买入条件（全宽显示） -->
```

## 需要添加的JavaScript函数

在 `<script>` 标签内添加以下函数：

### 1. 切换反向操作配置面板

```javascript
function toggleContrarianConfig() {
    const enabled = document.getElementById('contrarianEnabled').checked;
    const panel = document.getElementById('contrarianConfigPanel');
    if (panel) {
        panel.style.display = enabled ? 'block' : 'none';
    }
}
```

### 2. 切换市场检测配置

```javascript
function toggleMarketDetectionConfig() {
    const autoMode = document.getElementById('marketRegimeAuto').checked;
    const panel = document.getElementById('marketDetectionPanel');
    if (panel) {
        panel.style.display = autoMode ? 'block' : 'none';
    }
}
```

### 3. 修改表单提交（在 `collectFormData` 函数中）

在 `collectFormData` 函数的返回对象中添加：

```javascript
const formData = {
    // ...existing fields...

    // 反向操作策略配置 ⚡ 新增
    contrarianEnabled: document.getElementById('contrarianEnabled') ?
        document.getElementById('contrarianEnabled').checked : false,
    marketRegime: document.querySelector('input[name="marketRegime"]:checked') ?
        document.querySelector('input[name="marketRegime"]:checked').value : 'auto_detect',

    // 市场检测参数
    marketDetection: {
        lookbackHours: document.getElementById('lookbackHours') ?
            parseInt(document.getElementById('lookbackHours').value) : 24,
        minTrades: document.getElementById('minTrades') ?
            parseInt(document.getElementById('minTrades').value) : 10
    },

    // 反向操作风险参数
    contrarianRisk: {
        stopLoss: document.getElementById('contrarianStopLoss') ?
            parseFloat(document.getElementById('contrarianStopLoss').value) : 1.5,
        takeProfit: document.getElementById('contrarianTakeProfit') ?
            parseFloat(document.getElementById('contrarianTakeProfit').value) : 1.0,
        limitOrderOffset: document.getElementById('contrarianLimitOffset') ?
            parseFloat(document.getElementById('contrarianLimitOffset').value) : 0.5
    }
};
```

### 4. 修改编辑策略回填（在 `editStrategy` 函数中）

添加反向操作配置的回填：

```javascript
function editStrategy(id) {
    // ...existing code...

    // 反向操作策略配置 ⚡ 新增
    if (strategy.contrari anEnabled) {
        const contrarianEnabled = document.getElementById('contrarianEnabled');
        if (contrarianEnabled) {
            contrarianEnabled.checked = true;
            toggleContrarianConfig();
        }
    }

    // 市场环境模式
    if (strategy.marketRegime) {
        const marketRegimeRadio = document.querySelector(
            `input[name="marketRegime"][value="${strategy.marketRegime}"]`
        );
        if (marketRegimeRadio) {
            marketRegimeRadio.checked = true;
            toggleMarketDetectionConfig();
        }
    }

    // 市场检测参数
    if (strategy.marketDetection) {
        const lookbackHours = document.getElementById('lookbackHours');
        const minTrades = document.getElementById('minTrades');
        if (lookbackHours) lookbackHours.value = strategy.marketDetection.lookbackHours || 24;
        if (minTrades) minTrades.value = strategy.marketDetection.minTrades || 10;
    }

    // 反向操作风险参数
    if (strategy.contrarianRisk) {
        const stopLoss = document.getElementById('contrarianStopLoss');
        const takeProfit = document.getElementById('contrarianTakeProfit');
        const limitOffset = document.getElementById('contrarianLimitOffset');

        if (stopLoss) stopLoss.value = strategy.contrarianRisk.stopLoss || 1.5;
        if (takeProfit) takeProfit.value = strategy.contrarianRisk.takeProfit || 1.0;
        if (limitOffset) limitOffset.value = strategy.contrarianRisk.limitOrderOffset || 0.5;
    }

    // ...existing code...
}
```

### 5. 清空表单时重置反向操作配置（在 `closeStrategyModal` 或 `clearForm` 中）

```javascript
function clearForm() {
    // ...existing code...

    // 重置反向操作配置
    const contrarianEnabled = document.getElementById('contrarianEnabled');
    if (contrarianEnabled) {
        contrarianEnabled.checked = false;
        toggleContrarianConfig();
    }

    // 重置为自动检测模式
    const marketRegimeAuto = document.getElementById('marketRegimeAuto');
    if (marketRegimeAuto) {
        marketRegimeAuto.checked = true;
        toggleMarketDetectionConfig();
    }

    // 重置默认参数
    document.getElementById('lookbackHours').value = 24;
    document.getElementById('minTrades').value = 10;
    document.getElementById('contrarianStopLoss').value = 1.5;
    document.getElementById('contrarianTakeProfit').value = 1.0;
    document.getElementById('contrarianLimitOffset').value = 0.5;
}
```

## 需要添加的CSS样式

在 `<style>` 标签中添加：

```css
/* 开关按钮样式 */
.switch {
    position: relative;
    display: inline-block;
    width: 48px;
    height: 24px;
}

.switch input {
    opacity: 0;
    width: 0;
    height: 0;
}

.slider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: var(--bg-tertiary);
    transition: .4s;
    border: 1px solid var(--border-default);
}

.slider:before {
    position: absolute;
    content: "";
    height: 16px;
    width: 16px;
    left: 3px;
    bottom: 3px;
    background-color: white;
    transition: .4s;
}

input:checked + .slider {
    background-color: var(--primary-blue);
    border-color: var(--primary-blue);
}

input:checked + .slider:before {
    transform: translateX(24px);
}

.slider.round {
    border-radius: 24px;
}

.slider.round:before {
    border-radius: 50%;
}
```

## 测试步骤

1. 打开策略配置页面
2. 选择V3策略
3. 应该看到"🔄 反向操作策略（震荡市神器）"配置区域
4. 打开开关，验证详细配置面板展开
5. 切换市场环境模式，验证参数显示/隐藏
6. 保存策略，检查数据是否正确保存到数据库
7. 编辑已有策略，验证配置正确回填

## 完成后的效果

用户可以在策略配置页面：
- ✅ 启用/禁用反向操作策略
- ✅ 选择市场环境模式（自动检测/强制反向/禁用反向）
- ✅ 配置市场检测参数
- ✅ 配置反向操作风险参数
- ✅ 看到数据验证结果（94.7% vs 5.3%胜率）

---

*如需帮助集成，请告知*
