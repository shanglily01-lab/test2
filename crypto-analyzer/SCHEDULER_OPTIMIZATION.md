# Scheduler 数据采集优化方案

## 问题诊断

### 核心问题
**任务执行时间 > 任务调度间隔** 导致任务堆积,数据采集延迟

### 问题数据
- 配置的交易对数: **39个**
- 合约数据采集间隔: **5秒/次**
- 每个交易对延迟: **0.5秒**
- **实际执行时间**: 39 × 0.5 = **19.5秒**
- **问题**: 19.5秒 >> 5秒,任务严重堆积!

## 解决方案

### 方案A: 快速修复 (推荐优先使用)

修改采集间隔,从5秒改为30秒:

```python
# app/scheduler.py Line 969-972
# 修改前:
schedule.every(5).seconds.do(
    lambda: asyncio.run(self.collect_binance_futures_data())
)

# 修改后:
schedule.every(30).seconds.do(  # ← 改为30秒
    lambda: asyncio.run(self.collect_binance_futures_data())
)
```

**优点**: 修改简单,立即生效
**缺点**: 数据更新频率降低 (从5秒变成30秒)

---

### 方案B: 性能优化 (推荐长期使用)

减少每个交易对的采集延迟:

```python
# app/scheduler.py Line 429
# 修改前:
await asyncio.sleep(0.5)

# 修改后:
await asyncio.sleep(0.1)  # ← 改为0.1秒
```

**计算**:
- 新的总耗时 = 39 × 0.1 = **3.9秒** < 5秒 ✅
- 可以保持5秒的采集间隔

**优点**: 保持高频采集,性能更好
**缺点**: 需要确认API限流不会触发

---

### 方案C: 并发采集 (最优方案)

使用 `asyncio.gather` 并发采集所有交易对:

#### 1. 修改 `collect_binance_futures_data` 方法

在 `app/scheduler.py` 的 Line 295-447 处替换为:

```python
async def collect_binance_futures_data(self):
    """采集币安合约数据 (每1分钟) - 并发版本"""
    if not self.futures_collector:
        return

    task_name = 'binance_futures_1m'
    try:
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集币安合约数据...")

        # 定义单个交易对的采集任务
        async def fetch_symbol_data(symbol: str) -> tuple:
            """采集单个交易对的数据,返回 (symbol, success, data)"""
            try:
                data = await self.futures_collector.fetch_all_data(symbol, timeframe='1m')

                if not data:
                    return (symbol, False, None)

                # 保存所有数据 (ticker, kline, funding, OI, long/short ratio)
                # ... 保存逻辑与原代码相同 ...

                return (symbol, True, data)

            except Exception as e:
                logger.error(f"  ✗ {symbol}: {e}")
                return (symbol, False, None)

        # 并发采集所有交易对
        tasks = [fetch_symbol_data(symbol) for symbol in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 统计结果
        collected_count = sum(1 for _, success, _ in results if success)
        error_count = len(results) - collected_count

        # 更新统计
        self.task_stats[task_name]['count'] += 1
        self.task_stats[task_name]['last_run'] = datetime.now()

        logger.info(
            f"  ✓ 合约数据采集完成: 成功 {collected_count}/{len(self.symbols)}, "
            f"失败 {error_count}"
        )

    except Exception as e:
        logger.error(f"合约数据采集任务失败: {e}")
        self.task_stats[task_name]['last_error'] = str(e)
```

**性能提升**:
- **原来**: 39 × 0.5秒 = 19.5秒 (顺序执行)
- **现在**: ≈ 1-2秒 (并发执行,受限于网络延迟)
- **提升**: **10倍+**

**优点**:
- 大幅提升采集速度
- 保持5秒的高频采集
- 不会堆积任务

**缺点**:
- 代码修改较多
- 需要测试API并发限流

---

### 方案D: 分批并发 (折中方案)

将39个交易对分成4批,每批10个并发:

```python
async def collect_binance_futures_data(self):
    """采集币安合约数据 - 分批并发版本"""
    if not self.futures_collector:
        return

    task_name = 'binance_futures_1m'
    batch_size = 10  # 每批10个交易对

    try:
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集币安合约数据...")

        collected_count = 0
        error_count = 0

        # 分批处理
        for i in range(0, len(self.symbols), batch_size):
            batch = self.symbols[i:i+batch_size]

            # 并发采集这一批
            async def fetch_symbol_data(symbol):
                try:
                    data = await self.futures_collector.fetch_all_data(symbol, timeframe='1m')
                    # 保存数据...
                    return True
                except Exception as e:
                    logger.error(f"  ✗ {symbol}: {e}")
                    return False

            tasks = [fetch_symbol_data(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            collected_count += sum(1 for r in results if r is True)
            error_count += sum(1 for r in results if r is not True)

            # 批次间短暂延迟
            if i + batch_size < len(self.symbols):
                await asyncio.sleep(0.2)

        # 更新统计
        self.task_stats[task_name]['count'] += 1
        self.task_stats[task_name]['last_run'] = datetime.now()

        logger.info(
            f"  ✓ 合约数据采集完成: 成功 {collected_count}/{len(self.symbols)}, "
            f"失败 {error_count}"
        )

    except Exception as e:
        logger.error(f"合约数据采集任务失败: {e}")
        self.task_stats[task_name]['last_error'] = str(e)
```

**性能**:
- 总耗时 = 4批 × (并发时间 + 0.2秒) ≈ **4-5秒** < 5秒 ✅

---

## 推荐实施步骤

### 第一步: 快速修复 (立即实施)

1. 修改 `app/scheduler.py` Line 969:
   ```python
   schedule.every(30).seconds.do(  # 改为30秒
   ```

2. 修改 `app/scheduler.py` Line 429:
   ```python
   await asyncio.sleep(0.1)  # 改为0.1秒
   ```

3. 重启 scheduler 服务

### 第二步: 监控效果

运行1-2天,观察:
- 数据采集是否及时?
- 是否还有延迟?
- 日志中是否有错误?

### 第三步: 长期优化 (推荐)

如果快速修复效果不佳,实施**方案C (并发采集)**:
- 大幅提升性能
- 支持更高频率采集
- 代码更健壮

---

## 其他优化建议

### 1. 添加任务监控

在 `collect_binance_futures_data` 开始时记录时间:

```python
start_time = datetime.now()
# ... 采集逻辑 ...
elapsed = (datetime.now() - start_time).total_seconds()

if elapsed > 4:  # 如果超过4秒,记录警告
    logger.warning(f"合约数据采集耗时过长: {elapsed:.1f}秒 (共{len(self.symbols)}个交易对)")
```

### 2. 动态调整延迟

根据交易对数量自动调整延迟:

```python
# 目标总耗时: 4秒
target_duration = 4.0
delay_per_symbol = target_duration / len(self.symbols)
await asyncio.sleep(min(delay_per_symbol, 0.5))  # 最多0.5秒
```

### 3. 错误重试机制

对失败的交易对进行重试:

```python
failed_symbols = []
for symbol in self.symbols:
    try:
        data = await self.futures_collector.fetch_all_data(symbol)
    except Exception as e:
        failed_symbols.append(symbol)

# 重试失败的交易对
if failed_symbols:
    logger.info(f"重试 {len(failed_symbols)} 个失败的交易对...")
    for symbol in failed_symbols:
        # 重试逻辑...
```

---

## 结论

**立即实施**: 方案A + 方案B (快速修复)
**长期目标**: 方案C (并发采集)

预期效果:
- 数据采集延迟从 **19.5秒** 降低到 **1-2秒**
- 采集频率保持 **5秒/次** 或更高
- 系统稳定性大幅提升
