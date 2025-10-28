# Dashboard数据加载问题诊断

## 问题症状

- ✅ 合约交易功能已修复（保证金问题已解决）
- ❌ Dashboard页面数据显示异常：
  - 监控币种：0
  - 聪明钱钱包：0
  - 风险等级：undefined
  - 合约数据：加载不出来

## 问题分析

### 1. API调用流程

Dashboard页面通过以下方式加载数据：

```javascript
// static/js/dashboard.js 第77-97行
async function loadDashboard() {
    const response = await fetch('/api/dashboard');
    const result = await response.json();

    if (result.success) {
        const data = result.data;
        updateStats(data.stats || {}, data.hyperliquid || {});
        updatePrices(data.prices || []);
        updateFuturesTable(data.futures || []);
        updateRecommendations(data.recommendations || []);
        updateNews(data.news || []);
        updateHyperliquid(data.hyperliquid || {});
    }
}
```

### 2. 期望的数据结构

`/api/dashboard` 应该返回：

```json
{
    "success": true,
    "data": {
        "stats": {
            "total_symbols": 20,
            "bullish_count": 8,
            "bearish_count": 5
        },
        "hyperliquid": {
            "monitored_wallets": 150
        },
        "prices": [...],
        "futures": [...],
        "recommendations": [...],
        "news": [...]
    }
}
```

### 3. 可能的原因

#### 原因A：数据库没有数据
- 数据采集服务未运行
- 定时任务未执行
- 数据库连接失败

#### 原因B：API返回错误
- `/api/dashboard` 返回500错误
- 数据格式不匹配
- AnalysisService异常

#### 原因C：前端JavaScript错误
- fetch失败（网络问题）
- JSON解析失败
- 数据字段映射错误

## 诊断步骤

### 步骤1：检查浏览器Console

1. 打开Dashboard页面
2. 按F12打开开发者工具
3. 切换到Console标签
4. 查看是否有错误信息：

**正常情况：**
```
(无错误)
```

**异常情况：**
```
加载仪表盘数据失败: TypeError: ...
或
Failed to fetch
或
SyntaxError: Unexpected token < in JSON
```

### 步骤2：检查Network请求

1. 在开发者工具中切换到Network标签
2. 刷新页面
3. 找到 `/api/dashboard` 请求
4. 查看：
   - Status Code：应该是200
   - Response：查看返回的JSON数据

**正常的Response：**
```json
{
    "success": true,
    "data": {
        "stats": {...},
        "prices": [...]
    }
}
```

**异常的Response：**
```json
{
    "detail": "某某错误"
}
```
或
```
Status 500 Internal Server Error
Status 404 Not Found
```

### 步骤3：检查服务器日志

在Windows控制台（运行python app/main.py的窗口），查找：

**启动时应该看到：**
```
✅ 主API路由已注册（/api/prices, /api/analysis等）
```

**访问Dashboard时应该看到：**
```
INFO:     127.0.0.1:xxxxx - "GET /api/dashboard HTTP/1.1" 200 OK
```

**如果看到错误：**
```
ERROR    | 获取仪表盘数据失败: ...
或
INFO:     127.0.0.1:xxxxx - "GET /api/dashboard HTTP/1.1" 500 Internal Server Error
```

### 步骤4：检查数据库数据

数据库中应该有以下表的数据：
- `prices` - 价格数据
- `investment_recommendations` - 投资建议
- `news_sentiment` - 新闻情绪
- `hyperliquid_symbol_data` - Hyperliquid数据
- `futures_funding_rates` - 资金费率

如果这些表是空的，说明数据采集没有运行。

## 解决方案

### 方案1：重启服务

```bash
# 停止当前服务 (Ctrl+C)
# 然后重新启动
cd crypto-analyzer
python app/main.py
```

确认看到：
```
✅ 主API路由已注册（/api/prices, /api/analysis等）
```

### 方案2：手动触发数据采集

```bash
# 运行一次数据采集
python app/scheduler.py
```

### 方案3：检查数据库连接

确保：
1. MySQL服务正在运行
2. 配置文件中的数据库连接信息正确
3. 数据库用户有权限访问

### 方案4：添加调试日志

临时修改 `static/js/dashboard.js` 第77-97行：

```javascript
async function loadDashboard() {
    try {
        console.log('正在加载Dashboard数据...');
        const response = await fetch(`${API_BASE}/api/dashboard`);
        console.log('Response status:', response.status);

        const result = await response.json();
        console.log('Dashboard数据:', result);

        if (result.success) {
            const data = result.data;
            console.log('Stats:', data.stats);
            console.log('Hyperliquid:', data.hyperliquid);

            updatePrices(data.prices || []);
            updateFuturesTable(data.futures || []);
            updateRecommendations(data.recommendations || []);
            updateNews(data.news || []);
            updateHyperliquid(data.hyperliquid || {});
            updateStats(data.stats || {}, data.hyperliquid || {});
            document.getElementById('last-update').textContent = data.last_updated || '-';
        } else {
            console.error('API返回success=false:', result);
        }
    } catch (error) {
        console.error('加载仪表盘数据失败:', error);
        console.error('Error stack:', error.stack);
        showError('加载数据失败，请检查后端服务是否运行');
    }
}
```

刷新页面后，在Console中查看详细日志。

## 下一步

请按照以上诊断步骤操作，并将以下信息发给我：

1. ✅ 浏览器Console的完整错误日志
2. ✅ Network标签中 `/api/dashboard` 请求的：
   - Status Code
   - Response内容（如果有）
3. ✅ Windows服务器控制台的日志输出
4. ✅ 是否看到"✅ 主API路由已注册"的消息

有了这些信息，我可以精确定位问题并提供修复方案。

## 合约交易已修复 ✅

合约交易的保证金不足问题已经修复：
- 初始余额从 $10,000 增加到 $100,000
- 默认数量从 1张 改为 0.1张
- 默认价格从 $100,000 改为 $50,000

现在可以正常进行合约交易测试了！
