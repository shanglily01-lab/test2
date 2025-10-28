# ETF 手动数据录入指南

## 📋 概述

由于 ETF API 在部分环境下无法访问，我们提供了手动录入 ETF 数据的工具。

---

## 🚀 快速开始

### 方式 1：CSV 批量导入（推荐）

#### 步骤 1：准备 CSV 文件

使用提供的模板 `scripts/etf_data_template.csv`，或创建新的 CSV 文件：

```csv
ticker,trade_date,net_inflow,gross_inflow,gross_outflow,aum,btc_holdings,eth_holdings,data_source
IBIT,2025-01-27,125.5,200.0,74.5,50000,21000,,manual
FBTC,2025-01-27,85.3,150.0,64.7,30000,15000,,manual
```

**字段说明：**
- `ticker`: ETF 代码（必填）
- `trade_date`: 交易日期，格式 YYYY-MM-DD（必填）
- `net_inflow`: 净流入，单位百万美元（必填）
- `gross_inflow`: 总流入，单位百万美元（可选）
- `gross_outflow`: 总流出，单位百万美元（可选）
- `aum`: 资产管理规模，单位百万美元（可选）
- `btc_holdings`: BTC 持仓量（BTC ETF 填写，可选）
- `eth_holdings`: ETH 持仓量（ETH ETF 填写，可选）
- `data_source`: 数据来源，默认 "manual"（可选）

#### 步骤 2：执行导入

```bash
# Windows
cd E:\mywork\crypto-analyzer
python scripts\manual_etf_import.py --csv scripts\etf_data_template.csv

# Linux
cd /home/tonny/code/test2/crypto-analyzer
python scripts/manual_etf_import.py --csv scripts/etf_data_template.csv
```

---

### 方式 2：命令行单条录入

```bash
# 格式
python scripts/manual_etf_import.py --single <代码> <日期> <净流入> [AUM] [持仓量] [类型]

# 示例：录入 IBIT 的数据
python scripts/manual_etf_import.py --single IBIT 2025-01-27 125.5 50000 21000 BTC

# 示例：只录入净流入
python scripts/manual_etf_import.py --single FBTC 2025-01-27 85.3
```

---

### 方式 3：交互式录入

```bash
python scripts/manual_etf_import.py
```

按提示逐个输入字段，适合少量数据录入。

---

## 📊 查看已注册的 ETF 产品

录入数据前，先查看数据库中有哪些 ETF 产品：

```bash
python scripts/manual_etf_import.py --list
```

输出示例：
```
📋 已注册的 ETF 产品:
================================================================================
代码     名称                             发行商               类型    上市日期
--------------------------------------------------------------------------------
IBIT     iShares Bitcoin Trust           BlackRock            BTC    2024-01-11
FBTC     Fidelity Wise Origin Bitcoin    Fidelity             BTC    2024-01-11
ARKB     ARK 21Shares Bitcoin ETF        ARK Invest           BTC    2024-01-11
BITB     Bitwise Bitcoin ETF             Bitwise              BTC    2024-01-11
GBTC     Grayscale Bitcoin Trust         Grayscale            BTC    2013-09-19
ETHA     iShares Ethereum Trust          BlackRock            ETH    2024-07-23
================================================================================
```

---

## 🔧 常见 BTC ETF 代码

| 代码 | 名称 | 发行商 |
|------|------|--------|
| IBIT | iShares Bitcoin Trust | BlackRock |
| FBTC | Fidelity Wise Origin Bitcoin | Fidelity |
| ARKB | ARK 21Shares Bitcoin ETF | ARK Invest |
| BITB | Bitwise Bitcoin ETF | Bitwise |
| GBTC | Grayscale Bitcoin Trust | Grayscale |
| HODL | VanEck Bitcoin Trust | VanEck |
| BTCO | Invesco Galaxy Bitcoin ETF | Invesco |
| BRRR | Valkyrie Bitcoin Fund | Valkyrie |
| EZBC | Franklin Bitcoin ETF | Franklin Templeton |
| BTCW | WisdomTree Bitcoin Fund | WisdomTree |

## 🔧 常见 ETH ETF 代码

