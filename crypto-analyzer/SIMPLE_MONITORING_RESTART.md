# SmartExitOptimizer 监控和自动重启机制

**核心思路**: 在主循环中定期检查SmartExitOptimizer是否正常工作，发现问题立即重启它。

**优先级**: P0 - 立即实施
**预计工作量**: 30分钟

---

## 实施方案

### 在主循环中添加监控和自动重启逻辑

修改文件: `smart_trader_service.py` 和 `coin_futures_trader_service.py`

```python
def run(self):
    """主循环 - 带SmartExitOptimizer监控和自动重启"""
    logger.info("========== 启动主循环 ==========")

    last_smart_exit_check = datetime.now()

    while self.running:
        try:
            # ========== 现有逻辑 ==========
            self.collect_latest_data()
            self.generate_signals()
            self.execute_trades()

            # ========== 新增: SmartExitOptimizer健康检查和自动重启 ==========
            now = datetime.now()
            if (now - last_smart_exit_check).total_seconds() >= 60:  # 每分钟检查
                self._check_and_restart_smart_exit_optimizer()
                last_smart_exit_check = now

            time.sleep(self.scan_interval)

        except Exception as e:
            logger.error(f"主循环异常: {e}")
            time.sleep(10)

def _check_and_restart_smart_exit_optimizer(self):
    """检查SmartExitOptimizer健康状态，发现问题立即重启"""
    try:
        if not self.smart_exit_optimizer or not self.event_loop:
            logger.warning("⚠️ SmartExitOptimizer未初始化")
            return

        # ========== 检查1: 监控任务数量是否匹配 ==========
        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取数据库中的开仓持仓数量
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM futures_positions
            WHERE status = 'open'
            AND account_id = %s
        """, (self.account_id,))

        db_count = cursor.fetchone()[0]

        # 获取SmartExitOptimizer中的监控任务数量
        monitoring_count = len(self.smart_exit_optimizer.monitoring_tasks)

        # ========== 检查2: 是否有超时未平仓的持仓 ==========
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM futures_positions
            WHERE status = 'open'
            AND account_id = %s
            AND planned_close_time IS NOT NULL
            AND NOW() > planned_close_time
        """, (self.account_id,))

        timeout_count = cursor.fetchone()[0]

        cursor.close()

        # ========== 判断是否需要重启 ==========
        need_restart = False
        restart_reason = ""

        # 情况1: 监控任务数量不匹配
        if db_count != monitoring_count:
            need_restart = True
            restart_reason = (
                f"监控任务数量不匹配 (数据库{db_count}个持仓, "
                f"SmartExitOptimizer监控{monitoring_count}个)"
            )

        # 情况2: 有超时持仓（说明SmartExitOptimizer没有正常工作）
        if timeout_count > 0:
            need_restart = True
            if restart_reason:
                restart_reason += f"; 发现{timeout_count}个超时未平仓持仓"
            else:
                restart_reason = f"发现{timeout_count}个超时未平仓持仓"

        # ========== 执行重启 ==========
        if need_restart:
            logger.error(
                f"❌ SmartExitOptimizer异常: {restart_reason}\n"
                f"   立即重启SmartExitOptimizer..."
            )

            # 发送告警
            if self.telegram_notifier:
                self.telegram_notifier.send_message(
                    f"⚠️ SmartExitOptimizer自动重启\n\n"
                    f"原因: {restart_reason}\n"
                    f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"操作: 正在重启监控..."
                )

            # 重启SmartExitOptimizer的监控
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._restart_smart_exit_monitoring(),
                self.event_loop
            )

            logger.info("✅ SmartExitOptimizer重启完成")

        else:
            # 正常情况，偶尔打印健康状态
            if datetime.now().minute % 10 == 0:  # 每10分钟打印一次
                logger.debug(
                    f"💓 SmartExitOptimizer健康检查: "
                    f"{monitoring_count}个持仓监控中, "
                    f"{timeout_count}个超时持仓"
                )

    except Exception as e:
        logger.error(f"SmartExitOptimizer健康检查失败: {e}")

async def _restart_smart_exit_monitoring(self):
    """重启SmartExitOptimizer监控"""
    try:
        logger.info("========== 重启SmartExitOptimizer监控 ==========")

        # 1. 取消所有现有监控任务
        if self.smart_exit_optimizer and self.smart_exit_optimizer.monitoring_tasks:
            logger.info(f"取消 {len(self.smart_exit_optimizer.monitoring_tasks)} 个现有监控任务...")

            for position_id, task in list(self.smart_exit_optimizer.monitoring_tasks.items()):
                try:
                    task.cancel()
                    logger.debug(f"  取消监控任务: 持仓{position_id}")
                except Exception as e:
                    logger.warning(f"  取消任务失败: 持仓{position_id} | {e}")

            # 等待任务取消
            await asyncio.sleep(1)

            # 清空监控任务字典
            self.smart_exit_optimizer.monitoring_tasks.clear()
            logger.info("✅ 已清空所有监控任务")

        # 2. 重新启动所有持仓的监控
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, symbol, position_side, planned_close_time
            FROM futures_positions
            WHERE status = 'open'
            AND account_id = %s
            ORDER BY id ASC
        """, (self.account_id,))

        positions = cursor.fetchall()
        cursor.close()

        logger.info(f"发现 {len(positions)} 个开仓持仓需要监控")

        success_count = 0
        fail_count = 0

        for pos in positions:
            position_id, symbol, side, planned_close = pos
            try:
                await self.smart_exit_optimizer.start_monitoring_position(position_id)

                planned_str = planned_close.strftime('%H:%M') if planned_close else 'None'
                logger.info(
                    f"✅ [{success_count+1}/{len(positions)}] 重启监控: "
                    f"持仓{position_id} {symbol} {side} | "
                    f"计划平仓: {planned_str}"
                )
                success_count += 1

            except Exception as e:
                logger.error(f"❌ 重启监控失败: 持仓{position_id} {symbol} | {e}")
                fail_count += 1

        logger.info(
            f"========== 监控重启完成: 成功{success_count}, 失败{fail_count} =========="
        )

        # 3. 发送完成通知
        if self.telegram_notifier:
            self.telegram_notifier.send_message(
                f"✅ SmartExitOptimizer重启完成\n\n"
                f"成功: {success_count}个持仓\n"
                f"失败: {fail_count}个持仓\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

    except Exception as e:
        logger.error(f"❌ 重启SmartExitOptimizer失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

        # 发送失败告警
        if self.telegram_notifier:
            self.telegram_notifier.send_message(
                f"❌ SmartExitOptimizer重启失败\n\n"
                f"错误: {str(e)}\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"请手动检查服务状态"
            )
```

