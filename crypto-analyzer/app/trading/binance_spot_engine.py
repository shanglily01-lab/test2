"""
币安实体现货交易引擎
对接币安现货API，执行真实现货交易
"""

import time as _time
import hashlib
import hmac
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Optional
from loguru import logger
import requests
import pymysql
import yaml
from urllib.parse import urlencode

from app.utils.binance_rate_guard import rate_guard, parse_ban_msg


class BinanceSpotEngine:
    """币安实测交易引擎"""

    BASE_URL = "https://api.binance.com"

    _symbol_info_cache = {}
    _cache_time = None
    _cache_duration = 3600

    def __init__(self, db_config: dict, api_key: str = None, api_secret: str = None, trade_notifier=None):
        self.db_config = db_config
        self.api_key = api_key
        self.api_secret = api_secret
        self.trade_notifier = trade_notifier

    # ──────────────────────────────────────────────
    # 签名
    # ──────────────────────────────────────────────
    def _sign(self, params: dict) -> dict:
        if not self.api_secret:
            return params
        query_str = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params

    def _request(self, method: str, endpoint: str, params: dict = None, signed: bool = True):
        url = f"{self.BASE_URL}{endpoint}"
        headers = {'X-MBX-APIKEY': self.api_key} if self.api_key else {}
        if signed and params:
            params = self._sign(params)

        try:
            if method == 'GET':
                resp = requests.get(url, params=params, headers=headers, timeout=10)
            elif method == 'POST':
                resp = requests.post(url, data=params, headers=headers, timeout=10)
            else:
                resp = requests.delete(url, data=params, headers=headers, timeout=10)

            # 频率限制检查
            ban_msg = parse_ban_msg(resp.headers)
            if ban_msg:
                logger.warning(f"[BinanceSpot] 频率限制: {ban_msg}")

            if resp.status_code == 200:
                return resp.json()
            else:
                err_body = resp.text[:200]
                logger.error(f"[BinanceSpot] API 错误 {resp.status_code}: {endpoint} {err_body}")
                return {'success': False, 'error': f"HTTP {resp.status_code}: {err_body}"}
        except requests.exceptions.Timeout:
            return {'success': False, 'error': '请求超时'}
        except Exception as e:
            logger.error(f"[BinanceSpot] 请求异常 {endpoint}: {e}")
            return {'success': False, 'error': str(e)}

    # ──────────────────────────────────────────────
    # 精度信息
    # ──────────────────────────────────────────────
    def _load_symbol_info(self):
        if self._symbol_info_cache:
            now = _time.time()
            if now - (self._cache_time or 0) < self._cache_duration:
                return
        result = self._request('GET', '/api/v3/exchangeInfo', signed=False)
        if isinstance(result, dict) and 'symbols' in result:
            for s in result['symbols']:
                if s['status'] != 'TRADING':
                    continue
                key = s['symbol']
                filters = {f['filterType']: f for f in s['filters']}
                lot_size = filters.get('LOT_SIZE', {})
                min_notional_f = filters.get('MIN_NOTIONAL', {}) or filters.get('NOTIONAL', {})
                self._symbol_info_cache[key] = {
                    'min_qty': Decimal(str(lot_size.get('minQty', '0.0001'))),
                    'step_size': Decimal(str(lot_size.get('stepSize', '0.0001'))),
                    'min_notional': Decimal(str(min_notional_f.get('minNotional', '5'))),
                }
            self._cache_time = _time.time()

    def _get_symbol_info(self, binance_symbol: str) -> dict:
        self._load_symbol_info()
        return self._symbol_info_cache.get(binance_symbol, {})

    def _round_qty(self, qty: Decimal, binance_symbol: str) -> Decimal:
        info = self._get_symbol_info(binance_symbol)
        step = info.get('step_size', Decimal('0.0001'))
        precision = abs(step.as_tuple().exponent) if step > 0 else 6
        rounded = qty.quantize(Decimal('0.' + '0' * precision), rounding=ROUND_DOWN)
        if rounded < info.get('min_qty', Decimal('0')):
            return Decimal('0')
        return rounded

    # ──────────────────────────────────────────────
    # 获取价格
    # ──────────────────────────────────────────────
    def get_spot_price(self, symbol: str) -> float:
        """获取现货最新价"""
        bs = symbol.replace('/', '').upper()
        result = self._request('GET', '/api/v3/ticker/price',
                                params={'symbol': bs}, signed=False)
        if isinstance(result, dict) and 'price' in result:
            return float(result['price'])
        return 0.0

    # ──────────────────────────────────────────────
    # 限价买入 (按 USDT 金额, 低于市价 0.2%)
    # ──────────────────────────────────────────────
    def create_limit_buy_order(self, account_id: int, symbol: str,
                                limit_price: float, quote_quantity: float,
                                source: str) -> dict:
        """限价买入 — 挂低于市价的 LIMIT 单"""
        bs = symbol.replace('/', '').upper()
        if not bs.endswith('USDT'):
            return {'success': False, 'error': f'未知交易对: {symbol}'}

        # 计算买入数量
        qty = Decimal(str(round(quote_quantity / limit_price, 8)))
        rounded_qty = self._round_qty(qty, bs)
        if rounded_qty <= 0:
            return {'success': False, 'error': f'数量过小: {qty}'}

        params = {
            'symbol': bs,
            'side': 'BUY',
            'type': 'LIMIT',
            'timeInForce': 'GTC',          # 一直挂到成交或被取消
            'price': str(round(limit_price, 8)),
            'quantity': str(rounded_qty),
            'newClientOrderId': f"spot_dca_{int(_time.time())}",
        }
        result = self._request('POST', '/api/v3/order', params)
        logger.info(f"[BinanceSpot] 限价买入 {symbol} {rounded_qty} @ {limit_price:.6g} => {result}")

        if isinstance(result, dict) and 'orderId' in result:
            self._save_live_order(account_id, symbol, 'BUY', result)
            return {'success': True, 'order': result}
        return {'success': False, 'error': str(result)}

    # ──────────────────────────────────────────────
    # 市价买入 (按 USDT 金额)
    # ──────────────────────────────────────────────
    def create_market_buy_order(self, account_id: int, symbol: str,
                                 quote_quantity: float, source: str) -> dict:
        """市价买入 — 用 quote_quantity USDT 买入"""
        bs = symbol.replace('/', '').upper()
        if bs.endswith('USDT'):
            params = {
                'symbol': bs,
                'side': 'BUY',
                'type': 'MARKET',
                'quoteOrderQty': str(round(quote_quantity, 2)),
                'newClientOrderId': f"spot_dca_{int(_time.time())}",
            }
        else:
            return {'success': False, 'error': f'未知交易对: {symbol}'}

        result = self._request('POST', '/api/v3/order', params)
        logger.info(f"[BinanceSpot] 市价买入 {symbol} {quote_quantity}USDT => {result}")

        if isinstance(result, dict) and 'orderId' in result:
            self._save_live_order(account_id, symbol, 'BUY', result)
            return {'success': True, 'order': result}
        return {'success': False, 'error': str(result)}

    # ──────────────────────────────────────────────
    # 市价卖出 (按数量)
    # ──────────────────────────────────────────────
    def create_market_sell_order(self, account_id: int, symbol: str,
                                  quantity: Decimal, source: str) -> dict:
        bs = symbol.replace('/', '').upper()
        qty = self._round_qty(quantity, bs)
        if qty <= 0:
            return {'success': False, 'error': f'数量 {quantity} 过小'}

        params = {
            'symbol': bs,
            'side': 'SELL',
            'type': 'MARKET',
            'quantity': str(qty),
            'newClientOrderId': f"spot_dca_sell_{int(_time.time())}",
        }
        result = self._request('POST', '/api/v3/order', params)
        logger.info(f"[BinanceSpot] 卖出 {symbol} {qty} => {result}")

        if isinstance(result, dict) and 'orderId' in result:
            self._save_live_order(account_id, symbol, 'SELL', result)
            return {'success': True, 'order': result}
        return {'success': False, 'error': str(result)}

    # ──────────────────────────────────────────────
    # 持久化实盘订单
    # ──────────────────────────────────────────────
    def _save_live_order(self, account_id: int, symbol: str, side: str, order: dict):
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor,
                                   autocommit=True, charset='utf8mb4')
            cur = conn.cursor()
            fills = order.get('fills', [])
            if fills:
                avg_price = sum(float(f['price']) * float(f['qty']) for f in fills) / sum(float(f['qty']) for f in fills)
            else:
                avg_price = float(order.get('cummulativeQuoteQty', 0)) / float(order.get('executedQty', 1))
            executed_qty = float(order.get('executedQty', 0))
            cum_quote = float(order.get('cummulativeQuoteQty', 0))

            cur.execute("""
                INSERT INTO live_futures_positions
                    (account_id, symbol, order_id, client_order_id,
                     position_side, quantity, entry_price, notional_value,
                     status, source, open_time, margin)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,NOW(),%s)
            """, (
                account_id, symbol, str(order.get('orderId', '')),
                order.get('clientOrderId', ''),
                'LONG' if side == 'BUY' else 'SHORT',
                executed_qty, round(avg_price, 8),
                round(cum_quote, 2),
                source, round(cum_quote, 2),
            ))
            cur.close(); conn.close()
        except Exception as e:
            logger.error(f"[BinanceSpot] 保存实盘订单失败: {e}")
