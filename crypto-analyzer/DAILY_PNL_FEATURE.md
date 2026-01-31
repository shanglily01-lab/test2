# 每日盈亏统计功能 - 实施文档

**功能**: 在复盘24H页面添加"每日盈亏统计"Tab
**实施时间**: 2026-01-31
**状态**: ✅ 完成

---

## 功能概述

在 `/futures-review` 复盘页面的"机会分析"Tab后面添加新Tab"每日盈亏统计"，提供：

1. **月份选择器** - 选择要查看的月份（最近12个月）
2. **月度统计概览** - 4个关键指标卡片
3. **盈亏趋势图** - 可视化每日盈亏变化
4. **每日盈亏明细表** - 详细的每日统计数据

---

## 修改内容

### 1. 前端模板 (templates/futures_review.html)

#### 1.1 添加Tab按钮

**位置**: 第267-270行

```html
<button class="tab-btn" onclick="switchTab('daily-pnl')">
    <i class="bi bi-calendar3"></i> 每日盈亏统计
</button>
```

#### 1.2 添加Tab内容

**位置**: 第583-677行

包含：
- 月份选择器（下拉框）
- 4个统计卡片（本月总盈亏、盈利天数、日均盈亏、最大单日盈亏）
- Chart.js盈亏趋势图
- 每日盈亏明细表（10列数据）

#### 1.3 添加JavaScript功能

**位置**: 第1399-1600行

新增函数：
- `initMonthSelector()` - 初始化月份选择器
- `loadDailyPnl()` - 加载每日盈亏数据
- `renderDailyPnlStats()` - 渲染统计卡片
- `renderDailyPnlChart()` - 渲染Chart.js图表
- `renderDailyPnlTable()` - 渲染明细表
- `filterDailyPnl()` - 过滤（全部/盈利日/亏损日）
- `showDailyPnlError()` - 显示错误信息

#### 1.4 添加Chart.js库

**位置**: 第8行

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
```

#### 1.5 修改switchTab函数

**位置**: 第785-798行

添加Tab切换时自动加载数据的逻辑。

---

### 2. 后端API (app/api/futures_review_api.py)

#### 2.1 新增API端点

**位置**: 文件末尾（1863-2000行）

```python
@router.get('/daily-pnl')
async def get_daily_pnl_stats(
    month: str = Query(..., description="月份，格式: YYYY-MM"),
    margin_type: str = Query('usdt', description="合约类型: usdt=U本位, coin=币本位")
):
    """获取每日盈亏统计"""
```

**功能**:
1. 验证月份格式（YYYY-MM）
2. 计算月份的开始和结束日期
3. 根据margin_type确定account_id（U本位=2，币本位=3）
4. 查询每日盈亏数据（GROUP BY DATE(close_time)）
5. 计算每日指标：
   - 交易笔数
   - 盈利/亏损笔数
   - 胜率
   - 盈亏金额
   - 盈亏比
   - ROI
6. 计算月度统计：
   - 总盈亏
   - 盈利天数
   - 日均盈亏
   - 最大单日盈亏

**SQL查询**:
```sql
SELECT
    DATE(close_time) as trade_date,
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as profit_trades,
    SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as loss_trades,
    SUM(realized_pnl) as total_pnl,
    SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) as profit_amount,
    SUM(CASE WHEN realized_pnl <= 0 THEN realized_pnl ELSE 0 END) as loss_amount,
    SUM(margin) as total_margin,
    AVG(unrealized_pnl_pct) as avg_pnl_pct
