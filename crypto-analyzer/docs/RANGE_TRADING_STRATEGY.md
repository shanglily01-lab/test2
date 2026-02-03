# 震荡市交易策略文档

> 实施日期: 2026-02-03
> 版本: v1.0
> 状态: 开发中 (Phase 1完成)

---

## 功能概述

在震荡市(横盘)环境下,传统的趋势跟踪策略表现不佳。本系统实现了专门针对震荡市的交易策略,包括:

1. **震荡市自动识别**: 基于Big4信号判断市场状态
2. **模式自动/手动切换**: 支持趋势模式、震荡模式、自动模式
3. **布林带均值回归策略**: 核心震荡市交易逻辑
4. **支撑阻力区间检测**: 识别关键价格区间

---

## 核心策略: 布林带均值回归

### 策略原理

震荡市的特点是价格在一定区间内反复波动,缺乏明确方向。布林带均值回归策略利用这一特性:

- **价格触及布林带下轨** → 超卖 → **做多**,目标回归中轨
- **价格触及布林带上轨** → 超买 → **做空**,目标回归中轨

### 开仓条件

#### 做多信号 (LONG)
```
必要条件:
1. Big4信号 = NEUTRAL (震荡市环境)
2. 价格位置 < 0.15 (接近或突破布林带下轨)
3. RSI < 30 (超卖)

加分项:
4. 成交量放大 (+15分)
5. 价格极度接近下轨 (<0.05, +10分)

基础分: 60
最高分: 85
```

#### 做空信号 (SHORT)
```
必要条件:
1. Big4信号 = NEUTRAL (震荡市环境)
2. 价格位置 > 0.85 (接近或突破布林带上轨)
3. RSI > 70 (超买)

加分项:
4. 成交量放大 (+15分)
5. 价格极度接近上轨 (>0.95, +10分)

基础分: 60
最高分: 85
```

### 止盈止损

| 项目 | 做多 (LONG) | 做空 (SHORT) |
|------|------------|-------------|
| **止盈目标** | 价格回归布林带中轨 | 价格回归布林带中轨 |
| **止损价格** | 入场价 × 0.98 (-2%) | 入场价 × 1.02 (+2%) |
| **最大持仓时间** | 4小时 | 4小时 |

### 平仓条件

#### 做多平仓
- ✅ **止盈**: 价格回归中轨附近 (布林带位置 > 0.45)
- ❌ **止损**: 价格继续下跌,亏损达2%

#### 做空平仓
- ✅ **止盈**: 价格回归中轨附近 (布林带位置 < 0.55)
- ❌ **止损**: 价格继续上涨,亏损达2%

---

## 震荡市识别

### 自动识别条件

系统自动判断是否为震荡市:

```python
震荡市 = (Big4信号 == 'NEUTRAL') AND (Big4强度 < 50)
趋势市 = (Big4信号 in ['BULLISH', 'BEARISH']) AND (Big4强度 >= 60)
```

### 支撑阻力区间检测

**检测逻辑**:
1. 分析最近24小时的K线数据
2. 找出最近20根K线的最高价和最低价
3. 计算区间幅度(3-15%为有效区间)
4. 统计价格触及支撑/阻力的次数(至少2次)

**可信度评分** (0-100):
- 触及次数越多,分数越高 (最高40分)
- 区间幅度适中 3-8% (最高30分)
- 当前价格在区间中部 (最高30分)

**有效区间**: 可信度 ≥ 60

---

## 交易模式

### 1. 趋势模式 (Trend Mode)

**适用场景**: Big4信号明确(BULLISH/BEARISH),强度≥60

**参数设置**:
- 最低信号分数: 65
- 单笔仓位: 5%
- 最大持仓数: 15
- 止盈目标: 8-12%
- 止损幅度: 5-7%
- 最大持仓时间: 24小时

### 2. 震荡模式 (Range Mode)

**适用场景**: Big4信号为NEUTRAL,强度<50

**参数设置**:
- 最低信号分数: **50** ⬇️
- 单笔仓位: **3%** ⬇️
- 最大持仓数: **8** ⬇️
- 止盈目标: **2-4%** ⬇️
- 止损幅度: **2-3%** ⬇️
- 最大持仓时间: **4小时** ⬇️

### 3. 自动模式 (Auto Mode)

**切换逻辑**:
- 当Big4切换到NEUTRAL且强度<50 → 自动切换到震荡模式
- 当Big4切换到BULLISH/BEARISH且强度≥60 → 自动切换到趋势模式
- 切换冷却时间: 120分钟(避免频繁切换)

---

## 数据库设计

### 1. trading_mode_config (模式配置表)

```sql
CREATE TABLE trading_mode_config (
    id INT PRIMARY KEY AUTO_INCREMENT,
    account_id INT,              -- 2=U本位, 3=币本位
    trading_type VARCHAR(50),    -- usdt_futures / coin_futures
    mode_type ENUM('trend', 'range', 'auto'),  -- 当前模式
    is_active BOOLEAN,

    -- 震荡市参数
    range_min_score INT,
    range_position_size DECIMAL(5,2),
    range_max_positions INT,
    range_take_profit DECIMAL(5,2),
    range_stop_loss DECIMAL(5,2),
    range_max_hold_hours INT,

    -- 自动切换
    auto_switch_enabled BOOLEAN,
    last_switch_time DATETIME,
    switch_cooldown_minutes INT
);
```

