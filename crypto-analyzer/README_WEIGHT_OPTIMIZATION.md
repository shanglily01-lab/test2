# 权重优化自动化说明

## 📋 问题分析

凌晨2点自动优化可能遇到的问题:
1. ❌ **编码错误**: emoji符号在Windows GBK编码下报错
2. ❌ **数据库连接失败**: 网络不稳定导致连接超时
3. ❌ **优化逻辑错误**: 数据不足或其他业务逻辑问题
4. ❌ **无错误通知**: 睡觉时无法发现优化失败

## ✅ 解决方案

### 1. 使用安全版优化器

新的 `safe_weight_optimizer.py` 提供:
- ✅ **完整错误处理**: 每个步骤都有try-catch
- ✅ **详细日志记录**: 所有操作记录到日志文件
- ✅ **错误文件生成**: 失败时自动创建ERROR文件
- ✅ **调整摘要报告**: 每次调整生成详细报告
- ✅ **编码问题修复**: 避免emoji导致的编码错误

### 2. 日志文件结构

```
logs/weight_optimization/
├── weight_optimization_20260121.log          # 每日运行日志
├── adjustment_summary_20260121_134354.txt    # 调整摘要报告
└── ERROR_20260121_140530.txt                 # 错误报告(仅失败时)
```

### 3. 手动运行测试

```bash
# 直接运行
python safe_weight_optimizer.py

# 检查退出码
echo $?  # Linux/Mac
echo %ERRORLEVEL%  # Windows

# 0 = 成功
# 1 = 失败
```

### 4. 设置定时任务

#### Linux (使用crontab)

```bash
# 编辑crontab
crontab -e

# 添加凌晨2点自动运行
0 2 * * * cd /path/to/crypto-analyzer && /usr/bin/python3 safe_weight_optimizer.py >> logs/weight_optimization/cron.log 2>&1

# 或者使用完整路径
0 2 * * * /usr/bin/python3 /path/to/crypto-analyzer/safe_weight_optimizer.py
```

#### Windows (使用任务计划程序)

1. 打开"任务计划程序" (taskschd.msc)
2. 创建基本任务
3. 触发器: 每天凌晨2:00
4. 操作: 启动程序
   - 程序: `python.exe`
   - 参数: `safe_weight_optimizer.py`
   - 起始于: `d:\test2\crypto-analyzer`

### 5. 监控优化结果

#### 方法1: 检查日志文件

```bash
# 查看今天的日志
cat logs/weight_optimization/weight_optimization_$(date +%Y%m%d).log

# 查看是否有错误
ls logs/weight_optimization/ERROR_*.txt

# 查看最新的调整摘要
ls -lt logs/weight_optimization/adjustment_summary_*.txt | head -1
```

#### 方法2: 每天早上快速检查

创建检查脚本 `check_optimization.sh`:

```bash
#!/bin/bash
TODAY=$(date +%Y%m%d)
LOG_FILE="logs/weight_optimization/weight_optimization_${TODAY}.log"
ERROR_FILES="logs/weight_optimization/ERROR_${TODAY}*.txt"

echo "=== Weight Optimization Status for $TODAY ==="

if [ -f "$LOG_FILE" ]; then
    if grep -q "Weight Optimization Completed" "$LOG_FILE"; then
        echo "✅ Optimization completed successfully"

        # 显示调整数量
        ADJUSTED=$(grep "Successfully adjusted" "$LOG_FILE" | tail -1)
        echo "$ADJUSTED"

        # 显示摘要文件
        SUMMARY=$(ls -t logs/weight_optimization/adjustment_summary_${TODAY}*.txt 2>/dev/null | head -1)
        if [ -f "$SUMMARY" ]; then
            echo "📄 Summary: $SUMMARY"
        fi
    else
        echo "⚠️ Optimization incomplete"
    fi
else
    echo "❌ No log file found - optimization may not have run"
fi

# 检查错误
if ls $ERROR_FILES 1> /dev/null 2>&1; then
    echo "❌ ERROR found:"
    ls -l $ERROR_FILES
else
    echo "✅ No errors"
fi
```

### 6. 错误通知 (可选扩展)

在 `safe_weight_optimizer.py` 中的 `send_error_notification()` 函数可以扩展为:

#### 邮件通知示例:
```python
import smtplib
from email.mime.text import MIMEText

def send_email_notification(error_msg):
    msg = MIMEText(error_msg)
    msg['Subject'] = '⚠️ Weight Optimization Failed'
    msg['From'] = 'your_email@gmail.com'
    msg['To'] = 'your_email@gmail.com'

    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login('your_email@gmail.com', 'your_password')
        server.send_message(msg)
```

#### Telegram通知示例:
```python
import requests

def send_telegram_notification(error_msg):
    token = 'YOUR_BOT_TOKEN'
    chat_id = 'YOUR_CHAT_ID'
    url = f'https://api.telegram.org/bot{token}/sendMessage'

    requests.post(url, data={
        'chat_id': chat_id,
        'text': f'⚠️ Weight Optimization Failed\n\n{error_msg}'
    })
```

## 🔄 优化频率建议

- **初期 (前2周)**: 每天凌晨2点运行一次
- **稳定期**: 每3天运行一次
- **手动触发**: 当发现胜率明显下降时

## 📊 查看优化历史

```bash
# 查看所有调整摘要
ls -lh logs/weight_optimization/adjustment_summary_*.txt

# 对比两次调整的差异
diff logs/weight_optimization/adjustment_summary_20260121_134354.txt \
     logs/weight_optimization/adjustment_summary_20260122_020005.txt
```

## ⚙️ 优化参数配置

在 `app/services/scoring_weight_optimizer.py` 中可以调整:

```python
# 权重调整阈值
performance_score > 10:  +3  # 表现极好,大幅提权
performance_score > 5:   +2  # 表现良好,适度提权
performance_score < -10: -3  # 表现极差,大幅降权
performance_score < -5:  -2  # 表现不佳,适度降权

# 权重范围限制
min_weight = 5   # 最小权重
max_weight = 30  # 最大权重

# 最少订单数要求
min_orders = 5   # 至少5个订单才会调整权重
```

## 🚨 紧急情况处理

如果发现优化导致严重问题:

```sql
-- 查看权重调整历史
SELECT * FROM signal_scoring_weights
ORDER BY last_adjusted DESC;

-- 手动回滚权重 (恢复到基础权重)
UPDATE signal_scoring_weights
SET weight_long = base_weight,
    weight_short = base_weight
WHERE signal_component = 'position_low';

-- 或者禁用某个组件
UPDATE signal_scoring_weights
SET is_active = FALSE
WHERE signal_component = 'trend_1d_bull';
```

## 📝 最佳实践

1. ✅ 每次优化后重启交易服务让新权重生效
2. ✅ 每天早上检查一次优化日志
3. ✅ 保留至少30天的调整摘要文件做对比
4. ✅ 发现异常时立即查看ERROR文件
5. ✅ 定期清理过期日志文件(保留30-60天)
