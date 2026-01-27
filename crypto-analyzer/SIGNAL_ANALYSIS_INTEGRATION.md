# 信号分析服务集成完成

## 完成时间
2026-01-27 16:20

## 集成内容

### 1. 核心服务
- ✅ [app/services/signal_analysis_service.py](app/services/signal_analysis_service.py)
  - K线强度分析（5M/15M/1H）
  - 信号捕捉状态检查
  - 错过机会分析
  - 统计报告生成

### 2. 后台服务
- ✅ [app/services/signal_analysis_background_service.py](app/services/signal_analysis_background_service.py)
  - 异步任务封装
  - 每6小时自动执行
  - 线程池执行（避免阻塞事件循环）
  - 自动保存到数据库

### 3. 调度器（独立运行）
- ✅ [app/schedulers/signal_analysis_scheduler.py](app/schedulers/signal_analysis_scheduler.py)
  - 使用schedule库
  - 每6小时执行：00:00, 06:00, 12:00, 18:00
  - 可独立启动

### 4. 手动执行脚本
- ✅ [run_signal_analysis.py](run_signal_analysis.py)
  - 详细的K线数据展示（5M/15M/1H）
  - Top 15强力信号
  - Top 10错过机会
  - 完整统计报告

### 5. 辅助脚本
- ✅ [clear_blacklist.py](clear_blacklist.py) - 清空黑名单
- ✅ [signal_analysis_page.py](signal_analysis_page.py) - 原始分析脚本

## 数据库表

```sql
CREATE TABLE IF NOT EXISTS signal_analysis_reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    analysis_time DATETIME NOT NULL,
    total_analyzed INT NOT NULL,
    has_position INT NOT NULL,
    should_trade INT NOT NULL,
    missed_opportunities INT NOT NULL,
    wrong_direction INT NOT NULL,
    correct_captures INT NOT NULL,
    capture_rate DECIMAL(5,2) NOT NULL,
    report_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_analysis_time (analysis_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

## 集成到main.py

### 启动位置
**文件**: [app/main.py](app/main.py)
**行号**: ~382-392

```python
# 启动信号分析后台服务（每6小时执行一次）
signal_analysis_service = None
try:
    from app.services.signal_analysis_background_service import SignalAnalysisBackgroundService
    signal_analysis_service = SignalAnalysisBackgroundService()
    asyncio.create_task(signal_analysis_service.run_loop(interval_hours=6))
    logger.info("✅ 信号分析后台服务已启动（每6小时执行一次）")
except Exception as e:
    logger.warning(f"⚠️  启动信号分析后台服务失败: {e}")
    import traceback
    traceback.print_exc()
    signal_analysis_service = None
```

### 停止位置
**文件**: [app/main.py](app/main.py)
**行号**: ~432-438

```python
# 停止信号分析后台服务
if signal_analysis_service:
    try:
        signal_analysis_service.stop()
        logger.info("✅ 信号分析后台服务已停止")
    except Exception as e:
        logger.warning(f"⚠️  停止信号分析后台服务失败: {e}")
```

## 使用方法

### 方法1: 自动运行（推荐）
当main.py启动时，信号分析服务会自动启动，并每6小时执行一次。

```bash
# 启动main.py
python app/main.py
```

服务会在以下时间自动执行：
- 启动时立即执行一次
- 之后每6小时执行一次

### 方法2: 独立调度器
使用schedule库，在固定时间点执行（00:00, 06:00, 12:00, 18:00）

```bash
python app/schedulers/signal_analysis_scheduler.py
```

### 方法3: 手动执行
随时手动执行分析

```bash
python run_signal_analysis.py
```

## 功能特性

### 1. K线强度分析
- **5分钟周期**: 24H内共310根K线
- **15分钟周期**: 24H内共108根K线
- **1小时周期**: 24H内共36根K线

### 2. 强力K线识别
- 成交量 > 1.2倍平均量
- 区分强阳线和强阴线
- 计算净力量（强阳 - 强阴）

### 3. 信号捕捉评估
- 检查是否已开仓
- 判断开仓方向是否正确
- 识别错过的机会

### 4. 错过原因分析
- 5M/15M/1H信号冲突
- 信号评分不足
- 黑名单限制
- 同向持仓已存在

## 输出示例

```
【信号分析报告 - 24H K线强度 + 捕捉情况】
========================================================================================================================

【总体统计】
  分析交易对: 81
  有交易机会: 46
  已开仓: 29 (正确26个, 方向错误3个)
  错过机会: 32
  有效捕获率: 56.5%

【Top 15 强力信号】
========================================================================================================================

 1. VET/USDT        | 强多   | ✗错过
    1H: 阳线  53% (19/36) | 强阳11 强阴 1 | 净力量+10
   15M: 阳线  44% ( 47/108) | 强阳21 强阴16 | 净力量 +5
    5M: 阳线  43% (135/311) | 强阳34 强阴52 | 净力量-18

 2. PUMP/USDT       | 强多   | ✗错过
    1H: 阳线  69% (25/36) | 强阳10 强阴 2 | 净力量 +8
   15M: 阳线  50% ( 54/108) | 强阳26 强阴15 | 净力量+11
    5M: 阳线  50% (155/310) | 强阳44 强阴28 | 净力量+16
```

## 性能优化

### 异步执行
- 使用 `asyncio.to_thread` 在线程池中执行分析
- 避免阻塞FastAPI事件循环
- 不影响Web服务响应速度

### 资源管理
- 数据库连接自动管理
- 分析完成后自动关闭连接
- 服务停止时清理资源

## 监控与日志

所有日志通过loguru输出，包括：
- ✅ 服务启动/停止
- 📊 分析任务执行
- ⚠️  错过的高质量机会（Top 5）
- ❌ 错误和异常

## 配置参数

### 执行间隔
在main.py中可调整：
```python
asyncio.create_task(signal_analysis_service.run_loop(interval_hours=6))
```

### 分析时长
默认分析24小时，可在服务中调整：
```python
report = await asyncio.to_thread(
    self.service.analyze_all_symbols,
    self.symbols,
    24  # 可修改为其他小时数
)
```

## 注意事项

1. **首次启动**: 会立即执行一次分析
2. **数据库表**: 首次运行会自动创建
3. **交易对列表**: 从config.yaml读取
4. **黑名单**: 已清空，所有交易对可正常分析

## 查询历史报告

```sql
-- 查询最新报告
SELECT * FROM signal_analysis_reports
ORDER BY analysis_time DESC
LIMIT 1;

-- 查询今天的所有报告
SELECT * FROM signal_analysis_reports
WHERE DATE(analysis_time) = CURDATE()
ORDER BY analysis_time DESC;

-- 查询捕获率趋势
SELECT
    DATE(analysis_time) as date,
    AVG(capture_rate) as avg_capture_rate,
    AVG(missed_opportunities) as avg_missed
FROM signal_analysis_reports
GROUP BY DATE(analysis_time)
ORDER BY date DESC
LIMIT 7;
```

## 故障排查

### 服务未启动
检查日志中是否有错误信息：
```bash
grep "信号分析" logs/main_*.log
```

### 数据库连接失败
确认.env文件中的数据库配置正确

### 分析数据为空
确认kline_data表中有数据：
```sql
SELECT COUNT(*) FROM kline_data WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR);
```

## 下一步优化建议

1. 添加Telegram通知（错过的高质量机会）
2. 创建Web界面展示分析报告
3. 增加更多时间周期（4H/1D）
4. 实现信号评分加成机制
5. 动态调整黑名单