### 2. range_market_zones (震荡区间表)

```sql
CREATE TABLE range_market_zones (
    id INT PRIMARY KEY AUTO_INCREMENT,
    symbol VARCHAR(20),
    support_price DECIMAL(20,8),      -- 支撑位
    resistance_price DECIMAL(20,8),   -- 阻力位
    range_pct DECIMAL(5,2),           -- 区间幅度%
    touch_count INT,                  -- 触及次数
    confidence_score DECIMAL(5,2),    -- 可信度0-100
    is_active BOOLEAN,
    detected_at TIMESTAMP,
    expires_at TIMESTAMP
);
```

### 3. trading_mode_switch_log (切换日志表)

```sql
CREATE TABLE trading_mode_switch_log (
    id INT PRIMARY KEY AUTO_INCREMENT,
    account_id INT,
    from_mode ENUM('trend', 'range', 'auto'),
    to_mode ENUM('trend', 'range', 'auto'),
    switch_trigger ENUM('manual', 'auto', 'schedule'),
    big4_signal VARCHAR(20),
    big4_strength DECIMAL(5,2),
    reason TEXT,
    switched_by VARCHAR(100),
    switched_at TIMESTAMP
);
```

---

## API接口

### 1. 获取模式状态

```http
GET /api/trading-mode/status/{account_id}/{trading_type}

Response:
{
    "account_id": 2,
    "trading_type": "usdt_futures",
    "mode_type": "trend",
    "is_active": true,
    "range_config": {
        "min_score": 50,
        "position_size": 3.0,
        "max_positions": 8,
        "take_profit": 2.5,
        "stop_loss": 2.0,
        "max_hold_hours": 4
    },
    "auto_switch": {
        "enabled": false,
        "cooldown_minutes": 120
    },
    "last_switch_time": "2026-02-03T10:30:00",
    "updated_by": "web_user"
}
```

### 2. 切换模式

```http
POST /api/trading-mode/switch

Body:
{
    "account_id": 2,
    "trading_type": "usdt_futures",
    "new_mode": "range",
    "trigger": "manual",
    "reason": "手动切换到震荡模式",
    "switched_by": "web_user"
}

Response:
{
    "success": true,
    "message": "成功切换到range模式",
    "mode_type": "range",
    "switched_at": "2026-02-03T11:00:00"
}
```

### 3. 更新参数

```http
POST /api/trading-mode/update-parameters

Body:
{
    "account_id": 2,
    "trading_type": "usdt_futures",
    "range_min_score": 55,
    "range_position_size": 3.5,
    "range_take_profit": 3.0,
    "auto_switch_enabled": true
}

Response:
{
    "success": true,
    "message": "参数更新成功",
    "updated_parameters": {...}
}
```

### 4. 获取震荡区间

```http
GET /api/trading-mode/zones/{symbol}

Response:
{
    "symbol": "BTC/USDT",
    "has_zone": true,
    "zone": {
        "support_price": 95000.00,
        "resistance_price": 98000.00,
        "range_pct": 3.16,
        "touch_count": 5,
        "confidence_score": 75.0,
        "detected_at": "2026-02-03T08:00:00",
        "expires_at": "2026-02-04T08:00:00"
    }
}
```

### 5. 获取市场状态

```http
GET /api/trading-mode/market-status

Response:
{
    "big4_signal": "NEUTRAL",
    "big4_strength": 45.0,
    "is_ranging_market": true,
    "recommended_mode": "range"
}
```

---

## 前端集成 (待实现)

### 模式切换按钮

在 `templates/futures_trading.html` 和 `templates/coin_futures_trading.html` 添加:

```html
<!-- 交易模式选择器 -->
<div class="trading-mode-selector">
    <label><i class="bi bi-arrow-left-right"></i> 交易模式:</label>
    <select id="tradingModeSelect" onchange="switchTradingMode()">
        <option value="trend">趋势模式</option>
        <option value="range">震荡模式</option>
        <option value="auto">自动模式</option>
    </select>
    <span id="currentModeIndicator" class="badge bg-primary">当前: 趋势</span>
</div>

<script>
async function loadTradingMode() {
    const response = await fetch('/api/trading-mode/status/2/usdt_futures');
    const data = await response.json();
    document.getElementById('tradingModeSelect').value = data.mode_type;
    updateModeIndicator(data.mode_type);
}

async function switchTradingMode() {
    const newMode = document.getElementById('tradingModeSelect').value;
    const confirmed = confirm(`确定切换到${getModeLabel(newMode)}吗?`);
    if (!confirmed) return;

    const response = await fetch('/api/trading-mode/switch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            account_id: 2,
            trading_type: 'usdt_futures',
            new_mode: newMode,
            trigger: 'manual',
            reason: '前端手动切换',
            switched_by: 'web_user'
        })
    });

    const result = await response.json();
    if (result.success) {
        alert(result.message);
        updateModeIndicator(newMode);
    }
}

function updateModeIndicator(mode) {
    const indicator = document.getElementById('currentModeIndicator');
    const labels = {
        'trend': '趋势模式',
        'range': '震荡模式',
        'auto': '自动模式'
    };
    const colors = {
        'trend': 'bg-primary',
        'range': 'bg-warning',
        'auto': 'bg-success'
    };
    indicator.textContent = `当前: ${labels[mode]}`;
    indicator.className = `badge ${colors[mode]}`;
}

// 页面加载时初始化
loadTradingMode();
</script>
```

