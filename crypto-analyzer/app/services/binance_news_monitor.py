#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance 公告监控 - 自动处理新币上线/下架/维护/Launchpool
每30分钟拉取一次，识别4类公告并采取对应行动
"""

import re
import json
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pymysql


# 拉取每页条数。注意：币安该接口在 pageSize=30 时会返回 HTTP 400（无正文），20/50 正常。
FETCH_PAGE_SIZE = 50

# 存储"已处理到的最新文章时间戳"的 system_settings key
SETTINGS_KEY_LAST_TS = 'news_monitor_last_article_ts'

# Binance 公告接口（无需 API Key）
ANNOUNCEMENT_URL = (
    'https://www.binance.com/bapi/composite/v1/public/cms/article/list/query'
)
# 公告正文（HTML）；需 POST JSON，与官网 support 一致
ANNOUNCEMENT_DETAIL_URL = (
    'https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query'
)
# 官网公告页（用 article code 拼接，无斜杠）
ANNOUNCEMENT_PAGE_BASE = 'https://www.binance.com/en/support/announcement/'

# 关键词 → 公告类型（优先级从上到下）
KEYWORD_MAP = [
    ('launchpool',   'launchpool'),
    ('megadrop',     'launchpool'),
    ('launchpad',    'launchpool'),
    ('will delist',  'delisting'),
    ('will remove',  'delisting'),
    ('下架',          'delisting'),
    ('removal',      'delisting'),
    ('maintenance',  'maintenance'),
    ('维护',          'maintenance'),
    ('system upgrade', 'maintenance'),
    ('will list',    'new_listing'),
    ('will launch',  'new_listing'),   # Binance Futures Will Launch XXXUSDT
    ('新上线',        'new_listing'),
    ('listing',      'new_listing'),
]

# 从标题提取交易对 symbol 的正则
# 匹配 "(XYZ)" 或 "(XYZ/USDT)" 格式（旧格式）
SYMBOL_RE = re.compile(r'\(([A-Z0-9]+)(?:/[A-Z]+)?\)')
# 直接出现在标题中的 XXXUSDT / XXXUSDC 格式（新格式，如 "Will Launch MUUSDT and SNDKUSDT"）
SYMBOL_INLINE_RE = re.compile(r'\b([A-Z]{2,10}(?:USDT|USDC|BTC|ETH|BNB))\b')


class BinanceNewsMonitor:
    """Binance 公告监控 - 识别并处理新币上线/下架/维护/Launchpool"""

    def __init__(self, db_config: dict, notifier=None):
        """
        Args:
            db_config: 数据库配置
            notifier: TradeNotifier 实例（可选，用于 Telegram 通知）
        """
        self.db_config = db_config
        self.notifier = notifier
        self.connection = None
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            'Lang': 'en',
            'clienttype': 'web',
            'Accept': 'application/json',
        })

    # ------------------------------------------------------------------
    # 数据库
    # ------------------------------------------------------------------

    def _get_connection(self):
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
            )
        return self.connection

    def _get_last_processed_ts(self, cursor) -> int:
        """从 system_settings 读取上次处理到的文章时间戳（毫秒）"""
        cursor.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = %s",
            (SETTINGS_KEY_LAST_TS,)
        )
        row = cursor.fetchone()
        return int(row['setting_value']) if row else 0

    def _save_last_processed_ts(self, cursor, ts_ms: int):
        """持久化已处理最新文章的时间戳"""
        cursor.execute("""
            INSERT INTO system_settings (setting_key, setting_value, updated_at)
            VALUES (%s, %s, NOW())
            ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value), updated_at = NOW()
        """, (SETTINGS_KEY_LAST_TS, str(ts_ms)))

    # ------------------------------------------------------------------
    # 拉取公告
    # ------------------------------------------------------------------

    def fetch_announcements(self) -> List[Dict]:
        """拉取最新公告列表，返回文章列表（按时间倒序）"""
        params = {
            'type': 1,
            'pageNo': 1,
            'pageSize': FETCH_PAGE_SIZE,
        }
        try:
            resp = self.session.get(ANNOUNCEMENT_URL, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning(
                    "Binance 公告 HTTP %s，将尝试减小 pageSize。body前200字: %s",
                    resp.status_code,
                    (resp.text or "")[:200],
                )
                # 部分环境/参数组合会 400，改用 20 重试一次
                params_retry = dict(params)
                params_retry["pageSize"] = 20
                resp = self.session.get(ANNOUNCEMENT_URL, params=params_retry, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            code = data.get("code")
            if str(code) not in ("000000", "0") or not data.get("data"):
                logger.warning("Binance 公告接口返回异常: code={}", code)
                return []

            # 返回结构: data.catalogs[].articles 或 data.articles；为每条带上 catalogId 供详情接口使用
            articles = []
            catalogs = data['data'].get('catalogs', [])
            for cat in catalogs:
                cid = cat.get('catalogId')
                for art in cat.get('articles', []) or []:
                    if not isinstance(art, dict):
                        continue
                    row = dict(art)
                    if cid is not None:
                        row['catalogId'] = row.get('catalogId') or cid
                    articles.append(row)

            # 兜底：直接在 data 层的 articles
            if not articles:
                for art in data['data'].get('articles', []) or []:
                    if isinstance(art, dict):
                        articles.append(dict(art))

            logger.info("Binance 公告拉取成功: {} 条", len(articles))
            return articles

        except requests.RequestException as e:
            logger.error("Binance 公告请求失败: {}", e)
            return []
        except Exception as e:
            logger.error("Binance 公告解析异常: {}", e)
            return []

    def fetch_article_detail(self, catalog_id: int, article_id: int) -> Optional[Dict]:
        """
        拉取单条公告正文（HTML）。币安返回字段多为无斜杠 id/code，与列表一致。
        失败时返回 None，前端可改用官网链接打开。
        """
        if not catalog_id or not article_id:
            return None
        headers = {
            'Content-Type': 'application/json',
            'Origin': 'https://www.binance.com',
            'Referer': f'https://www.binance.com/en/support/announcement/c-{catalog_id}',
        }
        payloads = (
            {'catalogId': int(catalog_id), 'articleId': int(article_id)},
            {'articleId': int(article_id)},
        )
        for body in payloads:
            try:
                r = self.session.post(
                    ANNOUNCEMENT_DETAIL_URL,
                    json=body,
                    headers=headers,
                    timeout=20,
                )
                if r.status_code != 200:
                    logger.debug(
                        "Binance 公告详情 HTTP {} body={}",
                        r.status_code,
                        (r.text or '')[:200],
                    )
                    continue
                data = r.json()
                if str(data.get('code')) not in ('000000', '0'):
                    continue
                payload = data.get('data')
                if not isinstance(payload, dict):
                    continue
                art = payload.get('article') if isinstance(payload.get('article'), dict) else {}
                # 常见字段名（币安返回无额外路径前缀，body 多为 HTML 字符串）
                body_html = (
                    payload.get('body')
                    or payload.get('content')
                    or art.get('body')
                    or art.get('content')
                )
                title = (
                    payload.get('title')
                    or payload.get('articleTitle')
                    or art.get('title')
                )
                if body_html or title:
                    return {
                        'title': title,
                        'body_html': body_html,
                        'catalog_id': payload.get('catalogId') or art.get('catalogId') or catalog_id,
                        'article_id': payload.get('id') or art.get('id') or article_id,
                        'raw': payload,
                    }
            except Exception as e:
                logger.warning("Binance 公告详情请求失败: {}", e)
        # 部分环境仅允许 GET 缓存请求
        try:
            r = self.session.get(
                ANNOUNCEMENT_DETAIL_URL,
                params={'catalogId': catalog_id, 'articleId': article_id},
                headers=headers,
                timeout=20,
            )
            if r.status_code == 200:
                data = r.json()
                if str(data.get('code')) in ('000000', '0') and isinstance(data.get('data'), dict):
                    payload = data['data']
                    art = payload.get('article') if isinstance(payload.get('article'), dict) else {}
                    body_html = (
                        payload.get('body')
                        or payload.get('content')
                        or art.get('body')
                    )
                    title = payload.get('title') or art.get('title')
                    if body_html or title:
                        return {
                            'title': title,
                            'body_html': body_html,
                            'catalog_id': payload.get('catalogId') or catalog_id,
                            'article_id': payload.get('id') or article_id,
                            'raw': payload,
                        }
        except Exception as e:
            logger.debug("Binance 公告详情 GET 回退失败: {}", e)
        return None

    @staticmethod
    def announcement_url(code: str) -> str:
        """官网单条公告 URL（路径为 article code）；无 code 时回到公告列表页。"""
        c = (code or '').strip()
        if not c:
            return 'https://www.binance.com/en/support/announcement'
        return ANNOUNCEMENT_PAGE_BASE + c

    # ------------------------------------------------------------------
    # 分类 & 解析
    # ------------------------------------------------------------------

    @staticmethod
    def classify_article(title: str) -> Optional[str]:
        """
        根据标题关键词判断公告类型
        返回: 'new_listing' | 'delisting' | 'maintenance' | 'launchpool' | None
        """
        title_lower = title.lower()
        for keyword, ann_type in KEYWORD_MAP:
            if keyword in title_lower:
                return ann_type
        return None

    @staticmethod
    def extract_symbols(title: str) -> List[str]:
        """
        从标题提取币种代码，转为 USDT 永续合约格式。
        支持两种格式：
          旧格式: "Binance Will List XYZ (XYZ)" -> ["XYZUSDT"]
          新格式: "Binance Futures Will Launch MUUSDT and SNDKUSDT..." -> ["MUUSDT", "SNDKUSDT"]
        """
        NOISE_TOKENS = {'USD', 'USDT', 'BTC', 'ETH', 'BNB', 'USDC'}

        symbols = []

        # 优先尝试括号格式（旧格式）
        bracket_matches = SYMBOL_RE.findall(title)
        for m in bracket_matches:
            sym = m.upper()
            if sym in NOISE_TOKENS and len(bracket_matches) > 1:
                continue
            # 过滤纯数字（如日期 "2026"）
            if sym.isdigit():
                continue
            if not sym.endswith('USDT'):
                sym = sym + 'USDT'
            symbols.append(sym)

        # 括号格式没有找到（或只找到日期），尝试内联格式（新格式）
        if not symbols:
            inline_matches = SYMBOL_INLINE_RE.findall(title)
            for sym in inline_matches:
                sym = sym.upper()
                if sym in NOISE_TOKENS:
                    continue
                symbols.append(sym)

        return list(dict.fromkeys(symbols))  # 去重保序

    # ------------------------------------------------------------------
    # 行动处理
    # ------------------------------------------------------------------

    def _handle_new_listing(self, cursor, title: str, symbols: List[str]):
        """新币上线：加入 trading_symbols (enabled=0)，发 Telegram 通知"""
        added = []
        for symbol in symbols:
            try:
                cursor.execute("""
                    INSERT IGNORE INTO trading_symbols (symbol, exchange, enabled)
                    VALUES (%s, 'binance_futures', 0)
                """, (symbol,))
                if cursor.rowcount > 0:
                    added.append(symbol)
            except Exception as e:
                logger.error("新币写入 trading_symbols 失败 [%s]: %s", symbol, e)

        symbols_str = ', '.join(symbols) if symbols else '(未识别)'
        added_str = ', '.join(added) if added else '无新增'
        msg = (
            "[新币上线]\n"
            f"{title}\n\n"
            f"识别交易对: {symbols_str}\n"
            f"已加入 trading_symbols(disabled): {added_str}\n"
            "需手动在管理页面启用后生效"
        )
        logger.info("新币上线公告: %s | 新增: %s", symbols_str, added_str)
        self._notify(msg)

    def _handle_delisting(self, cursor, title: str, symbols: List[str]):
        """下架公告：trading_symbol_rating 升为 Level 3（永久禁止）"""
        blocked = []
        for symbol in symbols:
            # 去掉 USDT 后缀匹配 trading_symbol_rating 的 symbol 格式
            # trading_symbol_rating.symbol 格式可能是 BTCUSDT 或 BTC/USDT，统一处理
            try:
                cursor.execute("""
                    INSERT INTO trading_symbol_rating (symbol, rating_level, margin_multiplier, updated_at)
                    VALUES (%s, 3, 0.0, NOW())
                    ON DUPLICATE KEY UPDATE
                        rating_level = 3,
                        margin_multiplier = 0.0,
                        updated_at = NOW()
                """, (symbol,))
                blocked.append(symbol)
                logger.warning("下架公告 - 设置 Level3 禁止: %s", symbol)
            except Exception as e:
                logger.error("下架处理失败 [%s]: %s", symbol, e)

        symbols_str = ', '.join(symbols) if symbols else '(未识别)'
        blocked_str = ', '.join(blocked) if blocked else '无'
        msg = (
            "[WARNING] [下架公告]\n"
            f"{title}\n\n"
            f"识别交易对: {symbols_str}\n"
            f"已设为 Level3（永久禁止）: {blocked_str}"
        )
        logger.warning("下架公告: %s | 已封禁: %s", symbols_str, blocked_str)
        self._notify(msg)

    def _handle_maintenance(self, title: str, symbols: List[str]):
        """维护公告：仅发 Telegram 通知，不自动改 DB"""
        symbols_str = ', '.join(symbols) if symbols else '不涉及特定交易对'
        msg = (
            "[WARNING] [维护公告]\n"
            f"{title}\n\n"
            f"涉及交易对: {symbols_str}\n"
            "维护期间建议暂停相关交易对"
        )
        logger.warning("维护公告: %s", title[:80])
        self._notify(msg)

    def _handle_launchpool(self, title: str, symbols: List[str]):
        """Launchpool 公告：仅发 Telegram 通知"""
        symbols_str = ', '.join(symbols) if symbols else '(未识别)'
        msg = (
            "[Launchpool/活动公告]\n"
            f"{title}\n\n"
            f"相关币种: {symbols_str}\n"
            "Launchpool 可能带来相关币种大量流入，注意做空风险"
        )
        logger.info("Launchpool 公告: %s", title[:80])
        self._notify(msg)

    def _notify(self, text: str):
        """发送 Telegram 通知（notifier 不存在时静默跳过）"""
        if self.notifier:
            try:
                self.notifier.send_message(text)
            except Exception as e:
                logger.error("Telegram 通知发送失败: %s", e)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self) -> Dict:
        """
        执行一次公告监控
        Returns:
            处理统计 {'new_listing': N, 'delisting': N, 'maintenance': N, 'launchpool': N, 'skipped': N}
        """
        stats = {'new_listing': 0, 'delisting': 0, 'maintenance': 0, 'launchpool': 0, 'skipped': 0}

        articles = self.fetch_announcements()
        if not articles:
            return stats

        conn = self._get_connection()
        cursor = conn.cursor()

        last_ts = self._get_last_processed_ts(cursor)
        max_ts = last_ts

        for article in articles:
            release_ts = article.get('releaseDate', 0)
            title = article.get('title', '').strip()

            if not title or release_ts <= last_ts:
                stats['skipped'] += 1
                continue

            max_ts = max(max_ts, release_ts)

            ann_type = self.classify_article(title)
            if ann_type is None:
                stats['skipped'] += 1
                continue

            symbols = self.extract_symbols(title)
            logger.info("[%s] %s | symbols: %s", ann_type, title[:70], symbols)

            if ann_type == 'new_listing':
                self._handle_new_listing(cursor, title, symbols)
                stats['new_listing'] += 1
            elif ann_type == 'delisting':
                self._handle_delisting(cursor, title, symbols)
                stats['delisting'] += 1
            elif ann_type == 'maintenance':
                self._handle_maintenance(title, symbols)
                stats['maintenance'] += 1
            elif ann_type == 'launchpool':
                self._handle_launchpool(title, symbols)
                stats['launchpool'] += 1

        if max_ts > last_ts:
            self._save_last_processed_ts(cursor, max_ts)

        conn.commit()
        cursor.close()

        logger.info(
            "Binance 公告监控完成: 新上线=%d, 下架=%d, 维护=%d, Launchpool=%d, 跳过=%d",
            stats['new_listing'], stats['delisting'],
            stats['maintenance'], stats['launchpool'], stats['skipped']
        )
        return stats

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            except Exception:
                pass
