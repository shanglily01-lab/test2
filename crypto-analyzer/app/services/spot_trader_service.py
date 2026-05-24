#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易策略服务 (Spot Trader Service)

核心逻辑:
  - 每 30 分钟扫描候选币, 寻找下跌至支撑位的买入机会
  - 技术条件: 日线 RSI<35(超卖) + 价格靠近 MA99
  - 买入后设置 止盈(+25%) / 止损(-15%) / 超时(14天)
  - 每 1 分钟检查持仓状态, 触发条件时自动卖出
  - 实盘同步: Binance 现货限价单 (低于市价 0.2%) / 市价卖出
  - 单笔金额: 从 API key 的 max_position_value 读取

策略风格:
  - DCA 抄底型 — "别人恐惧我贪婪"
  - 条件放宽, 日线大方向正确即可入场 (不做短线企稳判断)
  - 预期持有 1~14 天, 止盈 25% (顺趋势反弹)
  - 控制单笔仓位 (固定 max_position_value USDT / 单)
  - 最多同时持有 5 个现货仓位
"""

import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import pymysql
import pymysql.cursors
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Optional, List, Tuple
from loguru import logger
import time as _time

from app.analyzers.technical_indicators import TechnicalIndicators
from app.utils.config_loader import get_db_config
from app.services.binance_data_hub import get_global_data_hub

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
ACCOUNT_ID = 3                     # 现货专用账号 ID
MAX_POSITIONS = 5                  # 最大同时持仓数
MAX_HOLD_HOURS = 336.0             # 最长持有时间 (14 天, 现货以周为单位)
TP_PCT = 0.25                      # 止盈 +25% (超卖反弹空间大)
SL_PCT = 0.15                      # 止损 -15% (现货无爆仓, 宽松风控)
LIMIT_SPREAD = 0.002               # 限价单挂单: 低于市价 0.2%

SOURCE = 'spot_dca'                # 策略标识
SCAN_INTERVAL_MINUTES = 30         # 扫描间隔
POSITION_CHECK_INTERVAL_SECONDS = 60  # 持仓检查间隔

ti = TechnicalIndicators()


# ──────────────────────────────────────────────
# DB 工具
# ──────────────────────────────────────────────
def _get_conn():
    return pymysql.connect(
        **get_db_config(),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )

def _get_klines(symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    """查询 K 线, 返回标准 DataFrame"""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT open_time, open_price, high_price, low_price, close_price, volume
            FROM kline_data
            WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures'
            ORDER BY open_time DESC LIMIT %s
        """, (symbol, timeframe, limit))
        rows = cur.fetchall()
        cur.close(); conn.close()

        if len(rows) < max(10, limit // 3):
            return None

        df = pd.DataFrame(rows)
        df = df.rename(columns={
            'open_price': 'open', 'high_price': 'high',
            'low_price': 'low', 'close_price': 'close',
        })
        for col in ('open', 'high', 'low', 'close', 'volume'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.sort_values('open_time').reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"[现货] 获取K线失败 {symbol}/{timeframe}: {e}")
        return None


# ──────────────────────────────────────────────
# 价格获取 (统一走 HUB)
# ──────────────────────────────────────────────
def _get_current_price(symbol: str) -> Optional[float]:
    """
    获取实时价格 — 统一走 BinanceDataHub.
    数据源优先级: WS(50ms) → 进程内缓存(≤60s) → DB(≤5min) → REST 兜底
    """
    try:
        hub = get_global_data_hub()
        p = hub.get_price_sync(symbol, max_age_seconds=90)
        if p is not None and p > 0:
            return float(p)
    except Exception as e:
        logger.warning(f"[现货] HUB 取价失败 {symbol}: {e}")
    return None


# ──────────────────────────────────────────────
# 单笔金额获取 (从 API KEY)
# ──────────────────────────────────────────────
def _get_margin_per_trade() -> float:
    """
    获取单笔买入金额.
    优先从活跃 API key 的 max_position_value 读取,
    各 key 取最小值, 默认 100 USDT.
    """
    try:
        from app.services.api_key_service import APIKeyService
        svc = APIKeyService(get_db_config())
        keys = svc.get_all_active_api_keys('binance')
        if keys:
            vals = [float(k.get('max_position_value', 0) or 0) for k in keys if float(k.get('max_position_value', 0) or 0) > 0]
            if vals:
                return min(vals)
    except Exception as e:
        logger.debug(f"[现货] 读取 API key 金额失败: {e}")
    return 100.0


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def _read_setting_bool(key: str, default: bool = False) -> bool:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT setting_value FROM system_settings WHERE setting_key=%s", (key,))
        r = cur.fetchone()
        cur.close(); conn.close()
        if r:
            return r['setting_value'] in ('1', 'true', 'True')
        return default
    except Exception:
        return default

def _position_count() -> int:
    """当前现货持仓数量"""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM spot_positions WHERE status='open' AND account_id=%s",
            (ACCOUNT_ID,)
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        return int(row['cnt']) if row else 0
    except Exception:
        return 999

def _has_position(symbol: str) -> bool:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM spot_positions WHERE symbol=%s AND status='open' AND account_id=%s LIMIT 1",
            (symbol, ACCOUNT_ID)
        )
        r = cur.fetchone()
        cur.close(); conn.close()
        return r is not None
    except Exception:
        return True  # 保守: 如果不能查就跳过

