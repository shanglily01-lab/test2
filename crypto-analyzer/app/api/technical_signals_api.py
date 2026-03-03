#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术信号API - 存储过程缓存版（极速）

架构：
  MySQL 存储过程 update_technical_signals_cache()
    └─ 3条 REPLACE INTO ... SELECT (GROUP BY) 聚合写入缓存表
  Python 调度器每5分钟 CALL update_technical_signals_cache()
  前端请求直接 SELECT * FROM technical_signals_cache → 毫秒级响应

部署：服务器上执行一次 sql/create_technical_signals_cache.sql 即可
"""

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Optional
from datetime import datetime
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
        read_timeout=60,
        write_timeout=60
    )


# ===================== 存储过程调用（调度器每5分钟执行） =====================
def refresh_technical_signals_cache():
    """
    调用 MySQL 存储过程 update_technical_signals_cache()，
    在数据库内完成所有聚合计算并写入缓存表。
    由 app/main.py 调度器每5分钟调用一次。
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("CALL update_technical_signals_cache()")
        cursor.close()
        logger.debug("[TechSignalCache] 存储过程执行完成")
    except Exception as e:
        logger.error(f"[TechSignalCache] 存储过程执行失败: {e}")
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