| 代码 | 名称 | 发行商 |
|------|------|--------|
| ETHA | iShares Ethereum Trust | BlackRock |
| FETH | Fidelity Ethereum Fund | Fidelity |
| ETHW | Bitwise Ethereum ETF | Bitwise |
| CETH | 21Shares Core Ethereum ETF | 21Shares |
| ETHV | VanEck Ethereum ETF | VanEck |

---

## 📝 数据来源推荐

### 1. **SoSoValue**（推荐）
- 网址: https://sosovalue.com/etf
- 提供每日 ETF 资金流向数据
- 数据更新及时

### 2. **Farside Investors**
- 网址: https://farside.co.uk/
- 提供 Bitcoin ETF 和 Ethereum ETF 流向数据
- 每日更新

### 3. **The Block**
- 网址: https://www.theblock.co/data/crypto-markets/etf
- 提供 ETF 数据和分析

### 4. **Bloomberg / Yahoo Finance**
- 提供 ETF 价格和交易量数据

---

## 🎯 完整工作流程

### 每日数据录入流程

1. **访问数据源**
   ```
   打开 https://sosovalue.com/etf 或 https://farside.co.uk/
   ```

2. **复制数据到 Excel**
   ```
   将网页上的 ETF 流向数据复制到 Excel
   ```

3. **整理为 CSV 格式**
   ```
   按照模板格式整理数据，保存为 CSV
   ```

4. **执行导入**
   ```bash
   python scripts/manual_etf_import.py --csv 你的文件.csv
   ```

5. **验证数据**
   ```bash
   # 在数据库中查询
   mysql -u root -p
   USE binance-data;
   SELECT * FROM crypto_etf_flows
   WHERE trade_date = '2025-01-27'
   ORDER BY net_inflow DESC;
   ```

---

## ❗ 注意事项

1. **ETF 产品必须先注册**
   - 录入数据前，确保 ETF 产品已存在于 `crypto_etf_products` 表
   - 使用 `--list` 命令查看已注册产品

2. **日期格式**
   - 必须使用 `YYYY-MM-DD` 格式
   - 例如：`2025-01-27`

3. **数值单位**
   - 净流入、AUM：单位为百万美元
   - 持仓量：BTC 或 ETH 的数量（不是美元）

4. **重复数据处理**
   - 如果同一 ETF 的同一日期已有数据，会自动更新
   - 不会产生重复记录

5. **必填字段**
   - `ticker`（ETF 代码）
   - `trade_date`（交易日期）
   - `net_inflow`（净流入）

---

## 🐛 故障排查

### 问题 1：提示"ETF 产品不存在"

**原因**：数据库中没有该 ETF 产品信息

**解决**：
```sql
-- 添加 ETF 产品（示例）
INSERT INTO crypto_etf_products
(ticker, name, issuer, asset_type, launch_date)
VALUES
('IBIT', 'iShares Bitcoin Trust', 'BlackRock', 'BTC', '2024-01-11');
```

### 问题 2：数据库连接失败

**检查**：
- MySQL 是否运行
- `config.yaml` 中的数据库配置是否正确
- 数据库密码是否正确

### 问题 3：CSV 格式错误

**检查**：
- 确保第一行是列名
- 日期格式是否正确
- 数字字段是否包含非法字符

---

## 📈 数据验证

导入后验证数据：

```bash
# 查看最近的 ETF 数据
python << 'PYEOF'
from app.database.db_service import DatabaseService
import yaml

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

db = DatabaseService(config)
session = db.get_session()

from sqlalchemy import text
result = session.execute(text("""
    SELECT ticker, trade_date, net_inflow, aum, btc_holdings
    FROM crypto_etf_flows
    ORDER BY trade_date DESC, net_inflow DESC
    LIMIT 10
"""))

print("\n最近的 ETF 数据:")
print("=" * 80)
for row in result:
    print(f"{row.ticker:<8} {row.trade_date} | 净流入: ${row.net_inflow:>8.2f}M | AUM: ${row.aum:>10,.0f}M")
print("=" * 80)
PYEOF
```

---

## 🎉 完成

现在您可以使用这些工具手动录入 ETF 数据了！

如有问题，请查看日志或联系技术支持。