def _get_top50_symbols() -> List[str]:
    """从 top_performing_symbols 表获取 TOP 50"""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol FROM top_performing_symbols ORDER BY rank_score ASC LIMIT 50"
        )
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [r['symbol'] for r in rows if r['symbol'].endswith('/USDT')]
    except Exception as e:
        logger.warning(f"[现货] 获取 TOP50 失败: {e}")
        return []


# ──────────────────────────────────────────────
# 买入逻辑
# ──────────────────────────────────────────────
def _buy(symbol: str, market_price: float, reason: str) -> bool:
    """执行模拟现货买入 (以 market_price 作价, 实盘挂限价单)"""
    try:
        if not market_price or market_price <= 0:
            return False

        margin = _get_margin_per_trade()
        qty = round(margin / market_price, 8)
        if qty <= 0:
            logger.warning(f"[现货] {symbol} 数量过小 {qty}, 跳过")
            return False

        planned_close = datetime.utcnow() + timedelta(hours=MAX_HOLD_HOURS)
        limit_price = round(market_price * (1 - LIMIT_SPREAD), 8)  # 低于市价 0.2%
        tp_price = round(market_price * (1 + TP_PCT), 8)
        sl_price = round(market_price * (1 - SL_PCT), 8)

        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO spot_positions
                (account_id, symbol, direction, quantity, notional_value, cost_basis,
                 entry_price, mark_price,
                 take_profit_price, stop_loss_price, take_profit_pct, stop_loss_pct,
                 status, source, entry_reason, open_time, planned_close_time,
                 unrealized_pnl, realized_pnl)
            VALUES (%s,%s,'long',%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    'open',%s,%s,NOW(),%s,0,0)
        """, (
            ACCOUNT_ID, symbol, qty, round(margin, 2), round(margin, 2),
            market_price, market_price,
            tp_price, sl_price, TP_PCT * 100, SL_PCT * 100,
            SOURCE, reason, planned_close,
        ))
        cur.close(); conn.close()

        logger.info(f"[现货] 买入 {symbol} x {qty:.6g} @ 市价{market_price:.6g} "
                     f"限价{limit_price:.6g}(-{LIMIT_SPREAD*100:.1f}%)  "
                     f"TP={tp_price:.6g}(+{TP_PCT*100:.0f}%)  "
                     f"SL={sl_price:.6g}(-{SL_PCT*100:.0f}%)")

        # 实盘同步: 挂限价单
        if _read_setting_bool('spot_trading_enabled', default=True):
            _sync_live_buy(symbol, qty, limit_price, margin)

        return True
    except Exception as e:
        logger.error(f"[现货] 买入失败 {symbol}: {e}")
        return False

def _sync_live_buy(symbol: str, quantity: Decimal, limit_price: float, margin: float):
    """同步到 Binance 现货 — 挂 LIMIT 单 (低于市价 0.2%)"""
    try:
        from app.services.api_key_service import APIKeyService
        from app.trading.binance_spot_engine import BinanceSpotEngine
        dbc = get_db_config()
        svc = APIKeyService(dbc)
        active_keys = svc.get_all_active_api_keys('binance')
    except Exception as e:
        logger.warning(f"[现货] 获取 API key 失败: {e}")
        return

    for ak in active_keys:
        try:
            engine = BinanceSpotEngine(
                dbc,
                api_key=ak['api_key'],
                api_secret=ak['api_secret'],
            )
            engine.create_limit_buy_order(
                account_id=ak['id'],
                symbol=symbol,
                limit_price=limit_price,
                quote_quantity=margin,
                source=SOURCE,
            )
            logger.info(f"[现货实盘] {ak['account_name']} {symbol} 限价挂单 {limit_price:.6g} 共{margin:.0f}U")
        except Exception as e:
            logger.error(f"[现货实盘] 限价买入失败 {ak.get('account_name', '?')} {symbol}: {e}")


# ──────────────────────────────────────────────
# 卖出逻辑 (市价单)
# ──────────────────────────────────────────────
def _close_position(pos_id: int, symbol: str, reason: str):
    """平仓(市价卖出)"""
    try:
        price = _get_current_price(symbol)
        if not price or price <= 0:
            logger.warning(f"[现货] {symbol} 平仓价无效, 跳过")
            return

        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE spot_positions
            SET status='closed', close_price=%s, mark_price=%s,
                realized_pnl = (close_price - entry_price) * quantity,
                close_time=NOW(), close_reason=%s
            WHERE id=%s AND status='open'
        """, (price, price, reason, pos_id))
        cur.close(); conn.close()

        logger.info(f"[现货] 卖出 {symbol}  id={pos_id}  reason={reason}  price={price:.6g}")

        # 实盘同步 (市价卖出)
        if _read_setting_bool('spot_close_enabled', default=True):
            _sync_live_sell(symbol, pos_id, price)
    except Exception as e:
        logger.error(f"[现货] 卖出失败 {symbol}: {e}")

