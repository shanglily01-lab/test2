#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术信号API - 缓存表版（极速）

优化：原来每次请求对每个交易对执行 8 条 SQL → 50币种 = 400+ 次查询
     现在：后台每5分钟刷新一次 technical_signals_cache 表（3条聚合SQL）
           前端请求直接 SELECT * FROM technical_signals_cache，毫秒级响应
"""

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from loguru import logger
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

# ===================== 模块级配置缓存 =====================
_config_symbols: Optional[List[str]] = None
_config_symbols_time: Optional[datetime] = None


def _get_config_symbols() -> List[str]:
    """读取config.yaml中的交易对列表（5分钟内复用，减少磁盘IO）"""
    global _config_symbols, _config_symbols_time
    now = datetime.now()
    if _config_symbols is None or (now - _config_symbols_time).total_seconds() > 300:
        import yaml
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        _config_symbols = config.get('symbols', [])
        _config_symbols_time = now
    return _config_symbols


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30
    )


# ===================== 统计计算工具 =====================
def _calc_stats(row: Dict) -> Optional[Dict]:
    """从SQL聚合行计算阳线/阴线统计结构"""
    total = int(row.get('total_klines') or 0)
    if not total:
        return None
    bullish = int(row.get('bullish_count') or 0)
    bearish = int(row.get('bearish_count') or 0)
    bullish_pct = bullish / total * 100
    bearish_pct = bearish / total * 100
    avg_bullish = (float(row.get('bullish_strength_sum') or 0) / bullish) if bullish else 0
    avg_bearish = (float(row.get('bearish_strength_sum') or 0) / bearish) if bearish else 0

    if bullish_pct >= 65:
        trend = "强势上涨"
    elif bullish_pct >= 55:
        trend = "上涨"
    elif bearish_pct >= 65:
        trend = "强势下跌"
    elif bearish_pct >= 55:
        trend = "下跌"
    else:
        trend = "震荡"

    return {
        "total_klines": total,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "bullish_pct": round(bullish_pct, 2),
        "bearish_pct": round(bearish_pct, 2),
        "avg_bullish_strength": round(avg_bullish, 4),
        "avg_bearish_strength": round(avg_bearish, 4),
        "total_volume": round(float(row.get('total_volume') or 0), 2),
        "trend": trend
    }


# ===================== 缓存表维护 =====================
_CACHE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS technical_signals_cache (
        symbol       VARCHAR(20)    NOT NULL,
        window_label VARCHAR(10)    NOT NULL,
        timeframe    VARCHAR(10)    NOT NULL,
        total_klines INT            DEFAULT 0,
        bullish_count INT           DEFAULT 0,
        bearish_count INT           DEFAULT 0,
        bullish_pct   DECIMAL(5,2)  DEFAULT 0,
        bearish_pct   DECIMAL(5,2)  DEFAULT 0,
        avg_bullish_strength DECIMAL(12,6) DEFAULT 0,
        avg_bearish_strength DECIMAL(12,6) DEFAULT 0,
        total_volume  DECIMAL(20,4) DEFAULT 0,
        trend         VARCHAR(20)   DEFAULT '震荡',
        updated_at    DATETIME      NOT NULL,
        PRIMARY KEY (symbol, window_label, timeframe)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_WINDOWS = [
    # (窗口标签, 要查询的timeframe列表, 时间范围)
    ('24h', ['1h', '15m', '5m'], timedelta(hours=24)),
    ('4h',  ['15m', '5m', '1m'], timedelta(hours=4)),
    ('1h',  ['5m', '1m'],         timedelta(hours=1)),
]


def refresh_technical_signals_cache():
    """
    刷新 technical_signals_cache 表。
    3条聚合SQL取代原来400+条查询。
    由 app/main.py 调度器每5分钟调用一次。
    """
    symbols = _get_config_symbols()
    if not symbols:
        logger.warning("[TechSignalCache] config.yaml中没有交易对")
        return

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 首次运行时建表
        cursor.execute(_CACHE_TABLE_SQL)

        now = datetime.now()
        sym_placeholders = ','.join(['%s'] * len(symbols))

        rows_to_insert = []

        for window_label, timeframes, delta in _WINDOWS:
            since = now - delta
            tf_placeholders = ','.join(['%s'] * len(timeframes))

            cursor.execute(f"""
                SELECT symbol, timeframe,
                    COUNT(*) AS total_klines,
                    SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END) AS bullish_count,
                    SUM(CASE WHEN close_price < open_price THEN 1 ELSE 0 END) AS bearish_count,
                    SUM(volume) AS total_volume,
                    SUM(CASE WHEN close_price > open_price
                        THEN (close_price - open_price) / open_price * 100 * volume
                        ELSE 0 END) AS bullish_strength_sum,
                    SUM(CASE WHEN close_price < open_price
                        THEN (open_price - close_price) / open_price * 100 * volume
                        ELSE 0 END) AS bearish_strength_sum
                FROM kline_data
                WHERE symbol IN ({sym_placeholders})
                  AND timeframe IN ({tf_placeholders})
                  AND timestamp >= %s
                GROUP BY symbol, timeframe
            """, (*symbols, *timeframes, since))

            for row in cursor.fetchall():
                stats = _calc_stats(row)
                if stats:
                    rows_to_insert.append((
                        row['symbol'], window_label, row['timeframe'],
                        stats['total_klines'], stats['bullish_count'], stats['bearish_count'],
                        stats['bullish_pct'], stats['bearish_pct'],
                        stats['avg_bullish_strength'], stats['avg_bearish_strength'],
                        stats['total_volume'], stats['trend'],
                        now
                    ))

        if rows_to_insert:
            cursor.executemany("""
                REPLACE INTO technical_signals_cache
                    (symbol, window_label, timeframe, total_klines, bullish_count, bearish_count,
                     bullish_pct, bearish_pct, avg_bullish_strength, avg_bearish_strength,
                     total_volume, trend, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, rows_to_insert)
            logger.debug(f"[TechSignalCache] 刷新完成，写入 {len(rows_to_insert)} 行")

        cursor.close()

    except Exception as e:
        logger.error(f"[TechSignalCache] 刷新失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        if conn:
            conn.close()


# ===================== API 端点 =====================
@router.get("/api/technical-signals")
async def get_technical_signals(symbols: Optional[str] = None):
    """
    获取技术信号分析数据（从缓存表读取，极速响应）

    包含:
    - 24H内 1H/15M/5M K线阳阴线统计
    - 4H内 K线评估与统计
    - 1H内 K线评估与统计
    """
    try:
        all_symbols = _get_config_symbols()
        if symbols:
            symbol_list = symbols.split(',')
            all_symbols = [s for s in all_symbols if s in symbol_list]

        if not all_symbols:
            return {"success": True, "data": [], "total": 0, "timestamp": datetime.now().isoformat()}

        conn = get_db_connection()
        cursor = conn.cursor()

        placeholders = ','.join(['%s'] * len(all_symbols))

        # 从缓存表读取（极速）
        cursor.execute(f"""
            SELECT symbol, window_label, timeframe,
                   total_klines, bullish_count, bearish_count,
                   bullish_pct, bearish_pct, avg_bullish_strength, avg_bearish_strength,
                   total_volume, trend, updated_at
            FROM technical_signals_cache
            WHERE symbol IN ({placeholders})
        """, all_symbols)

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        # 缓存表为空时（首次部署），同步刷新一次
        if not rows:
            logger.info("[TechSignalCache] 缓存表为空，触发同步刷新...")
            refresh_technical_signals_cache()

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT symbol, window_label, timeframe,
                       total_klines, bullish_count, bearish_count,
                       bullish_pct, bearish_pct, avg_bullish_strength, avg_bearish_strength,
                       total_volume, trend, updated_at
                FROM technical_signals_cache
                WHERE symbol IN ({placeholders})
            """, all_symbols)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

        # 整理数据：{symbol: {window_label: {timeframe: stats}}}
        data_map: Dict[str, Dict[str, Dict]] = {}
        for row in rows:
            s  = row['symbol']
            wl = row['window_label']
            tf = row['timeframe']
            data_map.setdefault(s, {}).setdefault(wl, {})[tf] = {
                "total_klines":          int(row['total_klines'] or 0),
                "bullish_count":         int(row['bullish_count'] or 0),
                "bearish_count":         int(row['bearish_count'] or 0),
                "bullish_pct":           float(row['bullish_pct'] or 0),
                "bearish_pct":           float(row['bearish_pct'] or 0),
                "avg_bullish_strength":  float(row['avg_bullish_strength'] or 0),
                "avg_bearish_strength":  float(row['avg_bearish_strength'] or 0),
                "total_volume":          float(row['total_volume'] or 0),
                "trend":                 row['trend'],
            }

        results = []
        ts = datetime.now().isoformat()
        for symbol in all_symbols:
            if symbol not in data_map:
                continue
            d = data_map[symbol]
            results.append({
                "symbol":    symbol,
                "stats_24h": {
                    "1h":  d.get('24h', {}).get('1h'),
                    "15m": d.get('24h', {}).get('15m'),
                    "5m":  d.get('24h', {}).get('5m'),
                },
                "stats_4h": {
                    "15m": d.get('4h', {}).get('15m'),
                    "5m":  d.get('4h', {}).get('5m'),
                    "1m":  d.get('4h', {}).get('1m'),
                },
                "stats_1h": {
                    "5m": d.get('1h', {}).get('5m'),
                    "1m": d.get('1h', {}).get('1m'),
                },
                "updated_at": ts,
            })

        return {
            "success":   True,
            "data":      results,
            "total":     len(results),
            "timestamp": ts,
        }

    except Exception as e:
        logger.error(f"获取技术信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
