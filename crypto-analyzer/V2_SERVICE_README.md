# V2交易服务使用说明

## 📋 概述

`smart_trader_service_v2.py` 是专门运行V2信号的独立交易服务,支持与V3服务并行运行。

### 核心特性

1. **V2专用评分系统**: 使用1H K线多维度评分 + 72H位置评分
2. **并行运行支持**: 同一交易对同方向可以有V2和V3各一个持仓
3. **独立日志**: 所有日志带有`V2-`前缀,便于区分
4. **共享止损止盈**: 使用与主服务相同的止损止盈机制

---

## 🔧 关键逻辑改动

### 1. 持仓检查逻辑 (`has_position`)

```python
def has_position(self, symbol: str, side: str, signal_version: str = 'v2'):
    """
    🔥 关键逻辑: 只检查V2版本的持仓,不检查V3
    这样同一交易对同方向可以有V2和V3各一个持仓
    """
    cursor.execute("""
        SELECT COUNT(*) FROM futures_positions
        WHERE symbol = %s AND position_side = %s AND signal_version = %s
        AND status IN ('open', 'building') AND account_id = %s
    """, (symbol, side, signal_version, self.account_id))
```

**对比原逻辑**:
- 原服务: `signal_version`为None时检查所有版本
- V2服务: **强制只检查V2版本** (`signal_version='v2'`)

### 2. 开仓记录标记

```python
cursor.execute("""
    INSERT INTO futures_positions
    (..., signal_version, entry_score, signal_components)
    VALUES (..., %s, %s, %s)
""", (..., 'v2', score, str(score_details)))
```

所有V2开的仓都会标记为 `signal_version='v2'`

---

## 🚀 启动方式

### 方式1: 直接运行

```bash
cd /path/to/crypto-analyzer
python3 smart_trader_service_v2.py
```

### 方式2: 后台运行 (推荐)

```bash
# 启动
bash start_v2_service.sh

# 停止
bash stop_v2_service.sh

# 查看日志
tail -f logs/smart_trader_v2_$(date +%Y-%m-%d).log
```

### 方式3: Systemd服务

```bash
# 1. 修改 systemd/smart-trader-v2.service
#    - 替换 your_username 为实际用户名
#    - 替换 /path/to/crypto-analyzer 为实际路径

# 2. 安装服务
sudo cp systemd/smart-trader-v2.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3. 启动服务
sudo systemctl start smart-trader-v2
sudo systemctl enable smart-trader-v2  # 开机自启

# 4. 查看状态
sudo systemctl status smart-trader-v2

# 5. 查看日志
sudo journalctl -u smart-trader-v2 -f
```

---

## 📊 并行运行示例

### 场景: 同时运行V2和V3服务

**服务1**: `smart_trader_service.py` (V3模式)
- 环境变量: `USE_V3_MODE=true`
- 开仓条件: V3评分 ≥ threshold
- 标记: `signal_version='v3'`

**服务2**: `smart_trader_service_v2.py` (V2模式)
- 独立服务,无需环境变量
- 开仓条件: V2评分 ≥ threshold
- 标记: `signal_version='v2'`

### 持仓示例

| 交易对 | 方向 | 版本 | 状态 | 说明 |
|--------|------|------|------|------|
| BTC/USDT | LONG | v2 | open | V2服务开的仓 |
| BTC/USDT | LONG | v3 | open | V3服务开的仓 |
| ETH/USDT | SHORT | v2 | open | V2服务开的仓 |
| ETH/USDT | SHORT | v3 | open | V3服务开的仓 |

✅ **允许**: 同一交易对同方向,V2和V3各一个持仓
❌ **不允许**: 同一交易对同方向,V2开两个持仓

---

## 🎯 V2评分系统

### 评分维度 (参考 `signal_scorer_v2.py`)

1. **趋势强度** (30分)
   - 多周期MA排列
   - 趋势一致性

2. **位置评分** (20分)
   - 72H价格位置
   - 低位做多,高位做空

3. **动量指标** (20分)
   - RSI
   - MACD

4. **成交量** (15分)
   - 成交量变化
   - 量价配合

5. **波动率** (15分)
   - ATR
   - 布林带宽度

### 开仓阈值

V2评分阈值在 `SignalScorerV2` 中配置:
```python
self.min_score_to_trade = 60  # 建议60分以上开仓
```