FROM futures_positions
WHERE status = 'closed'
AND account_id = %s
AND DATE(close_time) >= %s
AND DATE(close_time) <= %s
GROUP BY DATE(close_time)
ORDER BY trade_date ASC
```

---

## 数据展示

### 月度统计概览（4个卡片）

1. **本月总盈亏**
   - 值: 总盈亏金额（颜色：盈利=绿色，亏损=红色）
   - 说明: 交易笔数

2. **盈利天数**
   - 值: 盈利天数
   - 说明: 盈利占比 (盈利天数/总天数)
   - 颜色: ≥50%=绿色，<50%=红色

3. **日均盈亏**
   - 值: 平均每日盈亏
   - 说明: 基于N个交易日

4. **最大单日盈亏**
   - 值: 最大单日盈亏（绝对值）
   - 说明: 发生日期

### 盈亏趋势图（Chart.js柱状图）

- **X轴**: 日期（MM-DD）
- **Y轴**: 盈亏金额（$）
- **颜色**: 盈利=绿色，亏损=红色
- **Tooltip**: 显示盈亏、交易笔数、胜率

### 每日盈亏明细表（10列）

| 列名 | 说明 | 格式 |
|------|------|------|
| 日期 | 交易日期 | YYYY-MM-DD |
| 交易笔数 | 当日总交易数 | 数字 |
| 盈利笔数 | 盈利交易数 | 绿色文字 |
| 亏损笔数 | 亏损交易数 | 红色文字 |
| 胜率 | 盈利笔数/总笔数 | Badge（≥50%绿色，<50%红色） |
| 总盈亏 | 当日总盈亏 | 加粗，盈利绿色，亏损红色 |
| 盈利金额 | 盈利交易总额 | 绿色 |
| 亏损金额 | 亏损交易总额（绝对值） | 红色 |
| 盈亏比 | 盈利金额/亏损金额 | 数字 |
| ROI | 盈亏/总保证金 | 百分比 |

---

## 功能特性

### 月份选择
- 下拉框显示最近12个月
- 默认选中当前月份
- 选择月份后自动刷新数据

### 数据过滤
- **全部**: 显示所有日期
- **盈利日**: 只显示盈利的日期
- **亏损日**: 只显示亏损的日期

### 合约类型切换
- 切换U本位/币本位时，自动刷新每日盈亏数据
- 使用全局变量 `currentMarginType`

### 加载状态
- 显示Loading动画
- 数据加载完成后显示内容
- 错误时显示错误提示

---

## API接口

### 请求

```
GET /api/futures/review/daily-pnl
```

**参数**:
- `month`: 月份，格式 YYYY-MM（必填）
- `margin_type`: 合约类型，usdt或coin（默认usdt）

**示例**:
```
/api/futures/review/daily-pnl?month=2026-01&margin_type=usdt
```

### 响应

```json
{
  "success": true,
  "data": {
    "summary": {
      "total_pnl": 1234.56,
      "total_trades": 150,
      "profit_days": 18,
      "loss_days": 7,
      "total_days": 25,
      "avg_daily_pnl": 49.38,
      "max_daily_pnl": 234.56,
      "max_daily_pnl_date": "2026-01-15",
      "month": "2026年1月"
    },
    "daily_data": [
      {
        "date": "2026-01-01",
        "total_trades": 8,
        "profit_trades": 5,
        "loss_trades": 3,
        "win_rate": 62.5,
        "total_pnl": 45.23,
        "profit_amount": 78.50,
        "loss_amount": -33.27,
        "profit_loss_ratio": 2.36,
        "roi": 1.13
      },
      ...
    ]
  }
}
```

---

## 测试

### 测试脚本

创建了 `test_daily_pnl_api.py` 用于测试API：

```bash
python test_daily_pnl_api.py
```

### 手动测试

1. 启动Web服务
2. 访问 http://localhost:9020/futures-review
3. 切换到"每日盈亏统计"Tab
4. 选择不同月份
5. 使用过滤功能（全部/盈利日/亏损日）
6. 切换合约类型（U本位/币本位）

---

## 文件修改清单

### 修改的文件

1. ✅ `templates/futures_review.html`
   - 添加Tab按钮
   - 添加Tab内容（HTML）
   - 添加JavaScript函数
   - 添加Chart.js库引用

2. ✅ `app/api/futures_review_api.py`
   - 添加 `/daily-pnl` API端点
   - 实现每日盈亏统计逻辑

### 新增的文件

1. ✅ `test_daily_pnl_api.py` - API测试脚本
2. ✅ `DAILY_PNL_FEATURE.md` - 功能文档（本文件）

---

## 部署

### 提交到Git

```bash
cd d:/test2/crypto-analyzer

git add templates/futures_review.html app/api/futures_review_api.py
git commit -m "feat: 添加每日盈亏统计Tab

- 在复盘24H页面添加每日盈亏统计Tab
- 支持月份选择（最近12个月）
- 月度统计概览（4个指标卡片）
- Chart.js盈亏趋势图
- 每日盈亏明细表（10列数据）
- 支持数据过滤（全部/盈利日/亏损日）
- 新增 /api/futures/review/daily-pnl 接口

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"

git push origin master
```

### 部署到服务器

```bash
# SSH到服务器
ssh user@your-server
cd /path/to/crypto-analyzer

# 拉取最新代码
git pull origin master

# 重启Web服务（如果使用PM2）
pm2 restart web

# 或者重启uvicorn服务
# 具体命令取决于部署方式
```

---

## 注意事项

1. **数据库依赖**
   - 依赖 `futures_positions` 表
   - 需要 `close_time` 和 `realized_pnl` 字段
   - Account ID: U本位=2，币本位=3

2. **性能考虑**
   - 按月查询，数据量可控
   - 使用 GROUP BY DATE(close_time) 聚合
   - 建议在 close_time 字段上有索引

3. **Chart.js版本**
   - 使用 Chart.js 4.4.0
   - CDN加载，需要网络连接

4. **浏览器兼容性**
   - 需要支持ES6语法
   - 需要支持async/await
   - 建议使用现代浏览器

---

## 预期效果

1. **用户体验**
   - 一目了然的月度盈亏情况
   - 直观的趋势图展示
   - 详细的每日数据分析

2. **数据洞察**
   - 识别盈利/亏损模式
   - 发现异常交易日
   - 评估交易稳定性

3. **决策支持**
   - 根据历史表现调整策略
   - 识别高盈利/高亏损日期模式
   - 优化交易频率和仓位

---

**实施人员**: Claude Code
**实施时间**: 2026-01-31
**状态**: ✅ 完成，待部署
