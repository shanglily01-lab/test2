"""持仓时间工具 — 统一 UTC 写入与时长计算（兼容 open_time 曾误存 CST 的历史数据）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

DtLike = Union[datetime, str, None]


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(value: DtLike) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip().replace('T', ' ')[:19]
        try:
            return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    return None


def calc_holding_minutes(
    open_time: DtLike,
    close_time: DtLike,
    *,
    created_at: DtLike = None,
    trade_time: DtLike = None,
) -> Optional[int]:
    """
    计算持仓分钟数。close_time 优先，否则 trade_time。
    open_time 若晚于平仓（历史 CST/UTC 混存），回退 created_at 或 open_time-8h。
    """
    close_dt = _parse_dt(close_time) or _parse_dt(trade_time)
    if not close_dt:
        return None

    open_dt = _parse_dt(open_time)
    created_dt = _parse_dt(created_at)

    if open_dt and open_dt <= close_dt:
        start = open_dt
    elif created_dt and created_dt <= close_dt:
        start = created_dt
    elif open_dt and open_dt > close_dt:
        adjusted = open_dt - timedelta(hours=8)
        start = adjusted if adjusted <= close_dt else created_dt
    else:
        start = created_dt

    if not start:
        return None
    return max(0, int((close_dt - start).total_seconds() / 60))
