#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理 kline_data 表：删除 3 个月之前的 K 线数据，释放表空间。

用法:
    python scripts/clean_kline_data.py                  # 默认清理 90 天前
    python scripts/clean_kline_data.py --days 180       # 自定义保留天数
    python scripts/clean_kline_data.py --dry-run        # 只统计，不删除
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, Any

from dotenv import dotenv_values
import pymysql
from loguru import logger

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _build_mysql_config() -> Dict[str, Any]:
    env = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
    return {
        "host": env.get("DB_HOST", "localhost"),
        "port": int(env.get("DB_PORT", 3306)),
        "user": env.get("DB_USER", "root"),
        "password": env.get("DB_PASSWORD", ""),
        "database": env.get("DB_NAME", "binance-data"),
    }


def main():
    parser = argparse.ArgumentParser(description="清理 kline_data 旧数据")
    parser.add_argument(
        "--days", type=int, default=90,
        help="保留最近 N 天的数据（默认 90 天）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅统计，不执行删除"
    )
    parser.add_argument(
        "--batch", type=int, default=50000,
        help="每批删除行数（默认 50000）"
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <level>{message}</level>", level="INFO")

    mysql_config = _build_mysql_config()
    cutoff = datetime.utcnow() - timedelta(days=args.days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    cutoff_ms = int(cutoff.timestamp() * 1000)

    logger.info(f"保留最近 {args.days} 天数据（{cutoff_str} 之后）")
    logger.info(f"数据库: {mysql_config['user']}@{mysql_config['host']}:{mysql_config['port']}/{mysql_config['database']}")
    if args.dry_run:
        logger.info("🧪 DRY RUN 模式 — 仅统计，不删除")

    conn = pymysql.connect(**mysql_config, autocommit=True, connect_timeout=30)
    cur = conn.cursor()

    try:
        # 1. 统计总量 & 待删除量
        cur.execute("SELECT COUNT(*) FROM kline_data")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM kline_data WHERE timestamp < %s", (cutoff_str,))
        to_delete = cur.fetchone()[0]
        logger.info(f"总行数: {total:,}")
        logger.info(f"待删除 (>{args.days}天): {to_delete:,} ({to_delete / total * 100:.1f}%)")

        # 查看数据的时间范围
        cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM kline_data")
        min_ts, max_ts = cur.fetchone()
        logger.info(f"数据范围: {min_ts} ~ {max_ts}")

        if args.dry_run or to_delete == 0:
            return

        # 2. 分批删除 (使用 timestamp 索引)
        total_deleted = 0
        batch_size = args.batch
        t0 = time.time()

        while True:
            sql = """DELETE FROM kline_data
                     WHERE timestamp < %s
                     ORDER BY timestamp
                     LIMIT %s"""
            affected = cur.execute(sql, (cutoff_str, batch_size))
            total_deleted += affected
            elapsed = time.time() - t0
            rate = total_deleted / elapsed if elapsed > 0 else 0

            if affected == 0:
                break

            if total_deleted % (batch_size * 10) == 0 or total_deleted < batch_size:
                logger.info(f"已删除 {total_deleted:,} 行, 速度 {rate:.0f} 行/秒, 当前批次 {affected:,}")

        logger.info(f"删除完成: 共 {total_deleted:,} 行, 耗时 {time.time() - t0:.1f}s")

        # 3. 释放表空间
        logger.info("开始 OPTIMIZE TABLE 释放空间...")
        t1 = time.time()
        cur.execute("OPTIMIZE TABLE kline_data")
        logger.info(f"OPTIMIZE 完成, 耗时 {time.time() - t1:.1f}s")

        # 4. 最终统计
        cur.execute("SELECT COUNT(*) FROM kline_data")
        remaining = cur.fetchone()[0]
        logger.info(f"剩余行数: {remaining:,}")
        logger.info("清理完成 ✅")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