---

## 工作原理

### 1. 每分钟检查健康状态

```
主循环 → 每60秒 → _check_and_restart_smart_exit_optimizer()
                     ↓
                检查两个指标:
                1. 监控任务数 vs 数据库持仓数
                2. 是否有超时未平仓持仓
                     ↓
                发现异常 → 立即重启
```

### 2. 重启流程

```
_restart_smart_exit_monitoring()
  ↓
1. 取消所有现有监控任务
  ↓
2. 清空 monitoring_tasks 字典
  ↓
3. 从数据库查询所有开仓持仓
  ↓
4. 为每个持仓重新启动监控
  ↓
5. 发送Telegram通知
```

### 3. 检测条件

| 条件 | 说明 | 触发重启 |
|------|------|----------|
| `db_count != monitoring_count` | 数据库持仓数与监控任务数不一致 | ✅ 是 |
| `timeout_count > 0` | 有超时未平仓的持仓 | ✅ 是 |
| 两者都正常 | SmartExitOptimizer工作正常 | ❌ 否 |

---

## 优势

1. **简单有效** ⭐
   - 不需要复杂的架构改动
   - 只在主循环添加一个检查函数
   - 约30分钟即可实施

2. **自动修复** ⭐⭐
   - 发现问题立即重启
   - 不需要人工干预
   - 1分钟内完成恢复

3. **实时监控** ⭐⭐⭐
   - 每分钟检查一次
   - 超时持仓最多延迟1分钟被发现
   - Telegram实时告警

4. **无侵入性** ⭐
   - 不修改SmartExitOptimizer核心逻辑
   - 不影响现有功能
   - 风险极低

