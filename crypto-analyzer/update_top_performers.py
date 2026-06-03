"""
每日更新 U 本位模拟仓盈亏榜单与评级联动

1. top_performing_symbols: 累计盈利前 50（至少 5 笔平仓）
2. 白名单: 盈利 > 200U 或 胜率 > 55% → rating_level=0
3. 黑名单3级: 亏损 > 200U 且 胜率 < 40% → rating_level=3
"""

from app.utils.config_loader import get_db_config
from app.services.optimization_config import OptimizationConfig
from app.utils.futures_symbol import futures_symbol_rating_canonical
import pymysql
from loguru import logger
from datetime import datetime
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

MYSQL_CONFIG = {**get_db_config()}

MIN_TRADES = 5
TOP_N_DEFAULT = 50
WHITELIST_MIN_PNL = 200.0
WHITELIST_MIN_WIN_RATE = 55.0
BLACKLIST_MAX_PNL = -200.0
BLACKLIST_MAX_WIN_RATE = 40.0


def qualifies_whitelist(pnl: float, win_rate: float) -> bool:
    """白名单: 盈利 > 200U 或 胜率 > 55%（严格大于）."""
    return pnl > WHITELIST_MIN_PNL or win_rate > WHITELIST_MIN_WIN_RATE


def _should_ban_level3(pnl: float, win_rate: float) -> tuple[bool, list[str]]:
    """黑名单3级: 累计亏损 > 200U 且 胜率 < 40%（两项同时满足）."""
    if pnl < BLACKLIST_MAX_PNL and win_rate < BLACKLIST_MAX_WIN_RATE:
        return True, [
            f"累计盈利{pnl:.0f}U",
            f"胜率{win_rate:.1f}%",
        ]
    return False, []

_SYMBOL_STATS_SQL = """
    SELECT
        symbol,
        COUNT(*) as total_trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
        COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
        COALESCE(AVG(realized_pnl), 0) as avg_pnl_per_trade,
        COALESCE(MAX(realized_pnl), 0) as max_single_profit,
        COALESCE(MIN(realized_pnl), 0) as max_single_loss,
        CASE
            WHEN SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) > 0
            THEN (SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*))
            ELSE 0
        END as win_rate,
        COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) as gross_profit,
        COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END), 0) as gross_loss,
        CASE
            WHEN SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END) > 0
            THEN SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) /
                 SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END)
            ELSE NULL
        END as profit_factor
    FROM futures_positions
    WHERE
        account_id = %s
        AND status = 'closed'
        AND realized_pnl IS NOT NULL
    GROUP BY symbol
    HAVING total_trades >= %s
"""


def _fetch_all_symbol_stats(cursor, account_id: int, min_trades: int = MIN_TRADES):
    cursor.execute(_SYMBOL_STATS_SQL, (account_id, min_trades))
    return cursor.fetchall()


def _apply_whitelist_and_blacklist(symbol_stats: list, opt: OptimizationConfig) -> dict:
    """按日终规则调整白名单 / 黑名单3级."""
    promoted = 0
    banned = 0
    skipped_wl = 0
    skipped_bl = 0

    for row in symbol_stats:
        symbol = row["symbol"]
        pnl = float(row["total_realized_pnl"] or 0)
        wr = float(row["win_rate"] or 0)
        gross_profit_est = float(row.get("gross_profit") or 0)
        gross_loss_est = float(row.get("gross_loss") or 0)
        total_trades = int(row["total_trades"] or 0)

        canon = futures_symbol_rating_canonical(symbol)
        cur_level = opt.get_symbol_rating_level(symbol)

        ban, parts = _should_ban_level3(pnl, wr)
        if ban:
            if cur_level >= 3:
                skipped_bl += 1
                continue
            reason = "日终自动黑名单3级: " + ", ".join(parts)
            opt.update_symbol_rating(
                symbol=canon,
                new_level=3,
                reason=reason,
                total_loss_amount=gross_loss_est,
                total_profit_amount=gross_profit_est,
                win_rate=wr / 100.0,
                total_trades=total_trades,
            )
            banned += 1
            logger.info(f"[评级] L3 {symbol} | {reason}")
            continue

        if qualifies_whitelist(pnl, wr):
            if cur_level == 0:
                skipped_wl += 1
                continue
            if cur_level >= 3:
                skipped_wl += 1
                continue
            parts = []
            if pnl > WHITELIST_MIN_PNL:
                parts.append(f"累计盈利{pnl:.0f}U")
            if wr > WHITELIST_MIN_WIN_RATE:
                parts.append(f"胜率{wr:.1f}%")
            reason = "日终自动白名单: " + ", ".join(parts)
            opt.update_symbol_rating(
                symbol=canon,
                new_level=0,
                reason=reason,
                total_loss_amount=gross_loss_est,
                total_profit_amount=gross_profit_est,
                win_rate=wr / 100.0,
                total_trades=total_trades,
            )
            promoted += 1
            logger.info(f"[评级] WL {symbol} | {reason}")

    return {
        "promoted_whitelist": promoted,
        "banned_level3": banned,
        "skipped_whitelist": skipped_wl,
        "skipped_blacklist": skipped_bl,
    }


