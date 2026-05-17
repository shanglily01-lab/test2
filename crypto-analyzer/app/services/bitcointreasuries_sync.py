#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 https://bitcointreasuries.net/ 抓取「上市公司 BTC 金库」公开表格，同步到
corporate_treasury_companies / corporate_treasury_purchases。

网站为 SvelteKit，服务端会输出 HTML 表格；若改版需调整解析逻辑。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import requests
from bs4 import BeautifulSoup
from loguru import logger

from app.services.corporate_treasury_holdings import upsert_corporate_holdings_batch

# 2026-05-17 修复: AWS / 数据中心 IP 常被 Cloudflare 拦,优先用 curl_cffi 模拟浏览器 TLS
# 指纹绕过 403。本地开发环境 curl_cffi 可能未装,允许 fallback 到 requests。
try:
    from curl_cffi import requests as cf_requests
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _CURL_CFFI_AVAILABLE = False

DEFAULT_URL = "https://bitcointreasuries.net/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


def fetch_homepage_html(url: str = DEFAULT_URL, timeout: int = 45) -> str:
    """
    抓取 bitcointreasuries.net 首页 HTML。
    网站由 Cloudflare 保护,普通 requests 在 AWS / 数据中心 IP 上几乎肯定被 403。
    优先用 curl_cffi 模拟 Chrome TLS 指纹绕过,失败再回退到 requests。
    """
    # 优先 curl_cffi (绕 Cloudflare)
    if _CURL_CFFI_AVAILABLE:
        try:
            r = cf_requests.get(url, impersonate="chrome110", timeout=timeout)
            if r.status_code == 200:
                return r.text
            logger.warning(
                "[curl_cffi] {} 返回 {},尝试 requests 回退",
                url, r.status_code
            )
        except Exception as e:
            logger.warning("[curl_cffi] 请求失败: {},回退 requests", e)
    else:
        logger.warning(
            "[bitcointreasuries] curl_cffi 未安装,直连可能被 Cloudflare 拦 403. "
            "建议: pip install curl_cffi"
        )

    # Fallback: 普通 requests (AWS IP 可能被 403)
    session = requests.Session()
    session.headers.update(_HEADERS)
    r = session.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def _parse_btc_amount(text: str) -> Optional[float]:
    if not text:
        return None
    t = text.replace("\xa0", " ").strip()
    if not t or t in ("-", "—", "–", "N/A"):
        return None
    t = t.replace(",", "")
    # 1.166M style
    m = re.match(r"^([\d.]+)\s*([kKmMbB])?$", t)
    if m:
        num = m.group(1)
        suf = (m.group(2) or "").upper()
        try:
            v = Decimal(num)
        except InvalidOperation:
            return None
        mult = {"K": 1000, "M": 10**6, "B": 10**9}.get(suf, 1)
        return float(v * Decimal(mult))
    try:
        return float(t)
    except ValueError:
        return None


def parse_public_companies_table(html: str) -> List[Tuple[str, str, float]]:
    """
    解析首页「Top 100 Public Bitcoin Treasury Companies」表格。
    返回 [(公司名, 股票代码, BTC 持仓量), ...]
    """
    soup = BeautifulSoup(html, "lxml")
    companies: List[Tuple[str, str, float]] = []
    seen_tickers: set = set()

    # 首页「Top 100」常拆成多张连续表格（1–34、35–64…），需全部扫完再按 ticker 去重
    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        if not header_cells:
            continue
        header_text = " ".join(th.get_text(" ", strip=True) for th in header_cells)
        if "Ticker" not in header_text or "Bitcoin" not in header_text:
            continue

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue

            link = tr.find("a", href=re.compile(r"/public-companies/"))
            if not link:
                continue

            company_name = link.get_text(strip=True)
            if not company_name:
                continue

            # 典型列: Rank | 公司+链接 | 国旗 | Ticker | Bitcoin | mNAV
            ticker = tds[3].get_text(strip=True)
            btc_cell = tds[4].get_text(strip=True)
            if not ticker:
                continue

            holdings = _parse_btc_amount(btc_cell)
            if holdings is None:
                continue

            tk = ticker.upper()
            if tk in seen_tickers:
                continue
            seen_tickers.add(tk)
            companies.append((company_name, ticker.strip(), holdings))

    if not companies:
        raise ValueError(
            "未能从页面解析上市公司 BTC 表格（表头需含 Ticker / Bitcoin）。"
            "网站结构可能已变更。"
        )

    logger.info("bitcointreasuries.net 解析到 {} 家公司", len(companies))
    return companies


def sync_bitcointreasuries_holdings(
    mysql_config: Dict[str, Any],
    page_url: str = DEFAULT_URL,
    purchase_date: Optional[date] = None,
    asset_type: str = "BTC",
) -> Dict[str, Any]:
    """
    抓取并写入数据库。purchase_date 默认 UTC 当日（与手工导入默认一致）。
    """
    if purchase_date is None:
        purchase_date = datetime.now(timezone.utc).date()

    html = fetch_homepage_html(page_url)
    companies = parse_public_companies_table(html)

    conn = pymysql.connect(
        host=mysql_config.get("host", "localhost"),
        port=int(mysql_config.get("port", 3306)),
        user=mysql_config.get("user", "root"),
        password=mysql_config.get("password", ""),
        database=mysql_config.get("database", "binance-data"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        cursor = conn.cursor()
        stats = upsert_corporate_holdings_batch(
            cursor,
            companies,
            purchase_date,
            asset_type,
            "bitcointreasuries.net",
        )
        conn.commit()
        stats["success"] = True
        stats["company_count"] = len(companies)
        stats["purchase_date"] = str(purchase_date)
        stats["source_url"] = page_url
        return stats
    finally:
        conn.close()
