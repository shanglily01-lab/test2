# test3 → test2 整合部署指南

**日期**: 2026-05-15
**Commit**: `75282b0` (代码整合) + `b669473` (live close 漏洞修复) + `11d6981` (1m K线 + reconcile PnL 修复)

---

## 整合背景

服务器上同时跑两个 crypto 系统 (test2 + test3) 导致:
- **两个 fast_collector_service 同时拉 Binance K 线** → IP 被 Binance ban (-1003)
- 重复的 DB 写入和资源浪费
- 维护成本翻倍

整合后只跑 **test2 一套系统**,保留 test3 的所有独有功能。

---

## 已整合的 7 个文件

| 文件 | 用途 | 调度方式 |
|------|------|---------|
| `strategy_live.py` | 顶部反转做空 (48h涨≥80% + N根无新高) | 独立进程,每 60s 主循环 |
| `strategy_bigmid.py` | Gemini AI 决策大币种 LONG/SHORT/SKIP | 独立进程,每 6h 调 Gemini |
| `strategy_state_db.py` | 上两个策略的状态持久化 | (被 strategy_live/bigmid import) |
| `app/services/gemini_swan_worker.py` | Gemini 红黑天鹅榜采集 | 嵌入 app/main.py,每 2h 跑 |
| `app/services/paper_limit_sync_service.py` | 模拟限价单成交后同步实盘 | 嵌入 app/main.py,每 10s 扫 |
| `app/services/position_sl_tp_monitor.py` | 独立轻量 SL/TP 监控 | 嵌入 app/main.py 或独立 |
| `app/services/securities_filter.py` | 代币化股票过滤器 | (被 gemini_swan_worker import) |

**所有 HTTP API 调用已从 `localhost:9021` 改为 `localhost:9020`**。

---

## 服务器部署步骤 (按顺序执行)

### Step 1: 紧急止血 — 解除 IP ban + 杀重复进程

```bash
# SSH 到服务器,先看 ban 还有多久
date -u    # 当前 UTC 时间
# ban 到 2026-05-15 17:34 UTC (= 北京 01:34 5/16)

# 找出所有 python 进程
ps aux | grep python | grep -v grep

# 杀掉 test2 + test3 所有进程 (准备干净重启)
ps aux | grep -E "smart_trader_service|coin_futures_trader_service|fast_collector|strategy_live|strategy_bigmid|app/main\.py" | grep -v grep | awk '{print $2}' | xargs -r kill -TERM
sleep 5
# 强杀残留
ps aux | grep python | grep -v grep | awk '{print $2}' | xargs -r kill -9

# 等到 ban 过期 (~北京 01:34 5/16)
# 期间什么都别启动
```

### Step 2: 拉最新代码到 test2

```bash
cd /path/to/test2/crypto-analyzer

# 备份当前 .env (重要!)
cp .env .env.backup_$(date +%Y%m%d_%H%M%S)

# 拉代码 (期待是 75282b0 或更新)
git fetch origin
git pull origin master
git log -1 --oneline
# 期待: 75282b0 feat: 整合 test3 系统进 test2

# 校验关键文件
ls -la strategy_live.py strategy_bigmid.py strategy_state_db.py
ls -la app/services/gemini_swan_worker.py app/services/paper_limit_sync_service.py \
       app/services/position_sl_tp_monitor.py app/services/securities_filter.py
```

### Step 3: 补 .env (GEMINI_API_KEY)

```bash
# 检查是否已有
grep -E "^GEMINI_" .env
# 应有: GEMINI_API_KEY=AIzaSyB... 和 GEMINI_MODEL=gemini-3-flash-preview

# 如果没有,从 test3 .env 复制
cat /path/to/test3/crypto-analyzer/.env | grep -E "^GEMINI_" >> .env
grep -E "^GEMINI_" .env    # 再验证
```

### Step 4: 语法检查 (确认服务器代码完整)

```bash
cd /path/to/test2/crypto-analyzer
for f in strategy_live.py strategy_bigmid.py strategy_state_db.py \
         smart_trader_service.py coin_futures_trader_service.py \
         app/services/btc_momentum_trader.py app/services/smart_exit_optimizer.py \
         app/services/gemini_swan_worker.py app/services/paper_limit_sync_service.py \
         app/services/position_sl_tp_monitor.py app/services/securities_filter.py \
         app/trading/futures_trading_engine.py app/trading/coin_futures_trading_engine.py; do
    python -c "import ast; ast.parse(open('$f', encoding='utf-8').read())" && echo "OK: $f" || echo "FAIL: $f"
done
```

### Step 5: 启动所有进程 (确认 ban 已过期)

**重要**: 启动顺序: `app/main.py` 必须先起,因为其他策略通过它的 9020 端口下单。