---

## 🔍 监控和诊断

### 查看V2持仓

```sql
SELECT
    symbol, position_side, entry_score,
    status, open_time, signal_components
FROM futures_positions
WHERE signal_version = 'v2'
AND status IN ('open', 'building')
ORDER BY open_time DESC;
```

### 查看V2交易统计

```sql
SELECT
    DATE(close_time) as date,
    COUNT(*) as trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    ROUND(AVG(realized_pnl), 2) as avg_pnl,
    ROUND(SUM(realized_pnl), 2) as total_pnl
FROM futures_positions
WHERE signal_version = 'v2'
AND status = 'closed'
AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY DATE(close_time)
ORDER BY date DESC;
```

### 对比V2 vs V3表现

```sql
SELECT
    signal_version,
    COUNT(*) as trades,
    ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100, 2) as win_rate,
    ROUND(AVG(realized_pnl), 2) as avg_pnl,
    ROUND(SUM(realized_pnl), 2) as total_pnl
FROM futures_positions
WHERE signal_version IN ('v2', 'v3')
AND status = 'closed'
AND close_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
GROUP BY signal_version;
```

---

## ⚠️ 注意事项

### 1. 持仓管理

V2服务**只负责开仓**,持仓管理(止损止盈)由主服务 `smart_trader_service.py` 统一处理:
- 快速止损 (10分钟-2.5%, 30分钟-3.5%, 60分钟-4%)
- 固定止损 (-3%)
- 移动止盈 (1.5%回撤)

**因此主服务必须保持运行**,否则V2开的仓没有止损保护!

### 2. 资源占用

同时运行V2和V3服务会增加:
- CPU使用 (两个服务并行扫描)
- 数据库查询 (两倍频率)
- 持仓数量 (最多2倍)

建议配置:
- 服务器内存: ≥4GB
- CPU核心: ≥2核
- 最大持仓数量: 根据资金量调整

### 3. 交易频率控制

如果V2+V3同时运行导致交易过于频繁:

**方案1**: 调整扫描间隔
```python
# V2服务
self.scan_interval = 600  # 10分钟扫描一次

# V3服务
self.scan_interval = 300  # 5分钟扫描一次
```

**方案2**: 提高评分阈值
```python
# V2
self.scorer_v2.min_score_to_trade = 70

# V3
self.scorer_v3.min_score_long = 30
self.scorer_v3.min_score_short = 24
```

---

## 🛠️ 故障排查

### 问题1: V2服务无法开仓

**检查清单**:
1. ✅ 交易开关是否启用 (`trading_control.trading_enabled`)
2. ✅ V2评分是否达到阈值
3. ✅ 是否已有V2持仓 (同交易对同方向只能一个V2仓)
4. ✅ 交易对是否被L3禁止

**调试命令**:
```bash
# 查看V2服务日志
tail -f logs/smart_trader_v2_$(date +%Y-%m-%d).log | grep "V2-SIGNAL"
```

### 问题2: V2和V3开了重复的仓

这是**预期行为**! V2和V3并行测试,同一交易对同方向允许各开一单。

如果要避免:
- 方案1: 只运行V2或V3其中一个
- 方案2: 在V2服务的`has_position`中改为检查所有版本

### 问题3: V2开的仓没有止损

**原因**: 主服务 `smart_trader_service.py` 未运行

**解决**: 确保主服务一直运行,它负责所有持仓的止损止盈管理

---

## 📈 性能优化建议

### 1. 错峰扫描

V2和V3服务错开扫描时间:
```python
# V2服务启动时等待2分钟
time.sleep(120)
```

### 2. 数据库索引

确保以下索引存在:
```sql
CREATE INDEX idx_signal_version ON futures_positions(signal_version, status, symbol, position_side);
```

### 3. 日志级别

生产环境建议调整日志级别:
```python
logger.add(sys.stderr, level="WARNING")  # 只输出警告和错误
```

---

## 📝 版本记录

### v1.0 (2026-02-09)
- ✅ 初始版本
- ✅ 支持V2评分系统
- ✅ 允许V2+V3并行运行
- ✅ 独立日志和监控

---

**作者**: 超级大脑优化团队
**最后更新**: 2026-02-09
**状态**: ✅ 已测试,可用于生产环境
