#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易对评级管理API
提供前端界面查看和手动触发评级更新
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
from loguru import logger
import sys
import os
import math

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.optimization_config import OptimizationConfig
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical
from app.utils.config_loader import load_config
from app.utils.pnl_stats import PNL_COUNT_SELECT, parse_pnl_counts


def safe_float(value, default=0.0):
    """安全转换float,避免inf和nan"""
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default

router = APIRouter()

# 从 config.yaml 读取数据库配置，避免硬编码外网 IP 导致连接失败
DB_CONFIG = load_config().get('database', {}).get('mysql', {})


class RatingUpdateRequest(BaseModel):
    """评级更新请求 — 统一核心机制不再需要 observation_days"""
    pass  # 全仓累计，无需参数


class ManualRatingRequest(BaseModel):
    """手动设置评级请求"""
    symbol: str
    rating_level: int  # 0=白名单, 1=黑名单1级, 2=黑名单2级, 3=永久禁止
    reason: Optional[str] = "手动设置"


@router.get("/api/rating/config")
async def get_rating_config():
    """获取评级配置（统一核心机制）"""
    try:
        opt_config = OptimizationConfig(DB_CONFIG)

        # 评级规则（与 update_top_performers.compute_rating_level 一致）
        rules = {
            "min_trades": 5,
            "白名单 L0": "盈利 > 200U 且 胜率 > 55%（双条件同时满足）",
            "黑名单1级 L1": "盈利 > 50U 或 胜率 > 50%",
            "黑名单2级 L2": "-100 < 盈利 < 0 或 胜率 > 44%",
            "黑名单3级 L3": "盈利 < -100U 且 胜率 < 44%（双条件同时满足）",
        }

        return {
            "success": True,
            "rules": rules,
            "margin_multipliers": {
                "level0": opt_config.get_blacklist_config(0)["margin_multiplier"],
                "level1": opt_config.get_blacklist_config(1)["margin_multiplier"],
                "level2": opt_config.get_blacklist_config(2)["margin_multiplier"],
                "level3": 0.0,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/rating/current")
async def get_current_ratings(trading_type: Optional[str] = None):
    """
    获取当前所有评级

    Args:
        trading_type: 可选的交易类型过滤 (仅 'usdt_futures')
                     usdt_futures: 只返回USDT结尾的交易对
                     None: 返回所有交易对
    """
    try:
        opt_config = OptimizationConfig(DB_CONFIG)

        # 获取所有评级
        ratings = opt_config.get_all_symbol_ratings()

        # 按等级分组
        grouped = {
            "level0": [],  # 白名单
            "level1": [],  # 黑名单1级
            "level2": [],  # 黑名单2级
            "level3": []   # 黑名单3级(永久禁止)
        }

        for rating in ratings:
            symbol = rating['symbol']

            if trading_type == 'usdt_futures':
                # U本位: 只显示USDT结尾的交易对
                if not symbol.endswith('USDT'):
                    continue

            level = rating['rating_level']
            grouped[f"level{level}"].append({
                "symbol": symbol,
                "rating_level": level,
                "reason": rating.get('level_change_reason', '') or '',
                "hard_stop_loss_count": rating.get('hard_stop_loss_count', 0),
                "total_loss_amount": safe_float(rating.get('total_loss_amount', 0)),
                "total_profit_amount": safe_float(rating.get('total_profit_amount', 0)),
                "win_rate": safe_float(rating.get('win_rate', 0)),
                "total_trades": rating.get('total_trades', 0),
                "updated_at": rating.get('updated_at').isoformat() if rating.get('updated_at') else None
            })

        total_count = sum(len(grouped[f"level{i}"]) for i in range(4))

        return {
            "success": True,
            "ratings": grouped,
            "total_count": total_count
        }
    except Exception as e:
        logger.error(f"[评级API] get_current_ratings 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/rating/update")
async def trigger_rating_update(request: RatingUpdateRequest):
    """手动触发评级更新（全仓累计统一核心机制）"""
    import asyncio
    try:
        from update_top_performers import update_top_performing_symbols
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, update_top_performing_symbols, 2, 50, False)
        return {"success": True, "message": "评级更新完成"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/rating/set")
async def set_symbol_rating(request: ManualRatingRequest):
    """手动设置单个交易对的评级"""
    try:
        if request.rating_level not in (0, 1, 2, 3):
            raise HTTPException(status_code=400, detail="rating_level must be 0, 1, 2, or 3")
        opt_config = OptimizationConfig(DB_CONFIG)
        opt_config.update_symbol_rating(
            symbol=futures_symbol_rating_canonical(request.symbol),
            new_level=request.rating_level,
            reason=request.reason or "手动设置"
        )
        canon = futures_symbol_rating_canonical(request.symbol)
        return {"success": True, "message": f"{canon} 评级已设置为 {request.rating_level}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DeleteRatingRequest(BaseModel):
    """删除评级请求"""
    symbol: str


@router.delete("/api/rating/delete")
async def delete_symbol_rating(symbol: str):
    """删除交易对的评级记录"""
    try:
        opt_config = OptimizationConfig(DB_CONFIG)
        deleted = opt_config.delete_symbol_rating(symbol.upper())
        if not deleted:
            raise HTTPException(status_code=404, detail=f"{symbol.upper()} 未找到评级记录")
        return {"success": True, "message": f"{symbol.upper()} 评级已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _normalize_symbol_key(symbol: str) -> str:
    return futures_symbol_clean(symbol)


def _empty_whitelist_stats(symbol_count: int = 0, in_top50: int = 0, account_id: int = 2) -> dict:
    return {
        "symbol_count": symbol_count,
        "in_top50_count": in_top50,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": 0,
        "net_pnl": 0,
        "gross_profit": 0,
        "gross_loss": 0,
        "profit_factor": None,
        "account_id": account_id,
    }


def _symbol_pnl_row(total_trades, wins, losses, breakeven, net_pnl, gross_profit, gross_loss):
    row = {
        "total_trades": total_trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "breakeven": breakeven,
        "total_realized_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }
    counts = parse_pnl_counts({
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    })
    row["win_rate"] = counts["win_rate"]
    row["avg_pnl_per_trade"] = round(net_pnl / total_trades, 2) if total_trades > 0 else 0
    row["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    return row


def _fetch_whitelist_bundle(cur, account_id: int = 2) -> dict:
    """白名单汇总统计 + 逐币列表 (rating_level=0 · 模拟仓已平仓)."""
    cur.execute(
        """
        SELECT symbol, level_change_reason, updated_at
        FROM trading_symbol_rating
        WHERE rating_level = 0
        ORDER BY symbol
        """
    )
    wl_rows = cur.fetchall()
    wl_meta = {}
    for r in wl_rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        key = _normalize_symbol_key(sym)
        wl_meta[key] = {
            "symbol": sym,
            "reason": (r.get("level_change_reason") or "").strip(),
            "updated_at": r.get("updated_at").isoformat() if r.get("updated_at") else None,
        }

    cur.execute("SELECT symbol FROM top_performing_symbols")
    top50_keys = {
        _normalize_symbol_key(r.get("symbol") or "")
        for r in cur.fetchall()
        if r.get("symbol")
    }

    per_symbol = {
        key: {"wins": 0, "losses": 0, "breakeven": 0, "net_pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
        for key in wl_meta
    }

    if wl_meta:
        cur.execute(
            """
            SELECT symbol, realized_pnl
            FROM futures_positions
            WHERE account_id = %s
              AND status = 'closed'
              AND realized_pnl IS NOT NULL
            """,
            (account_id,),
        )
        for fp in cur.fetchall():
            key = _normalize_symbol_key(fp.get("symbol") or "")
            if key not in per_symbol:
                continue
            agg = per_symbol[key]
            pnl = float(fp.get("realized_pnl") or 0)
            agg["net_pnl"] += pnl
            if pnl > 0:
                agg["wins"] += 1
                agg["gross_profit"] += pnl
            elif pnl < 0:
                agg["losses"] += 1
                agg["gross_loss"] += abs(pnl)
            else:
                agg["breakeven"] += 1

    symbols = []
    in_top50_count = 0
    for key, meta in wl_meta.items():
        in_top50 = key in top50_keys
        if in_top50:
            in_top50_count += 1
        agg = per_symbol[key]
        total_trades = agg["wins"] + agg["losses"] + agg["breakeven"]
        pnl_row = _symbol_pnl_row(
            total_trades,
            agg["wins"],
            agg["losses"],
            agg["breakeven"],
            agg["net_pnl"],
            agg["gross_profit"],
            agg["gross_loss"],
        )
        symbols.append({
            "symbol": meta["symbol"],
            "in_top50": in_top50,
            "reason": meta["reason"],
            "updated_at": meta["updated_at"],
            **pnl_row,
        })

    symbols.sort(key=lambda x: (-x["total_realized_pnl"], -x["total_trades"], x["symbol"]))

    if not wl_meta:
        return {"stats": _empty_whitelist_stats(0, 0, account_id), "symbols": []}

    total_wins = sum(s["winning_trades"] for s in symbols)
    total_losses = sum(s["losing_trades"] for s in symbols)
    total_breakeven = sum(s["breakeven"] for s in symbols)
    total_trades = total_wins + total_losses + total_breakeven
    net_pnl = sum(s["total_realized_pnl"] for s in symbols)
    gross_profit = sum(s["gross_profit"] for s in symbols)
    gross_loss = sum(s["gross_loss"] for s in symbols)
    counts = parse_pnl_counts({
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "breakeven": total_breakeven,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    })

    stats = {
        "symbol_count": len(wl_meta),
        "in_top50_count": in_top50_count,
        "total_trades": total_trades,
        "wins": counts["wins"],
        "losses": counts["losses"],
        "breakeven": counts["breakeven"],
        "win_rate": counts["win_rate"],
        "net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "account_id": account_id,
    }
    return {"stats": stats, "symbols": symbols}


def _fetch_whitelist_pnl_stats(cur, account_id: int = 2) -> dict:
    return _fetch_whitelist_bundle(cur, account_id)["stats"]


def _fetch_losers_bundle(cur, account_id: int = 2, limit: int = 50, min_trades: int = 5) -> dict:
    """累计亏损交易对（模拟仓已平仓，至少 min_trades 笔）."""
    cur.execute(
        """
        SELECT
            symbol,
            COUNT(*) AS total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) AS breakeven,
            COALESCE(SUM(realized_pnl), 0) AS net_pnl,
            COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) AS gross_profit,
            COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END), 0) AS gross_loss
        FROM futures_positions
        WHERE account_id = %s
          AND status = 'closed'
          AND realized_pnl IS NOT NULL
        GROUP BY symbol
        HAVING total_trades >= %s AND net_pnl < 0
        ORDER BY net_pnl ASC
        LIMIT %s
        """,
        (account_id, min_trades, limit),
    )
    rows = cur.fetchall()
    symbols = []
    for r in rows:
        total_trades = int(r["total_trades"] or 0)
        wins = int(r["wins"] or 0)
        losses = int(r["losses"] or 0)
        breakeven = int(r["breakeven"] or 0)
        net_pnl = float(r["net_pnl"] or 0)
        gross_profit = float(r["gross_profit"] or 0)
        gross_loss = float(r["gross_loss"] or 0)
        pnl_row = _symbol_pnl_row(
            total_trades, wins, losses, breakeven, net_pnl, gross_profit, gross_loss
        )
        symbols.append({
            "symbol": r["symbol"],
            "rank_score": len(symbols) + 1,
            **pnl_row,
        })

    if not symbols:
        return {
            "stats": {
                "symbol_count": 0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0,
                "net_pnl": 0,
                "gross_profit": 0,
                "gross_loss": 0,
                "profit_factor": None,
                "account_id": account_id,
            },
            "symbols": [],
        }

    total_wins = sum(s["winning_trades"] for s in symbols)
    total_losses = sum(s["losing_trades"] for s in symbols)
    total_breakeven = sum(s["breakeven"] for s in symbols)
    total_trades = total_wins + total_losses + total_breakeven
    net_pnl = sum(s["total_realized_pnl"] for s in symbols)
    gross_profit = sum(s["gross_profit"] for s in symbols)
    gross_loss = sum(s["gross_loss"] for s in symbols)
    counts = parse_pnl_counts({
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "breakeven": total_breakeven,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    })
    stats = {
        "symbol_count": len(symbols),
        "total_trades": total_trades,
        "wins": counts["wins"],
        "losses": counts["losses"],
        "breakeven": counts["breakeven"],
        "win_rate": counts["win_rate"],
        "net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "account_id": account_id,
    }
    return {"stats": stats, "symbols": symbols}


@router.get("/api/top50")
async def get_top50():
    """盈亏分析：TOP50 盈利 / 白名单盈利 / 亏损榜单"""
    import pymysql
    try:
        conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, total_realized_pnl, total_trades, winning_trades, losing_trades,
                   win_rate, avg_pnl_per_trade, max_single_profit, max_single_loss,
                   profit_factor, rank_score, last_updated
            FROM top_performing_symbols
            ORDER BY rank_score ASC
            LIMIT 50
        """)
        rows = cur.fetchall()
        whitelist_bundle = _fetch_whitelist_bundle(cur, account_id=2)
        whitelist_stats = whitelist_bundle["stats"]
        whitelist_data = whitelist_bundle["symbols"]
        losers_bundle = _fetch_losers_bundle(cur, account_id=2)
        losers_stats = losers_bundle["stats"]
        losers_data = losers_bundle["symbols"]
        cur.close()
        conn.close()

        data = []
        for r in rows:
            data.append({
                'symbol':             r['symbol'],
                'total_realized_pnl': float(r['total_realized_pnl'] or 0),
                'total_trades':       int(r['total_trades'] or 0),
                'winning_trades':     int(r['winning_trades'] or 0),
                'losing_trades':      int(r['losing_trades'] or 0),
                'win_rate':           float(r['win_rate'] or 0),
                'avg_pnl_per_trade':  float(r['avg_pnl_per_trade'] or 0),
                'max_single_profit':  float(r['max_single_profit'] or 0) if r['max_single_profit'] is not None else 0,
                'max_single_loss':    float(r['max_single_loss'] or 0) if r['max_single_loss'] is not None else 0,
                'profit_factor':      float(r['profit_factor'] or 0) if r['profit_factor'] is not None else 0,
                'rank_score':         int(r['rank_score'] or 0),
                'last_updated':       r['last_updated'].isoformat() if r['last_updated'] else None,
            })

        total_pnl = sum(r['total_realized_pnl'] for r in data)
        avg_wr = (sum(r['win_rate'] for r in data) / len(data)) if data else 0
        last_updated = data[0]['last_updated'] if data else None

        return {
            'success': True,
            'data': data,
            'stats': {
                'count':         len(data),
                'total_pnl':     round(total_pnl, 2),
                'avg_win_rate':  round(avg_wr, 2),
                'last_updated':  last_updated,
            },
            'whitelist_stats': whitelist_stats,
            'whitelist_data': whitelist_data,
            'losers_stats': losers_stats,
            'losers_data': losers_data,
        }
    except Exception as e:
        logger.error(f"获取盈亏分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/top50/refresh")
async def refresh_top50():
    """手动触发日终维护：重算 Top50 榜单 + 统一评级"""
    import asyncio
    try:
        from update_top_performers import update_top_performing_symbols
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, update_top_performing_symbols, 2, 50, False)
        return {'success': True, 'message': 'Top50 与统一评级已更新'}
    except Exception as e:
        logger.error(f"手动日终维护失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/rating/symbol/{symbol}")
async def get_symbol_rating(symbol: str):
    """获取单个交易对的评级和全仓累计表现分析"""
    try:
        from update_top_performers import compute_rating_level, MIN_TRADES
        import pymysql

        opt_config = OptimizationConfig(DB_CONFIG)
        current_rating = opt_config.get_symbol_rating(symbol)

        conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) AS total_trades,
                COALESCE(SUM(realized_pnl), 0) AS net_pnl,
                CASE
                    WHEN COUNT(*) > 0
                    THEN SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
                    ELSE 0
                END AS win_rate_pct
            FROM futures_positions
            WHERE symbol = %s
              AND account_id = 2
              AND status = 'closed'
              AND realized_pnl IS NOT NULL
            """,
            (futures_symbol_rating_canonical(symbol),),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        total_trades = int(row["total_trades"] or 0) if row else 0
        net_pnl = float(row["net_pnl"] or 0) if row else 0
        win_rate_pct = float(row["win_rate_pct"] or 0) if row else 0

        current_level = current_rating["rating_level"] if current_rating else 0
        potential_level, reason = compute_rating_level(net_pnl, win_rate_pct, total_trades)

        return {
            "success": True,
            "symbol": symbol,
            "current_rating": {
                "level": current_level,
                "reason": current_rating.get("level_change_reason", "无评级") if current_rating else "无评级",
                "updated_at": current_rating["updated_at"].isoformat() if current_rating and current_rating.get("updated_at") else None,
            },
            "performance_stats": {
                "total_trades": total_trades,
                "net_pnl": net_pnl,
                "win_rate_pct": win_rate_pct,
            },
            "potential_change": {
                "would_change": potential_level != current_level,
                "new_level": potential_level,
                "reason": reason,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
