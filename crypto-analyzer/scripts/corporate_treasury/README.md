# 企业金库监控使用指南

## 📋 目录
1. [快速开始](#快速开始)
2. [批量导入](#批量导入)
3. [单条录入](#单条录入)
4. [查询分析](#查询分析)
5. [数据来源](#数据来源)

---

## 🚀 快速开始

### 1. 初始化数据库

```bash
mysql -uroot -p"Tonny@1000" binance-data < app/database/corporate_treasury_schema.sql
```

这会创建：
- ✅ 4个数据表（公司、购买、融资、股价）
- ✅ 1个汇总视图
- ✅ 预设2家公司（Strategy Inc, BitMine）

---

## 📦 批量导入

### 推荐方式：从 Bitcoin Treasuries 导入

#### 步骤1：访问网站
打开 https://bitcointreasuries.net/

#### 步骤2：复制数据
**方法A：复制整个页面的公司列表**
1. 点击页面，按 `Ctrl+A` 全选
2. 按 `Ctrl+C` 复制
3. 或者手动选择需要的公司行（从排名到持仓量）

**方法B：使用提供的模板**
打开 `import_template.txt`，复制内容

#### 步骤3：运行导入工具
```bash
cd E:\mywork\crypto-analyzer
python scripts\corporate_treasury\batch_import.py
```

#### 步骤4：按提示操作
```
请输入数据日期 (YYYY-MM-DD，回车=今天):
→ 直接回车（使用今天日期）或输入：2025-10-28

资产类型 (BTC/ETH, 默认=BTC):
→ 直接回车（默认BTC）

请粘贴数据（粘贴完成后按 Ctrl+Z (Windows) 然后回车）:
→ 按 Ctrl+V 粘贴
→ 粘贴完成后按 Ctrl+Z 然后回车

确认导入这 XX 条数据？(yes/no):
→ 输入 yes
```

### 导入示例

**输入数据格式：**
```
1
Strategy
🇺🇸	MSTR	640,808
2
MARA Holdings, Inc.
🇺🇸	MARA	53,250
3
Tesla, Inc.
🇺🇸	TSLA	11,509
```

**输出结果：**
```
✅ 新增公司: MARA Holdings, Inc. (MARA)
🟢 Strategy (MSTR): +12,623 BTC → 640,808
⏭️  跳过（已存在）: Tesla, Inc. - 11,509 BTC

✅ 导入完成！
   成功: 2 条
   跳过: 1 条
   错误: 0 条
```

**状态说明：**
- 🟢 **增持**：持仓增加
- 🔴 **减持**：持仓减少
- ⚪ **持平**：持仓不变
- 🆕 **首次**：首次录入
- ⏭️  **跳过**：已有相同数据

---

## ✏️ 单条录入

如果只需要录入少量数据，使用交互式工具：

```bash
python scripts\corporate_treasury\interactive_input.py
```

### 功能菜单

```
🏦 企业金库监控 - 数据录入工具
1. 录入购买记录  ← 记录 BTC/ETH 购买
2. 录入融资信息  ← 记录融资活动
3. 录入股价数据  ← 记录股价变动
4. 查看持仓汇总  ← 查看所有公司持仓
5. 查看公司列表  ← 列出监控的公司
0. 退出
```

### 录入示例

#### 录入购买记录

```
选择操作: 1

请选择公司编号: 1
已选择: Strategy Inc

📊 上一次记录（2025-10-20）:
   持仓量: 628,185.00 BTC
   购买量: 5,445.00 BTC
💡 提示: 如果新的累计持仓 > 628,185.00 则为增持，< 则为减持

购买日期 (YYYY-MM-DD): 2025-10-28
资产类型 (BTC/ETH): BTC
购买数量: 12623
平均价格(USD, 可选): 113500
总金额(USD, 可选): 1432710500
累计持仓量(可选): 640808
公告链接(可选): https://...
备注(可选):

✅ 购买记录已保存！
   Strategy Inc 购入 12623 BTC
```

---

## 📊 查询分析

### 1. 查看持仓变化

```bash
python scripts\corporate_treasury\view_holdings_changes.py
```

**查看所有公司最近90天：**
```bash
python scripts\corporate_treasury\view_holdings_changes.py
```

**查看特定公司：**
```bash
python scripts\corporate_treasury\view_holdings_changes.py --company "Strategy Inc"
```

**查看特定资产和时间范围：**
```bash
python scripts\corporate_treasury\view_holdings_changes.py --asset BTC --days 30
```

**输出示例：**
```
🏢 Strategy Inc (MSTR) - BTC
📊 最新持仓: 640,808.00 BTC

日期         变化            价格          金额                累计持仓           状态
2025-10-28   +12,623.00     $113,500      $1,432,710,500     640,808.00        🟢 增持
2025-10-20   +5,445.00      $113,000      $615,285,000       628,185.00        🟢 增持
2025-10-15   +0.00          -             -                  622,740.00        ⚪ 持平
2025-09-25   +6,556.00      $110,500      $724,438,000       622,740.00        🟢 增持

📈 统计汇总（最近90天）:
   累计购买: 24,624.00 BTC
   总投资: $2,772,433,500
   平均成本: $112,583.45
```

### 2. 查看持仓汇总

在交互式工具中选择 "4. 查看持仓汇总"

```
📊 企业金库持仓汇总

Strategy Inc (MSTR)
------------------------------------------------------------
  BTC持仓: 640,808.00 BTC
  BTC投资: $71,234,567,890
  最近购买: 2025-10-28
  总融资: $8,500,000,000
  最新股价: $445.67 (+2.34%)
```

---

## 🌐 数据来源

### 1. BTC持仓数据

#### Bitcoin Treasuries（推荐）⭐
- **网址**: https://bitcointreasuries.net/
- **更新频率**: 实时
- **数据完整度**: ⭐⭐⭐⭐⭐
- **优点**:
  - 最全面的BTC企业持仓榜单
  - 包含历史购买记录
  - 每次购买的价格和数量
  - 支持直接复制导入

#### SaylorTracker
- **网址**: https://saylortracker.com/
- **专注**: MicroStrategy (Strategy)
- **优点**: 专门追踪 Michael Saylor 和 MSTR
- **数据**: 平均成本、收益率、时间线

### 2. 官方公告

#### SEC EDGAR（美国上市公司）
- **网址**: https://www.sec.gov/edgar/searchedgar/companysearch.html
- **搜索**: 输入公司名或股票代码（如 MSTR）
- **关键文件**:
  - **8-K**: 重大事件公告（购买BTC、融资）
  - **10-Q**: 季度报告
  - **10-K**: 年度报告

#### 公司官网
- MicroStrategy: https://www.microstrategy.com/en/investor-relations
- Tesla: https://ir.tesla.com/

### 3. 股价数据

#### Yahoo Finance（推荐）
- **网址**: https://finance.yahoo.com/quote/MSTR
- **功能**:
  - 实时股价
  - 历史数据下载（CSV）
  - 财务报表

#### Google Finance
- **网址**: https://www.google.com/finance/quote/MSTR:NASDAQ
- **优点**: 界面简洁、快速

#### TradingView
- **网址**: https://www.tradingview.com/symbols/NASDAQ-MSTR/
- **优点**: 专业图表、技术分析

### 4. 融资信息

#### PR Newswire / Business Wire
- **网址**:
  - https://www.prnewswire.com/
  - https://www.businesswire.com/
- **搜索**: "MicroStrategy Bitcoin" 或 "MSTR convertible"

#### CoinDesk / The Block（加密货币新闻）
- **网址**:
  - https://www.coindesk.com/
  - https://www.theblock.co/
- **优点**: 快速获取加密行业新闻

---

## 💡 常见问题

### Q1: 如何判断是增持还是减持？
**A**: 对比两次的**累计持仓量**
- 新持仓 > 旧持仓 = 🟢 增持
- 新持仓 < 旧持仓 = 🔴 减持
- 新持仓 = 旧持仓 = ⚪ 持平

系统会自动计算并显示。

### Q2: 批量导入时提示"无法解析数据"？
**A**: 确保复制的数据包含：
1. 排名数字（1, 2, 3...）
2. 公司名称
3. 国旗 + 股票代码 + 持仓量（用 Tab 分隔）

### Q3: 如何更新已有公司的数据？
**A**: 直接重新导入，系统会：
- 如果持仓量相同 → 跳过
- 如果持仓量不同 → 更新并显示变化

### Q4: 如何添加新公司？
**A**:
- **方法1**: 批量导入时自动创建
- **方法2**: 手动在数据库添加到 `corporate_treasury_companies` 表

### Q5: 支持 ETH 持仓吗？
**A**: 支持！导入时输入资产类型 `ETH` 即可

### Q6: 数据多久更新一次？
**A**:
- Bitcoin Treasuries 通常每天更新
- 建议每周或每月导入一次即可

---

## 📝 数据表结构

### corporate_treasury_companies（公司信息）
- `company_name`: 公司名称
- `ticker_symbol`: 股票代码
- `category`: 分类（holding/mining/payment）
- `is_active`: 是否活跃监控

### corporate_treasury_purchases（购买记录）
- `company_id`: 公司ID
- `purchase_date`: 购买日期
- `asset_type`: 资产类型（BTC/ETH）
- `quantity`: 购买数量
- `average_price`: 平均价格
- `cumulative_holdings`: 累计持仓量 ⭐

### corporate_treasury_financing（融资记录）
- `company_id`: 公司ID
- `financing_date`: 融资日期
- `financing_type`: 融资类型
- `amount`: 金额
- `purpose`: 用途

### corporate_treasury_stock_prices（股价数据）
- `company_id`: 公司ID
- `trade_date`: 交易日期
- `close_price`: 收盘价
- `change_pct`: 涨跌幅

---

## 🔧 故障排除

### 问题：导入时报错 "Table doesn't exist"
**解决**：运行数据库初始化脚本
```bash
mysql -uroot -p"Tonny@1000" binance-data < app/database/corporate_treasury_schema.sql
```

### 问题：Windows 下 Ctrl+D 不工作
**解决**：使用 **Ctrl+Z** 然后按回车

### 问题：中文乱码
**解决**：确保数据库字符集为 `utf8mb4`

---

## 📞 获取帮助

- 查看代码注释
- 检查日志输出
- 使用 `--help` 参数（如果支持）

---

**最后更新**: 2025-10-28
**版本**: 1.0