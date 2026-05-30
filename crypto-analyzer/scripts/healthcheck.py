#!/usr/bin/env python3
"""
全系统健康检查 — 检查核心进程是否在跑。

用法:
    python scripts/healthcheck.py            # 简洁输出 + exit code
    python scripts/healthcheck.py --verbose  # 详细输出
    python scripts/healthcheck.py --json     # JSON 输出 (供 cron/monitoring 用)

Exit code:
    0  — 全部 alive
    1  — 至少一个 dead (CRITICAL)
    2  — PID 文件丢失但其他 OK (WARNING)

可加到 crontab 每 5 分钟跑一次,失败时发 Telegram 通知。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 项目根目录 (scripts/ 的上一级)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.pid_lock import is_running  # noqa: E402

# 被监控的服务 (名字必须跟入口脚本里 acquire_pid_lock 的参数一致)
SERVICES = [
    "smart_trader_service",
    "fast_collector_service",
    # app/main.py 用 uvicorn 启动,没走 acquire_pid_lock,改用 HTTP 检查
]

# FastAPI 服务通过 HTTP 检查
HTTP_HEALTHCHECKS = [
    ("app_main", "http://127.0.0.1:9020/health"),
]


def check_pid_services(pid_dir: Path) -> dict:
    """检查所有 PID 锁定服务"""
    results = {}
    for svc in SERVICES:
        alive, pid = is_running(svc, pid_dir=str(pid_dir))
        results[svc] = {"alive": alive, "pid": pid}
    return results


def check_http_services() -> dict:
    """检查 FastAPI 服务"""
    try:
        import requests
    except ImportError:
        return {svc: {"alive": False, "error": "requests not installed"}
                for svc, _ in HTTP_HEALTHCHECKS}

    results = {}
    for svc, url in HTTP_HEALTHCHECKS:
        try:
            r = requests.get(url, timeout=3)
            results[svc] = {"alive": r.status_code == 200, "status_code": r.status_code, "url": url}
        except Exception as e:
            results[svc] = {"alive": False, "error": str(e)[:100], "url": url}
    return results


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    as_json = "--json" in args

    pid_dir = PROJECT_ROOT / "logs"

    pid_results = check_pid_services(pid_dir)
    http_results = check_http_services()

    all_results = {**pid_results, **http_results}
    alive_count = sum(1 for r in all_results.values() if r.get("alive"))
    total = len(all_results)
    all_alive = alive_count == total

    if as_json:
        out = {
            "timestamp": time.time(),
            "alive_count": alive_count,
            "total": total,
            "all_alive": all_alive,
            "services": all_results,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        status_emoji = "OK" if all_alive else "FAIL"
        print(f"[{status_emoji}] {alive_count}/{total} services alive")
        print()
        for svc, r in all_results.items():
            mark = "[OK] " if r.get("alive") else "[FAIL]"
            extra = ""
            if "pid" in r and r["pid"]:
                extra = f" PID={r['pid']}"
            if not r.get("alive"):
                if "error" in r:
                    extra = f" ({r['error']})"
                elif "pid" in r and not r["pid"]:
                    extra = " (无 PID 文件)"
            print(f"  {mark} {svc:35s}{extra}")
            if verbose and "url" in r:
                print(f"         url={r['url']}")

    # Exit code
    if all_alive:
        sys.exit(0)
    elif alive_count == 0:
        sys.exit(1)  # CRITICAL: 全死
    else:
        # 部分死,看具体哪些
        pid_dead = [svc for svc, r in pid_results.items() if not r.get("alive")]
        if pid_dead:
            sys.exit(1)  # 进程死了
        else:
            sys.exit(2)  # 仅 HTTP 检查失败 (FastAPI 死),也是严重


if __name__ == "__main__":
    main()
