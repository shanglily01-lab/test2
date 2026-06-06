"""
每日更新 U 本位模拟仓盈利 TOP50 榜单 + 交易对评级（统一核心机制）

1. top_performing_symbols: 累计盈利前 50（至少 5 笔平仓）
2. trading_symbol_rating: 按全仓累计规则评定 L0/L1/L2/L3

评级规则（全仓累计，至少5笔交易）:
  L0 白名单:    盈利 > 200U 且 胜率 > 55%（双条件）
  L1 黑名单1级: 盈利 > 50U 或 胜率 > 50%
  L2 黑名单2级: -100 < 盈利 < 0 或 胜率 > 44%
  L3 黑名单3级: 盈利 < -100U 且 胜率 < 44%（双条件）

优先级: L3(最严重)→L0(最优)→L1→L2→默认0
"""

from app.utils.config_loader import get_db_config
from app.services.optimization_config import OptimizationConfig
from app.utils.futures_symbol import futures_symbol_rating_canonical
from typing import Dict, List, Optional, Any, Tuple
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


# ── 评级规则 ──────────────────────────────────────────────

def compute_rating_level(pnl: float, win_rate_pct: float, total_trades: int) -> Tuple[int, str]:
    """
    根据全仓累计 PnL 和胜率计算评级等级。
    优先级：最严重/最具体的条件优先。
    """
    if total_trades < MIN_TRADES:
        return 0, "交易不足5笔，默认白名单"

    # L3: 亏损 > 100U 且 胜率 < 44%（双条件最严重，优先拦截）
    if pnl < -100.0 and win_rate_pct < 44.0:
        return 3, f"黑名单3级: 累计盈利{pnl:.0f}U, 胜率{win_rate_pct:.1f}%"

    # L0: 盈利 > 200U 且 胜率 > 55%（双条件最优）
    if pnl > 200.0 and win_rate_pct > 55.0:
        return 0, f"白名单: 累计盈利{pnl:.0f}U, 胜率{win_rate_pct:.1f}%"

    # L1: 盈利 > 50U 或 胜率 > 50%
    if pnl > 50.0 or win_rate_pct > 50.0:
        parts = []
        if pnl > 50.0:
            parts.append(f"累计盈利{pnl:.0f}U")
        if win_rate_pct > 50.0:
            parts.append(f"胜率{win_rate_pct:.1f}%")
        return 1, "黑名单1级: " + ", ".join(parts)

    # L2: -100 < 盈利 < 0 或 胜率 > 44%
    if (-100.0 < pnl < 0) or win_rate_pct > 44.0:
        parts = []
        if -100.0 < pnl < 0:
            parts.append(f"累计亏损{pnl:.0f}U")
        if win_rate_pct > 44.0:
            parts.append(f"胜率{win_rate_pct:.1f}%")
        return 2, "黑名单2级: " + ", ".join(parts)

    return 0, "未触发限制，默认白名单"


# ── SQL ───────────────────────────────────────────────────

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


# ── 工具函数 ──────────────────────────────────────────────

def _fetch_all_symbol_stats(cursor, account_id: int, min_trades: int = MIN_TRADES) -> List[Dict[str, Any]]:
    cursor.execute(_SYMBOL_STATS_SQL, (account_id, min_trades))
    return cursor.fetchall()


