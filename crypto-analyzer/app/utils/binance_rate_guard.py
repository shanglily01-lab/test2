"""
Binance API IP级别熔断 (跨进程共享)

历史背景:
- 2026-05-15 因两个 fast_collector 同跑导致 Binance IP 被 ban (-1003)
  当时仅靠 pid_lock 解决"重复进程"问题。
- 2026-05-17 再次出现 IP ban，根因是收到 -1003 后调用方继续轮询，
  封禁时间被自己不断延长。需要 IP 级别熔断。

设计:
- 状态文件: logs/binance_ban_state.json
- 进程内 5s 读盘缓存，避免热路径频繁 IO
- 三条路径都接入: binance_futures_engine (signed REST) / smart_futures_collector (public REST)
- API 侧: 发请求前 is_banned() 命中则直接返回，不打 API
- 解析 -1003: parse_ban_msg() 从错误消息提取 banned until 毫秒时间戳

用法:
    from app.utils.binance_rate_guard import rate_guard, parse_ban_msg

    # 发请求前
    if rate_guard.is_banned():
        return {'success': False, 'error': f'IP banned, skip {rate_guard.seconds_until_unban():.0f}s'}

    # 收到 -1003 后
    until_ms = parse_ban_msg(error_msg)
    if until_ms:
        rate_guard.set_banned_until(until_ms, source='engine')

手动旁路:
    人工误判时可直接删除 logs/binance_ban_state.json 或将 force_clear 改为 true
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional


_BAN_RE = re.compile(r"banned until\s+(\d{10,16})", re.IGNORECASE)


def parse_ban_msg(msg: str) -> Optional[int]:
    """
    从 -1003 错误消息中提取 banned until 毫秒时间戳

    样例消息:
        "Way too many requests; IP(54.179.112.251) banned until 1779063479473.
         Please use the websocket for live updates to avoid bans."

    Returns:
        毫秒时间戳, 解析失败返回 None
    """
    if not msg:
        return None
    m = _BAN_RE.search(msg)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, TypeError):
        return None


class _BinanceRateGuard:
    """单例熔断状态管理器 (跨进程通过文件共享)"""

    # 状态文件路径 (logs/binance_ban_state.json)
    _STATE_FILE = Path.cwd() / "logs" / "binance_ban_state.json"
    # 进程内缓存有效期 (秒) - 避免热路径每次读盘
    _CACHE_TTL = 5.0

    def __init__(self) -> None:
        self._cached_until_ms: int = 0
        self._cached_force_clear: bool = False
        self._cached_at: float = 0.0

    def _refresh_cache(self) -> None:
        """按需读盘刷新内存缓存"""
        now = time.time()
        if (now - self._cached_at) < self._CACHE_TTL:
            return
        self._cached_at = now

        if not self._STATE_FILE.exists():
            self._cached_until_ms = 0
            self._cached_force_clear = False
            return

        try:
            data = json.loads(self._STATE_FILE.read_text(encoding="utf-8"))
            self._cached_until_ms = int(data.get("banned_until_ms") or 0)
            self._cached_force_clear = bool(data.get("force_clear") or False)
        except (OSError, ValueError, TypeError):
            self._cached_until_ms = 0
            self._cached_force_clear = False

    def is_banned(self) -> bool:
        """当前是否处于封禁状态"""
        self._refresh_cache()
        if self._cached_force_clear:
            return False
        if self._cached_until_ms <= 0:
            return False
        now_ms = int(time.time() * 1000)
        return now_ms < self._cached_until_ms

    def seconds_until_unban(self) -> float:
        """距离解封还有多少秒 (未封禁返回 0)"""
        self._refresh_cache()
        if not self.is_banned():
            return 0.0
        now_ms = int(time.time() * 1000)
        return max(0.0, (self._cached_until_ms - now_ms) / 1000.0)

    def banned_until_ms(self) -> int:
        """当前封禁截止时间 (毫秒)，未封禁返回 0"""
        self._refresh_cache()
        return self._cached_until_ms if self.is_banned() else 0

    def set_banned_until(self, until_ms: int, source: str = "unknown") -> bool:
        """
        记录封禁截止时间到状态文件

        Args:
            until_ms: 封禁截止毫秒时间戳
            source: 触发来源 (engine / fast_collector / ...) 用于排查

        Returns:
            True 表示新记录的 until_ms 比已存在的更晚（即扩展了封禁），
            调用方可据此决定是否打 TG 告警等
        """
        if until_ms <= 0:
            return False

        # 强制刷新一次最新状态（不走缓存）
        self._cached_at = 0.0
        self._refresh_cache()
        existing = self._cached_until_ms

        # 只在新封禁时间更晚时才写入
        if until_ms <= existing:
            return False

        try:
            self._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "banned_until_ms": until_ms,
                "set_at_ms": int(time.time() * 1000),
                "source": source,
                "force_clear": False,
            }
            self._STATE_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            # 写盘失败也至少先记到内存，避免完全无熔断
            self._cached_until_ms = until_ms
            self._cached_force_clear = False
            self._cached_at = time.time()
            return True

        # 立刻刷新内存缓存
        self._cached_until_ms = until_ms
        self._cached_force_clear = False
        self._cached_at = time.time()
        return True

    def clear(self) -> None:
        """手动清除封禁状态 (仅用于测试或人工误判)"""
        try:
            if self._STATE_FILE.exists():
                self._STATE_FILE.unlink()
        except OSError:
            pass
        self._cached_until_ms = 0
        self._cached_force_clear = False
        self._cached_at = time.time()


# 模块级单例
rate_guard = _BinanceRateGuard()