---

## 实施步骤

### 步骤1: 修改代码

在 `smart_trader_service.py` 中添加上述两个方法：
- `_check_and_restart_smart_exit_optimizer()`
- `_restart_smart_exit_monitoring()`

在 `run()` 方法中添加定时检查：
```python
if (now - last_smart_exit_check).total_seconds() >= 60:
    self._check_and_restart_smart_exit_optimizer()
    last_smart_exit_check = now
```

### 步骤2: 同样修改 coin_futures_trader_service.py

复制同样的逻辑到币本位合约服务。

### 步骤3: 本地测试

```bash
# 本地运行测试
cd d:\test2\crypto-analyzer
python -c "from services.smart_trader_service import SmartTrader; st = SmartTrader(); st.run()"

# 观察日志，确认检查逻辑正常运行
```

### 步骤4: 部署到服务器

```bash
# 上传修改后的文件
git add smart_trader_service.py coin_futures_trader_service.py
git commit -m "feat: 添加SmartExitOptimizer自动监控和重启机制"
git push origin master

# SSH到服务器
ssh user@your-server
cd /path/to/crypto-analyzer

# 拉取最新代码
git pull origin master

# 重启服务
pm2 restart smart_trader
pm2 restart coin_futures_trader

# 查看日志，确认监控正常
pm2 logs smart_trader --lines 50
```

### 步骤5: 验证

```bash
# 观察日志中是否有健康检查消息
pm2 logs smart_trader | grep "健康检查"

# 应该看到（每10分钟）:
# 💓 SmartExitOptimizer健康检查: N个持仓监控中, 0个超时持仓

# 如果发现问题，应该看到:
# ❌ SmartExitOptimizer异常: ...
# ✅ SmartExitOptimizer重启完成
```

---

## 测试场景

### 测试1: 模拟监控任务丢失

```python
# 手动清空监控任务（模拟丢失）
self.smart_exit_optimizer.monitoring_tasks.clear()

# 等待1分钟
# 预期: 主循环检测到不一致，自动重启监控
```

### 测试2: 模拟超时持仓

```python
# 在数据库中设置一个持仓的planned_close_time为过去时间
UPDATE futures_positions
SET planned_close_time = DATE_SUB(NOW(), INTERVAL 1 HOUR)
WHERE id = XXX;

# 等待1分钟
# 预期: 主循环检测到超时持仓，重启SmartExitOptimizer
# 预期: 重启后，超时持仓在1-2分钟内被强制平仓
```

---

## 预期效果

### 当前问题 (修复前)

- ❌ 6个持仓超时67分钟未平仓
- ❌ SmartExitOptimizer监控丢失后无法恢复
- ❌ 需要手动重启服务

### 修复后

- ✅ 每分钟自动检查监控状态
- ✅ 发现问题1分钟内自动重启
- ✅ 超时持仓最多延迟1分钟被发现
- ✅ 完全自动化，无需人工干预
- ✅ Telegram实时告警

---

## 后续优化（可选）

1. **增加检查频率**
   ```python
   # 从60秒改为30秒
   if (now - last_smart_exit_check).total_seconds() >= 30:
   ```

2. **添加更多检查条件**
   ```python
   # 检查event_loop是否正常
   if self.event_loop.is_closed():
       need_restart = True
   ```

3. **记录重启历史**
   ```python
   # 写入数据库
   INSERT INTO smart_exit_restart_log
   (restart_time, reason, success_count, fail_count)
   VALUES (NOW(), %s, %s, %s)
   ```

---

## 总结

**核心优势**:
- ✅ 简单: 只需添加2个方法，30分钟完成
- ✅ 有效: 自动检测和修复，1分钟内恢复
- ✅ 安全: 不修改核心逻辑，风险极低
- ✅ 可靠: 每分钟检查，问题不会被忽略

**立即操作**:
1. 修改 `smart_trader_service.py` 添加监控和重启逻辑
2. 修改 `coin_futures_trader_service.py` 添加同样逻辑
3. 部署到服务器
4. 观察日志确认正常工作

---

**创建时间**: 2026-01-31 19:20
**优先级**: P0 (最高)
**预计工作量**: 30分钟
**风险**: 极低