def update_top_performing_symbols(account_id: int = 2, top_n: int = TOP_N_DEFAULT):
    """
    更新盈利 Top N 交易对，并执行白名单 / 黑名单3级日终规则。
    """
    conn = None
    try:
        conn = pymysql.connect(
            **MYSQL_CONFIG,
            autocommit=True,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
        )
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        logger.info("=" * 80)
        logger.info(f"开始盈亏日终更新 (账户ID: {account_id}, Top {top_n})")
        logger.info("=" * 80)

        logger.info("统计所有交易对历史表现...")
        all_stats = _fetch_all_symbol_stats(cursor, account_id, MIN_TRADES)
        if not all_stats:
            logger.warning("没有找到符合条件的交易对")
            return

        all_stats_sorted = sorted(
            all_stats,
            key=lambda r: float(r["total_realized_pnl"] or 0),
            reverse=True,
        )
        top_symbols = all_stats_sorted[:top_n]
        logger.info(f"找到 {len(all_stats)} 个达标交易对，写入 Top {len(top_symbols)}")

        logger.info("清空 top_performing_symbols 旧数据...")
        cursor.execute("TRUNCATE TABLE top_performing_symbols")

        logger.info(f"插入 Top {top_n} 盈利榜单...")
        insert_count = 0
        for rank, symbol_data in enumerate(top_symbols, start=1):
            cursor.execute(
                """
                INSERT INTO top_performing_symbols (
                    symbol, total_realized_pnl, total_trades,
                    winning_trades, losing_trades, win_rate,
                    avg_pnl_per_trade, max_single_profit, max_single_loss,
                    profit_factor, rank_score, last_updated
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    symbol_data["symbol"],
                    float(symbol_data["total_realized_pnl"]),
                    symbol_data["total_trades"],
                    symbol_data["winning_trades"],
                    symbol_data["losing_trades"],
                    float(symbol_data["win_rate"]),
                    float(symbol_data["avg_pnl_per_trade"]),
                    float(symbol_data["max_single_profit"]),
                    float(symbol_data["max_single_loss"]),
                    float(symbol_data["profit_factor"])
                    if symbol_data["profit_factor"]
                    else None,
                    rank,
                    datetime.now(),
                ),
            )
            insert_count += 1
            if rank <= 10:
                logger.info(
                    f"#{rank:2d} {symbol_data['symbol']:12s} | "
                    f"盈利: {symbol_data['total_realized_pnl']:+10.2f} USDT | "
                    f"交易: {symbol_data['total_trades']:4d} | "
                    f"胜率: {symbol_data['win_rate']:5.1f}%"
                )

        logger.info("=" * 80)
        logger.info(f"Top {top_n} 榜单完成，共 {insert_count} 条")
        logger.info("=" * 80)

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_count,
                SUM(total_realized_pnl) as total_pnl,
                AVG(win_rate) as avg_win_rate,
                MIN(total_realized_pnl) as min_pnl,
                MAX(total_realized_pnl) as max_pnl
            FROM top_performing_symbols
            """
        )
        summary = cursor.fetchone()
        if summary and summary.get("total_count"):
            logger.info("榜单摘要:")
            logger.info(f"   交易对数量: {summary['total_count']}")
            logger.info(f"   总盈利: {summary['total_pnl']:+.2f} USDT")
            logger.info(f"   平均胜率: {summary['avg_win_rate']:.1f}%")
            logger.info(
                f"   盈利范围: {summary['min_pnl']:+.2f} ~ {summary['max_pnl']:+.2f} USDT"
            )

        logger.info(
            f"评级规则: 白名单 盈利>{WHITELIST_MIN_PNL}U 或 胜率>{WHITELIST_MIN_WIN_RATE}% | "
            f"黑名单3级 盈利<{BLACKLIST_MAX_PNL}U 且 胜率<{BLACKLIST_MAX_WIN_RATE}%"
        )
        opt = OptimizationConfig(MYSQL_CONFIG)
        rating_result = _apply_whitelist_and_blacklist(all_stats, opt)
        logger.info(
            f"评级完成: 白名单+{rating_result['promoted_whitelist']} "
            f"黑名单3级+{rating_result['banned_level3']} "
            f"(跳过已有白名单 {rating_result['skipped_whitelist']}, "
            f"已是L3 {rating_result['skipped_blacklist']})"
        )

        cursor.close()

    except Exception as e:
        logger.error(f"盈亏日终更新失败: {e}")
        import traceback

        logger.error(traceback.format_exc())

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    logger.info("开始更新盈利 Top 50 + 白名单/黑名单3级...")
    update_top_performing_symbols(account_id=2, top_n=50)
    logger.info("更新完成！")
