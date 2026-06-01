"""Closed-position PnL win/loss/breakeven counts (wins + losses + breakeven == total)."""

PNL_COUNT_SELECT = """
    COUNT(*) AS total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses,
    SUM(CASE WHEN COALESCE(realized_pnl, 0) = 0 THEN 1 ELSE 0 END) AS breakeven
"""


def parse_pnl_counts(row: dict) -> dict:
    """Parse aggregate row from PNL_COUNT_SELECT (or compatible columns)."""
    total = int(row.get("total_trades") or row.get("total") or 0)
    wins = int(row.get("wins") or 0)
    losses = int(row.get("losses") or 0)
    if "breakeven" in row and row["breakeven"] is not None:
        breakeven = int(row["breakeven"] or 0)
    else:
        breakeven = max(0, total - wins - losses)
    win_rate = round(wins / total * 100, 2) if total > 0 else 0
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
    }