def _apply_rating(symbol_stats: list, opt: OptimizationConfig):
    """遍历所有有交易过的币，按全仓累计规则更新评级。"""
    results = {
        "updated": 0,
        "detail": {"L0": 0, "L1": 0, "L2": 0, "L3": 0},
    }

    for row in symbol_stats:
        symbol = row["symbol"]
        pnl = float(row["total_realized_pnl"] or 0)
        wr = float(row["win_rate"] or 0)
        trades = int(row["total_trades"] or 0)
        gross_loss = float(row["gross_loss"] or 0)
        gross_profit = float(row["gross_profit"] or 0)

        new_level, reason = compute_rating_level(pnl, wr, trades)

        cur = opt.get_symbol_rating(symbol)
        old_level = cur["rating_level"] if cur else 0

        if new_level != old_level:
            opt.update_symbol_rating(
                symbol=futures_symbol_rating_canonical(symbol),
                new_level=new_level,
                reason=reason,
                total_loss_amount=gross_loss,
                total_profit_amount=gross_profit,
                win_rate=wr / 100.0,
                total_trades=trades,
            )
            results["updated"] += 1
            results["detail"][f"L{new_level}"] = results["detail"].get(f"L{new_level}", 0) + 1
            logger.info(f"[评级] {symbol} L{old_level}→L{new_level} | {reason}")
        else:
            results["detail"][f"L{new_level}"] = results["detail"].get(f"L{new_level}", 0) + 1

    return results


def _clean_stale_ratings(cursor, active_symbols: set):
    """清理交易不足5笔的旧自动评级记录，避免历史垃圾数据残留。"""
    try:
        cursor.execute(
            """
            SELECT symbol, rating_level, level_change_reason, total_trades
            FROM trading_symbol_rating
            """
        )
        stale = []
        for r in cursor.fetchall():
            sym = r["symbol"]
            if sym in active_symbols:
                continue
            reason = (r.get("level_change_reason") or "")
            total_trades = int(r.get("total_trades") or 0)
            if total_trades == 0 and "白名单" in reason:
                stale.append(sym)

        if stale:
            placeholders = ",".join("%s" for _ in stale)
            cursor.execute(
                f"DELETE FROM trading_symbol_rating WHERE symbol IN ({placeholders})",
                stale,
            )
            logger.info(f"[评级清理] 移除 {len(stale)} 个交易不足5笔的旧自动白名单: {', '.join(stale[:10])}{'...' if len(stale) > 10 else ''}")
    except Exception as e:
        logger.warning(f"[评级清理] 出错(不影响主流程): {e}")


# ── 主入口 ────────────────────────────────────────────────

def update_top_performing_symbols(
    account_id: int = 2,
    top_n: int = TOP_N_DEFAULT,
    skip_rating: bool = False,
):
    """
    日终维护：更新 TOP50 榜单 + 交易对评级。

    参数:
        account_id: 模拟仓 account_id
        top_n: TOP 榜数量
        skip_rating: True 则跳过评级更新（仅刷新榜单时用）
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
        logger.info(f"开始更新盈利 Top {top_n} 榜单 (账户ID: {account_id})")
        logger.info("=" * 80)

        logger.info("统计所有交易对历史表现...")
        all_stats = _fetch_all_symbol_stats(cursor, account_id, MIN_TRADES)
        if not all_stats:
            logger.warning("没有找到符合条件的交易对")
            return

        # ── TOP50 榜单 ──
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
                    float(symbol_data["profit_factor"]) if symbol_data["profit_factor"] else None,
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
            logger.info(f"   盈利范围: {summary['min_pnl']:+.2f} ~ {summary['max_pnl']:+.2f} USDT")

        # ── 评级更新 ──
        if not skip_rating:
            logger.info("=" * 80)
            logger.info("🏆 开始更新交易对评级 (统一核心机制)")
            logger.info("=" * 80)
            opt = OptimizationConfig(MYSQL_CONFIG)
            rating_result = _apply_rating(all_stats, opt)
            logger.info(
                f"评级完成: 更新 {rating_result['updated']} 个, "
                f"分布 L0={rating_result['detail']['L0']} "
                f"L1={rating_result['detail']['L1']} "
                f"L2={rating_result['detail']['L2']} "
                f"L3={rating_result['detail']['L3']}"
            )

            # 清理过期自动评级（交易不足5笔的旧白名单记录）
            processed_symbols = {r["symbol"] for r in all_stats}
            _clean_stale_ratings(cursor, processed_symbols)

        cursor.close()

    except Exception as e:
        logger.error(f"日终维护失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    logger.info("开始日终维护: TOP50 + 统一评级...")
    update_top_performing_symbols(account_id=2, top_n=50)
    logger.info("更新完成！")