def _sync_live_sell(symbol: str, pos_id: int, price: float):
    """同步到 Binance 现货市价卖出"""
    try:
        from app.services.api_key_service import APIKeyService
        from app.trading.binance_spot_engine import BinanceSpotEngine
        dbc = get_db_config()
        svc = APIKeyService(dbc)
        active_keys = svc.get_all_active_api_keys('binance')
    except Exception as e:
        logger.warning(f"[现货] 获取 API key 失败: {e}")
        return

    # 获取持仓数量
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT quantity FROM spot_positions WHERE id=%s", (pos_id,)
        )
        r = cur.fetchone()
        cur.close(); conn.close()
        if not r:
            return
        qty = Decimal(str(r['quantity']))
    except Exception:
        return

    for ak in active_keys:
        try:
            engine = BinanceSpotEngine(
                dbc,
                api_key=ak['api_key'],
                api_secret=ak['api_secret'],
            )
            engine.create_market_sell_order(
                account_id=ak['id'],
                symbol=symbol,
                quantity=qty,
                source=SOURCE,
            )
            logger.info(f"[现货实盘] {ak['account_name']} {symbol} 市价卖出 {qty}")
        except Exception as e:
            logger.error(f"[现货实盘] 卖出失败 {ak.get('account_name', '?')} {symbol}: {e}")


# ──────────────────────────────────────────────
# 核心扫描
# ──────────────────────────────────────────────
def _check_entry_conditions(symbol: str) -> Optional[str]:
    """
    检查买入条件
    返回: 买入理由字符串, 或 None(不符合条件)
    """
    try:
        df_daily = _get_klines(symbol, '1d', 120)
        if df_daily is None or len(df_daily) < 100:
            return None

        close_d = df_daily['close'].astype(float)

        # 日线 RSI < 35 (超卖区域) — "别人恐惧我贪婪"
        rsi_d = ti.calculate_rsi(df_daily)
        if rsi_d is None or len(rsi_d) < 2:
            return None
        last_rsi = float(rsi_d.iloc[-1])
        if last_rsi >= 35:
            return None

        # 价格在 MA99 附近, 允许惯性下跌到 -15% (超卖后可能继续跌一段)
        ma99 = close_d.rolling(99).mean().iloc[-1]
        cur_close = float(close_d.iloc[-1])
        dist_to_ma99 = (cur_close - ma99) / ma99
        if dist_to_ma99 < -0.15 or dist_to_ma99 > 0.08:
            return None

        # 1H 快速确认: 只要不是单边崩盘即可
        df_1h = _get_klines(symbol, '1h', 24)
        if df_1h is None or len(df_1h) < 12:
            return None

        close_1h = df_1h['close'].astype(float)

        # 1H 价格不低于 MA20 的 97% (刚跌破但收回的情况可接受)
        ma20_1h = close_1h.rolling(20).mean().iloc[-1]
        cur_1h_close = float(close_1h.iloc[-1])
        if cur_1h_close < ma20_1h * 0.97:
            return None

        reason = (
            f"日RSI={last_rsi:.0f}超卖+"
            f"MA99偏离={dist_to_ma99*100:+.1f}%+"
            f"1H>MA20={cur_1h_close/ma20_1h*100-100:+.1f}%"
        )
        return reason[:200]

    except Exception as e:
        logger.debug(f"[现货] {symbol} 条件检查异常: {e}")
        return None