```bash
cd /path/to/test2/crypto-analyzer
mkdir -p logs

# 1) app/main.py — FastAPI + scheduler + WS 价格服务 + gemini_swan_worker
nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 9020 \
    > logs/main_$(date +%Y-%m-%d).log 2>&1 &
disown
echo "main PID=$!"
sleep 10
# 检查 9020 起来了
curl -s http://localhost:9020/api/futures/health | head -3

# 2) smart_trader_service.py — U本位主策略 (内含 BTC动量 + 多策略 + reconcile)
nohup python smart_trader_service.py \
    > logs/smart_trader_$(date +%Y-%m-%d).log 2>&1 &
disown
echo "smart_trader PID=$!"

# 3) coin_futures_trader_service.py — 币本位
nohup python coin_futures_trader_service.py \
    > logs/coin_futures_$(date +%Y-%m-%d).log 2>&1 &
disown
echo "coin_futures PID=$!"

# 4) fast_collector_service.py — K线采集 (只跑一份!)
nohup python fast_collector_service.py \
    > logs/fast_collector_$(date +%Y-%m-%d).log 2>&1 &
disown
echo "fast_collector PID=$!"

# 5) strategy_live.py — 顶部反转做空
nohup python strategy_live.py \
    > logs/strategy_live_$(date +%Y-%m-%d).log 2>&1 &
disown
echo "strategy_live PID=$!"

# 6) strategy_bigmid.py — Gemini AI 决策
nohup python strategy_bigmid.py \
    > logs/strategy_bigmid_$(date +%Y-%m-%d).log 2>&1 &
disown
echo "strategy_bigmid PID=$!"
```

### Step 6: 验证启动成功

```bash
# 看 6 个进程都在跑
ps aux | grep -E "uvicorn|smart_trader_service|coin_futures_trader|fast_collector|strategy_live|strategy_bigmid" | grep -v grep

# 检查 log 没有报错
tail -30 logs/main_*.log
tail -30 logs/smart_trader_*.log
tail -30 logs/strategy_live_*.log
tail -30 logs/strategy_bigmid_*.log
tail -30 logs/fast_collector_*.log

# 应该看不到 -1003 ban 错误
grep -E "1003|ban" logs/*.log 2>/dev/null | tail -10

# 5-10 分钟后检查有没有新开仓
mysql -h ... -e "SELECT id, symbol, position_side, source, open_time FROM futures_positions WHERE open_time >= DATE_SUB(NOW(), INTERVAL 10 MINUTE) ORDER BY open_time DESC LIMIT 10"
```

### Step 7: 关闭 test3

```bash
# 确认 test2 跑稳后 (建议 24 小时)
# kill 任何还在跑的 test3 进程
ps aux | grep test3 | grep -v grep | awk '{print $2}' | xargs -r kill -TERM

# 备份 test3 后归档
cd /path/to
tar czf test3_archive_$(date +%Y%m%d).tar.gz test3/
# 确认备份 OK 后删除
# rm -rf test3/    # 谨慎执行,确认无依赖
```

---

## 防止重复启动 (Phase 6 建议)

每个进程在启动时加 PID 文件锁:

```bash
# 通用启动模板 (start_<service>.sh)
SERVICE_NAME=$1
PID_FILE="/var/run/${SERVICE_NAME}.pid"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "ERROR: ${SERVICE_NAME} 已在运行 (PID=$OLD_PID),拒绝重复启动"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

cd /path/to/test2/crypto-analyzer
nohup python "${SERVICE_NAME}.py" > "logs/${SERVICE_NAME}_$(date +%Y-%m-%d).log" 2>&1 &
echo $! > "$PID_FILE"
disown
```

或者用 **systemd** 管理(更稳):每个服务一个 .service 文件,自动重启 + 防重复。

---

## 整合后的进程清单

| # | 进程 | 文件 | 作用 |
|---|------|------|------|
| 1 | app/main.py (uvicorn :9020) | app/main.py | FastAPI + WS 价格 + scheduler |
| 2 | smart_trader_service | smart_trader_service.py | U本位主策略 + 多策略 + BTC 动量 + reconcile |
| 3 | coin_futures_trader_service | coin_futures_trader_service.py | 币本位策略 |
| 4 | fast_collector_service | fast_collector_service.py | K线采集 (**只能跑一份**) |
| 5 | strategy_live | strategy_live.py | 顶部反转做空 (test3 迁来) |
| 6 | strategy_bigmid | strategy_bigmid.py | Gemini AI 决策 (test3 迁来) |

---

## 验收 checklist

部署完成后,1 小时内确认:

- [ ] 6 个进程都在 ps aux 里看得到
- [ ] log 无 -1003 IP ban 错误
- [ ] futures_positions 有新开仓 (主策略 + 多策略 + strategy_live)
- [ ] prediction_backtest 每 6h 有新虚拟单
- [ ] big4_trend_history 持续更新 (每 1-3 min 一条)
- [ ] coin_kline_scores 每 5 min 更新
- [ ] live_trading_enabled=0 时无任何实盘 close 调用
- [ ] 资金费率/ETF 数据按定时任务正常同步

---

## 已知风险点

1. **fast_collector_service 没有 PID 锁**: 启动时务必先 kill 所有同名进程,**否则会再次触发 IP ban**
2. **Gemini API 配额**: 免费版有日上限,strategy_bigmid + gemini_swan_worker 同时调用可能超量
3. **paper_limit_sync_service 默认 sync_live=True**: 我已在代码层加了 live_trading_enabled 守卫 (commit b669473),双保险
4. **strategy_live 和 multi_strategy_service.S3 都做顶部做空**: 可能在同 symbol 同时间各开一单 (互不感知)。可加去重逻辑,暂时观察

---

## 后续优化 (Phase 6)

1. PID 文件锁 / systemd 化
2. 每个进程加 healthcheck endpoint
3. strategy_live (topshort) 重构为 multi_strategy_service.S8
4. strategy_bigmid (Gemini) 重构为 S9
5. gemini_swan_worker 输出整合进 SignalScoreV3

---

*文档版本: v1.0 | 创建: 2026-05-15 | 对应 commit: 75282b0*
