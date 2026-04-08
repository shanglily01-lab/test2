#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance 公告监控 API
GET  /api/binance-news/status    - 状态信息（最后检测时间、今日处理数）
GET  /api/binance-news/list      - 公告列表（最近50条已处理记录）
GET  /api/binance-news/detail    - 单条公告正文（HTML）
POST /api/binance-news/run       - 立即触发一次检测
"""

from fastapi import APIRouter, HTTPException
from typing import Optional
import re
import os
import pymysql
from loguru import logger
from datetime import datetime, timezone

router = APIRouter(prefix="/api/binance-news", tags=["binance-news"])


def _sanitize_announcement_html(html: str) -> str:
    """去掉 script/iframe，避免前端 innerHTML 执行脚本。"""
    if not html:
        return ""
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", "", html)
    s = re.sub(r"(?is)<\s*iframe[^>]*>.*?</iframe>", "", s)
    return s


# ──────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────

def _get_db():
    from dotenv import load_dotenv
    load_dotenv()
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "binance-data"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@router.get("/status")
def get_status():
    """返回监控状态：最后检测时间、今日处理条数、运行状态"""
    try:
        conn = _get_db()
        cur = conn.cursor()

        # 最后一次检测时间（毫秒时间戳存在 system_settings）
        cur.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = 'news_monitor_last_article_ts'"
        )
        row = cur.fetchone()
        last_ts_ms = int(row["setting_value"]) if row else 0
        last_ts_str = "从未检测"
        if last_ts_ms:
            dt = datetime.fromtimestamp(last_ts_ms / 1000, tz=timezone.utc)
            diff_s = (datetime.now(tz=timezone.utc) - dt).total_seconds()
            if diff_s < 60:
                last_ts_str = f"{int(diff_s)} 秒前"
            elif diff_s < 3600:
                last_ts_str = f"{int(diff_s/60)} 分钟前"
            elif diff_s < 86400:
                last_ts_str = f"{int(diff_s/3600)} 小时前"
            else:
                last_ts_str = dt.strftime("%m-%d %H:%M")

        conn.close()
        return {
            "last_check": last_ts_str,
            "last_ts_ms": last_ts_ms,
            "status": "正常",
            "check_interval_minutes": 30,
        }
    except Exception as e:
        logger.error("binance-news status error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
def get_news_list(ann_type: Optional[str] = None, limit: int = 50):
    """
    从 system_settings 或专用日志表读取已处理的公告记录
    由于目前 binance_news_monitor 只在内存/日志中记录，
    这里直接触发一次拉取并返回最新内容（不改 DB）供前端展示
    """
    try:
        from app.services.binance_news_monitor import BinanceNewsMonitor
        from dotenv import load_dotenv
        load_dotenv()

        db_config = {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", 3306)),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "binance-data"),
        }

        monitor = BinanceNewsMonitor(db_config)
        articles = monitor.fetch_announcements()
        monitor.close()

        TYPE_COLORS = {
            "new_listing": "green",
            "delisting":   "red",
            "maintenance": "orange",
            "launchpool":  "blue",
        }
        TYPE_LABELS = {
            "new_listing": "LISTING",
            "delisting":   "DELISTING",
            "maintenance": "SYSTEM",
            "launchpool":  "LAUNCHPOOL",
        }

        items = []
        for art in articles:
            title = art.get("title", "")
            release_ms = art.get("releaseDate", 0)
            ann_type_detected = monitor.classify_article(title)
            if ann_type_detected is None:
                continue
            if ann_type and ann_type != ann_type_detected:
                continue

            symbols = monitor.extract_symbols(title)
            dt = datetime.fromtimestamp(release_ms / 1000, tz=timezone.utc) if release_ms else None
            ts_str = dt.strftime("%m-%d %H:%M") if dt else ""

            action_text = ""
            action_color = "slate"
            if ann_type_detected == "new_listing":
                action_text = "已加入 trading_symbols(disabled)"
                action_color = "green"
            elif ann_type_detected == "delisting":
                action_text = "已设为 Level3 永久禁止"
                action_color = "red"
            else:
                action_text = "Telegram 已通知"
                action_color = "slate"

            cid = art.get("catalogId")
            aid = art.get("id")
            code = art.get("code") or ""

            items.append({
                "title": title,
                "type": ann_type_detected,
                "color": TYPE_COLORS.get(ann_type_detected, "slate"),
                "badge": TYPE_LABELS.get(ann_type_detected, "INFO"),
                "symbols": symbols,
                "action": action_text,
                "action_color": action_color,
                "timestamp": ts_str,
                "release_ms": release_ms,
                "catalog_id": cid,
                "article_id": aid,
                "code": code,
                "announcement_url": BinanceNewsMonitor.announcement_url(code),
            })

        # 按时间倒序
        items.sort(key=lambda x: x["release_ms"], reverse=True)
        items = items[:limit]

        counts = {
            "all": len(items),
            "new_listing": sum(1 for x in items if x["type"] == "new_listing"),
            "delisting":   sum(1 for x in items if x["type"] == "delisting"),
            "maintenance": sum(1 for x in items if x["type"] == "maintenance"),
            "launchpool":  sum(1 for x in items if x["type"] == "launchpool"),
        }
        return {"items": items, "counts": counts}

    except Exception as e:
        logger.error("binance-news list error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail")
def get_article_detail(
    catalog_id: int,
    article_id: int,
    code: Optional[str] = None,
):
    """
    拉取币安单条公告 HTML 正文；失败时仍返回官网链接供浏览器打开。
    前端应传入列表中的 code，用于拼接官网 URL（币安路径为无斜杠的 article code）。
    """
    try:
        from app.services.binance_news_monitor import BinanceNewsMonitor
        from dotenv import load_dotenv
        load_dotenv()

        db_config = {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", 3306)),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "binance-data"),
        }

        official = BinanceNewsMonitor.announcement_url((code or "").strip())

        monitor = BinanceNewsMonitor(db_config)
        try:
            detail = monitor.fetch_article_detail(catalog_id, article_id)
        finally:
            monitor.close()

        if detail:
            body = _sanitize_announcement_html(detail.get("body_html") or "")
            return {
                "ok": True,
                "title": detail.get("title"),
                "body_html": body,
                "catalog_id": detail.get("catalog_id") or catalog_id,
                "article_id": detail.get("article_id") or article_id,
                "announcement_url": official,
            }

        return {
            "ok": False,
            "title": None,
            "body_html": "",
            "catalog_id": catalog_id,
            "article_id": article_id,
            "announcement_url": official,
            "message": "未能从币安接口获取正文，请使用下方链接在官网查看",
        }
    except Exception as e:
        logger.error("binance-news detail error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
def run_now():
    """立即触发一次公告检测"""
    try:
        from app.services.binance_news_monitor import BinanceNewsMonitor
        from dotenv import load_dotenv
        load_dotenv()

        db_config = {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", 3306)),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "binance-data"),
        }

        monitor = BinanceNewsMonitor(db_config)
        stats = monitor.run()
        monitor.close()

        return {"ok": True, "stats": stats}
    except Exception as e:
        logger.error("binance-news run error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