def _check_open_positions():
    """扫描所有持仓, 检查是否需要卖出"""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, symbol, entry_price, take_profit_price, stop_loss_price,
                   planned_close_time, open_time, quantity, source, entry_reason
            FROM spot_positions
            WHERE status='open' AND account_id=%s
        """, (ACCOUNT_ID,))
        positions = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        logger.error(f"[现货] 查询持仓失败: {e}")
        return

    for pos in positions:
        pid = pos['id']
        symbol = pos['symbol']
        entry = float(pos['entry_price'])
        tp = float(pos['take_profit_price']) if pos.get('take_profit_price') else None
        sl = float(pos['stop_loss_price']) if pos.get('stop_loss_price') else None
        planned_close = pos.get('planned_close_time')

        price = _get_current_price(symbol)
        if not price or price <= 0:
            continue

        profit_pct = (price - entry) / entry

        # 止盈 +25%
        if tp and price >= tp:
            _close_position(pid, symbol, f"止盈+{profit_pct*100:.1f}%")
            continue

        # 止损 -15%
        if sl and price <= sl:
            _close_position(pid, symbol, f"止损{profit_pct*100:.1f}%")
            continue

        # 超时 14 天
        if planned_close and datetime.utcnow() >= planned_close:
            _close_position(pid, symbol, f"超时平仓(profit={profit_pct*100:+.1f}%)")
            continue


# ──────────────────────────────────────────────
# 主循环
# ──────────────────────────────────────────────
def _scan_buy_opportunities():
    """扫描买入机会"""
    if not _read_setting_bool('spot_trading_enabled', default=True):
        logger.debug("[现货] 现货交易已关闭, 跳过扫描")
        return

    count = _position_count()
    if count >= MAX_POSITIONS:
        logger.debug(f"[现货] 持仓已达 {MAX_POSITIONS}, 跳过扫描")
        return

    symbols = _get_top50_symbols()
    if not symbols:
        logger.warning("[现货] 候选池为空, 跳过扫描")
        return

    margin = _get_margin_per_trade()
    opened = 0
    remaining = MAX_POSITIONS - count
    logger.info(f"[现货] 扫描买入机会: 当前持仓{count}, 还可开{remaining}, "
                 f"每单{margin:.0f}U")

    for symbol in symbols:
        if opened >= remaining:
            break
        if _has_position(symbol):
            continue

        reason = _check_entry_conditions(symbol)
        if reason:
            price = _get_current_price(symbol)
            if _buy(symbol, price, reason):
                opened += 1


def run_scan():
    """外部调度入口 — 每 30 分钟扫描买入"""
    _scan_buy_opportunities()


def run_position_check():
    """外部调度入口 — 每 1 分钟检查持仓"""
    if not _read_setting_bool('spot_trading_enabled', default=True):
        return
    _check_open_positions()


# ==========================================================
# asyncio 后台任务
# ==========================================================
async def spot_trader_loop():
    """后台常驻循环: 扫描买入(30min) + 持仓检查(1min)"""
    logger.info("[现货] 后台循环启动")
    last_scan = 0
    while True:
        try:
            _check_open_positions()
        except Exception as e:
            logger.error(f"[现货] 持仓检查异常: {e}")

        now = _time.time()
        if now - last_scan >= SCAN_INTERVAL_MINUTES * 60:
            try:
                _scan_buy_opportunities()
            except Exception as e:
                logger.error(f"[现货] 扫描异常: {e}")
            last_scan = now

        await asyncio.sleep(POSITION_CHECK_INTERVAL_SECONDS)


# ==========================================================
# 独立启动入口
# ==========================================================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("现货交易策略服务 — 独立启动")
    logger.info("=" * 50)

    # 首次运行时立即扫描一轮
    logger.info("[现货] 首次扫描买入机会...")
    _scan_buy_opportunities()
    logger.info("[现货] 首次扫描完成")

    # 进入后台循环
    asyncio.run(spot_trader_loop())
