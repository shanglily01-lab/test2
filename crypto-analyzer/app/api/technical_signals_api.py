#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术信号API - 提供K线统计和技术分析
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


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor
    )


@router.get("/api/technical-signals")
async def get_technical_signals(symbols: Optional[str] = None):
    """
    获取技术信号分析数据

    包含:
    - 24H内 1H/15M/5M K线阳阴线统计
    - 4H内 K线评估与统计
    - 1H内 K线评估与统计
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 从config.yaml获取交易对列表
        import yaml
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            all_symbols = config.get('symbols', [])

        # 如果提供了symbols参数,过滤交易对
        if symbols:
            symbol_list = symbols.split(',')
            all_symbols = [s for s in all_symbols if s in symbol_list]

        results = []

        for symbol in all_symbols:
            try:
                signal_data = analyze_symbol(cursor, symbol)
                if signal_data:
                    results.append(signal_data)
            except Exception as e:
                logger.error(f"分析 {symbol} 失败: {e}")
                continue

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": results,
            "total": len(results),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"获取技术信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def analyze_symbol(cursor, symbol: str) -> Optional[Dict]:
    """分析单个交易对的技术信号"""

    # 1. 24H内的K线统计
    stats_24h = get_kline_stats_24h(cursor, symbol)

    # 2. 4H内的K线评估
    stats_4h = get_kline_stats_4h(cursor, symbol)

    # 3. 1H内的K线评估
    stats_1h = get_kline_stats_1h(cursor, symbol)

    # 如果没有任何数据,返回None
    if not any([stats_24h, stats_4h, stats_1h]):
        return None

    return {
        "symbol": symbol,
        "stats_24h": stats_24h or {},
        "stats_4h": stats_4h or {},
        "stats_1h": stats_1h or {},
        "updated_at": datetime.now().isoformat()
    }


def get_kline_stats_24h(cursor, symbol: str) -> Optional[Dict]:
    """获取24H内的1H/15M/5M K线统计"""

    time_24h_ago = datetime.now() - timedelta(hours=24)

    stats = {}

    # 统计1H K线(24H内应该有24根)
    stats['1h'] = get_timeframe_stats(cursor, symbol, '1h', time_24h_ago)

    # 统计15M K线(24H内应该有96根)
    stats['15m'] = get_timeframe_stats(cursor, symbol, '15m', time_24h_ago)

    # 统计5M K线(24H内应该有288根)
    stats['5m'] = get_timeframe_stats(cursor, symbol, '5m', time_24h_ago)

    return stats if any(stats.values()) else None


def get_kline_stats_4h(cursor, symbol: str) -> Optional[Dict]:
    """获取4H内的K线统计"""

    time_4h_ago = datetime.now() - timedelta(hours=4)

    # 4H内主要看15M和5M
    stats = {}
    stats['15m'] = get_timeframe_stats(cursor, symbol, '15m', time_4h_ago)
    stats['5m'] = get_timeframe_stats(cursor, symbol, '5m', time_4h_ago)
    stats['1m'] = get_timeframe_stats(cursor, symbol, '1m', time_4h_ago)

    return stats if any(stats.values()) else None


def get_kline_stats_1h(cursor, symbol: str) -> Optional[Dict]:
    """获取1H内的K线统计"""

    time_1h_ago = datetime.now() - timedelta(hours=1)

    # 1H内主要看5M和1M
    stats = {}
    stats['5m'] = get_timeframe_stats(cursor, symbol, '5m', time_1h_ago)
    stats['1m'] = get_timeframe_stats(cursor, symbol, '1m', time_1h_ago)

    return stats if any(stats.values()) else None


def get_timeframe_stats(cursor, symbol: str, timeframe: str, start_time: datetime) -> Optional[Dict]:
    """获取指定时间框架的K线统计"""

    try:
        # 查询K线数据
        cursor.execute("""
            SELECT
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                timestamp
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            AND timestamp >= %s
            ORDER BY timestamp ASC
        """, (symbol, timeframe, start_time))

        klines = cursor.fetchall()

        if not klines:
            return None

        # 统计阳线和阴线
        bullish_count = 0  # 阳线数量
        bearish_count = 0  # 阴线数量

        bullish_strength = 0  # 阳线力度(涨幅 * 成交量)
        bearish_strength = 0  # 阴线力度(跌幅 * 成交量)

        total_volume = 0

        for kline in klines:
            open_p = float(kline['open_price'])
            close_p = float(kline['close_price'])
            high_p = float(kline['high_price'])
            low_p = float(kline['low_price'])
            volume = float(kline['volume'])

            total_volume += volume

            # 判断阳线还是阴线
            if close_p > open_p:
                bullish_count += 1
                # 阳线力度 = 涨幅% * 成交量
                change_pct = (close_p - open_p) / open_p * 100
                bullish_strength += change_pct * volume
            elif close_p < open_p:
                bearish_count += 1
                # 阴线力度 = 跌幅% * 成交量
                change_pct = (open_p - close_p) / open_p * 100
                bearish_strength += change_pct * volume

        total_count = len(klines)
        bullish_pct = bullish_count / total_count * 100 if total_count > 0 else 0
        bearish_pct = bearish_count / total_count * 100 if total_count > 0 else 0

        # 计算平均力度
        avg_bullish_strength = bullish_strength / bullish_count if bullish_count > 0 else 0
        avg_bearish_strength = bearish_strength / bearish_count if bearish_count > 0 else 0

        # 整体趋势判断
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
            "total_klines": total_count,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "bullish_pct": round(bullish_pct, 2),
            "bearish_pct": round(bearish_pct, 2),
            "avg_bullish_strength": round(avg_bullish_strength, 4),
            "avg_bearish_strength": round(avg_bearish_strength, 4),
            "total_volume": round(total_volume, 2),
            "trend": trend
        }

    except Exception as e:
        logger.error(f"获取 {symbol} {timeframe} K线统计失败: {e}")
        return None
