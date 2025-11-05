# ETF 数据源说明

## 数据源优先级

ETF 数据采集器使用三层数据源，按以下优先级自动切换：

### 1. SoSoValue API（推荐）⭐
- **URL**: https://open-api.sosovalue.com/api
- **优点**:
  - 数据最完整（包含 AUM, 持仓量等）
  - 响应速度快
  - 稳定性高
  - 无需登录
- **缺点**:
  - 偶尔会有 SSL 错误
  - 需要稳定的网络连接

### 2. CoinGlass API（备用）
- **URL**: https://open-api.coinglass.com
- **优点**:
  - 数据较全面
  - 更新及时
- **缺点**:
  - 有时返回 HTTP 500
  - 可能需要 API Key（未来）

### 3. Farside.co.uk 网页爬虫（兜底）
- **URL**: https://farside.co.uk/btc/ 和 https://farside.co.uk/eth/
- **优点**:
  - 数据源权威（Farside Investors 官方）
  - 始终可以通过浏览器访问
- **缺点**:
  - ⚠️ **有严格的反爬虫保护**
  - 经常返回 HTTP 403 Forbidden
  - 只能获取净流入数据，无法获取 AUM 等详细信息
  - 需要 BeautifulSoup 库

## 常见问题

### ❌ 所有数据源都失败了怎么办？

如果你看到以下错误：

```
❌ 获取数据失败: SSL Error
❌ API 返回错误: HTTP 500
❌ 无法访问 Farside: HTTP 403
⚠️  无法从任何数据源获取 ETF 数据
```

**解决方案**：

#### 方案 1: 检查网络连接
```bash
# 测试 SoSoValue API
curl -v https://open-api.sosovalue.com/api/etf/spot-bitcoin-etf/flows?days=1

# 测试 Farside 访问
curl -v https://farside.co.uk/btc/
```

#### 方案 2: 使用代理或 VPN
某些地区可能无法访问这些国外API，建议：
- 使用 VPN
- 配置 HTTP 代理
- 使用国内镜像（如果有）

#### 方案 3: 手动从网页获取数据
1. 访问 https://farside.co.uk/btc/ (BTC) 或 https://farside.co.uk/eth/ (ETH)
2. 查看最新的 ETF 流入数据
3. 手动录入到系统（如果需要）

#### 方案 4: 增加请求延迟和重试
修改 `config.yaml`:
```yaml
etf_collector:
  enabled: true
  retry_count: 5        # 增加重试次数
  retry_delay: 5        # 增加延迟（秒）
```

### ⚠️ Farside 403 错误特别说明

Farside.co.uk 使用了 Cloudflare 或类似的反爬虫保护系统，特征：
- 检测请求头
- 检测 JavaScript 执行
- 检测 TLS 指纹
- 检测请求频率

**当前实现的绕过措施**：
1. ✅ 完整的 Chrome 浏览器请求头
2. ✅ Session 保持会话状态
3. ✅ 3次重试机制with延迟
4. ✅ Referer, Origin, Sec-Ch-Ua 等头部

**仍然可能失败的原因**：
- IP 被临时封禁
- 需要验证 JavaScript 执行
- 需要 Cloudflare Challenge
- 检测到自动化工具

**终极解决方案**（如果必须使用 Farside）：
1. 使用 Selenium + ChromeDriver（真实浏览器）
2. 使用专业的反反爬虫服务
3. 使用代理IP池轮换

## 推荐配置

### 生产环境推荐
```yaml
etf_collector:
  enabled: true
  primary_source: "sosovalue"    # 优先使用 SoSoValue
  fallback_enabled: true         # 启用备用源
  farside_enabled: false         # 禁用 Farside（403 太频繁）
```

### 开发/测试环境
```yaml
etf_collector:
  enabled: true
  primary_source: "sosovalue"
  fallback_enabled: true
  farside_enabled: true          # 可以测试 Farside
  retry_count: 3
```

## 数据更新频率

| 数据源 | 更新时间（UTC） | 更新时间（北京时间） | 说明 |
|--------|----------------|---------------------|------|
| SoSoValue | 每日 17:00 | 次日 01:00 | 美国市场收盘后 |
| CoinGlass | 每日 17:30 | 次日 01:30 | 略有延迟 |
| Farside | 每日更新 | 次日 00:00-02:00 | 人工更新 |

**建议采集时间**:
- 每天 **13:17**（北京时间 21:17）- 确保数据已更新
- 或每天 **02:00**（北京时间，即美东收盘后）

## 数据字段对比

| 字段 | SoSoValue | CoinGlass | Farside |
|------|-----------|-----------|---------|
| 净流入 | ✅ | ✅ | ✅ |
| 总流入 | ✅ | ✅ | ❌ |
| 总流出 | ✅ | ✅ | ❌ |
| AUM | ✅ | ✅ | ❌ |
| 持仓量 | ✅ | ✅ | ❌ |
| ETF 列表 | ✅ | ✅ | ✅ |
| 单个 ETF 详情 | ✅ | ✅ | ✅ |

**建议**: 优先使用 SoSoValue 或 CoinGlass 获取完整数据。

## 监控和告警

如果你想监控数据采集状态，可以查看日志：

```bash
# 查看最近的 ETF 采集日志
tail -f logs/scheduler.log | grep "ETF"

# 查看采集统计
python scripts/check_etf_status.py
```

## 联系支持

如果长期无法获取 ETF 数据：
1. 检查网络和防火墙设置
2. 尝试从浏览器手动访问数据源
3. 查看 GitHub Issues: https://github.com/your-repo/issues
4. 提交问题报告，包含完整错误日志

---

**最后更新**: 2025-11-05
**维护者**: Claude Code
