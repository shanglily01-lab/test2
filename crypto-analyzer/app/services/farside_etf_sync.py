#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 Farside Investors 官网抓取美国现货 BTC/ETH ETF 日度资金流。

网站表格数值单位均为 M$（百万美元，与页面一致）；入库前乘以 1e6 换算为美元，
写入 crypto_etf_flows，数据源标记 farside.co.uk。

页面: https://farside.co.uk/btc/ 、 https://farside.co.uk/eth/
说明: 网站表格为 HTML，无官方 API；解析可能随页面改版需维护。
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from loguru import logger

# 页面列名中需跳过的非单只 ETF 代码列
_SKIP_COLUMNS = frozenset(
    {
        "TOTAL",
        "FEE",
        "FEES",
        "",
    }
)

# Farside 表头常见 BTC ETF ticker；新增产品时可在此补充
_KNOWN_BTC_ETF_TICKERS = frozenset(
    {
        "IBIT",
        "FBTC",
        "BITB",
        "ARKB",
        "BTCO",
        "EZBC",
        "BRRR",
        "HODL",
        "BTCW",
        "MSBT",
        "GBTC",
        "DEFI",
    }
)

# 常见 ETH 现货 ETF ticker（与数据管理模板一致；网站新代码可 regex 兜底）
_KNOWN_ETH_ETF_TICKERS = frozenset(
    {
        "ETHA",
        "FETH",
        "ETHW",
        "ETHV",
        "QETH",
        "EZET",
        "CETH",
        "ETHE",
        "ETH",
    }
)

_SKIP_ROW_PREFIXES = ("total", "average", "maximum", "minimum", "fee")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}


def _parse_flow_cell(text: str) -> Optional[Decimal]:
    """解析 (12.3) 或 -12.3 或 - 表示空；数值单位为 M$（百万美元）。"""
    if text is None:
        return None
    t = text.replace("\xa0", " ").strip()
    if not t or t in ("-", "—", "–", "N/A", "n/a"):
        return None
    neg_paren = t.startswith("(") and t.endswith(")")
    if neg_paren:
        t = t[1:-1].strip()
    t = t.replace(",", "")
    try:
        v = Decimal(t)
        if neg_paren:
            v = -v
        return v
    except Exception:
        return None


def _is_date_row_first_cell(cell: str) -> bool:
    s = cell.strip()
    if not s:
        return False
    low = s.lower()
    if any(low.startswith(p) for p in _SKIP_ROW_PREFIXES):
        return False
    try:
        date_parser.parse(s, dayfirst=True, yearfirst=False)
        return True
    except Exception:
        return False


def _pick_flow_table(soup: BeautifulSoup, kind: str):
    """kind: 'btc' | 'eth' — 选含对应 ETF 表头的 table。"""
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("页面中未找到 table")

    if kind == "btc":
        keywords = ("IBIT", "GBTC", "FBTC")
    else:
        keywords = ("ETHA", "FETH", "ETHE", "ETH")

    best_table = None
    best_score = 0
    for tb in tables:
        text = tb.get_text(" ", strip=True).upper()
        score = sum(1 for x in keywords if x in text)
        if score > best_score:
            best_score = score
            best_table = tb

    if not best_table or best_score == 0:
        raise ValueError(f"未识别到 {'BTC' if kind == 'btc' else 'ETH'} ETF 流量表")

    return best_table


