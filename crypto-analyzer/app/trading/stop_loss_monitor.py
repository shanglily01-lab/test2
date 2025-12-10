#!/usr/bin/env python3
"""
止盈止损监控系统
Stop-Loss/Take-Profit Monitoring System

自动监控所有持仓，触发止盈、止损、强平
Automatically monitors all positions and triggers stop-loss, take-profit, and liquidation
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
from decimal import Decimal
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger
import time

from app.trading.futures_trading_engine import FuturesTradingEngine

try:
    from app.trading.binance_futures_engine import BinanceFuturesEngine
except ImportError:
    BinanceFuturesEngine = None


class StopLossMonitor:
    """止盈止损监控器"""

    def __init__(self, db_config: dict, binance_config: dict = None):
        """
        初始化监控器

        Args:
            db_config: 数据库配置
            binance_config: 币安实盘配置（可选）
        """
        self.db_config = db_config
        self.connection = pymysql.connect(**db_config)
        self._connection_created_at = time.time()  # 连接创建时间（Unix时间戳）
        self._connection_max_age = 300  # 连接最大存活时间（秒），5分钟
        self.engine = FuturesTradingEngine(db_config)

        # 初始化实盘引擎（如果提供了配置）
        self.live_engine = None
        if binance_config and BinanceFuturesEngine:
            try:
                # BinanceFuturesEngine(db_config, api_key, api_secret)
                # 不传api_key和api_secret，让它自己从配置文件加载
                self.live_engine = BinanceFuturesEngine(db_config)
                logger.info("✅ 止损监控：实盘引擎已初始化")
            except Exception as e:
                logger.warning(f"⚠️ 止损监控：实盘引擎初始化失败: {e}")
                import traceback
                traceback.print_exc()

        logger.info("StopLossMonitor initialized")

    def _should_refresh_connection(self):
        """检查是否需要刷新连接（基于连接年龄）"""
        if self._connection_created_at is None:
            return True
        
        current_time = time.time()
        connection_age = current_time - self._connection_created_at
        
        # 如果连接年龄超过最大存活时间，需要刷新
        return connection_age > self._connection_max_age

    def _ensure_connection(self):
        """确保数据库连接有效（静默检查，不打印日志）"""
        # 检查连接年龄，如果超过最大存活时间则主动刷新
        if self._should_refresh_connection():
            logger.debug("连接已过期，主动刷新数据库连接（止损监控）")
            if self.connection and self.connection.open:
                try:
                    self.connection.close()
                except:
                    pass
            try:
                self.connection = pymysql.connect(**self.db_config)
                self._connection_created_at = time.time()
            except Exception as e:
                logger.error(f"❌ 创建数据库连接失败: {e}")
                raise
            return
        
        if self.connection is None or not self.connection.open:
            try:
                self.connection = pymysql.connect(**self.db_config)
                self._connection_created_at = time.time()
                # 只在首次创建连接时记录（DEBUG级别）
            except Exception as e:
                logger.error(f"❌ 创建数据库连接失败: {e}")
                raise
        else:
            # 静默检查连接是否还活着（不打印日志）
            try:
                self.connection.ping(reconnect=False)
            except Exception as e:
                # 只有在连接真正断开需要重连时才记录
                logger.warning(f"数据库连接已断开，尝试重连: {e}")
                try:
                    if self.connection and self.connection.open:
                        self.connection.close()
                    self.connection = pymysql.connect(**self.db_config)
                    self._connection_created_at = time.time()
                    logger.debug("✅ 数据库连接已重新建立（止损监控）")
                except Exception as e2:
                    logger.error(f"❌ 重连数据库失败: {e2}")
                    raise

    def get_open_positions(self, account_id: Optional[int] = None) -> List[Dict]:
        """
        获取所有持仓中的合约（每次查询都创建新连接，确保获取最新数据）

        Args:
            account_id: 账户ID（可选，如果为None则获取所有账户的持仓）

        Returns:
            持仓列表
        """
        # 每次查询都创建新连接，确保获取最新数据
        connection = pymysql.connect(
            **self.db_config,
            autocommit=True
        )
        
        try:
            cursor = connection.cursor(pymysql.cursors.DictCursor)

            sql = """
            SELECT
                id,
                account_id,
                symbol,
                position_side,
                quantity,
                entry_price,
                leverage,
                margin,
                stop_loss_price,
                take_profit_price,
                liquidation_price,
                unrealized_pnl,
                open_time
            FROM futures_positions
            WHERE status = 'open'
            """
            
            params = []
            if account_id is not None:
                sql += " AND account_id = %s"
                params.append(account_id)
            
            sql += " ORDER BY open_time ASC"

            cursor.execute(sql, tuple(params) if params else None)
            positions = cursor.fetchall()
            cursor.close()
            
            # 转换 Decimal 类型为 float，确保所有数值字段都能正确序列化
            for pos in positions:
                for key, value in pos.items():
                    if isinstance(value, Decimal):
                        pos[key] = float(value)
            
            return positions
        finally:
            connection.close()

    def get_current_price(self, symbol: str, use_realtime: bool = False) -> Optional[Decimal]:
        """
        获取当前市场价格

        Args:
            symbol: 交易对（如 BTC/USDT）
            use_realtime: 是否使用实时API价格（监控止盈止损时使用）

        Returns:
            当前价格，如果没有数据返回 None
        """
        # 如果要求使用实时价格，尝试从交易所API获取
        if use_realtime:
            try:
                import requests
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
                
                # 标准化交易对格式
                symbol_clean = symbol.replace('/', '').upper()
                
                # 配置重试策略
                session = requests.Session()
                retry_strategy = Retry(
                    total=2,
                    backoff_factor=0.1,
                    status_forcelist=[429, 500, 502, 503, 504],
                )
                adapter = HTTPAdapter(max_retries=retry_strategy)
                session.mount("https://", adapter)
                
                # 优先从Binance合约API获取实时价格
                try:
                    response = session.get(
                        'https://fapi.binance.com/fapi/v1/ticker/price',
                        params={'symbol': symbol_clean},
                        timeout=2
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data and 'price' in data:
                            price = Decimal(str(data['price']))
                            logger.debug(f"从Binance合约API获取实时价格: {symbol} = {price}")
                            return price
                except Exception as e:
                    logger.debug(f"Binance合约API获取失败: {e}")
                
                # 如果Binance失败，尝试从Binance现货API获取
                try:
                    response = session.get(
                        'https://api.binance.com/api/v3/ticker/price',
                        params={'symbol': symbol_clean},
                        timeout=2
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data and 'price' in data:
                            price = Decimal(str(data['price']))
                            logger.debug(f"从Binance现货API获取实时价格: {symbol} = {price}")
                            return price
                except Exception as e:
                    logger.debug(f"Binance现货API获取失败: {e}")
                
                # 如果实时API都失败，回退到数据库缓存
                logger.warning(f"实时API获取失败，回退到数据库缓存: {symbol}")
            except Exception as e:
                logger.warning(f"获取实时价格异常，回退到数据库缓存: {symbol}, {e}")
        
        # 从数据库获取缓存价格（默认行为）
        # 每次查询都创建新连接，确保获取最新价格
        connection = pymysql.connect(
            **self.db_config,
            autocommit=True
        )
        
        try:
            cursor = connection.cursor(pymysql.cursors.DictCursor)

            # kline_data 表中的 symbol 格式是 BTC/USDT（带斜杠）
            # 优先使用1分钟K线（更及时），如果没有则使用5分钟K线
            sql = """
            SELECT close_price
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1m'
            AND exchange = 'binance'
            ORDER BY open_time DESC
            LIMIT 1
            """

            cursor.execute(sql, (symbol,))
            result = cursor.fetchone()
            
            # 如果1分钟K线没有数据，尝试5分钟K线
            if not result:
                sql = """
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '5m'
                AND exchange = 'binance'
                ORDER BY open_time DESC
                LIMIT 1
                """
                cursor.execute(sql, (symbol,))
                result = cursor.fetchone()
            
            # 如果5分钟K线也没有数据，尝试1小时K线（最后回退）
            if not result:
                sql = """
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND exchange = 'binance'
                ORDER BY open_time DESC
                LIMIT 1
                """
                cursor.execute(sql, (symbol,))
                result = cursor.fetchone()
            
            cursor.close()
            
            if result:
                return Decimal(str(result['close_price']))
            else:
                logger.warning(f"No price data found for {symbol}")
                return None
        finally:
            connection.close()

    def should_trigger_stop_loss(self, position: Dict, current_price: Decimal) -> bool:
        """
        判断是否触发止损

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            是否触发止损
        """
        # 检查是否有止损价格
        stop_loss_price = position.get('stop_loss_price')
        if not stop_loss_price or stop_loss_price == 0:
            return False

        try:
            stop_loss_price = Decimal(str(stop_loss_price))
        except (ValueError, TypeError):
            logger.warning(f"Position #{position['id']} has invalid stop_loss_price: {position.get('stop_loss_price')}")
            return False

        position_side = position['position_side']
        symbol = position['symbol']
        position_id = position['id']

        if position_side == 'LONG':
            # 多头：当前价格 <= 止损价（价格跌破止损价）
            should_trigger = current_price <= stop_loss_price
            if should_trigger:
                logger.info(f"🛑 Stop-loss triggered for LONG position #{position_id} {symbol}: "
                          f"current={current_price:.8f}, stop_loss={stop_loss_price:.8f}")
                return True
            else:
                # 添加调试日志，帮助诊断为什么没有触发
                logger.debug(f"LONG #{position_id} {symbol}: 价格={current_price:.8f}, 止损={stop_loss_price:.8f}, "
                           f"差值={float(current_price - stop_loss_price):.8f}, 未触发")
        else:  # SHORT
            # 空头：当前价格 >= 止损价（价格涨破止损价）
            should_trigger = current_price >= stop_loss_price
            if should_trigger:
                logger.info(f"🛑 Stop-loss triggered for SHORT position #{position_id} {symbol}: "
                          f"current={current_price:.8f}, stop_loss={stop_loss_price:.8f}")
                return True
            else:
                # 添加调试日志，帮助诊断为什么没有触发
                logger.debug(f"SHORT #{position_id} {symbol}: 价格={current_price:.8f}, 止损={stop_loss_price:.8f}, "
                           f"差值={float(current_price - stop_loss_price):.8f}, 未触发")

        return False

    def should_trigger_take_profit(self, position: Dict, current_price: Decimal) -> bool:
        """
        判断是否触发止盈

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            是否触发止盈
        """
        # 检查是否有止盈价格
        take_profit_price = position.get('take_profit_price')
        if not take_profit_price or take_profit_price == 0:
            return False

        try:
            take_profit_price = Decimal(str(take_profit_price))
        except (ValueError, TypeError):
            logger.warning(f"Position #{position['id']} has invalid take_profit_price: {position.get('take_profit_price')}")
            return False

        position_side = position['position_side']

        if position_side == 'LONG':
            # 多头：当前价格 >= 止盈价
            if current_price >= take_profit_price:
                logger.info(f"✅ Take-profit triggered for LONG position #{position['id']} {position['symbol']}: "
                          f"current={current_price:.8f}, take_profit={take_profit_price:.8f}")
                return True
        else:  # SHORT
            # 空头：当前价格 <= 止盈价
            if current_price <= take_profit_price:
                logger.info(f"✅ Take-profit triggered for SHORT position #{position['id']} {position['symbol']}: "
                          f"current={current_price:.8f}, take_profit={take_profit_price:.8f}")
                return True

        return False

    def should_trigger_liquidation(self, position: Dict, current_price: Decimal) -> bool:
        """
        判断是否触发强平

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            是否触发强平
        """
        if not position['liquidation_price']:
            return False

        liquidation_price = Decimal(str(position['liquidation_price']))
        position_side = position['position_side']

        if position_side == 'LONG':
            # 多头：当前价格 <= 强平价
            if current_price <= liquidation_price:
                logger.warning(f"⚠️ LIQUIDATION triggered for LONG position #{position['id']}: "
                             f"current={current_price:.2f}, liquidation={liquidation_price:.2f}")
                return True
        else:  # SHORT
            # 空头：当前价格 >= 强平价
            if current_price >= liquidation_price:
                logger.warning(f"⚠️ LIQUIDATION triggered for SHORT position #{position['id']}: "
                             f"current={current_price:.2f}, liquidation={liquidation_price:.2f}")
                return True

        return False

    def update_unrealized_pnl(self, position: Dict, current_price: Decimal):
        """
        更新未实现盈亏

        Args:
            position: 持仓信息
            current_price: 当前价格
        """
        entry_price = Decimal(str(position['entry_price']))
        quantity = Decimal(str(position['quantity']))
        position_side = position['position_side']

        # 计算未实现盈亏
        if position_side == 'LONG':
            unrealized_pnl = (current_price - entry_price) * quantity
        else:  # SHORT
            unrealized_pnl = (entry_price - current_price) * quantity

        # 计算收益率
        unrealized_pnl_pct = (unrealized_pnl / Decimal(str(position['margin']))) * 100

        # 更新数据库
        cursor = self.connection.cursor()

        sql = """
        UPDATE futures_positions
        SET
            mark_price = %s,
            unrealized_pnl = %s,
            unrealized_pnl_pct = %s,
            last_update_time = NOW()
        WHERE id = %s
        """

        try:
            cursor.execute(sql, (
                float(current_price),
                float(unrealized_pnl),
                float(unrealized_pnl_pct),
                position['id']
            ))
            self.connection.commit()
        except Exception as e:
            logger.error(f"Failed to update unrealized PnL for position #{position['id']}: {e}")
            self.connection.rollback()
        finally:
            cursor.close()

    def monitor_position(self, position: Dict) -> Dict:
        """
        监控单个持仓

        Args:
            position: 持仓信息

        Returns:
            监控结果
        """
        symbol = position['symbol']
        position_id = position['id']

        # 获取当前价格（监控止盈止损时使用实时价格）
        current_price = self.get_current_price(symbol, use_realtime=True)

        if not current_price:
            logger.warning(f"Position #{position_id} {symbol}: 无法获取当前价格")
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'no_price',
                'message': 'No price data available'
            }
        
        # 添加调试日志：显示持仓信息和价格对比
        stop_loss_price = position.get('stop_loss_price')
        take_profit_price = position.get('take_profit_price')
        position_side = position.get('position_side', 'UNKNOWN')
        entry_price = position.get('entry_price', 0)
        
        # 计算价格与止损价的关系
        if stop_loss_price:
            if position_side == 'LONG':
                # 多头：止损价应该低于开仓价，如果当前价低于止损价，应该触发
                price_to_stop_loss = float(current_price - Decimal(str(stop_loss_price)))
            else:  # SHORT
                # 空头：止损价应该高于开仓价，如果当前价高于止损价，应该触发
                price_to_stop_loss = float(current_price - Decimal(str(stop_loss_price)))

        # 更新未实现盈亏
        self.update_unrealized_pnl(position, current_price)

        # 优先级1: 检查强平
        if self.should_trigger_liquidation(position, current_price):
            logger.warning(f"🚨 Liquidating position #{position_id} {symbol}")
            result = self.engine.close_position(
                position_id=position_id,
                reason='liquidation'
            )
            # 同步平掉实盘仓位
            self._sync_close_live_position(position, 'liquidation')
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'liquidated',
                'current_price': float(current_price),
                'result': result
            }

        # 优先级2: 检查连续K线止损（亏损时提前离场）
        consecutive_stop_result = self._check_consecutive_kline_stop_loss(position, current_price)
        if consecutive_stop_result:
            logger.warning(f"🔻 Consecutive kline stop-loss triggered for position #{position_id} {symbol}: {consecutive_stop_result['reason']}")
            result = self.engine.close_position(
                position_id=position_id,
                reason='consecutive_kline_stop',
                close_price=current_price
            )
            # 同步平掉实盘仓位
            self._sync_close_live_position(position, 'consecutive_kline_stop')
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'consecutive_kline_stop',
                'current_price': float(current_price),
                'reason': consecutive_stop_result['reason'],
                'result': result
            }

        # 优先级3: 检查固定止损（使用持仓中保存的止损价格）
        if self.should_trigger_stop_loss(position, current_price):
            stop_loss_price = Decimal(str(position.get('stop_loss_price', 0)))
            logger.info(f"🛑 Stop-loss triggered for position #{position_id} {symbol} @ {current_price:.8f} (stop_loss={stop_loss_price:.8f})")
            result = self.engine.close_position(
                position_id=position_id,
                reason='stop_loss',
                close_price=stop_loss_price  # 使用止损价格平仓
            )
            # 同步平掉实盘仓位
            self._sync_close_live_position(position, 'stop_loss')
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'stop_loss',
                'current_price': float(current_price),
                'stop_loss_price': float(stop_loss_price),
                'result': result
            }

        # 优先级4: 检查止盈（使用持仓中保存的止盈价格）
        if self.should_trigger_take_profit(position, current_price):
            take_profit_price = Decimal(str(position.get('take_profit_price', 0)))
            logger.info(f"✅ Take-profit triggered for position #{position_id} {symbol} @ {current_price:.8f} (take_profit={take_profit_price:.8f})")
            result = self.engine.close_position(
                position_id=position_id,
                reason='take_profit',
                close_price=take_profit_price  # 使用止盈价格平仓
            )
            # 同步平掉实盘仓位
            self._sync_close_live_position(position, 'take_profit')
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'take_profit',
                'current_price': float(current_price),
                'take_profit_price': float(take_profit_price),
                'result': result
            }

        # 无触发
        return {
            'position_id': position_id,
            'symbol': symbol,
            'status': 'monitoring',
            'current_price': float(current_price),
            'unrealized_pnl': float(position.get('unrealized_pnl', 0))
        }

    def _sync_close_live_position(self, position: Dict, reason: str):
        """
        同步平掉实盘对应的仓位

        Args:
            position: 模拟盘仓位信息
            reason: 平仓原因 (stop_loss/take_profit/liquidation)
        """
        if not self.live_engine:
            return

        try:
            symbol = position['symbol']
            position_side = position['position_side']
            strategy_id = position.get('strategy_id')

            # 查询对应的实盘仓位
            self._ensure_connection()
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT id, quantity FROM live_futures_positions
                WHERE symbol = %s AND position_side = %s AND strategy_id = %s AND status = 'OPEN'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol, position_side, strategy_id))
            live_position = cursor.fetchone()
            cursor.close()

            if not live_position:
                logger.debug(f"[止损监控] 未找到对应的实盘仓位: {symbol} {position_side} (策略ID: {strategy_id})")
                return

            live_position_id = live_position['id']
            logger.info(f"[止损监控] 同步平仓实盘仓位 #{live_position_id}: {symbol} {position_side} (原因: {reason})")

            # 平掉实盘仓位
            close_result = self.live_engine.close_position(
                position_id=live_position_id,
                reason=f"sync_{reason}"
            )

            if close_result.get('success'):
                logger.info(f"[止损监控] ✅ 实盘平仓成功: {symbol} {position_side}")
            else:
                error_msg = close_result.get('error', '未知错误')
                logger.error(f"[止损监控] ❌ 实盘平仓失败: {symbol} {position_side} - {error_msg}")

        except Exception as e:
            logger.error(f"[止损监控] 同步实盘平仓异常: {e}")
            import traceback
            traceback.print_exc()

    def _check_consecutive_kline_stop_loss(self, position: Dict, current_price: Decimal) -> Optional[Dict]:
        """
        检查连续K线止损条件（亏损时提前离场）

        做多时：连续N根阴线（收盘<开盘）则提前止损
        做空时：连续N根阳线（收盘>开盘）则提前止损

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            如果触发止损返回 {'reason': str}，否则返回 None
        """
        try:
            # 获取策略配置
            strategy_id = position.get('strategy_id')
            if not strategy_id:
                return None

            # 从数据库获取策略配置
            self._ensure_connection()
            cursor = self.connection.cursor()
            cursor.execute("SELECT config FROM trading_strategies WHERE id = %s", (strategy_id,))
            strategy = cursor.fetchone()
            cursor.close()

            if not strategy or not strategy.get('config'):
                return None

            # 解析策略配置
            import json
            config = json.loads(strategy['config']) if isinstance(strategy['config'], str) else strategy['config']
            consecutive_config = config.get('consecutiveBearishStopLoss', {})

            if not consecutive_config.get('enabled', False):
                return None

            bars = consecutive_config.get('bars', 2)
            timeframe = consecutive_config.get('timeframe', '5m')
            max_loss_pct = consecutive_config.get('maxLossPct', -0.5)

            # 计算当前盈亏
            entry_price = Decimal(str(position.get('entry_price', 0)))
            position_side = position.get('position_side')

            if entry_price == 0:
                return None

            if position_side == 'LONG':
                current_profit_pct = float((current_price - entry_price) / entry_price * 100)
            else:  # SHORT
                current_profit_pct = float((entry_price - current_price) / entry_price * 100)

            # 检查是否在亏损区间
            if current_profit_pct > 0:
                return None

            # 检查是否超过最大亏损限制
            if current_profit_pct < max_loss_pct:
                return None

            # 获取K线数据
            symbol = position['symbol']
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT open_price, close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (symbol, timeframe, bars))

            klines = cursor.fetchall()
            cursor.close()

            if not klines or len(klines) < bars:
                return None

            # 检查连续K线
            if position_side == 'LONG':
                # 做多：检查连续阴线（价格持续下跌）
                consecutive_bearish = all(
                    float(k['close_price']) < float(k['open_price'])
                    for k in klines[:bars]
                )
                if consecutive_bearish:
                    return {
                        'reason': f"连续{bars}根阴线止损(亏损{current_profit_pct:.2f}%)"
                    }
            else:  # SHORT
                # 做空：检查连续阳线（价格持续上涨）
                consecutive_bullish = all(
                    float(k['close_price']) > float(k['open_price'])
                    for k in klines[:bars]
                )
                if consecutive_bullish:
                    return {
                        'reason': f"连续{bars}根阳线止损(亏损{current_profit_pct:.2f}%)"
                    }

            return None

        except Exception as e:
            logger.error(f"连续K线止损检测失败: {e}")
            return None

    def monitor_all_positions(self) -> Dict:
        """
        监控所有持仓

        Returns:
            监控结果统计
        """
        # 使用DEBUG级别，避免频繁打印

        # 获取所有持仓
        positions = self.get_open_positions()

        if not positions:
            return {
                'total_positions': 0,
                'monitoring': 0,
                'stop_loss': 0,
                'take_profit': 0,
                'liquidated': 0,
                'no_price': 0
            }


        # 监控每个持仓
        results = {
            'total_positions': len(positions),
            'monitoring': 0,
            'stop_loss': 0,
            'take_profit': 0,
            'liquidated': 0,
            'no_price': 0,
            'details': []
        }

        for position in positions:
            result = self.monitor_position(position)
            results['details'].append(result)

            # 统计
            status = result['status']
            if status in results:
                results[status] += 1

        # 只在有重要事件时打印INFO，否则使用DEBUG
        has_important_events = (
            results['stop_loss'] > 0 or 
            results['take_profit'] > 0 or 
            results['liquidated'] > 0
        )
        
        if has_important_events:
            logger.info("=" * 60)
            logger.info(f"监控周期完成（有重要事件）:")
            logger.info(f"  总持仓: {results['total_positions']}")
            logger.info(f"  监控中: {results['monitoring']}")
            if results['stop_loss'] > 0:
                logger.info(f"  🛑 止损触发: {results['stop_loss']}")
            if results['take_profit'] > 0:
                logger.info(f"  ✅ 止盈触发: {results['take_profit']}")
            if results['liquidated'] > 0:
                logger.warning(f"  ⚠️  强平触发: {results['liquidated']}")
            logger.info("=" * 60)
        else:
            pass

        return results

    def run_continuous(self, interval_seconds: int = 60):
        """
        持续运行监控（每N秒检查一次）

        Args:
            interval_seconds: 检查间隔（秒），默认60秒
        """
        logger.info(f"Starting continuous monitoring (interval: {interval_seconds}s)")
        logger.info("Press Ctrl+C to stop")

        try:
            while True:
                try:
                    self.monitor_all_positions()
                except Exception as e:
                    logger.error(f"Error in monitoring cycle: {e}", exc_info=True)

                # 等待下一个周期
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
        finally:
            self.close()

    def close(self):
        """关闭数据库连接"""
        if hasattr(self, 'connection') and self.connection:
            self.connection.close()
            # 静默关闭，不打印日志

        if hasattr(self, 'engine'):
            # FuturesTradingEngine 没有 close 方法，不需要调用
            pass


def main():
    """主函数 - 用于直接运行监控器"""
    from pathlib import Path
    from app.utils.config_loader import load_config

    # 加载配置（支持环境变量）
    config_path = Path(__file__).parent.parent.parent / 'config.yaml'
    config = load_config(config_path)

    db_config = config['database']['mysql']

    # 创建监控器
    monitor = StopLossMonitor(db_config)

    # 持续运行（每60秒检查一次）
    monitor.run_continuous(interval_seconds=60)


if __name__ == '__main__':
    main()