---

## 后端集成 (待实现)

### smart_trader_service.py 集成

```python
from app.strategies.range_market_detector import RangeMarketDetector
from app.strategies.bollinger_mean_reversion import BollingerMeanReversionStrategy
from app.strategies.mode_switcher import TradingModeSwitcher

# 初始化
range_detector = RangeMarketDetector(db_config)
bollinger_strategy = BollingerMeanReversionStrategy(db_config)
mode_switcher = TradingModeSwitcher(db_config)

# 主循环中
def main_trading_loop():
    # 1. 获取Big4信号
    big4_signal, big4_strength = get_latest_big4_signal()

    # 2. 检查是否需要自动切换模式
    suggested_mode = mode_switcher.auto_switch_check(
        account_id=2,
        trading_type='usdt_futures',
        big4_signal=big4_signal,
        big4_strength=big4_strength
    )

    if suggested_mode:
        mode_switcher.switch_mode(
            account_id=2,
            trading_type='usdt_futures',
            new_mode=suggested_mode,
            trigger='auto',
            reason=f'Big4: {big4_signal} 强度:{big4_strength}',
            big4_signal=big4_signal,
            big4_strength=big4_strength
        )

    # 3. 获取当前模式
    current_mode_config = mode_switcher.get_current_mode(2, 'usdt_futures')
    current_mode = current_mode_config['mode_type']

    # 4. 根据模式执行相应策略
    if current_mode == 'range':
        # 震荡模式: 使用布林带均值回归
        for symbol in trading_symbols:
            signal = bollinger_strategy.generate_signal(
                symbol=symbol,
                big4_signal=big4_signal
            )

            if signal and signal['score'] >= current_mode_config['range_min_score']:
                # 开仓
                open_position(
                    symbol=signal['symbol'],
                    side=signal['signal'],
                    strategy='bollinger_mean_reversion',
                    position_size=current_mode_config['range_position_size']
                )

    elif current_mode == 'trend':
        # 趋势模式: 使用原有策略
        run_trend_strategy()
```

---

## 测试计划

### Phase 1: 单元测试 ✅
- [x] 震荡市检测逻辑
- [x] 布林带计算正确性
- [x] RSI计算正确性
- [x] 支撑阻力识别

### Phase 2: 集成测试 (进行中)
- [ ] API接口测试
- [ ] 模式切换流程
- [ ] 数据库读写

### Phase 3: 实盘测试 (待开始)
- [ ] 小资金测试(单次仓位1%)
- [ ] 监控胜率和盈亏比
- [ ] 记录每笔交易详情

### Phase 4: 参数优化 (待开始)
- [ ] 回测不同参数组合
- [ ] 优化止盈止损比例
- [ ] 调整RSI阈值

---

## 风险控制

### 1. 模式切换风险
- ✅ 设置120分钟冷却期,避免频繁切换
- ✅ 记录切换日志,可追溯
- ⚠️ 切换时不强平现有持仓(需要注意)

### 2. 震荡市误判风险
- ✅ 需要Big4信号+强度双重确认
- ✅ 区间可信度评分机制
- ⚠️ 震荡转趋势时可能亏损

### 3. 仓位控制
- ✅ 震荡模式单笔3%,总仓位不超过24%
- ✅ 严格止损2%
- ✅ 最大持仓时间4小时强制平仓

### 4. 假突破防护
- ✅ 需要成交量确认
- ✅ 需要RSI超买超卖确认
- ✅ 价格必须实际触及布林带边界

---

## 性能指标 (待测试)

| 指标 | 目标值 | 当前值 | 状态 |
|------|-------|-------|------|
| 胜率 | ≥50% | - | 待测试 |
| 盈亏比 | ≥1.5 | - | 待测试 |
| 最大回撤 | <15% | - | 待测试 |
| 平均持仓时间 | <3小时 | - | 待测试 |
| 日均交易次数 | 5-10次 | - | 待测试 |

---

## 后续优化方向

### 短期 (1周内)
1. ✅ 完成前端UI集成
2. ✅ smart_trader_service集成策略
3. ✅ 小资金实盘测试

### 中期 (2-4周)
1. 添加支撑阻力区间交易策略
2. 实现网格交易(可选)
3. 优化参数和算法

### 长期 (1-2月)
1. 机器学习优化区间识别
2. 多周期协同分析
3. 动态参数自适应调整

---

**文档版本**: v1.0
**创建日期**: 2026-02-03
**最后更新**: 2026-02-03
**创建人**: Claude Sonnet 4.5
