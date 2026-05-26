#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地手动执行脚本：同步 Farside ETF 数据 + BitcoinTreasuries 企业金库数据。

服务器（AWS）IP 在 Cloudflare 上被拦截，无法正常爬取，需在本机执行。
从 .env 读取数据库配置，直接写入远程 MySQL。

用法:
    python local_sync_data.py              # 同步全部
    python local_sync_data.py --etf         # 仅同步 ETF
    python local_sync_data.py --treasury    # 仅同步企业金库
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict

from dotenv import dotenv_values
from loguru import logger

# 添加项目根目录到 sys.path，使 app.xxx 可导入
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _build_mysql_config() -> Dict[str, Any]:
    """从 .env 读取数据库配置，拼成 sync 函数需要的 dict。"""
    env = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
    return {
        "host": env.get("DB_HOST", "localhost"),
        "port": int(env.get("DB_PORT", 3306)),
        "user": env.get("DB_USER", "root"),
        "password": env.get("DB_PASSWORD", ""),
        "database": env.get("DB_NAME", "binance-data"),
    }


def sync_etf(mysql_config: Dict[str, Any]):
    """同步 BTC / ETH ETF 资金流。"""
    from app.services.farside_etf_sync import (
        sync_farside_btc_flows,
        sync_farside_eth_flows,
    )

    logger.info("=" * 60)
    logger.info("开始同步 Farside BTC ETF 资金流...")
    try:
        r_btc = sync_farside_btc_flows(mysql_config)
        logger.info(
            "BTC ETF 同步完成: imported={}, tickers={}, errors={}",
            r_btc.get("imported_rows"),
            len(r_btc.get("tickers") or []),
            r_btc.get("error_count", 0),
        )
    except Exception as e:
        logger.error("BTC ETF 同步失败: {}", e, exc_info=True)

    logger.info("开始同步 Farside ETH ETF 资金流...")
    try:
        r_eth = sync_farside_eth_flows(mysql_config)
        logger.info(
            "ETH ETF 同步完成: imported={}, tickers={}, errors={}",
            r_eth.get("imported_rows"),
            len(r_eth.get("tickers") or []),
            r_eth.get("error_count", 0),
        )
    except Exception as e:
        logger.error("ETH ETF 同步失败: {}", e, exc_info=True)


def sync_treasury(mysql_config: Dict[str, Any]):
    """同步企业金库。"""
    from app.services.bitcointreasuries_sync import sync_bitcointreasuries_holdings

    logger.info("=" * 60)
    logger.info("开始同步 BitcoinTreasuries 企业金库...")
    try:
        r = sync_bitcointreasuries_holdings(mysql_config)
        logger.info(
            "企业金库同步完成: companies={}, imported={}, updated={}, skipped={}",
            r.get("company_count"),
            r.get("imported"),
            r.get("updated"),
            r.get("skipped"),
        )
    except Exception as e:
        logger.error("企业金库同步失败: {}", e, exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="本地手动执行数据同步")
    parser.add_argument(
        "--etf", action="store_true", help="仅同步 ETF 数据"
    )
    parser.add_argument(
        "--treasury", action="store_true", help="仅同步企业金库数据"
    )
    args = parser.parse_args()

    # 移除默认的 loguru handler，输出到终端
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <level>{message}</level>",
        level="INFO",
    )

    mysql_config = _build_mysql_config()
    logger.info("数据库: {}@{}:{}/{}", mysql_config["user"], mysql_config["host"], mysql_config["port"], mysql_config["database"])

    do_etf = args.etf or not args.treasury
    do_treasury = args.treasury or not args.etf

    if do_etf:
        sync_etf(mysql_config)
    if do_treasury:
        sync_treasury(mysql_config)

    logger.info("全部同步任务完成。")


if __name__ == "__main__":
    main()