def parse_farside_flow_table(
    html: str, *, kind: str
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    解析 Farside /btc/ 或 /eth/ 页面 HTML，返回 (列 tickers, 行数据)。
    kind: 'btc' | 'eth'
    每行: { 'trade_date': date, 'flows': { ticker: Decimal|None, ... } }；
    flows 数值与网站一致，单位为 M$（百万美元）。
    """
    soup = BeautifulSoup(html, "lxml")
    best_table = _pick_flow_table(soup, kind)
    known = _KNOWN_BTC_ETF_TICKERS if kind == "btc" else _KNOWN_ETH_ETF_TICKERS

    rows = best_table.find_all("tr")
    if len(rows) < 2:
        raise ValueError("表格行数不足")

    # BTC 表常有汇总列「BTC」需跳过；ETH 表若存在代码为 ETH 的 ETF，不能全局跳过列名 ETH
    skip_cols = set(_SKIP_COLUMNS)
    if kind == "btc":
        skip_cols.add("BTC")

    ticker_to_col: Dict[str, int] = {}
    header_row_idx = 0
    for i, tr in enumerate(rows):
        cells = tr.find_all(["th", "td"])
        parts = [c.get_text(strip=True) for c in cells]
        if len(parts) < 3:
            continue
        row_map: Dict[str, int] = {}
        for j, raw in enumerate(parts):
            hu = raw.upper().replace(" ", "")
            if j == 0:
                continue
            if not hu or hu in skip_cols or hu == "TOTAL":
                continue
            if hu in known or re.match(r"^[A-Z]{2,5}$", hu):
                row_map[hu] = j
        if len(row_map) >= 2:
            ticker_to_col = row_map
            header_row_idx = i
            break

    if not ticker_to_col:
        raise ValueError("未找到 ETF 表头列")

    tickers = sorted(ticker_to_col.keys())
    out: List[Dict[str, Any]] = []

    for tr in rows[header_row_idx + 1 :]:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        parts = [c.get_text(strip=True) for c in cells]
        if not parts:
            continue
        first = parts[0]
        if not _is_date_row_first_cell(first):
            continue

        try:
            dt = date_parser.parse(first, dayfirst=True, yearfirst=False)
            trade_date = dt.date()
        except Exception:
            continue

        flows: Dict[str, Optional[Decimal]] = {}
        for t in tickers:
            j = ticker_to_col[t]
            if j >= len(parts):
                flows[t] = None
                continue
            flows[t] = _parse_flow_cell(parts[j])

        out.append({"trade_date": trade_date, "flows": flows})

    if not out:
        raise ValueError("未解析到任何日期行数据")

    out.sort(key=lambda x: x["trade_date"])
    return tickers, out


def parse_farside_btc_table(html: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """兼容旧名，等价 parse_farside_flow_table(html, kind='btc')。"""
    return parse_farside_flow_table(html, kind="btc")


def parse_farside_eth_table(html: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """解析 /eth/ 页面。"""
    return parse_farside_flow_table(html, kind="eth")


def fetch_farside_html(url: str, timeout: int = 30) -> str:
    """
    抓取 Farside 页面。部分网络环境返回 403，可尝试改用 https://www.farside.co.uk/...
    """
    session = requests.Session()
    session.headers.update(_DEFAULT_HEADERS)
    r = session.get(url, timeout=timeout, allow_redirects=True)
    if r.status_code == 403 and "farside.co.uk" in url and url.startswith("https://farside"):
        alt = url.replace("https://farside", "https://www.farside", 1)
        r = session.get(alt, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def fetch_farside_btc_html(url: str = "https://farside.co.uk/btc/", timeout: int = 30) -> str:
    """兼容旧名。"""
    return fetch_farside_html(url, timeout=timeout)


def _get_db_conn(mysql_config: Dict[str, Any]):
    return pymysql.connect(
        host=mysql_config.get("host", "localhost"),
        port=int(mysql_config.get("port", 3306)),
        user=mysql_config.get("user", "root"),
        password=mysql_config.get("password", ""),
        database=mysql_config.get("database", "binance-data"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_farside_etf_product(
    cursor, ticker: str, asset_type: str
) -> Optional[int]:
    """若不存在则插入 Farside ETF 产品（BTC 或 ETH），返回 etf_id。"""
    cursor.execute(
        "SELECT id FROM crypto_etf_products WHERE ticker = %s",
        (ticker,),
    )
    row = cursor.fetchone()
    if row:
        return int(row["id"])

    label = "BTC" if asset_type.upper() == "BTC" else "ETH"
    try:
        cursor.execute(
            """
            INSERT INTO crypto_etf_products
                (ticker, full_name, provider, asset_type, is_active)
            VALUES (%s, %s, %s, %s, 1)
            """,
            (ticker, f"{ticker} ({label} ETF)", "Farside", label),
        )
        return int(cursor.lastrowid)
    except Exception as e:
        logger.warning("插入 ETF 产品 {} 失败: {}", ticker, e)
        return None


def ensure_btc_etf_product(cursor, ticker: str) -> Optional[int]:
    """兼容旧名。"""
    return ensure_farside_etf_product(cursor, ticker, "BTC")


def _sync_farside_flows_impl(
    mysql_config: Dict[str, Any],
    page_url: str,
    *,
    kind: str,
    asset_type: str,
) -> Dict[str, Any]:
    """BTC/ETH 页解析后均为 M$ 口径，再乘 1e6 写入 net_inflow（美元）。"""
    imported = 0
    skipped = 0
    errors: List[str] = []

    html = fetch_farside_html(page_url)
    if kind == "btc":
        tickers, rows = parse_farside_btc_table(html)
    else:
        tickers, rows = parse_farside_eth_table(html)

    conn = _get_db_conn(mysql_config)
    try:
        cursor = conn.cursor()
        ticker_ids: Dict[str, int] = {}
        for t in tickers:
            eid = ensure_farside_etf_product(cursor, t, asset_type)
            if eid:
                ticker_ids[t] = eid
            else:
                errors.append(f"无产品记录且无法创建: {t}")

        # Farside /btc/ 与 /eth/ 表内数字均为 M$，此处换为美元
        million = Decimal("1000000")

        for row in rows:
            td: date = row["trade_date"]
            flows = row["flows"]
            for t, mil in flows.items():
                if t not in ticker_ids:
                    skipped += 1
                    continue
                if mil is None:
                    skipped += 1
                    continue
                etf_id = ticker_ids[t]
                net_usd = mil * million
                try:
                    cursor.execute(
                        """
                        INSERT INTO crypto_etf_flows
                            (etf_id, ticker, trade_date, net_inflow,
                             gross_inflow, gross_outflow, data_source)
                        VALUES (%s, %s, %s, %s, NULL, NULL, %s)
                        ON DUPLICATE KEY UPDATE
                            net_inflow = VALUES(net_inflow),
                            data_source = VALUES(data_source),
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (etf_id, t, td, net_usd, "farside.co.uk"),
                    )
                    imported += 1
                except Exception as e:
                    err = f"{t} {td}: {e}"
                    errors.append(err)
                    logger.warning("写入 ETF 流失败 {}", err)

        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "asset": asset_type.lower(),
        "imported_rows": imported,
        "skipped_cells": skipped,
        "tickers": tickers,
        "errors": errors[:50],
        "error_count": len(errors),
        "source_url": page_url,
    }


def sync_farside_btc_flows(
    mysql_config: Dict[str, Any],
    page_url: str = "https://farside.co.uk/btc/",
) -> Dict[str, Any]:
    """抓取 Farside BTC ETF 页；表内单位为 M$，入库为美元。"""
    return _sync_farside_flows_impl(
        mysql_config, page_url, kind="btc", asset_type="BTC"
    )


def sync_farside_eth_flows(
    mysql_config: Dict[str, Any],
    page_url: str = "https://farside.co.uk/eth/",
) -> Dict[str, Any]:
    """抓取 Farside ETH ETF 页；表内单位为 M$，入库为美元。"""
    return _sync_farside_flows_impl(
        mysql_config, page_url, kind="eth", asset_type="ETH"
    )
