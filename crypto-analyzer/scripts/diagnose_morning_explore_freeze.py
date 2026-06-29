#!/usr/bin/env python3
"""早上探索页卡死巡检 — 查定时任务相关 SQL、锁等待、慢查询、快照新鲜度。

用法（生产服务器）:
  cd /home/test2/crypto-analyzer
  python scripts/diagnose_morning_explore_freeze.py
  python scripts/diagnose_morning_explore_freeze.py --since-hours 12

北京时间 08:00 ≈ UTC 00:00（若服务器为 UTC）:
  - main.py: 12h 复盘 + daily_review 子进程（扫 kline_data / futures_positions）
  - scheduler: position_stats 每 30min 在 :00/:30 触发
  - scheduler: TOP50+评级 每 1h + 15min 轮询（update_top_performing_symbols 全表 GROUP BY）
  - scheduler: update_account_statistics 每 5min（account 全量 closed 聚合）
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config

EXPLORE_SOURCES = ("gemini_explore", "deepseek_explore", "gemini_predict", "deepseek_predict")

# 与探索页/scheduler 相关的高风险 SQL（用于 PROCESSLIST 匹配 & 计时）
HOT_SQL = [
    (
        "account_stats_closed_agg",
        """
        SELECT COUNT(*) AS cnt, COALESCE(SUM(realized_pnl),0) AS pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed' AND realized_pnl IS NOT NULL
        """,
        (),
    ),
    (
        "top50_symbol_stats",
        """
        SELECT symbol, COUNT(*) AS total_trades, COALESCE(SUM(realized_pnl),0) AS pnl
        FROM futures_positions
        WHERE account_id=2 AND status='closed' AND realized_pnl IS NOT NULL
        GROUP BY symbol
        HAVING total_trades >= 1
        LIMIT 5
        """,
        (),
    ),
    (
        "position_stats_all_30d",
        """
        SELECT COUNT(*) AS cnt, COALESCE(SUM(realized_pnl),0) AS pnl
        FROM futures_positions
        WHERE status='closed' AND account_id=2
          AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """,
        (),
    ),
    (
        "gemini_explore_closed_30d",
        """
        SELECT COUNT(*) AS cnt, COALESCE(SUM(realized_pnl),0) AS pnl
        FROM futures_positions
        WHERE source='gemini_explore' AND status='closed' AND account_id=2
          AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """,
        (),
    ),
    (
        "deepseek_explore_closed_30d",
        """
        SELECT COUNT(*) AS cnt, COALESCE(SUM(realized_pnl),0) AS pnl
        FROM futures_positions
        WHERE source='deepseek_explore' AND status='closed' AND account_id=2
          AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """,
        (),
    ),
    (
        "explore_open_list",
        """
        SELECT id, symbol FROM futures_positions
        WHERE source='gemini_explore' AND status='open' AND account_id=2
        ORDER BY open_time DESC LIMIT 50
        """,
        (),
    ),
]

PROCESSLIST_KEYWORDS = (
    "futures_positions",
    "kline_data",
    "position_stats_snapshot",
    "top_performing_symbols",
    "trading_symbol_rating",
    "update_top_performers",
    "candidate_pool",
    "explore_prepared",
    "GET_LOCK",
    "daily_review",
    "retrospective",
    "update_all_coin",
    "CALL update_",
)


def _connect():
    cfg = dict(get_db_config())
    cfg.setdefault("charset", "utf8mb4")
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    cfg.setdefault("connect_timeout", 10)
    cfg.setdefault("read_timeout", 120)
    return pymysql.connect(**cfg)


def _now_labels() -> dict:
    utc = datetime.now(timezone.utc)
    cst = utc + timedelta(hours=8)
    return {
        "utc": utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "cst": cst.strftime("%Y-%m-%d %H:%M:%S CST"),
        "utc_hour": utc.hour,
        "cst_hour": cst.hour,
    }


def _print_schedule_hint(cst_hour: int) -> None:
    print("\n=== 与早上 8 点相关的定时任务（代码侧）===")
    rows = [
        ("00:00 UTC (=08:00 CST)", "main.py", "12h_retrospective 子进程"),
        ("00:00 UTC (=08:00 CST)", "main.py", "daily_review_report 子进程"),
        ("每 5min", "scheduler", "update_account_statistics（全量 closed 聚合）"),
        ("每 30min :00/:30", "scheduler", "refresh_position_stats（多路 futures_positions 聚合）"),
        ("每 1h + 15min 轮询", "scheduler", "TOP50 + trading_symbol_rating（全 symbol GROUP BY）"),
        ("每 6min", "scheduler", "candidate_pool_snapshot（K 线叙事）"),
        ("每 15min", "scheduler", "explore_prepared_snapshot"),
        ("每 4h + 10min", "scheduler", "gemini/deepseek explore worker"),
    ]
    for when, where, what in rows:
        print(f"  [{when:22}] {where:12} {what}")
    if cst_hour >= 8 and cst_hour < 10:
        print("\n  [WARN] 当前处于北京时间 08:00-10:00 窗口，上述任务易叠加，连接池/锁竞争概率高。")


def _check_indexes(cur) -> None:
    print("\n=== futures_positions 关键索引 ===")
    need = {
        "idx_fp_source_account_status_close",
        "idx_fp_source_account_status_open",
        "idx_fp_account_status_close_pnl",
    }
    cur.execute(
        """
        SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols
        FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'futures_positions'
        GROUP BY index_name ORDER BY index_name
        """
    )
    have = set()
    for r in cur.fetchall():
        have.add(r["index_name"])
        if r["index_name"] in need:
            print(f"  OK {r['index_name']}: {r['cols']}")
    missing = sorted(need - have)
    if missing:
        print(f"  缺失: {', '.join(missing)}")
        print("  → migrations/019 + scripts/ensure_db_runtime_guards.py")


def _check_snapshot(cur) -> None:
    print("\n=== data_cache.position_stats_snapshot ===")
    try:
        cur.execute(
            """
            SELECT source, open_count, closed_30d, floating_pnl, win_rate_30d, updated_at
            FROM data_cache.position_stats_snapshot
            WHERE account_id=2
            ORDER BY source
            """
        )
        rows = cur.fetchall()
    except Exception as e:
        print(f"  读取失败: {e}")
        print("  → 确认 migration 022 floating_pnl 已执行；探索页 /stats 依赖此表")
        return
    if not rows:
        print("  (空表 — 探索页统计为 stale/0，但不应再扫主表；请 refresh position-stats)")
        return
    now = datetime.utcnow()
    for r in rows:
        ua = r.get("updated_at")
        age = ""
        if ua:
            if hasattr(ua, "tzinfo") and ua.tzinfo:
                ua_naive = ua.replace(tzinfo=None)
            else:
                ua_naive = ua
            age_min = (now - ua_naive).total_seconds() / 60
            age = f" age={age_min:.0f}min"
            if age_min > 45:
                age += " [STALE]"
        fl = r.get("floating_pnl")
        print(
            f"  {r['source']:20} open={r.get('open_count')} closed30={r.get('closed_30d')} "
            f"float={fl} wr={r.get('win_rate_30d')}{age}"
        )


def _check_locks(cur) -> None:
    print("\n=== 命名锁 / TOP50 刷新状态 ===")
    for lock_name in ("update_top_performers_refresh",):
        cur.execute("SELECT IS_USED_LOCK(%s) AS holder", (lock_name,))
        holder = (cur.fetchone() or {}).get("holder")
        cur.execute("SELECT IS_FREE_LOCK(%s) AS free_lock", (lock_name,))
        free = (cur.fetchone() or {}).get("free_lock")
        print(f"  {lock_name}: holder={holder} free={free}")
    cur.execute(
        """
        SELECT setting_key, setting_value, updated_at
        FROM system_settings
        WHERE setting_key IN ('rating_refresh_next_due_utc', 'rating_refresh_last_ok_utc')
        ORDER BY setting_key
        """
    )
    for r in cur.fetchall():
        print(f"  {r['setting_key']}: {r['setting_value']} (updated {r.get('updated_at')})")


def _check_processlist(cur, min_time_s: int = 5) -> None:
    print(f"\n=== 当前慢查询 (TIME>={min_time_s}s, 探索/维护相关) ===")
    cur.execute(
        """
        SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE, LEFT(INFO, 400) AS info
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep' AND TIME >= %s
        ORDER BY TIME DESC
        LIMIT 30
        """,
        (min_time_s,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  (无长时间运行中的非 Sleep 连接)")
    else:
        for r in rows:
            info = (r.get("info") or "").lower()
            flag = "!" if any(k in info for k in PROCESSLIST_KEYWORDS) else " "
            print(
                f"  {flag} id={r['ID']} time={r['TIME']}s state={r.get('STATE')} "
                f"user={r.get('USER')} info={(r.get('info') or '')[:200]}"
            )

    print("\n=== InnoDB 锁等待 (Top 10) ===")
    try:
        cur.execute(
            """
            SELECT
              r.trx_mysql_thread_id AS waiting_thread,
              TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_s,
              LEFT(r.trx_query, 200) AS waiting_query,
              b.trx_mysql_thread_id AS blocking_thread,
              LEFT(b.trx_query, 200) AS blocking_query
            FROM information_schema.innodb_lock_waits w
            JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
            JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id
            ORDER BY wait_s DESC LIMIT 10
            """
        )
        locks = cur.fetchall()
        if not locks:
            print("  (无)")
        else:
            for row in locks:
                print(f"  wait={row.get('wait_s')}s waiting={row.get('waiting_query')}")
                print(f"    blocked_by={row.get('blocking_query')}")
    except Exception as e:
        print(f"  (无法查询: {e})")


def _bench_hot_sql(cur) -> None:
    print("\n=== 高风险 SQL 实测耗时 ===")
    for name, sql, params in HOT_SQL:
        t0 = time.perf_counter()
        try:
            cur.execute(sql, params)
            row = cur.fetchone()
            ms = int((time.perf_counter() - t0) * 1000)
            extra = ""
            if row and "cnt" in row:
                extra = f" rows={row.get('cnt')}"
            warn = " [SLOW]" if ms > 3000 else ""
            print(f"  {name:30} {ms:5}ms{extra}{warn}")
        except Exception as e:
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"  {name:30} FAIL {ms}ms: {e}")

        if "closed" in name or "stats" in name:
            try:
                cur.execute("EXPLAIN " + sql, params)
                plan = cur.fetchone() or {}
                print(
                    f"    EXPLAIN: type={plan.get('type')} key={plan.get('key')} "
                    f"rows={plan.get('rows')}"
                )
            except Exception:
                pass


def _check_explore_runs(cur, since_hours: int) -> None:
    print(f"\n=== 探索 worker 近 {since_hours}h 运行（是否卡在 partial）===")
    for table, label in (
        ("gemini_explore_runs", "gemini_explore"),
        ("deepseek_explore_runs", "deepseek_explore"),
    ):
        cur.execute(
            f"""
            SELECT id, status, elapsed_s, asof_utc, triggered_by, LEFT(error_msg,80) AS err
            FROM {table}
            WHERE asof_utc >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY id DESC LIMIT 5
            """,
            (since_hours,),
        )
        rows = cur.fetchall()
        print(f"  -- {label} --")
        if not rows:
            print("    (无记录)")
            continue
        for r in rows:
            st = r.get("status")
            flag = " [PARTIAL]" if st == "partial" else ""
            print(
                f"    id={r['id']} status={st}{flag} elapsed={r.get('elapsed_s')}s "
                f"at={r.get('asof_utc')} by={r.get('triggered_by')} err={r.get('err') or ''}"
            )


def _check_mysql_events(cur) -> None:
    print("\n=== MySQL EVENT（若有 update_all_coin_scores 等）===")
    try:
        cur.execute(
            """
            SELECT EVENT_NAME, STATUS, INTERVAL_VALUE, INTERVAL_FIELD,
                   LAST_EXECUTED, STARTS, ENDS
            FROM information_schema.EVENTS
            WHERE EVENT_SCHEMA = DATABASE()
            ORDER BY EVENT_NAME
            """
        )
        rows = cur.fetchall()
        if not rows:
            print("  (当前库无 EVENT)")
        else:
            for r in rows:
                print(
                    f"  {r['EVENT_NAME']}: {r['STATUS']} every {r['INTERVAL_VALUE']}{r['INTERVAL_FIELD']} "
                    f"last={r.get('LAST_EXECUTED')}"
                )
    except Exception as e:
        print(f"  (无法读取 EVENTS: {e})")


def main() -> int:
    ap = argparse.ArgumentParser(description="早上探索页卡死巡检")
    ap.add_argument("--since-hours", type=int, default=6, help="探索 runs 回看小时数")
    ap.add_argument("--min-process-time", type=int, default=5, help="PROCESSLIST 最短秒数")
    args = ap.parse_args()

    labels = _now_labels()
    print("=== 时间 ===")
    print(f"  服务器 UTC: {labels['utc']}")
    print(f"  北京时间:   {labels['cst']}")

    _print_schedule_hint(labels["cst_hour"])

    conn = _connect()
    try:
        with conn.cursor() as cur:
            _check_indexes(cur)
            _check_snapshot(cur)
            _check_locks(cur)
            _check_processlist(cur, min_time_s=args.min_process_time)
            _bench_hot_sql(cur)
            _check_explore_runs(cur, args.since_hours)
            _check_mysql_events(cur)
    finally:
        conn.close()

    print("\n=== 建议 ===")
    print("  1. 若 account_stats / top50_symbol_stats >3s → 跑 ensure_db_runtime_guards.py 补索引")
    print("  2. 若 PROCESSLIST 有 update_top_performers / daily_review → 等其结束或错峰 scheduler")
    print("  3. 若 snapshot updated_at >45min → curl -X POST .../api/data-cache/refresh/position-stats")
    print("  4. 若 gemini_explore_runs status=partial 持续 → 查 scheduler 日志 explore 卡死")
    print("  5. 探索页 API 已禁止回退扫主表；卡死多为连接池被维护任务占满，看上面慢查询")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
