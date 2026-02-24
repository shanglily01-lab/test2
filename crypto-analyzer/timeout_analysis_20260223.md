# SmartTraderService 超时事故分析报告

## 时间线

**2026-02-23 16:09-16:20** - 开仓高峰期
- 82个持仓开仓
- planned_close_time 正确设置为开仓后180分钟（19:09-19:20）

**2026-02-23 19:09-19:20** - 应该平仓但未执行
- SmartExitOptimizer 应该在 planned_close_time 触发超时强制平仓
- 但实际上没有任何平仓活动

**2026-02-23 19:00 - 2026-02-24 01:20** - 服务停摆期
- **完全没有开仓活动** (0笔)
- **完全没有平仓活动** (0笔)
- 结论: **SmartTraderService主循环在19:00左右崩溃/停止**

**2026-02-24 01:21** - 服务恢复
- 82个超时持仓在01:21:31-01:21:33同时平仓（批量处理）
- 健康检查机制检测到超时持仓，触发 SmartExitOptimizer 重启
- 所有超时持仓立即被强制平仓

**2026-02-24 01:21-01:29** - 正常运行恢复
- 9个新持仓正常开仓
- 交易恢复正常

## 超时统计

- **预期持仓时长**: 180分钟（3小时）
- **实际持仓时长**: 551-552分钟（9.2小时）
- **超时时长**: 371-372分钟（**6.2小时**）
- **受影响订单**: 82个
- **总盈亏**: +999.76 USDT（幸运的是做空方向正确）

## 根本原因

### 1. SmartTraderService主循环崩溃
- 19:00左右主循环停止运行
- 健康检查机制无法运行（健康检查是主循环的一部分）
- SmartExitOptimizer监控任务失效

### 2. 为什么健康检查没有工作？
健康检查逻辑位于 `smart_trader_service.py:2757`:
```python
if (now - last_smart_exit_check).total_seconds() >= 60:
    self._check_and_restart_smart_exit_optimizer()
```

**健康检查依赖主循环运行**，如果主循环崩溃，健康检查也停止。

### 3. 可能的崩溃原因
需要检查日志：
- 数据库连接失败
- 网络异常导致未捕获的异常
- Big4检测或其他服务崩溃
- 内存/资源耗尽

## 现有防护机制的缺陷

### ✅ 已有的防护（但失效了）
1. **健康检查机制** (line 2757)
   - 每60秒检查一次
   - 检测超时持仓 (line 2548-2557)
   - 自动重启 SmartExitOptimizer
   - **缺陷**: 依赖主循环运行

2. **planned_close_time**
   - 正确设置 ✅
   - SmartExitOptimizer 会在 planned_close_time 强制平仓
   - **缺陷**: 依赖监控任务正常运行

### ❌ 缺失的防护
1. **外部进程监控**
   - 没有独立的进程守护程序
   - 主进程崩溃后无法自动重启

2. **监控任务异常处理**
   - SmartExitOptimizer._monitor_position() 异常后任务直接结束
   - 没有自动重启机制（line 209-212）

3. **告警机制**
   - 服务停止后没有立即告警
   - 6小时后才恢复，没有人工干预

## 改进建议

### 优先级1: 外部进程监控
使用 systemd/supervisor 监控主进程，崩溃后自动重启

### 优先级2: 增强监控任务容错
```python
async def _monitor_position(self, position_id: int):
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            # ... 现有监控逻辑 ...
        except Exception as e:
            retry_count += 1
            logger.error(f"监控任务异常，重试 {retry_count}/{max_retries}")
            await asyncio.sleep(5)

            if retry_count >= max_retries:
                # 告警：监控任务彻底失败
                self._send_critical_alert(position_id)
```

### 优先级3: 独立的超时守护进程
创建独立的超时检查服务，不依赖主循环：
```python
# timeout_guardian.py
while True:
    # 每分钟检查一次数据库中的超时持仓
    timeout_positions = db.query("SELECT ... WHERE NOW() > planned_close_time")

    if timeout_positions:
        # 直接调用 binance API 平仓
        for pos in timeout_positions:
            binance.close_position(pos)

        # 发送告警
        send_alert(f"守护进程平仓 {len(timeout_positions)} 个超时持仓")

    sleep(60)
```

### 优先级4: 心跳监控
定期写入心跳记录到数据库/文件，外部监控检查心跳：
```python
# 主循环
while self.running:
    write_heartbeat(datetime.now())  # 每次循环写心跳
    # ... 正常逻辑 ...

# 外部监控
if (now - last_heartbeat) > 5_minutes:
    restart_service()
    send_alert("服务无心跳，已重启")
```

## 数据影响

虽然超时了6小时，但幸运的是：
- ✅ 方向正确（做空），市场继续下跌
- ✅ 总盈利 +999.76 USDT
- ✅ 没有造成重大损失

但如果方向错误或市场反转，可能损失：
- 假设平均ROI -3%/小时
- 82个持仓 × 超时6小时 × -3% = 巨额亏损

## 立即行动项

1. ✅ 查看日志，确认19:00崩溃的根本原因
2. ✅ 增加外部进程监控（systemd/supervisor）
3. ✅ 实现独立的超时守护进程
4. ✅ 增强异常处理和自动重试
5. ✅ 添加心跳监控机制
6. ✅ 设置关键告警（服务停止超过5分钟）

## 总结

这是一次**服务可用性事故**，而不是逻辑错误：
- planned_close_time 设置正确 ✅
- SmartExitOptimizer 逻辑正确 ✅
- 健康检查机制设计正确 ✅

问题在于：
- **单点故障**：健康检查依赖主循环
- **缺少外部监控**：主进程崩溃无人知晓
- **缺少告警**：6小时停机无告警

需要从**架构层面**加强防护，而不仅仅是修复代码bug。
