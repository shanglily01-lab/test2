"""
币安实盘合约交易引擎
对接币安U本位合约API，执行真实交易
"""

import uuid
import time
import hmac
import hashlib
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple
from loguru import logger
import requests
import pymysql
import yaml

from app.utils.indicators import get_single_ema
from app.utils.binance_rate_guard import rate_guard, parse_ban_msg

# 导入交易通知器
try:
    from app.services.trade_notifier import get_trade_notifier
except ImportError:
    get_trade_notifier = None

class BinanceFuturesEngine:
    """币安实盘合约交易引擎"""

    # 币安合约API端点
    BASE_URL = "https://fapi.binance.com"

    # 交易对精度缓存
    _symbol_info_cache = {}
    _cache_time = None
    _cache_duration = 3600  # 缓存1小时

    # 挂单缓存（减少API调用）
    _open_orders_cache = {}
    _open_orders_cache_time = None
    _open_orders_cache_duration = 5  # 缓存5秒

    # 无效交易对缓存（避免重复请求已知无效的交易对）
    _invalid_symbols_cache = {}  # symbol -> timestamp
    _invalid_symbols_cache_duration = 300  # 5分钟内不再重试

    def __init__(self, db_config: dict, api_key: str = None, api_secret: str = None, trade_notifier=None):
        """
        初始化币安实盘合约交易引擎

        Args:
            db_config: 数据库配置
            api_key: 币安API Key（可选，不传则从配置文件读取）
            api_secret: 币安API Secret（可选，不传则从配置文件读取）
            trade_notifier: Telegram通知服务（可选）
        """
        self.db_config = db_config
        self.connection = None
        self._is_first_connection = True
        self.trade_notifier = trade_notifier

        # 加载API配置
        if api_key and api_secret:
            self.api_key = api_key
            self.api_secret = api_secret
        else:
            self._load_api_config()

        # 验证API配置
        if not self.api_key or not self.api_secret:
            raise ValueError("币安API Key和Secret未配置")

        # 连接数据库
        self._connect_db()

        # 加载交易对信息
        self._load_exchange_info()

        logger.info("币安实盘合约交易引擎初始化完成")

    def _resolve_trade_notifier(self):
        if self.trade_notifier:
            return self.trade_notifier
        if get_trade_notifier:
            return get_trade_notifier()
        return None

    def _notify_live_close_telegram(
        self,
        symbol: str,
        position_side: str,
        executed_qty,
        entry_price,
        avg_price,
        pnl,
        roi,
        reason: str,
        strategy_name: Optional[str] = None,
        open_time=None,
    ) -> None:
        """实盘平仓成功后发送 Telegram（与开仓 notify_open_position 对称）。"""
        try:
            from app.services.trade_notifier import format_hold_time_from_open

            notifier = self._resolve_trade_notifier()
            if not notifier:
                logger.debug(f"[实盘平仓] TradeNotifier 未初始化,跳过 TG {symbol}")
                return
            notifier.notify_close_position(
                symbol=symbol,
                direction=position_side,
                quantity=float(executed_qty),
                entry_price=float(entry_price),
                exit_price=float(avg_price),
                pnl=float(pnl),
                pnl_pct=float(roi),
                reason=reason,
                hold_time=format_hold_time_from_open(open_time),
                strategy_name=strategy_name,
                is_paper=False,
            )
        except Exception as notify_err:
            logger.warning(f"[实盘平仓] 发送TG通知失败 {symbol}: {notify_err}")

    def _load_api_config(self):
        """从配置文件加载API配置"""
        try:
            from app.utils.config_loader import load_config
            config = load_config()

            binance_config = config.get('exchanges', {}).get('binance', {})
            self.api_key = binance_config.get('api_key', '').strip()
            self.api_secret = binance_config.get('api_secret', '').strip()

            if self.api_key and self.api_secret:
                logger.info("已从配置加载币安API配置")
            else:
                logger.warning("配置中未找到有效的币安API配置")
        except Exception as e:
            logger.error(f"加载API配置失败: {e}")
            self.api_key = None
            self.api_secret = None

    def _connect_db(self):
        """连接数据库"""
        try:
            if self.connection and self.connection.open:
                try:
                    self.connection.close()
                except:
                    pass

            self.connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
                connect_timeout=int(self.db_config.get('connect_timeout', 5)),
                read_timeout=int(self.db_config.get('read_timeout', 10)),
                write_timeout=int(self.db_config.get('write_timeout', 10)),
            )

            if self._is_first_connection:
                logger.info("币安实盘交易引擎数据库连接成功")
                self._is_first_connection = False
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            raise

    def _get_cursor(self):
        """获取数据库游标"""
        try:
            if not self.connection or not self.connection.open:
                self._connect_db()
            else:
                try:
                    self.connection.ping(reconnect=True)
                except:
                    self._connect_db()
            return self.connection.cursor()
        except Exception as e:
            logger.error(f"获取数据库游标失败: {e}")
            self._connect_db()
            return self.connection.cursor()

    @staticmethod
    def calculate_ema(prices: list, period: int) -> float:
        """计算EMA - 委托给公共模块"""
        return get_single_ema(prices, period)

    def get_ema_diff(self, symbol: str, timeframe: str = '15m') -> Optional[float]:
        """
        获取当前 EMA9 - EMA26 的差值

        Args:
            symbol: 交易对
            timeframe: K线周期，默认15分钟

        Returns:
            EMA9 - EMA26 的差值，无法计算时返回 None
        """
        try:
            cursor = self._get_cursor()
            # 获取最近30根K线（确保有足够数据计算EMA26）
            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                ORDER BY timestamp DESC
                LIMIT 30
            """, (symbol, timeframe))

            rows = cursor.fetchall()
            if not rows or len(rows) < 26:
                logger.warning(f"[实盘EMA差值] {symbol} K线数据不足，无法计算EMA")
                return None

            # 转换为从旧到新的价格列表
            prices = [float(row['close_price']) for row in reversed(rows)]

            ema9 = self.calculate_ema(prices, 9)
            ema26 = self.calculate_ema(prices, 26)

            if ema9 == 0 or ema26 == 0:
                return None

            ema_diff = ema9 - ema26
            logger.debug(f"[实盘EMA差值] {symbol}: EMA9={ema9:.6f}, EMA26={ema26:.6f}, 差值={ema_diff:.6f}")
            return ema_diff

        except Exception as e:
            logger.error(f"[实盘EMA差值] 计算失败: {e}")
            return None

    def _generate_signature(self, params: dict) -> str:
        """生成请求签名。值须 URL 编码，与 requests 表单提交一致（中文 symbol 否则 -1022）。"""
        query_string = urlencode(list(params.items()))
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _get_headers(self) -> dict:
        """获取请求头"""
        return {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }

    def _request(self, method: str, endpoint: str, params: dict = None, signed: bool = True) -> dict:
        """
        发送API请求

        Args:
            method: HTTP方法 (GET, POST, DELETE)
            endpoint: API端点
            params: 请求参数
            signed: 是否需要签名

        Returns:
            API响应
        """
        url = f"{self.BASE_URL}{endpoint}"
        params = params or {}

        # IP级熔断: 处于封禁期则直接拒绝, 不发请求 (避免封禁被自己延长)
        if rate_guard.is_banned():
            return {
                'success': False,
                'error': f'IP banned, skip request ({rate_guard.seconds_until_unban():.0f}s remaining)',
                'code': -1003
            }

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 5000
            params['signature'] = self._generate_signature(params)

        try:
            if method == 'GET':
                response = requests.get(url, params=params, headers=self._get_headers(), timeout=10)
            elif method == 'POST':
                response = requests.post(url, data=params, headers=self._get_headers(), timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, params=params, headers=self._get_headers(), timeout=10)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")

            # 处理空响应
            if not response.text:
                logger.error(f"币安API返回空响应: {method} {endpoint}")
                return {'success': False, 'error': '服务器返回空响应'}

            try:
                result = response.json()
            except ValueError as json_err:
                logger.error(f"币安API返回非JSON响应: {response.text[:200]}")
                return {'success': False, 'error': f'无效的JSON响应: {str(json_err)}'}

            if response.status_code != 200:
                error_msg = result.get('msg', '未知错误')
                error_code = result.get('code', -1)
                # 某些错误码降级日志，不触发告警
                warn_only_codes = [-4120]  # 该账号需用Algo Order API下止损/止盈单，SmartExitOptimizer兜底
                ignorable_codes = [-4046]  # No need to change margin type
                if error_code in ignorable_codes:
                    logger.debug(f"币安API提示 [{error_code}]: {error_msg}")
                elif error_code in warn_only_codes:
                    logger.warning(f"币安API提示 [{error_code}]: {error_msg}")
                else:
                    logger.error(f"币安API错误 [{error_code}]: {error_msg}")

                # -1003 IP被ban: 解析banned until并激活熔断, 防止封禁被自己延长
                if error_code == -1003:
                    until_ms = parse_ban_msg(error_msg)
                    if until_ms and rate_guard.set_banned_until(until_ms, source='engine'):
                        # 新封禁事件 → TG告警一次
                        try:
                            if get_trade_notifier:
                                notifier = get_trade_notifier()
                                if notifier:
                                    sec = (until_ms - int(time.time() * 1000)) / 1000
                                    notifier.send_message(
                                        f"[告警] Binance IP 被封禁约 {sec:.0f}s\n"
                                        f"已启用熔断, 所有 API 请求暂停直到解封\n"
                                        f"消息: {error_msg[:200]}"
                                    )
                        except Exception as notify_err:
                            logger.error(f"IP ban TG 告警失败: {notify_err}")

                return {'success': False, 'error': error_msg, 'code': error_code}

            return result

        except requests.exceptions.Timeout:
            logger.error("币安API请求超时")
            return {'success': False, 'error': '请求超时'}
        except requests.exceptions.RequestException as e:
            logger.error(f"币安API请求异常: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"币安API请求失败: {e}")
            return {'success': False, 'error': str(e)}

    def _load_exchange_info(self):
        """加载交易所信息（交易对精度等）"""
        current_time = time.time()

        # 检查缓存是否有效
        if self._cache_time and (current_time - self._cache_time) < self._cache_duration:
            return

        try:
            result = self._request('GET', '/fapi/v1/exchangeInfo', signed=False)

            if 'symbols' not in result:
                logger.warning("无法获取交易所信息")
                return

            for symbol_info in result['symbols']:
                symbol = symbol_info['symbol']

                # 获取精度信息
                price_precision = symbol_info.get('pricePrecision', 2)
                quantity_precision = symbol_info.get('quantityPrecision', 3)

                # 获取过滤器信息
                filters = {f['filterType']: f for f in symbol_info.get('filters', [])}

                min_qty = Decimal('0.001')
                min_notional = Decimal('5')
                step_size = Decimal('0.001')
                tick_size = Decimal('0.01')

                if 'LOT_SIZE' in filters:
                    lot_size = filters['LOT_SIZE']
                    min_qty = Decimal(str(lot_size.get('minQty', '0.001')))
                    step_size = Decimal(str(lot_size.get('stepSize', '0.001')))

                if 'MIN_NOTIONAL' in filters:
                    min_notional = Decimal(str(filters['MIN_NOTIONAL'].get('notional', '5')))

                if 'PRICE_FILTER' in filters:
                    tick_size = Decimal(str(filters['PRICE_FILTER'].get('tickSize', '0.01')))

                self._symbol_info_cache[symbol] = {
                    'price_precision': price_precision,
                    'quantity_precision': quantity_precision,
                    'min_qty': min_qty,
                    'min_notional': min_notional,
                    'step_size': step_size,
                    'tick_size': tick_size
                }

            self._cache_time = current_time
            logger.info(f"已加载 {len(self._symbol_info_cache)} 个交易对信息")

        except Exception as e:
            logger.error(f"加载交易所信息失败: {e}")

    def _convert_symbol(self, symbol: str) -> str:
        """
        转换交易对格式
        'BTC/USDT' -> 'BTCUSDT'
        """
        return symbol.replace('/', '').upper()

    def _reverse_symbol(self, symbol: str) -> str:
        """
        反转交易对格式
        'BTCUSDT' -> 'BTC/USDT'
        """
        if 'USDT' in symbol:
            base = symbol.replace('USDT', '')
            return f"{base}/USDT"
        return symbol

    def _round_quantity(self, quantity: Decimal, symbol: str) -> Decimal:
        """根据交易对精度对数量进行四舍五入"""
        binance_symbol = self._convert_symbol(symbol)
        info = self._symbol_info_cache.get(binance_symbol, {})
        step_size = info.get('step_size', Decimal('0.001'))

        # 使用step_size进行精度控制
        return (quantity / step_size).quantize(Decimal('1'), rounding=ROUND_DOWN) * step_size

    def _round_price(self, price: Decimal, symbol: str) -> Decimal:
        """根据交易对精度对价格进行四舍五入"""
        binance_symbol = self._convert_symbol(symbol)
        info = self._symbol_info_cache.get(binance_symbol, {})
        tick_size = info.get('tick_size', Decimal('0.01'))

        return (price / tick_size).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick_size

    # ==================== 账户相关 ====================

    def get_account_balance(self) -> Dict:
        """
        获取合约账户余额

        Returns:
            账户余额信息
        """
        result = self._request('GET', '/fapi/v2/balance')

        if isinstance(result, dict) and result.get('success') == False:
            return result

        # 查找USDT余额
        for asset in result:
            if asset.get('asset') == 'USDT':
                return {
                    'success': True,
                    'asset': 'USDT',
                    'balance': Decimal(str(asset.get('balance', '0'))),  # 总余额
                    'available': Decimal(str(asset.get('availableBalance', '0'))),  # 可用余额
                    'unrealized_pnl': Decimal(str(asset.get('crossUnPnl', '0')))  # 未实现盈亏
                }

        return {'success': False, 'error': '未找到USDT余额'}

    def get_account_info(self) -> Dict:
        """
        获取合约账户详细信息

        Returns:
            账户详细信息
        """
        result = self._request('GET', '/fapi/v2/account')

        if isinstance(result, dict) and result.get('success') == False:
            return result

        return {
            'success': True,
            'total_margin_balance': Decimal(str(result.get('totalMarginBalance', '0'))),
            'available_balance': Decimal(str(result.get('availableBalance', '0'))),
            'total_unrealized_profit': Decimal(str(result.get('totalUnrealizedProfit', '0'))),
            'total_wallet_balance': Decimal(str(result.get('totalWalletBalance', '0'))),
            'positions': result.get('positions', [])
        }

    def get_current_price(self, symbol: str) -> Decimal:
        """
        获取当前市场价格

        Args:
            symbol: 交易对 (如 'BTC/USDT')

        Returns:
            当前价格
        """
        binance_symbol = self._convert_symbol(symbol)

        # 检查是否在无效交易对缓存中
        current_time = time.time()
        if symbol in self._invalid_symbols_cache:
            cache_time = self._invalid_symbols_cache[symbol]
            if (current_time - cache_time) < self._invalid_symbols_cache_duration:
                # 5分钟内不再重试，直接返回0（静默失败）
                return Decimal('0')
            else:
                # 缓存过期，移除并重试
                del self._invalid_symbols_cache[symbol]

        try:
            result = self._request('GET', '/fapi/v1/ticker/price',
                                  {'symbol': binance_symbol}, signed=False)

            if 'price' in result:
                return Decimal(str(result['price']))

            # 检查是否是无效交易对错误
            if 'code' in result and result['code'] == -1121:
                # 无效交易对，加入缓存
                self._invalid_symbols_cache[symbol] = current_time
                logger.warning(f"交易对 {symbol} 无效，已加入缓存，5分钟内不再重试")
                return Decimal('0')

            logger.warning(f"无法获取 {symbol} 价格")
            return Decimal('0')

        except Exception as e:
            logger.error(f"获取 {symbol} 实时价格失败: {e}")
            return Decimal('0')

    # ==================== 杠杆设置 ====================

    def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """
        设置杠杆倍数

        Args:
            symbol: 交易对
            leverage: 杠杆倍数 (1-125)

        Returns:
            设置结果
        """
        binance_symbol = self._convert_symbol(symbol)

        params = {
            'symbol': binance_symbol,
            'leverage': leverage
        }

        result = self._request('POST', '/fapi/v1/leverage', params)

        if isinstance(result, dict) and result.get('success') == False:
            return result

        return {
            'success': True,
            'symbol': symbol,
            'leverage': result.get('leverage', leverage)
        }

    def set_margin_type(self, symbol: str, margin_type: str = 'CROSSED') -> Dict:
        """
        设置保证金模式

        Args:
            symbol: 交易对
            margin_type: 'ISOLATED' 或 'CROSSED'

        Returns:
            设置结果
        """
        binance_symbol = self._convert_symbol(symbol)

        params = {
            'symbol': binance_symbol,
            'marginType': margin_type
        }

        result = self._request('POST', '/fapi/v1/marginType', params)

        # 如果已经是该模式，会返回错误，但实际上是成功的
        if isinstance(result, dict):
            if result.get('code') == -4046:  # No need to change margin type
                return {'success': True, 'message': '保证金模式已设置'}
            if result.get('success') == False:
                return result

        return {'success': True, 'margin_type': margin_type}

    # ==================== 开仓 ====================

    def open_position(
        self,
        account_id: int,
        symbol: str,
        position_side: str,  # 'LONG' or 'SHORT'
        quantity: Decimal,
        leverage: int = 1,
        limit_price: Optional[Decimal] = None,
        stop_loss_pct: Optional[Decimal] = None,
        take_profit_pct: Optional[Decimal] = None,
        stop_loss_price: Optional[Decimal] = None,
        take_profit_price: Optional[Decimal] = None,
        source: str = 'manual',
        signal_id: Optional[int] = None,
        strategy_id: Optional[int] = None,
        paper_position_id: Optional[int] = None
    ) -> Dict:
        """
        开仓（实盘）

        Args:
            account_id: 账户ID（用于本地记录）
            symbol: 交易对
            position_side: 'LONG' 或 'SHORT'
            quantity: 开仓数量
            leverage: 杠杆倍数
            limit_price: 限价（None为市价）
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            stop_loss_price: 止损价格
            take_profit_price: 止盈价格
            source: 来源
            signal_id: 信号ID
            strategy_id: 策略ID

        Returns:
            开仓结果
        """
        from app.services.trading_gates import get_beijing_open_window_status

        time_allowed, time_reason = get_beijing_open_window_status()
        if not time_allowed:
            logger.info(f"[实盘开仓时间闸门] {symbol} {position_side} source={source}: {time_reason}")
            return {"success": False, "error": time_reason}

        binance_symbol = self._convert_symbol(symbol)
        position_side = position_side.upper()

        try:
            # 1. 设置杠杆
            leverage_result = self.set_leverage(symbol, leverage)
            if not leverage_result.get('success', True):
                logger.warning(f"设置杠杆失败: {leverage_result.get('error')}")

            # 2. 设置为逐仓模式
            margin_result = self.set_margin_type(symbol, 'ISOLATED')
            if not margin_result.get('success', True):
                logger.warning(f"设置保证金模式失败: {margin_result.get('error')}")

            # 3. 获取当前价格
            current_price = self.get_current_price(symbol)
            if current_price == 0:
                return {'success': False, 'error': f'无法获取 {symbol} 价格'}

            # 4. 精度处理
            quantity = self._round_quantity(quantity, symbol)

            # 检查最小数量
            info = self._symbol_info_cache.get(binance_symbol, {})
            min_qty = info.get('min_qty', Decimal('0.001'))
            min_notional = info.get('min_notional', Decimal('5'))

            if quantity < min_qty:
                return {'success': False, 'error': f'数量 {quantity} 小于最小值 {min_qty}'}

            notional = quantity * current_price
            if notional < min_notional:
                return {'success': False, 'error': f'名义价值 {notional} 小于最小值 {min_notional}'}

            # 5. 构建订单参数
            side = 'BUY' if position_side == 'LONG' else 'SELL'
            order_type = 'LIMIT' if limit_price else 'MARKET'

            params = {
                'symbol': binance_symbol,
                'side': side,
                'positionSide': position_side,  # 双向持仓模式必须指定
                'type': order_type,
                'quantity': str(quantity)
            }

            # 5.5. 添加 clientOrderId 标记策略来源（在交易所订单列表可见）
            source_prefix = source.lower().replace(' ', '_')
            known_sources = {
                'btc_momentum', 'predictor',
            }
            if source_prefix in known_sources:
                # newClientOrderId 最长 36 字符，截短以防溢出
                short_src = source_prefix[:20]
                params['newClientOrderId'] = f"{short_src}_{int(time.time())}"

            # 如果是限价单
            if limit_price:
                limit_price = self._round_price(limit_price, symbol)
                params['price'] = str(limit_price)
                params['timeInForce'] = 'GTC'  # Good Till Cancel

            # 6. 发送开仓订单
            logger.info(f"[实盘] 发送开仓订单: {symbol} {position_side} {quantity} @ {limit_price or '市价'}")

            result = self._request('POST', '/fapi/v1/order', params)

            if isinstance(result, dict) and result.get('success') == False:
                logger.error(f"[实盘] 开仓失败: {result.get('error')}")
                return result

            # 7. 解析订单结果
            order_id = str(result.get('orderId', ''))
            status = result.get('status', '')
            executed_qty = Decimal(str(result.get('executedQty', '0')))
            avg_price = Decimal(str(result.get('avgPrice', '0')))

            if avg_price == 0 and executed_qty > 0:
                # 市价单的avgPrice可能为0，使用当前价格
                avg_price = current_price

            entry_price = avg_price if avg_price > 0 else (limit_price or current_price)

            logger.info(f"[实盘] 开仓订单已提交: order_id={order_id}, status={status}, "
                       f"executed={executed_qty}, avg_price={avg_price}")

            # 7.5. 市价单如果未立即成交，等待并查询状态
            if order_type == 'MARKET' and executed_qty == 0:
                import time
                for i in range(3):  # 最多等待3次，每次0.5秒
                    time.sleep(0.5)
                    order_status = self._request('GET', '/fapi/v1/order', {
                        'symbol': binance_symbol,
                        'orderId': order_id
                    })
                    if isinstance(order_status, dict) and order_status.get('status') == 'FILLED':
                        executed_qty = Decimal(str(order_status.get('executedQty', '0')))
                        avg_price = Decimal(str(order_status.get('avgPrice', '0')))
                        if avg_price > 0:
                            entry_price = avg_price
                        logger.info(f"[实盘] 市价单已成交: executed={executed_qty}, avg_price={avg_price}")
                        break
                    logger.debug(f"[实盘] 等待市价单成交... ({i+1}/3)")

            # 8. 计算止盈止损价格
            if stop_loss_price is None and stop_loss_pct:
                # 确保所有值都是 Decimal，避免 Decimal * float 的类型错误
                sl_pct = Decimal(str(stop_loss_pct))
                if position_side == 'LONG':
                    stop_loss_price = entry_price * (1 - sl_pct / 100)
                else:
                    stop_loss_price = entry_price * (1 + sl_pct / 100)

            if take_profit_price is None and take_profit_pct:
                # 确保所有值都是 Decimal，避免 Decimal * float 的类型错误
                tp_pct = Decimal(str(take_profit_pct))
                if position_side == 'LONG':
                    take_profit_price = entry_price * (1 + tp_pct / 100)
                else:
                    take_profit_price = entry_price * (1 - tp_pct / 100)

            # 9. 设置止损止盈订单（仅市价单立即设置，限价单由监控服务处理）
            sl_order_id = None
            tp_order_id = None

            # 限价单不在此处设置止盈止损，由 live_order_monitor 统一处理
            is_limit_order = limit_price is not None

            # 市价单：如果executed_qty仍为0，使用提交的quantity
            order_qty = executed_qty if executed_qty > 0 else quantity

            if stop_loss_price and order_qty > 0 and not is_limit_order:
                # 验证止损价格
                # 做多：止损价必须低于入场价
                # 做空：止损价必须高于入场价
                sl_valid = False
                if position_side == 'LONG' and stop_loss_price < entry_price:
                    sl_valid = True
                elif position_side == 'SHORT' and stop_loss_price > entry_price:
                    sl_valid = True

                if sl_valid:
                    sl_result = self._place_stop_loss(symbol, position_side, order_qty, stop_loss_price)
                    if sl_result.get('success'):
                        sl_order_id = sl_result.get('order_id')
                        logger.info(f"[实盘] 止损单已设置: {stop_loss_price}")
                    else:
                        logger.warning(f"[实盘] 止损单设置失败: {sl_result.get('error')}")
                else:
                    logger.warning(f"[实盘] 止损价 {stop_loss_price} 无效 ({position_side} 入场价 {entry_price})，跳过止损设置")

            if take_profit_price and order_qty > 0 and not is_limit_order:
                # 验证止盈价格
                # 做多：止盈价必须高于入场价
                # 做空：止盈价必须低于入场价
                tp_valid = False
                if position_side == 'LONG' and take_profit_price > entry_price:
                    tp_valid = True
                elif position_side == 'SHORT' and take_profit_price < entry_price:
                    tp_valid = True

                if tp_valid:
                    tp_result = self._place_take_profit(symbol, position_side, order_qty, take_profit_price)
                    if tp_result.get('success'):
                        tp_order_id = tp_result.get('order_id')
                        logger.info(f"[实盘] 止盈单已设置: {take_profit_price}")
                    else:
                        logger.warning(f"[实盘] 止盈单设置失败: {tp_result.get('error')}")
                else:
                    logger.warning(f"[实盘] 止盈价 {take_profit_price} 无效 ({position_side} 入场价 {entry_price})，跳过止盈设置")

            # 9.5. 计算开仓时的 EMA 差值（用于趋势反转检测）
            entry_ema_diff = self.get_ema_diff(symbol, '15m')
            if entry_ema_diff is not None:
                logger.info(f"[实盘EMA差值] {symbol} {position_side} 开仓EMA差值: {entry_ema_diff:.6f}")

            # 10. 保存到本地数据库（建立 paper_position_id 链接，平仓同步时需要）
            #     定时任务每15M从币安全量同步会覆盖 fields，
            #     但 paper_position_id 链接必须在此建立，否则平仓同步查不到关联。
            position_id = self._save_position_to_db(
                account_id=account_id,
                symbol=symbol,
                position_side=position_side,
                quantity=executed_qty if executed_qty > 0 else quantity,
                entry_price=entry_price,
                leverage=leverage,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                source=source,
                signal_id=signal_id,
                strategy_id=strategy_id,
                binance_order_id=order_id,
                status='OPEN' if status == 'FILLED' else 'PENDING',
                entry_ema_diff=entry_ema_diff,
                paper_position_id=paper_position_id
            )

            # 更新止盈止损订单ID到数据库（防止 LiveOrderMonitor 重复设置）
            if position_id and (sl_order_id or tp_order_id):
                try:
                    cursor = self._get_cursor()
                    cursor.execute("""
                        UPDATE live_futures_positions
                        SET sl_order_id = %s, tp_order_id = %s
                        WHERE id = %s
                    """, (sl_order_id, tp_order_id, position_id))
                    logger.debug(f"[实盘] 止盈止损订单ID已保存: SL={sl_order_id}, TP={tp_order_id}")
                except Exception as e:
                    logger.warning(f"[实盘] 保存止盈止损订单ID失败: {e}")

            # 发送Telegram通知
            try:
                notifier = get_trade_notifier() if get_trade_notifier else None
                if notifier:
                    actual_qty = float(executed_qty if executed_qty > 0 else quantity)
                    margin = (float(entry_price) * actual_qty) / leverage
                    order_type_str = 'LIMIT' if limit_price else 'MARKET'
                    logger.info(f"[实盘] 发送Telegram开仓通知: {symbol} {position_side} {actual_qty} @ {entry_price} ({order_type_str})")
                    notifier.notify_open_position(
                        symbol=symbol,
                        direction=position_side,
                        quantity=actual_qty,
                        entry_price=float(entry_price),
                        leverage=leverage,
                        stop_loss_price=float(stop_loss_price) if stop_loss_price else None,
                        take_profit_price=float(take_profit_price) if take_profit_price else None,
                        margin=margin,
                        strategy_name=source,
                        order_type=order_type_str
                    )
                else:
                    logger.warning(f"[实盘] Telegram通知器未初始化，跳过开仓通知")
            except Exception as notify_err:
                logger.warning(f"发送开仓通知失败: {notify_err}")
                import traceback
                traceback.print_exc()

            # 清除挂单缓存，确保下次查询获取最新数据
            self.invalidate_orders_cache()

            return {
                'success': True,
                'position_id': position_id,
                'order_id': order_id,
                'binance_order_id': order_id,
                'symbol': symbol,
                'position_side': position_side,
                'quantity': float(executed_qty if executed_qty > 0 else quantity),
                'entry_price': float(entry_price),
                'leverage': leverage,
                'stop_loss_price': float(stop_loss_price) if stop_loss_price else None,
                'take_profit_price': float(take_profit_price) if take_profit_price else None,
                'sl_order_id': sl_order_id,
                'tp_order_id': tp_order_id,
                'status': status,
                'message': f'开仓成功: {symbol} {position_side} {executed_qty} @ {entry_price}'
            }

        except Exception as e:
            logger.error(f"[实盘] 开仓异常: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    def _submit_algo_conditional(
        self,
        binance_symbol: str,
        side: str,
        position_side: str,
        order_type: str,
        trigger_price: Decimal,
        quantity: Decimal,
    ) -> dict:
        """Binance 2025-12+ 条件单：POST /fapi/v1/algoOrder (algoType=CONDITIONAL)."""
        return self._request('POST', '/fapi/v1/algoOrder', {
            'algoType': 'CONDITIONAL',
            'symbol': binance_symbol,
            'side': side,
            'positionSide': position_side,
            'type': order_type,
            'triggerPrice': str(trigger_price),
            'quantity': str(quantity),
            'workingType': 'MARK_PRICE',
        })

    def _place_stop_loss(self, symbol: str, position_side: str, quantity: Decimal,
                         stop_price: Decimal) -> Dict:
        """设置止损单 (STOP_MARKET)，含 Algo Order API 回退。"""
        binance_symbol = self._convert_symbol(symbol)
        side = 'SELL' if position_side == 'LONG' else 'BUY'
        stop_price = self._round_price(stop_price, symbol)
        quantity = self._round_quantity(quantity, symbol)

        params = {
            'symbol': binance_symbol,
            'side': side,
            'positionSide': position_side,
            'type': 'STOP_MARKET',
            'stopPrice': str(stop_price),
            'quantity': str(quantity),
            'workingType': 'MARK_PRICE',
            'timeInForce': 'GTE_GTC',
        }

        result = self._request('POST', '/fapi/v1/order', params)

        # -4120: 条件单已迁移 Algo Order API
        if isinstance(result, dict) and result.get('code') == -4120:
            logger.warning(f"[止损] 账号需用Algo Order API, 切换重试 {symbol} {position_side}")
            result = self._submit_algo_conditional(
                binance_symbol, side, position_side, 'STOP_MARKET', stop_price, quantity,
            )

        if isinstance(result, dict) and result.get('success') == False:
            return result

        return {
            'success': True,
            'order_id': str(result.get('algoId') or result.get('orderId', '')),
            'stop_price': float(stop_price)
        }

    def update_stop_loss(self, symbol: str, position_side: str, quantity: Decimal,
                         new_stop_loss_price: Decimal) -> Dict:
        """
        更新止损价格（用于移动止损同步）

        先取消现有止损单，再设置新的止损单

        Args:
            symbol: 交易对
            position_side: 'LONG' 或 'SHORT'
            quantity: 数量
            new_stop_loss_price: 新的止损价格

        Returns:
            操作结果
        """
        binance_symbol = self._convert_symbol(symbol)

        try:
            # 1. 查询并取消现有止损单
            # 查询 Algo Orders
            algo_orders = self._request('GET', '/fapi/v1/algoOrders', {
                'symbol': binance_symbol
            })

            if isinstance(algo_orders, list):
                for order in algo_orders:
                    # 找到该持仓方向的止损单
                    if (order.get('positionSide') == position_side and
                        order.get('type') == 'STOP_MARKET' and
                        order.get('status') in ('NEW', 'PENDING')):

                        algo_id = order.get('algoId')
                        if algo_id:
                            # 取消旧止损单
                            cancel_result = self._request('DELETE', '/fapi/v1/algoOrder', {
                                'symbol': binance_symbol,
                                'algoId': algo_id
                            })
                            logger.info(f"[移动止损] 取消旧止损单: algoId={algo_id}")

            # 2. 设置新的止损单
            result = self._place_stop_loss(symbol, position_side, quantity, new_stop_loss_price)

            if result.get('success'):
                logger.info(f"[移动止损] 新止损单已设置: {symbol} {position_side} @ {new_stop_loss_price}")

            return result

        except Exception as e:
            logger.error(f"[移动止损] 更新止损失败: {e}")
            return {'success': False, 'error': str(e)}

    def update_take_profit(self, symbol: str, position_side: str, quantity: Decimal,
                           new_take_profit_price: Decimal) -> Dict:
        """
        更新止盈价格（用于移动止盈同步）

        先取消现有止盈单，再设置新的止盈单

        Args:
            symbol: 交易对
            position_side: 'LONG' 或 'SHORT'
            quantity: 数量
            new_take_profit_price: 新的止盈价格

        Returns:
            操作结果
        """
        binance_symbol = self._convert_symbol(symbol)

        try:
            # 1. 查询并取消现有止盈单
            algo_orders = self._request('GET', '/fapi/v1/algoOrders', {
                'symbol': binance_symbol
            })

            if isinstance(algo_orders, list):
                for order in algo_orders:
                    # 找到该持仓方向的止盈单
                    if (order.get('positionSide') == position_side and
                        order.get('type') == 'TAKE_PROFIT_MARKET' and
                        order.get('status') in ('NEW', 'PENDING')):

                        algo_id = order.get('algoId')
                        if algo_id:
                            # 取消旧止盈单
                            cancel_result = self._request('DELETE', '/fapi/v1/algoOrder', {
                                'symbol': binance_symbol,
                                'algoId': algo_id
                            })
                            logger.info(f"[移动止盈] 取消旧止盈单: algoId={algo_id}")

            # 2. 设置新的止盈单
            result = self._place_take_profit(symbol, position_side, quantity, new_take_profit_price)

            if result.get('success'):
                logger.info(f"[移动止盈] 新止盈单已设置: {symbol} {position_side} @ {new_take_profit_price}")

            return result

        except Exception as e:
            logger.error(f"[移动止盈] 更新止盈失败: {e}")
            return {'success': False, 'error': str(e)}

    def _place_take_profit(self, symbol: str, position_side: str, quantity: Decimal,
                           take_profit_price: Decimal) -> Dict:
        """设置止盈单 (TAKE_PROFIT_MARKET)，含 Algo Order API 回退。"""
        binance_symbol = self._convert_symbol(symbol)
        side = 'SELL' if position_side == 'LONG' else 'BUY'
        take_profit_price = self._round_price(take_profit_price, symbol)
        quantity = self._round_quantity(quantity, symbol)

        params = {
            'symbol': binance_symbol,
            'side': side,
            'positionSide': position_side,
            'type': 'TAKE_PROFIT_MARKET',
            'stopPrice': str(take_profit_price),
            'quantity': str(quantity),
            'workingType': 'MARK_PRICE',
            'timeInForce': 'GTE_GTC',
        }

        result = self._request('POST', '/fapi/v1/order', params)

        # -4120: 条件单已迁移 Algo Order API
        if isinstance(result, dict) and result.get('code') == -4120:
            logger.warning(f"[止盈] 账号需用Algo Order API, 切换重试 {symbol} {position_side}")
            result = self._submit_algo_conditional(
                binance_symbol, side, position_side, 'TAKE_PROFIT_MARKET',
                take_profit_price, quantity,
            )

        if isinstance(result, dict) and result.get('success') == False:
            return result

        return {
            'success': True,
            'order_id': str(result.get('algoId') or result.get('orderId', '')),
            'take_profit_price': float(take_profit_price)
        }

    # ==================== 平仓 ====================

    def close_position(
        self,
        position_id: int,
        close_quantity: Optional[Decimal] = None,
        reason: str = 'manual',
        close_price: Optional[Decimal] = None
    ) -> Dict:
        """
        平仓（实盘）

        Args:
            position_id: 本地持仓ID
            close_quantity: 平仓数量（None为全部）
            reason: 平仓原因
            close_price: 平仓价格（None为市价）

        Returns:
            平仓结果
        """
        try:
            # 1. 从数据库获取持仓信息
            cursor = self._get_cursor()
            cursor.execute(
                """SELECT * FROM live_futures_positions
                WHERE id = %s AND status = 'OPEN'""",
                (position_id,)
            )
            position = cursor.fetchone()

            if not position:
                return {'success': False, 'error': f'未找到持仓 ID={position_id}'}

            symbol = position['symbol']
            position_side = position['position_side']
            quantity = Decimal(str(position['quantity']))
            entry_price = Decimal(str(position['entry_price']))

            # 2. 确定平仓数量
            if close_quantity is None:
                # 全部平仓时，从 Binance 获取实际持仓数量（使用原始字符串避免精度丢失）
                binance_positions = self.get_open_positions()
                quantity_raw = None
                for pos in binance_positions:
                    if pos['symbol'] == symbol and pos['position_side'] == position_side:
                        quantity_raw = pos.get('quantity_raw', '')
                        break

                if quantity_raw and quantity_raw != '0':
                    # 原始值可能是负数（SHORT），取绝对值
                    close_quantity_str = quantity_raw.lstrip('-')
                    close_quantity = Decimal(close_quantity_str)
                    logger.info(f"[实盘] 使用原始数量字符串平仓: {close_quantity_str} (数据库: {quantity})")
                else:
                    # 回退到数据库数量
                    close_quantity = quantity
            else:
                close_quantity = min(Decimal(str(close_quantity)), quantity)
                # 部分平仓时才做取整处理
                close_quantity = self._round_quantity(close_quantity, symbol)

            # 3. 发送平仓订单
            binance_symbol = self._convert_symbol(symbol)
            side = 'SELL' if position_side == 'LONG' else 'BUY'

            params = {
                'symbol': binance_symbol,
                'side': side,
                'positionSide': position_side,  # 双向持仓模式必须指定
                'type': 'MARKET',
                'quantity': str(close_quantity)
            }

            logger.info(f"[实盘] 发送平仓订单: {symbol} {side} {close_quantity} (reason: {reason})")

            result = self._request('POST', '/fapi/v1/order', params)

            if isinstance(result, dict) and result.get('success') == False:
                logger.error(f"[实盘] 平仓失败: {result.get('error')}")
                return result

            # 4. 解析结果
            order_id = str(result.get('orderId', ''))
            status = result.get('status', '')
            executed_qty = Decimal(str(result.get('executedQty', '0')))
            avg_price = Decimal(str(result.get('avgPrice', '0')))

            if avg_price == 0:
                avg_price = self.get_current_price(symbol)

            # 5. 计算盈亏
            if position_side == 'LONG':
                pnl = (avg_price - entry_price) * executed_qty
            else:
                pnl = (entry_price - avg_price) * executed_qty

            roi = (pnl / (entry_price * executed_qty)) * 100 if entry_price > 0 and executed_qty > 0 else Decimal('0')

            logger.info(f"[实盘] 平仓成功: {symbol} {executed_qty} @ {avg_price}, PnL={pnl:.2f} USDT, ROI={roi:.2f}%")

            # 6. 不再更新本地 DB — 实盘交易记录由定时任务每 15M 从币安同步
            #     这里只负责在交易所完成平仓操作

            # 7. 取消相关止盈止损单
            self._cancel_position_orders(position)

            # 8. 发送 Telegram 实盘平仓通知
            self._notify_live_close_telegram(
                symbol, position_side, executed_qty, entry_price, avg_price,
                pnl, roi, reason,
                strategy_name=position.get('source'),
                open_time=position.get('open_time'),
            )

            # 清除挂单缓存，确保下次查询获取最新数据
            self.invalidate_orders_cache()

            return {
                'success': True,
                'position_id': position_id,
                'order_id': order_id,
                'symbol': symbol,
                'position_side': position_side,
                'close_quantity': float(executed_qty),
                'close_price': float(avg_price),
                'realized_pnl': float(pnl),
                'roi': float(roi),
                'reason': reason,
                'status': new_status,
                'message': f'平仓成功: PnL={pnl:.2f} USDT'
            }

        except Exception as e:
            logger.error(f"[实盘] 平仓异常: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    def close_position_direct(
        self,
        symbol: str,
        position_side: str,
        quantity: Decimal,
        entry_price: Decimal,
        reason: str = 'paper_sync',
        strategy_name: Optional[str] = None,
        open_time=None,
    ) -> Dict:
        """
        直接用已知数量平仓，不调用 get_open_positions()。
        适用于已有 live_futures_positions 记录、quantity/entry_price 已知的场景，
        避免因 API 查询失败而误判"持仓不存在"。
        """
        try:
            self._cancel_position_orders({'symbol': symbol})

            binance_symbol = self._convert_symbol(symbol)
            side = 'SELL' if position_side == 'LONG' else 'BUY'
            close_qty = self._round_quantity(quantity, symbol)

            params = {
                'symbol': binance_symbol,
                'side': side,
                'positionSide': position_side,
                'type': 'MARKET',
                'quantity': str(close_qty)
            }
            logger.info(f"[实盘直接平仓] {symbol} {position_side} qty={close_qty} reason={reason}")
            result = self._request('POST', '/fapi/v1/order', params)

            if isinstance(result, dict) and result.get('success') == False:
                err = result.get('error', '')
                # 数量超过实际持仓（交易所已部分平仓）→ fallback 查实际数量再平
                logger.warning(f"[实盘直接平仓] 下单失败: {err}，尝试 fallback 查实际持仓数量")
                return self.close_position_by_symbol(symbol, position_side, reason=reason)

            executed_qty = Decimal(str(result.get('executedQty', '0'))) or close_qty
            avg_price = Decimal(str(result.get('avgPrice', '0')))
            if avg_price == 0:
                cp = self.get_current_price(symbol)
                avg_price = Decimal(str(cp)) if cp else entry_price

            if position_side == 'LONG':
                pnl = (avg_price - entry_price) * executed_qty
            else:
                pnl = (entry_price - avg_price) * executed_qty

            roi = (pnl / (entry_price * executed_qty)) * 100 if entry_price > 0 and executed_qty > 0 else Decimal('0')

            self._notify_live_close_telegram(
                symbol, position_side, executed_qty, entry_price, avg_price,
                pnl, roi, reason,
                strategy_name=strategy_name,
                open_time=open_time,
            )

            return {
                'success': True,
                'order_id': str(result.get('orderId', '')),
                'close_price': float(avg_price),
                'executed_qty': float(executed_qty),
                'realized_pnl': float(pnl)
            }
        except Exception as e:
            logger.error(f"[实盘直接平仓] {symbol} {position_side} 失败: {e}，尝试 fallback")
            try:
                return self.close_position_by_symbol(symbol, position_side, reason=reason)
            except Exception as e2:
                logger.error(f"[实盘直接平仓] fallback 也失败: {e2}")
                return {'success': False, 'error': str(e2)}

    def close_position_by_symbol(
        self,
        symbol: str,
        position_side: str,
        close_quantity: Optional[Decimal] = None,
        reason: str = 'manual'
    ) -> Dict:
        """
        通过交易对和方向平仓（不依赖本地position_id）

        Args:
            symbol: 交易对（如 "BTC/USDT"）
            position_side: 持仓方向 LONG/SHORT
            close_quantity: 平仓数量（None为全部）
            reason: 平仓原因

        Returns:
            平仓结果
        """
        try:
            # 1. 从币安获取当前持仓
            positions = self.get_open_positions()
            target_position = None

            for pos in positions:
                if pos['symbol'] == symbol and pos['position_side'] == position_side:
                    target_position = pos
                    break

            if not target_position:
                return {
                    'success': False,
                    'error': f'未找到 {symbol} {position_side} 持仓'
                }

            # 2. 确定平仓数量
            quantity = Decimal(str(target_position['quantity']))
            entry_price = Decimal(str(target_position['entry_price']))

            # 获取原始数量字符串（用于全部平仓，避免精度丢失）
            quantity_raw = target_position.get('quantity_raw', '')

            if close_quantity is None:
                # 全部平仓时，使用 Binance 返回的原始数量字符串（取绝对值）
                if quantity_raw and quantity_raw != '0':
                    # 原始值可能是负数（SHORT），取绝对值
                    close_quantity_str = quantity_raw.lstrip('-')
                    close_quantity = Decimal(close_quantity_str)
                    logger.info(f"[实盘] 使用原始数量字符串平仓: {close_quantity_str}")
                else:
                    close_quantity = quantity
            else:
                close_quantity = min(Decimal(str(close_quantity)), quantity)
                # 部分平仓时才做取整处理
                close_quantity = self._round_quantity(close_quantity, symbol)

            # 2.5. 取消相关的止损止盈订单（Algo订单）
            self._cancel_position_orders({'symbol': symbol})

            # 3. 发送平仓订单
            binance_symbol = self._convert_symbol(symbol)
            side = 'SELL' if position_side == 'LONG' else 'BUY'

            params = {
                'symbol': binance_symbol,
                'side': side,
                'positionSide': position_side,
                'type': 'MARKET',
                'quantity': str(close_quantity)
            }

            logger.info(f"[实盘] 按交易对平仓: {symbol} {position_side} {close_quantity} (reason: {reason})")

            result = self._request('POST', '/fapi/v1/order', params)

            if isinstance(result, dict) and result.get('success') == False:
                logger.error(f"[实盘] 平仓失败: {result.get('error')}")
                return result

            # 4. 解析结果
            order_id = str(result.get('orderId', ''))
            executed_qty = Decimal(str(result.get('executedQty', '0')))
            avg_price = Decimal(str(result.get('avgPrice', '0')))

            if avg_price == 0:
                current_price = self.get_current_price(symbol)
                avg_price = Decimal(str(current_price)) if not isinstance(current_price, Decimal) else current_price

            # 4.5. 如果 executed_qty 为 0，使用请求的数量（可能是止盈/止损单已自动触发）
            if executed_qty == 0:
                executed_qty = close_quantity
                logger.info(f"[实盘] executedQty=0，使用请求数量计算盈亏: {executed_qty}")

            # 5. 计算盈亏
            if position_side == 'LONG':
                pnl = (avg_price - entry_price) * executed_qty
            else:
                pnl = (entry_price - avg_price) * executed_qty

            roi = (pnl / (entry_price * executed_qty)) * Decimal('100') if entry_price > 0 and executed_qty > 0 else Decimal('0')

            logger.info(f"[实盘] 平仓成功: {symbol} {executed_qty} @ {avg_price}, PnL={pnl:.2f} USDT")

            self._notify_live_close_telegram(
                symbol, position_side, executed_qty, entry_price, avg_price,
                pnl, roi, reason,
            )

            return {
                'success': True,
                'order_id': order_id,
                'symbol': symbol,
                'position_side': position_side,
                'close_quantity': float(executed_qty),
                'close_price': float(avg_price),
                'realized_pnl': float(pnl),
                'roi': float(roi),
                'reason': reason,
                'message': f'平仓成功: PnL={pnl:.2f} USDT'
            }

        except Exception as e:
            logger.error(f"[实盘] 按交易对平仓异常: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    def _cancel_position_orders(self, position: dict):
        """
        取消持仓相关的止盈止损单

        注意：从 2025-12-09 起，条件订单迁移到 Algo Service，
        需要同时检查普通订单和 Algo 订单
        """
        try:
            binance_symbol = self._convert_symbol(position['symbol'])

            # 1. 取消 Algo 条件单（STOP_MARKET, TAKE_PROFIT_MARKET 等）
            algo_result = self._request('GET', '/fapi/v1/openAlgoOrders', {'symbol': binance_symbol})

            if isinstance(algo_result, dict) and algo_result.get('orders'):
                for order in algo_result['orders']:
                    algo_id = order.get('algoId')
                    if algo_id:
                        cancel_result = self._request('DELETE', '/fapi/v1/algoOrder', {
                            'symbol': binance_symbol,
                            'algoId': algo_id
                        })
                        logger.info(f"[实盘] 取消Algo条件单: {algo_id}")
            elif isinstance(algo_result, list):
                # 备选格式：直接返回列表
                for order in algo_result:
                    algo_id = order.get('algoId')
                    if algo_id:
                        cancel_result = self._request('DELETE', '/fapi/v1/algoOrder', {
                            'symbol': binance_symbol,
                            'algoId': algo_id
                        })
                        logger.info(f"[实盘] 取消Algo条件单: {algo_id}")

            # 2. 取消普通挂单（限价单等）
            result = self._request('GET', '/fapi/v1/openOrders', {'symbol': binance_symbol})

            if isinstance(result, list):
                for order in result:
                    order_type = order.get('type', '')
                    # 普通订单类型：LIMIT, MARKET 等
                    if order_type in ['LIMIT', 'STOP', 'TAKE_PROFIT']:
                        order_id = order.get('orderId')
                        cancel_result = self._request('DELETE', '/fapi/v1/order', {
                            'symbol': binance_symbol,
                            'orderId': order_id
                        })
                        logger.info(f"[实盘] 取消普通订单: {order_id}")
        except Exception as e:
            logger.warning(f"取消条件单失败: {e}")

    # ==================== 持仓查询 ====================

    def get_open_positions(self, account_id: int = None) -> List[Dict]:
        """
        获取所有持仓

        Args:
            account_id: 账户ID（可选，用于过滤本地记录）

        Returns:
            持仓列表
        """
        try:
            # 从币安获取实时持仓
            result = self._request('GET', '/fapi/v2/positionRisk')

            if isinstance(result, dict) and result.get('success') == False:
                return []

            positions = []
            for pos in result:
                position_amt = Decimal(str(pos.get('positionAmt', '0')))

                # 跳过空仓
                if position_amt == 0:
                    continue

                symbol = self._reverse_symbol(pos.get('symbol', ''))
                # 双向持仓模式使用 positionSide 字段，单向模式根据 positionAmt 判断
                position_side = pos.get('positionSide', 'BOTH')
                if position_side == 'BOTH':
                    position_side = 'LONG' if position_amt > 0 else 'SHORT'
                entry_price = Decimal(str(pos.get('entryPrice', '0')))
                mark_price = Decimal(str(pos.get('markPrice', '0')))
                unrealized_pnl = Decimal(str(pos.get('unRealizedProfit', '0')))
                leverage = int(pos.get('leverage', 1))
                liquidation_price = Decimal(str(pos.get('liquidationPrice', '0')))

                positions.append({
                    'symbol': symbol,
                    'position_side': position_side,
                    'quantity': abs(position_amt),
                    'quantity_raw': pos.get('positionAmt', '0'),  # 保留原始字符串，平仓时使用
                    'entry_price': entry_price,
                    'mark_price': mark_price,
                    'unrealized_pnl': unrealized_pnl,
                    'leverage': leverage,
                    'liquidation_price': liquidation_price,
                    'notional_value': abs(position_amt) * mark_price,
                    'margin': abs(position_amt) * mark_price / leverage
                })

            return positions

        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []

    def get_binance_positions(self) -> List[Dict]:
        """直接获取币安持仓（不经过本地数据库）"""
        return self.get_open_positions()

    # ==================== 订单查询 ====================

    def get_open_orders(self, symbol: str = None, force_refresh: bool = False) -> List[Dict]:
        """获取挂单（带缓存）

        Args:
            symbol: 交易对（可选）
            force_refresh: 强制刷新缓存
        """
        current_time = time.time()
        cache_key = symbol or '__all__'

        # 检查缓存是否有效
        if not force_refresh and self._open_orders_cache_time:
            if (current_time - self._open_orders_cache_time) < self._open_orders_cache_duration:
                if cache_key in self._open_orders_cache:
                    return self._open_orders_cache[cache_key]

        # 缓存失效，重新请求
        params = {}
        if symbol:
            params['symbol'] = self._convert_symbol(symbol)

        result = self._request('GET', '/fapi/v1/openOrders', params)

        if isinstance(result, dict) and result.get('success') == False:
            return []

        # 更新缓存
        self._open_orders_cache[cache_key] = result
        self._open_orders_cache_time = current_time

        return result

    def invalidate_orders_cache(self):
        """清除挂单缓存（下单/撤单后调用）"""
        self._open_orders_cache = {}
        self._open_orders_cache_time = None

    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """取消订单"""
        binance_symbol = self._convert_symbol(symbol)

        params = {
            'symbol': binance_symbol,
            'orderId': order_id
        }

        logger.info(f"[实盘] 发送取消订单请求: {symbol} orderId={order_id}")
        result = self._request('DELETE', '/fapi/v1/order', params)
        logger.info(f"[实盘] 取消订单响应: {result}")

        if isinstance(result, dict) and result.get('success') == False:
            # -2011: Unknown order sent - 订单已经不存在（已成交或已取消），视为成功
            error_msg = result.get('error', '')
            if '-2011' in error_msg or 'Unknown order' in error_msg:
                logger.info(f"[实盘] 订单 {order_id} 已不存在（可能已成交或取消）")
                return {'success': True, 'order_id': order_id, 'message': '订单已不存在'}
            logger.error(f"[实盘] 取消订单失败: {error_msg}")
            return result

        # 检查返回的订单状态
        order_status = result.get('status', '')
        logger.info(f"[实盘] 订单 {order_id} 取消后状态: {order_status}")

        # 清除挂单缓存，确保下次查询获取最新数据
        self.invalidate_orders_cache()

        return {'success': True, 'order_id': order_id, 'message': '订单已取消', 'status': order_status}

    def get_order_status(self, symbol: str, order_id: str) -> Dict:
        """查询订单状态"""
        binance_symbol = self._convert_symbol(symbol)

        params = {
            'symbol': binance_symbol,
            'orderId': order_id
        }

        result = self._request('GET', '/fapi/v1/order', params)

        if isinstance(result, dict) and result.get('success') == False:
            return {'status': 'UNKNOWN', 'error': result.get('error')}

        return {
            'status': result.get('status', 'UNKNOWN'),
            'executed_qty': result.get('executedQty', '0'),
            'avg_price': result.get('avgPrice', '0'),
            'order_id': order_id
        }

    def cancel_all_orders(self, symbol: str) -> Dict:
        """取消某交易对的所有订单"""
        binance_symbol = self._convert_symbol(symbol)

        result = self._request('DELETE', '/fapi/v1/allOpenOrders', {'symbol': binance_symbol})

        if isinstance(result, dict) and result.get('success') == False:
            return result

        return {'success': True, 'message': f'已取消 {symbol} 的所有订单'}

    def cancel_pending_order(self, symbol: str) -> Tuple[bool, str]:
        """取消某交易对的挂单（限价单）

        Args:
            symbol: 交易对，如 'BTC/USDT'

        Returns:
            (成功与否, 消息)
        """
        try:
            # 获取该交易对的所有挂单
            open_orders = self.get_open_orders(symbol, force_refresh=True)

            if not open_orders:
                return True, f"{symbol} 没有挂单需要取消"

            # 过滤出限价单（LIMIT类型）
            limit_orders = [o for o in open_orders if o.get('type') == 'LIMIT']

            if not limit_orders:
                return True, f"{symbol} 没有限价挂单需要取消"

            cancelled_count = 0
            for order in limit_orders:
                order_id = order.get('orderId')
                if order_id:
                    result = self.cancel_order(symbol, str(order_id))
                    if result.get('success'):
                        cancelled_count += 1
                        logger.info(f"[实盘] 已取消 {symbol} 限价单 {order_id}")

            return True, f"已取消 {symbol} 的 {cancelled_count} 个限价单"

        except Exception as e:
            logger.error(f"[实盘] 取消 {symbol} 挂单失败: {e}")
            return False, str(e)

    # ==================== 数据库操作 ====================

    def _save_position_to_db(
        self,
        account_id: int,
        symbol: str,
        position_side: str,
        quantity: Decimal,
        entry_price: Decimal,
        leverage: int,
        stop_loss_price: Optional[Decimal],
        take_profit_price: Optional[Decimal],
        source: str,
        signal_id: Optional[int],
        strategy_id: Optional[int],
        binance_order_id: str,
        status: str,
        entry_ema_diff: Optional[float] = None,
        paper_position_id: Optional[int] = None
    ) -> int:
        """保存持仓到本地数据库"""
        try:
            cursor = self._get_cursor()

            # 计算名义价值和保证金
            notional_value = quantity * entry_price
            margin = notional_value / leverage

            insert_sql = """INSERT INTO live_futures_positions
                (account_id, symbol, position_side, leverage, quantity,
                 notional_value, margin, entry_price, stop_loss_price,
                 take_profit_price, entry_ema_diff, open_time, status, source, signal_id,
                 strategy_id, binance_order_id, paper_position_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            insert_params = (account_id, symbol, position_side, leverage, float(quantity),
                 float(notional_value), float(margin), float(entry_price),
                 float(stop_loss_price) if stop_loss_price else None,
                 float(take_profit_price) if take_profit_price else None,
                 entry_ema_diff,
                 datetime.now(), status, source, signal_id, strategy_id, binance_order_id, paper_position_id)

            cursor.execute(insert_sql, insert_params)

            position_id = cursor.lastrowid
            logger.info(f"[实盘] 持仓已保存到数据库: ID={position_id}")

            return position_id

        except Exception as e:
            logger.error(f"保存持仓到数据库失败: {e}")
            return 0

    # ==================== 从币安同步数据 ====================

    def sync_positions_from_binance(self, account_id: int = 1) -> Dict:
        """
        从币安同步持仓状态到本地数据库

        处理以下情况：
        1. 在币安APP手动平仓的订单 -> 更新状态为CLOSED
        2. 在币安APP撤销的限价单 -> 更新状态为CANCELED
        3. 在币安APP手动开的仓 -> 新增记录到数据库

        Args:
            account_id: 账户ID

        Returns:
            同步结果
        """
        try:
            cursor = self._get_cursor()
            synced_count = 0
            closed_count = 0
            canceled_count = 0
            new_count = 0

            # 1. 获取币安当前实际持仓
            binance_positions = self._get_binance_positions()
            binance_position_map = {}  # {symbol_side: position}

            for pos in binance_positions:
                key = f"{pos['symbol']}_{pos['position_side']}"
                binance_position_map[key] = pos

            # 2. 获取本地数据库中状态为 OPEN 的持仓
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price, binance_order_id, open_time
                FROM live_futures_positions
                WHERE status = 'OPEN' AND account_id = %s
            """, (account_id,))
            local_open_positions = cursor.fetchall()

            # 3. 检查本地OPEN持仓是否在币安已平仓
            for local_pos in local_open_positions:
                key = f"{local_pos['symbol']}_{local_pos['position_side']}"

                if key not in binance_position_map:
                    # 币安已没有这个持仓，说明已被平仓
                    # 获取最近的成交记录来确定平仓价格
                    binance_symbol = self._convert_symbol(local_pos['symbol'])
                    trades = self._get_recent_trades(binance_symbol, limit=50)
                    open_ms = self._datetime_to_ms(local_pos.get('open_time'))
                    if open_ms is not None:
                        trades = [t for t in trades if int(t.get('time') or 0) >= open_ms]

                    close_price = Decimal('0')
                    realized_pnl = Decimal('0')

                    # 尝试找到平仓的成交记录
                    for trade in trades:
                        # 双向持仓模式：必须同时匹配 side + positionSide
                        # 平 LONG: side=SELL, positionSide=LONG
                        # 平 SHORT: side=BUY, positionSide=SHORT
                        # 开 SHORT: side=SELL, positionSide=SHORT (不能匹配!)
                        trade_side = trade.get('side', '')
                        trade_pos_side = trade.get('positionSide', 'BOTH')
                        if local_pos['position_side'] == 'LONG':
                            if trade_side == 'SELL' and trade_pos_side == 'LONG':
                                close_price = Decimal(str(trade.get('price', '0')))
                                realized_pnl += Decimal(str(trade.get('realizedPnl', '0')))
                        elif local_pos['position_side'] == 'SHORT':
                            if trade_side == 'BUY' and trade_pos_side == 'SHORT':
                                close_price = Decimal(str(trade.get('price', '0')))
                                realized_pnl += Decimal(str(trade.get('realizedPnl', '0')))

                    # 如果没找到成交记录，使用当前价格
                    if close_price == 0:
                        close_price = self.get_current_price(local_pos['symbol'])

                    # 如果 realized_pnl 仍为 0（取到的成交 realizedPnl 都为 0），用量价差估算
                    if realized_pnl == 0 and close_price > 0:
                        entry_price = Decimal(str(local_pos['entry_price']))
                        quantity = Decimal(str(local_pos['quantity']))
                        if local_pos['position_side'] == 'LONG':
                            realized_pnl = (close_price - entry_price) * quantity
                        else:
                            realized_pnl = (entry_price - close_price) * quantity

                    # 更新本地数据库
                    cursor.execute("""
                        UPDATE live_futures_positions
                        SET status = 'CLOSED',
                            close_price = %s,
                            realized_pnl = %s,
                            close_time = NOW(),
                            close_reason = 'binance_sync'
                        WHERE id = %s
                    """, (float(close_price), float(realized_pnl), local_pos['id']))

                    logger.info(f"[同步] 检测到已平仓: {local_pos['symbol']} {local_pos['position_side']} "
                               f"@ {close_price}, PnL={realized_pnl:.2f}")
                    closed_count += 1

            # 4. 获取本地数据库中状态为 PENDING 的限价单
            cursor.execute("""
                SELECT id, symbol, position_side, binance_order_id
                FROM live_futures_positions
                WHERE status = 'PENDING' AND account_id = %s AND binance_order_id IS NOT NULL
            """, (account_id,))
            local_pending_positions = cursor.fetchall()

            # 5. 检查PENDING限价单在币安的状态
            for local_pos in local_pending_positions:
                binance_symbol = self._convert_symbol(local_pos['symbol'])
                order_id = local_pos['binance_order_id']

                # 查询币安订单状态
                order_status = self._request('GET', '/fapi/v1/order', {
                    'symbol': binance_symbol,
                    'orderId': order_id
                })

                if isinstance(order_status, dict) and order_status.get('success') == False:
                    continue

                status = order_status.get('status', '')

                if status == 'FILLED':
                    # 已成交，更新为OPEN
                    executed_qty = Decimal(str(order_status.get('executedQty', '0')))
                    avg_price = Decimal(str(order_status.get('avgPrice', '0')))

                    cursor.execute("""
                        UPDATE live_futures_positions
                        SET status = 'OPEN',
                            quantity = %s,
                            entry_price = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (float(executed_qty), float(avg_price), local_pos['id']))

                    logger.info(f"[同步] 限价单已成交: {local_pos['symbol']} @ {avg_price}")
                    synced_count += 1

                elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                    # 已取消/过期/拒绝
                    cursor.execute("""
                        UPDATE live_futures_positions
                        SET status = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (status, local_pos['id']))

                    logger.info(f"[同步] 限价单已取消: {local_pos['symbol']} #{order_id} -> {status}")
                    canceled_count += 1

            # 6. 检查币安是否有本地没有的持仓（在APP手动开的仓）
            cursor.execute("""
                SELECT symbol, position_side FROM live_futures_positions
                WHERE status = 'OPEN' AND account_id = %s
            """, (account_id,))
            local_open_keys = {f"{r['symbol']}_{r['position_side']}" for r in cursor.fetchall()}

            for key, binance_pos in binance_position_map.items():
                if key not in local_open_keys:
                    # 本地没有这个持仓，是在APP手动开的
                    position_id = self._save_position_to_db(
                        account_id=account_id,
                        symbol=binance_pos['symbol'],
                        position_side=binance_pos['position_side'],
                        quantity=binance_pos['quantity'],
                        entry_price=binance_pos['entry_price'],
                        leverage=binance_pos['leverage'],
                        stop_loss_price=None,
                        take_profit_price=None,
                        status='OPEN',
                        source='binance_sync',
                        signal_id=None,
                        strategy_id=None,
                        binance_order_id=None
                    )

                    logger.info(f"[同步] 检测到新持仓: {binance_pos['symbol']} {binance_pos['position_side']} "
                               f"数量={binance_pos['quantity']} @ {binance_pos['entry_price']}")
                    new_count += 1

            total_synced = closed_count + canceled_count + synced_count + new_count

            return {
                'success': True,
                'message': f'同步完成',
                'closed': closed_count,
                'canceled': canceled_count,
                'filled': synced_count,
                'new': new_count,
                'total': total_synced
            }

        except Exception as e:
            logger.error(f"从币安同步持仓失败: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    def _get_binance_positions(self) -> List[Dict]:
        """获取币安当前实际持仓（内部方法）"""
        result = self._request('GET', '/fapi/v2/positionRisk')

        if isinstance(result, dict) and result.get('success') == False:
            return []

        positions = []
        for pos in result:
            position_amt = Decimal(str(pos.get('positionAmt', '0')))

            # 跳过空仓
            if position_amt == 0:
                continue

            symbol = self._reverse_symbol(pos.get('symbol', ''))
            position_side = 'LONG' if position_amt > 0 else 'SHORT'
            entry_price = Decimal(str(pos.get('entryPrice', '0')))
            leverage = int(pos.get('leverage', 1))

            positions.append({
                'symbol': symbol,
                'position_side': position_side,
                'quantity': abs(position_amt),
                'entry_price': entry_price,
                'leverage': leverage,
                'mark_price': Decimal(str(pos.get('markPrice', '0'))) if pos.get('markPrice') else Decimal('0'),
                'liquidation_price': Decimal(str(pos.get('liquidationPrice', '0'))) if pos.get('liquidationPrice') else Decimal('0'),
                'unrealized_pnl': Decimal(str(pos.get('unRealizedProfit', '0'))) if pos.get('unRealizedProfit') else Decimal('0'),
            })

        return positions

    def _get_recent_trades(self, symbol: str, limit: int = 50) -> List[Dict]:
        """获取最近成交记录（内部方法）"""
        try:
            result = self._request('GET', '/fapi/v1/userTrades', {
                'symbol': symbol,
                'limit': limit
            })

            if isinstance(result, dict) and result.get('success') == False:
                return []

            return result
        except Exception as e:
            logger.error(f"获取成交记录失败: {e}")
            return []

    # ==================== 获取成交记录 ====================

    def _datetime_to_ms(self, value, default_ms: Optional[int] = None) -> Optional[int]:
        if value is None:
            return default_ms
        if isinstance(value, datetime):
            return int(value.timestamp() * 1000)
        try:
            return int(datetime.fromisoformat(str(value)).timestamp() * 1000)
        except Exception:
            return default_ms

    def _summarize_closing_trades(
        self,
        trades: List[Dict],
        position_side: str,
        open_time=None,
        close_time=None,
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """Summarize Binance userTrades that close this position."""
        start_ms = self._datetime_to_ms(open_time)
        end_ms = self._datetime_to_ms(close_time)
        close_side = 'SELL' if position_side == 'LONG' else 'BUY'

        total_qty = Decimal('0')
        total_quote = Decimal('0')
        realized_pnl = Decimal('0')

        for trade in trades:
            trade_ms = int(trade.get('time') or 0)
            if start_ms is not None and trade_ms and trade_ms < start_ms:
                continue
            if end_ms is not None and trade_ms and trade_ms > end_ms + 5 * 60 * 1000:
                continue
            if trade.get('side', '') != close_side:
                continue
            if trade.get('positionSide', 'BOTH') not in (position_side, 'BOTH'):
                continue

            qty = Decimal(str(trade.get('qty', trade.get('quantity', '0')) or '0'))
            price = Decimal(str(trade.get('price', '0') or '0'))
            total_qty += qty
            total_quote += price * qty
            realized_pnl += Decimal(str(trade.get('realizedPnl', '0') or '0'))

        close_price = (total_quote / total_qty) if total_qty > 0 else Decimal('0')
        return close_price, realized_pnl, total_qty

    def get_trade_history(self, symbol: str, start_time: int = None, end_time: int = None, limit: int = 200) -> List[Dict]:
        """
        获取币安合约成交历史记录（公开方法）

        Args:
            symbol: 交易对 (格式: BTC/USDT)
            start_time: 开始时间戳（毫秒）
            end_time: 结束时间戳（毫秒）
            limit: 返回条数 (默认200，最大1000)

        Returns:
            成交记录列表，每项包含 price, qty, realizedPnl, side, time 等
        """
        try:
            binance_symbol = self._convert_symbol(symbol)
            params = {'symbol': binance_symbol, 'limit': limit}
            if start_time:
                params['startTime'] = start_time
            if end_time:
                params['endTime'] = end_time

            result = self._request('GET', '/fapi/v1/userTrades', params)
            if isinstance(result, dict) and result.get('success') == False:
                return []
            return result
        except Exception as e:
            logger.error(f"获取成交记录失败 {symbol}: {e}")
            return []

    def get_position_history(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        """
        获取币安合约历史持仓记录
        调用 /fapi/v1/positionRisk/history 端点（部分币安API版本支持）。
        如果不支持，回退到 get_trade_history + get_open_positions 推断。

        Args:
            symbol: 可选，指定交易对
            limit: 返回条数

        Returns:
            历史持仓记录列表
        """
        try:
            params = {'limit': limit}
            if symbol:
                params['symbol'] = self._convert_symbol(symbol)
            result = self._request('GET', '/fapi/v1/positionRisk/history', params)
            if isinstance(result, dict) and result.get('success') == False:
                return []
            positions = []
            for pos in result if isinstance(result, list) else []:
                position_amt = Decimal(str(pos.get('positionAmt', '0')))
                if position_amt == 0:
                    continue
                positions.append({
                    'symbol': self._reverse_symbol(pos.get('symbol', '')),
                    'position_side': 'LONG' if position_amt > 0 else 'SHORT',
                    'quantity': abs(position_amt),
                    'entry_price': Decimal(str(pos.get('entryPrice', '0'))),
                    'close_price': Decimal(str(pos.get('closePrice', '0'))) if pos.get('closePrice') else None,
                    'realized_pnl': Decimal(str(pos.get('realizedPnl', '0'))) if pos.get('realizedPnl') else None,
                    'leverage': int(pos.get('leverage', 1)),
                    'status': pos.get('positionStatus', ''),
                    'update_time': pos.get('updateTime'),
                })
            return positions
        except Exception as e:
            logger.debug(f"获取历史持仓记录失败 (可能不支持此API): {e}")
            return []

    def correct_live_trade_records(self, account_id: int = 1) -> Dict:
        """
        从币安拉取正确的交易数据，纠正 live_futures_positions 表中的错误记录。

        修正以下字段：
        - 未平仓 (OPEN) 持仓：entry_price, mark_price, unrealized_pnl, liquidation_price
        - 已平仓 (CLOSED) 持仓：close_price, realized_pnl, close_time

        Args:
            account_id: 账户ID

        Returns:
            修正统计结果
        """
        try:
            cursor = self._get_cursor()
            corrected_count = 0
            closed_corrected = 0
            open_corrected = 0

            # ---- 1. 从币安获取当前所有持仓 ----
            binance_positions = self._get_binance_positions()
            binance_pos_map = {}
            for pos in binance_positions:
                key = f"{pos['symbol']}_{pos['position_side']}"
                binance_pos_map[key] = pos

            # ---- 2. 修正 OPEN 持仓的 entry_price / mark_price ----
            cursor.execute("""
                SELECT id, symbol, position_side, entry_price, mark_price
                FROM live_futures_positions
                WHERE status = 'OPEN' AND account_id = %s
            """, (account_id,))
            open_positions = cursor.fetchall()

            for local_pos in open_positions:
                key = f"{local_pos['symbol']}_{local_pos['position_side']}"
                if key in binance_pos_map:
                    real_pos = binance_pos_map[key]
                    updates = []
                    params = []

                    # 修正 entry_price
                    if float(real_pos['entry_price']) != float(local_pos['entry_price']):
                        updates.append("entry_price = %s")
                        params.append(float(real_pos['entry_price']))

                    # 修正 mark_price
                    if real_pos['mark_price'] > 0:
                        updates.append("mark_price = %s")
                        params.append(float(real_pos['mark_price']))

                    # 修正 liquidation_price
                    if real_pos['liquidation_price'] > 0:
                        updates.append("liquidation_price = %s")
                        params.append(float(real_pos['liquidation_price']))

                    # 修正 unrealized_pnl
                    updates.append("unrealized_pnl = %s")
                    params.append(float(real_pos['unrealized_pnl']))

                    if len(updates) > 0:
                        updates.append("updated_at = NOW()")
                        cursor.execute(
                            f"UPDATE live_futures_positions SET {', '.join(updates)} WHERE id = %s",
                            params + [local_pos['id']]
                        )
                        open_corrected += 1

            # ---- 3. 修正 CLOSED 持仓的 close_price / realized_pnl ----
            # 查找近7天内已平仓的所有记录，从币安成交记录中获取准确的平仓数据。
            # 注意：不限制 realized_pnl=0，因为 sync_positions_from_binance 可能已写入
            # 错误的估算值（缺少 positionSide 过滤），需要覆盖修正。

            cursor.execute("""
                SELECT id, symbol, position_side, close_price, realized_pnl, open_time, close_time
                FROM live_futures_positions
                WHERE status = 'CLOSED' AND account_id = %s
                  AND close_time IS NOT NULL
                  AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                ORDER BY close_time DESC
            """, (account_id,))
            closed_positions = cursor.fetchall()
            if closed_positions:
                # 计算需要查询的最早时间
                earliest_close = min(p['close_time'] for p in closed_positions if p['close_time'])
                if isinstance(earliest_close, datetime):
                    earliest_ms = int(earliest_close.timestamp() * 1000)
                else:
                    earliest_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 7 * 86400 * 1000

                # 按 symbol 分组获取成交记录（取最多1000条）
                symbol_trades_cache = {}
                for local_pos in closed_positions:
                    sym = local_pos['symbol']
                    if sym not in symbol_trades_cache:
                        symbol_trades_cache[sym] = self.get_trade_history(sym, start_time=earliest_ms, limit=1000)

                for local_pos in closed_positions:
                    trades = symbol_trades_cache.get(local_pos['symbol'], [])
                    if not trades:
                        continue
                    start_ms = self._datetime_to_ms(local_pos.get('open_time'))
                    end_ms = self._datetime_to_ms(local_pos.get('close_time'))
                    if start_ms is not None:
                        trades = [t for t in trades if int(t.get('time') or 0) >= start_ms]
                    if end_ms is not None:
                        trades = [t for t in trades if int(t.get('time') or 0) <= end_ms + 5 * 60 * 1000]

                    close_price = Decimal('0')
                    realized_pnl = Decimal('0')

                    for trade in trades:
                        # 双向持仓模式：必须同时匹配 side + positionSide
                        trade_side = trade.get('side', '')
                        trade_pos_side = trade.get('positionSide', 'BOTH')
                        if local_pos['position_side'] == 'LONG':
                            if trade_side == 'SELL' and trade_pos_side == 'LONG':
                                close_price = Decimal(str(trade.get('price', '0')))
                                realized_pnl += Decimal(str(trade.get('realizedPnl', '0')))
                        elif local_pos['position_side'] == 'SHORT':
                            if trade_side == 'BUY' and trade_pos_side == 'SHORT':
                                close_price = Decimal(str(trade.get('price', '0')))
                                realized_pnl += Decimal(str(trade.get('realizedPnl', '0')))

                    if close_price > 0:
                        cursor.execute("""
                            UPDATE live_futures_positions
                            SET close_price = %s, realized_pnl = %s, updated_at = NOW()
                            WHERE id = %s
                        """, (float(close_price), float(realized_pnl), local_pos['id']))
                        corrected_count += 1
                        closed_corrected += 1

            cursor.connection.commit()
            logger.info(f"[修正持仓] 账号[{account_id}] 修正完成: OPEN={open_corrected}, CLOSED={closed_corrected}, 总计={corrected_count}")

            return {
                'success': True,
                'account_id': account_id,
                'open_corrected': open_corrected,
                'closed_corrected': closed_corrected,
                'total_corrected': corrected_count
            }

        except Exception as e:
            logger.error(f"[修正持仓] 账号[{account_id}] 修正失败: {e}")
            return {'success': False, 'error': str(e)}


    # ==================== 测试连接 ====================

    def test_connection(self) -> Dict:
        """
        测试API连接

        Returns:
            测试结果
        """
        try:
            # 测试服务器时间
            server_time = self._request('GET', '/fapi/v1/time', signed=False)

            if 'serverTime' not in server_time:
                return {'success': False, 'error': '无法获取服务器时间'}

            # 测试账户权限
            balance = self.get_account_balance()

            if not balance.get('success'):
                return {
                    'success': False,
                    'error': f"API权限验证失败: {balance.get('error')}"
                }

            return {
                'success': True,
                'server_time': server_time['serverTime'],
                'balance': float(balance.get('balance', 0)),
                'available': float(balance.get('available', 0)),
                'message': '币安API连接正常'
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}


# 便捷函数
def create_live_engine(db_config: dict) -> BinanceFuturesEngine:
    """创建实盘交易引擎实例"""
    return BinanceFuturesEngine(db_config)
