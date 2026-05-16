"""
跨平台单实例 PID 文件锁

用法 (放在 main 入口最前):

    from app.utils.pid_lock import acquire_pid_lock
    acquire_pid_lock("fast_collector_service")  # 不传路径默认 logs/{name}.pid

防止同名进程重复启动 (2026-05-15 因两个 fast_collector 同跑导致 Binance IP 被 ban)。

行为:
- 检测到同名 PID 文件 + 进程仍存活 → 退出,exit code = 1
- PID 文件存在但进程已死 → 删旧 PID 文件,继续
- 自动注册 atexit 删 PID 文件
"""
from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path


def _process_alive(pid: int) -> bool:
    """跨平台判断 PID 是否对应活进程。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows: tasklist 检查
        try:
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=3
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        # Unix: signal 0 不会发任何信号,但会触发权限/存在检查
        # ProcessLookupError → 进程死了
        # PermissionError → 进程活着但属于其他用户 (按活着算)
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False


def acquire_pid_lock(service_name: str, pid_dir: str | None = None) -> None:
    """
    获取 PID 文件锁。同名活进程已在跑 → 打印错误退出。

    Args:
        service_name: 服务名,用作 PID 文件名 (.pid 后缀)
        pid_dir: PID 文件目录,默认项目根目录的 logs/

    Raises:
        SystemExit(1): 检测到重复实例
    """
    # 默认放在项目根目录 logs/
    if pid_dir is None:
        # 假定调用方在项目根目录或子目录
        cwd = Path.cwd()
        pid_dir = cwd / "logs"
    else:
        pid_dir = Path(pid_dir)

    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / f"{service_name}.pid"

    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            old_pid = 0

        if old_pid > 0 and _process_alive(old_pid):
            sys.stderr.write(
                f"\n[PID-LOCK] {service_name} 已在运行 (PID={old_pid}),"
                f"拒绝重复启动。\n"
                f"  PID file: {pid_file}\n"
                f"  如需强制启动,请先 kill {old_pid} 再删 {pid_file}\n\n"
            )
            sys.exit(1)
        else:
            # 旧 PID 但进程已死,清理
            try:
                pid_file.unlink()
            except OSError:
                pass

    # 写新 PID
    cur_pid = os.getpid()
    try:
        pid_file.write_text(str(cur_pid))
    except OSError as e:
        sys.stderr.write(f"[PID-LOCK] 写 PID 文件失败: {e}\n")
        # 写失败不致命,继续运行
        return

    # 注册退出清理
    def _cleanup():
        try:
            if pid_file.exists():
                f_pid = int(pid_file.read_text().strip())
                if f_pid == cur_pid:
                    pid_file.unlink()
        except Exception:
            pass

    atexit.register(_cleanup)
    print(f"[PID-LOCK] {service_name} 启动 (PID={cur_pid}), lock={pid_file}")


def is_running(service_name: str, pid_dir: str | None = None) -> tuple[bool, int]:
    """
    无副作用查询 service 是否在运行。

    Returns:
        (是否运行, PID) — 不运行时 PID=0
    """
    if pid_dir is None:
        pid_dir = Path.cwd() / "logs"
    pid_file = Path(pid_dir) / f"{service_name}.pid"

    if not pid_file.exists():
        return False, 0
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return False, 0

    return _process_alive(pid), pid
