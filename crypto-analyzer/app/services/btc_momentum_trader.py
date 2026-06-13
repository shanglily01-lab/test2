#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC动量跟随策略
- 监测BTC实时价格，15~60分钟内涨跌幅 >= 1.5% 触发
- 开仓：TOP50全部交易对，方向与BTC一致
- 模拟盘：400U x5；实盘：账号配置(100U) x5
- 止损2%，止盈6%，触发后4小时冷却
- 同向持仓保留，反向持仓先平后开
"""

import pymysql
import pymysql.cursors
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
from loguru import logger

from app.utils.position_time import utc_now_naive


class BTCMomentumTrader:
    COOLDOWN_HOURS = 4
    TRIGGER_PCT = 1.5          # 触发阈值 %
    WINDOWS_MIN = [15, 30, 45, 60]  # 检测窗口（分钟）
    PAPER_MARGIN = 400         # 模拟盘每单保证金(U)
    LEVERAGE = 5
    STOP_LOSS_PCT = 0.03       # 默认3%，运行时从system_settings读取
    TAKE_PROFIT_PCT = 0.05     # 默认5%，运行时从system_settings读取
    PAPER_ACCOUNT_ID = 2

    def __init__(self, db_config: dict, ws_price_service=None):
        self.db_config = db_config
        self.ws_service = ws_price_service
        self._btc_history: List[Tuple[datetime, float]] = []  # [(time, price)]
        self._last_trigger_time: Optional[datetime] = None
        self._preload_btc_history()  # 启动时从DB预填充，避免重启后等待15分钟

    def _get_conn(self):
        return pymysql.connect(
            **self.db_config, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=True
        )

    def _get_sl_tp_from_settings(self):
        """从 system_settings 读取止损止盈比例（小数，0.03=3%）。"""
        from app.services.system_settings_loader import get_sl_tp_decimal
        return get_sl_tp_decimal()

    # ──────────────────────────────────────────
    # 价格跟踪
    # ──────────────────────────────────────────

    def _preload_btc_history(self):
        """启动时从kline_data预加载最近90分钟BTC价格，避免重启后等待积累。
        注: 1m K线已于2026-01-22停采，改用5m。90分钟窗口对应18根5m K线，足够check_trigger使用。"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT open_time, close_price FROM kline_data "
                "WHERE symbol='BTC/USDT' AND timeframe='5m' AND exchange='binance_futures' "
                "AND open_time >= (UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 90 MINUTE)) * 1000) "
                "ORDER BY open_time ASC"
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            for row in rows:
                ts = datetime.utcfromtimestamp(row['open_time'] / 1000)
                self._btc_history.append((ts, float(row['close_price'])))
            logger.info(f"[BTC动量] 预加载 {len(self._btc_history)} 条BTC价格历史（最近90分钟5m K线）")
        except Exception as e:
            logger.warning(f"[BTC动量] 预加载历史失败: {e}")

    def record_btc_price(self, price: float):
        """主循环每分钟调用，记录BTC当前价格"""
        now = datetime.now()
        self._btc_history.append((now, price))
        cutoff = now - timedelta(minutes=90)
        self._btc_history = [(t, p) for t, p in self._btc_history if t >= cutoff]

    def _get_btc_current_price(self) -> Optional[float]:
        if self.ws_service:
            p = self.ws_service.get_price('BTC/USDT')
            if p:
                return float(p)
        if self._btc_history:
            return self._btc_history[-1][1]
        return None

    # ──────────────────────────────────────────
    # 触发检测
    # ──────────────────────────────────────────

    def check_trigger(self) -> Optional[Tuple[str, int, float]]:
        """
        检查是否触发动量信号
        返回 (direction, window_min, pct) 或 None
        """
        # 冷却期
        if self._last_trigger_time:
            elapsed = (datetime.now() - self._last_trigger_time).total_seconds()
            if elapsed < self.COOLDOWN_HOURS * 3600:
                remaining = (self.COOLDOWN_HOURS * 3600 - elapsed) / 60
                logger.debug(f"[BTC动量] 冷却中，剩余 {remaining:.0f} 分钟")
                return None

        current = self._get_btc_current_price()
        if not current or len(self._btc_history) < 5:
            return None

        now = datetime.now()
        for window in self.WINDOWS_MIN:
            cutoff = now - timedelta(minutes=window)
            past_prices = [(t, p) for t, p in self._btc_history if t <= cutoff]
            if not past_prices:
                continue
            past_price = past_prices[-1][1]
            pct = (current - past_price) / past_price * 100
            if abs(pct) >= self.TRIGGER_PCT:
                direction = 'LONG' if pct > 0 else 'SHORT'
                return (direction, window, round(pct, 2))

        return None

    # ──────────────────────────────────────────
    # 数据查询
    # ──────────────────────────────────────────

    def _get_top100(self) -> List[str]:
        """从 top_performing_symbols 获取TOP50列表（可配置排除 L3）"""
        try:
            from app.services.trading_gates import load_blacklist_level3_symbols
            from app.utils.futures_symbol import futures_symbol_clean
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT symbol FROM top_performing_symbols ORDER BY rank_score DESC LIMIT 50")
            rows = cur.fetchall()
            banned = load_blacklist_level3_symbols(conn)
            cur.close(); conn.close()
            if rows:
                return [
                    r['symbol'] for r in rows
                    if futures_symbol_clean(r['symbol']) not in banned
                ]
        except Exception as e:
            logger.warning(f"[BTC动量] 获取TOP50失败: {e}")
        return []

    def _get_symbol_price(self, symbol: str) -> Optional[float]:
        """获取交易对当前价格（WS优先，fallback DB 5m K线，超15分钟拒绝）。
        注: 1m K线已于2026-01-22停采，改用5m并加新鲜度检查防止用过期价格开仓。"""
        if self.ws_service:
            p = self.ws_service.get_price(symbol)
            if p:
                return float(p)
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT close_price, open_time FROM kline_data "
                "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1", (symbol,)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if not row:
                return None
            age_min = (datetime.now().timestamp() - row['open_time'] / 1000) / 60
            if age_min > 15:
                logger.warning(f"[BTC动量] {symbol} 5m K线数据 {age_min:.0f} 分钟旧，拒绝使用")
                return None
            return float(row['close_price'])
        except Exception as e:
            logger.warning(f"[BTC动量] _get_symbol_price 异常 {symbol}: {e}")
            return None

    def _get_open_positions(self) -> Dict[str, dict]:
        """获取当前模拟盘所有开仓，返回 {symbol: position_row}"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, symbol, position_side, entry_price, margin "
                "FROM futures_positions WHERE status='open' AND account_id=%s",
                (self.PAPER_ACCOUNT_ID,)
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return {r['symbol']: r for r in rows}
        except Exception as e:
            logger.error(f"[BTC动量] 查询持仓失败: {e}")
            return {}

    def _is_live_enabled(self) -> bool:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT setting_value FROM system_settings WHERE setting_key='live_trading_enabled'")
            row = cur.fetchone()
            cur.close(); conn.close()
            return row and str(row['setting_value']) in ('1', 'true')
        except:
            return False

    def _get_active_live_accounts(self) -> List[dict]:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, account_name, max_position_value, max_leverage "
                "FROM user_api_keys WHERE status='active'"
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return rows
        except:
            return []

    # ──────────────────────────────────────────
    # 开平仓操作
    # ──────────────────────────────────────────

    def _close_position(self, pos: dict, reason: str = 'BTC动量反向平仓'):
        """平掉模拟盘指定持仓（写入 futures_trades）"""
        try:
            price = self._get_symbol_price(pos['symbol'])
            if not price:
                return
            from app.trading.futures_trading_engine import FuturesTradingEngine

            result = FuturesTradingEngine(self.db_config).close_position(
                pos['id'],
                reason=reason,
                close_price=Decimal(str(price)),
            )
            if result.get('success'):
                pnl = float(result.get('realized_pnl') or 0)
                logger.info(f"[BTC动量] 平仓 {pos['symbol']} {pos['position_side']} pnl={pnl:+.2f}U")
            else:
                logger.warning(
                    f"[BTC动量] 平仓失败 {pos['symbol']}: {result.get('message', result)}"
                )
        except Exception as e:
            logger.error(f"[BTC动量] 平仓失败 {pos['symbol']}: {e}")

    def _open_paper_position(self, symbol: str, direction: str,
                              entry_price: float, trigger_info: str) -> bool:
        """开模拟盘仓位"""
        try:
            sl_pct, tp_pct = self._get_sl_tp_from_settings()
            margin = self.PAPER_MARGIN
            notional = margin * self.LEVERAGE
            qty = round(notional / entry_price, 6)
            if direction == 'LONG':
                sl = round(entry_price * (1 - sl_pct), 8)
                tp = round(entry_price * (1 + tp_pct), 8)
            else:
                sl = round(entry_price * (1 + sl_pct), 8)
                tp = round(entry_price * (1 - tp_pct), 8)

            from app.services.paper_open_gate import gate_simulated_open
            allowed, _gate_reason = gate_simulated_open(
                symbol, direction, entry_price, 'BTC_MOMENTUM', trigger_info,
                leverage=self.LEVERAGE,
                sl_pct=sl_pct * 100, tp_pct=tp_pct * 100,
                hold_hours=float(self._get_max_hold_hours()),
            )
            if not allowed:
                return False

            from app.services.paper_limit_entry import create_paper_limit_order
            conn = self._get_conn()
            planned_close_time = utc_now_naive() + timedelta(hours=self._get_max_hold_hours())
            order_id = create_paper_limit_order(
                conn,
                symbol=symbol,
                side=direction,
                ref_price=entry_price,
                source='BTC_MOMENTUM',
                leverage=self.LEVERAGE,
                margin=margin,
                quantity=qty,
                stop_loss_price=sl,
                take_profit_price=tp,
                stop_loss_pct=sl_pct * 100,
                take_profit_pct=tp_pct * 100,
                entry_reason=trigger_info,
                max_hold_minutes=int(self._get_max_hold_hours() * 60),
                planned_close_time=planned_close_time,
                account_id=self.PAPER_ACCOUNT_ID,
            )
            conn.close()
            if order_id is None:
                return False
            logger.info(f"[BTC动量] 限价挂单 {symbol} {direction} 参考价={entry_price:.6g} 订单={order_id}")
            return True
        except Exception as e:
            logger.error(f"[BTC动量] 开仓失败 {symbol}: {e}")
            return False

    def _sync_live(self, symbol: str, direction: str, entry_price: float,
                   paper_pos_id: int, trigger_info: str):
        """同步到实盘账号（调用交易引擎真实下单）"""
        from app.services.trading_gates import should_sync_live_for_source
        if not self._is_live_enabled() or not should_sync_live_for_source("btc_momentum"):
            return
        try:
            from app.services.api_key_service import APIKeyService
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            svc = APIKeyService(self.db_config)
            active_keys = svc.get_all_active_api_keys('binance')
        except Exception as e:
            logger.error(f"[BTC动量] 获取实盘账号失败: {e}")
            return

        MAX_LIVE_POSITIONS = 20
        sl_pct, tp_pct = self._get_sl_tp_from_settings()

        for ak in active_keys:
            try:
                # 检查该账号实盘持仓数量上限
                conn = self._get_conn()
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM live_futures_positions "
                    "WHERE account_id=%s AND status='OPEN'",
                    (ak['id'],)
                )
                live_cnt = (cur.fetchone() or {}).get('cnt', 0)
                cur.close(); conn.close()
                if live_cnt >= MAX_LIVE_POSITIONS:
                    logger.info(f"[BTC动量] {ak['account_name']} 实盘已有 {live_cnt} 单，跳过 {symbol}")
                    continue

                margin = float(ak.get('max_position_value') or 100)
                lev = int(ak.get('max_leverage') or 5)
                notional = margin * lev
                qty = Decimal(str(round(notional / entry_price, 6)))

                engine = BinanceFuturesEngine(
                    self.db_config,
                    api_key=ak['api_key'],
                    api_secret=ak['api_secret']
                )
                result = engine.open_position(
                    account_id=ak['id'],
                    symbol=symbol,
                    position_side=direction,
                    quantity=qty,
                    leverage=lev,
                    stop_loss_pct=Decimal(str(sl_pct * 100)),
                    take_profit_pct=Decimal(str(tp_pct * 100)),
                    source='BTC_MOMENTUM',
                    paper_position_id=paper_pos_id
                )
                if result.get('success'):
                    logger.info(f"[BTC动量] ✅ 实盘下单成功 {ak['account_name']} {symbol} {direction}")
                else:
                    logger.error(f"[BTC动量] ❌ 实盘下单失败 {ak['account_name']} {symbol}: {result.get('error','')}")
            except Exception as e:
                logger.error(f"[BTC动量] 实盘下单异常 {ak.get('account_name','')} {symbol}: {e}")

    # ──────────────────────────────────────────
    # 主执行入口
    # ──────────────────────────────────────────

    def execute(self, direction: str, window: int, pct: float):
        """触发后执行全部TOP50交易"""
        trigger_info = f"BTC {window}分内{pct:+.2f}%"
        logger.info(f"🚀 [BTC动量] 触发！{trigger_info} → 开{direction}")

        top100 = self._get_top100()
        if not top100:
            logger.warning("[BTC动量] TOP50为空，跳过")
            return

        existing = self._get_open_positions()
        opened = 0

        for symbol in top100:
            if symbol in existing:
                pos = existing[symbol]
                if pos['position_side'] == direction:
                    logger.debug(f"[BTC动量] {symbol} 已有同向仓，保留")
                    continue
                else:
                    logger.info(f"[BTC动量] {symbol} 有反向仓，先平后开")
                    self._close_position(pos)

            entry_price = self._get_symbol_price(symbol)
            if not entry_price:
                logger.warning(f"[BTC动量] {symbol} 获取价格失败，跳过")
                continue

            if self._open_paper_position(symbol, direction, entry_price, trigger_info):
                opened += 1
                # 获取刚插入的ID用于实盘同步
                try:
                    conn = self._get_conn()
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id FROM futures_positions WHERE account_id=%s AND symbol=%s "
                        "AND status='open' ORDER BY open_time DESC LIMIT 1",
                        (self.PAPER_ACCOUNT_ID, symbol)
                    )
                    row = cur.fetchone()
                    cur.close(); conn.close()
                    if row:
                        self._sync_live(symbol, direction, entry_price, row['id'], trigger_info)
                except:
                    pass

        self._last_trigger_time = datetime.now()
        logger.info(f"[BTC动量] 完成，共开仓 {opened}/{len(top100)} 个交易对，4小时内不再触发")

    def _get_max_hold_hours(self) -> int:
        """从 system_settings 读取最大持仓时间（小时）。"""
        from app.services.system_settings_loader import get_max_hold_hours
        return get_max_hold_hours()

    def _is_momentum_enabled(self) -> bool:
        """从 system_settings 读取 btc_momentum_enabled + u_futures_trading_enabled + trend_following_enabled，任一关闭则停止"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_key, setting_value FROM system_settings "
                "WHERE setting_key IN ('btc_momentum_enabled', 'u_futures_trading_enabled', 'trend_following_enabled')"
            )
            rows = {r['setting_key']: str(r['setting_value']).strip() for r in cur.fetchall()}
            cur.close(); conn.close()
            if rows.get('u_futures_trading_enabled') not in ('1', 'true', 'True', None):
                return False
            # 趋势跟随总开关关闭时，BTC动量也不执行
            tf = rows.get('trend_following_enabled', '0')
            if tf not in ('1', 'true', 'True'):
                return False
            if 'btc_momentum_enabled' not in rows:
                return True  # 未配置时默认启用
            return rows['btc_momentum_enabled'] in ('1', 'true', 'True')
        except Exception:
            return True  # 查询失败时默认启用

    def check_and_execute(self):
        """
        主循环每分钟调用一次：
        1. 记录当前BTC价格
        2. 检测是否触发
        3. 触发则执行
        """
        if not self._is_momentum_enabled():
            return

        btc_price = self._get_btc_current_price()
        if btc_price:
            self.record_btc_price(btc_price)

        result = self.check_trigger()
        if result:
            direction, window, pct = result
            self.execute(direction, window, pct)
