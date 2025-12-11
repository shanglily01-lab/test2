"""
统一交易引擎
根据策略配置的market_type自动选择模拟或实盘引擎
"""

from decimal import Decimal
from typing import Dict, List, Optional
from loguru import logger


class UnifiedTradingEngine:
    """
    统一交易引擎

    根据账户类型或策略配置，自动路由到模拟或实盘引擎
    """

    def __init__(self, db_config: dict, trade_notifier=None):
        """
        初始化统一交易引擎

        Args:
            db_config: 数据库配置
            trade_notifier: Telegram通知服务（可选）
        """
        self.db_config = db_config
        self.trade_notifier = trade_notifier
        self._paper_engine = None
        self._live_engine = None
        self._live_engine_error = None

        # 先初始化实盘引擎（可能失败，但模拟引擎需要引用它）
        self._init_live_engine()

        # 初始化模拟引擎（始终可用，传入实盘引擎用于平仓同步）
        self._init_paper_engine()

    def _init_paper_engine(self):
        """初始化模拟交易引擎"""
        try:
            from app.trading.futures_trading_engine import FuturesTradingEngine
            self._paper_engine = FuturesTradingEngine(
                self.db_config,
                trade_notifier=self.trade_notifier,
                live_engine=self._live_engine  # 传入实盘引擎用于平仓同步
            )
            logger.info("模拟交易引擎初始化成功")
        except Exception as e:
            logger.error(f"模拟交易引擎初始化失败: {e}")
            raise

    def _init_live_engine(self):
        """初始化实盘交易引擎"""
        try:
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            self._live_engine = BinanceFuturesEngine(self.db_config)
            logger.info("实盘交易引擎初始化成功")
        except Exception as e:
            self._live_engine_error = str(e)
            logger.warning(f"实盘交易引擎初始化失败（实盘功能不可用）: {e}")

    def _get_engine(self, market_type: str = 'test'):
        """
        根据市场类型获取对应的引擎

        Args:
            market_type: 'test' 模拟, 'live' 实盘

        Returns:
            对应的交易引擎
        """
        if market_type == 'live':
            if self._live_engine is None:
                raise RuntimeError(f"实盘引擎不可用: {self._live_engine_error}")
            return self._live_engine
        else:
            return self._paper_engine

    @property
    def paper_engine(self):
        """获取模拟引擎"""
        return self._paper_engine

    @property
    def live_engine(self):
        """获取实盘引擎"""
        if self._live_engine is None:
            raise RuntimeError(f"实盘引擎不可用: {self._live_engine_error}")
        return self._live_engine

    def is_live_available(self) -> bool:
        """检查实盘引擎是否可用"""
        return self._live_engine is not None

    # ==================== 统一接口 ====================

    def get_current_price(self, symbol: str, use_realtime: bool = False,
                         market_type: str = 'test') -> Decimal:
        """
        获取当前价格

        Args:
            symbol: 交易对
            use_realtime: 是否使用实时价格
            market_type: 市场类型

        Returns:
            当前价格
        """
        engine = self._get_engine(market_type)

        if market_type == 'live':
            # 实盘始终使用实时价格
            return engine.get_current_price(symbol)
        else:
            return engine.get_current_price(symbol, use_realtime=use_realtime)

    def open_position(
        self,
        account_id: int,
        symbol: str,
        position_side: str,
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
        market_type: str = 'test'
    ) -> Dict:
        """
        开仓

        Args:
            account_id: 账户ID
            symbol: 交易对
            position_side: 持仓方向
            quantity: 数量
            leverage: 杠杆
            limit_price: 限价
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            stop_loss_price: 止损价格
            take_profit_price: 止盈价格
            source: 来源
            signal_id: 信号ID
            strategy_id: 策略ID
            market_type: 市场类型 'test' 或 'live'

        Returns:
            开仓结果
        """
        engine = self._get_engine(market_type)

        # 记录日志
        log_prefix = "[实盘]" if market_type == 'live' else "[模拟]"
        logger.info(f"{log_prefix} 开仓请求: {symbol} {position_side} {quantity} "
                   f"杠杆={leverage} 策略ID={strategy_id}")

        result = engine.open_position(
            account_id=account_id,
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            leverage=leverage,
            limit_price=limit_price,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            source=source,
            signal_id=signal_id,
            strategy_id=strategy_id
        )

        if result.get('success'):
            logger.info(f"{log_prefix} 开仓成功: {result.get('message', '')}")
        else:
            logger.error(f"{log_prefix} 开仓失败: {result.get('error', '')}")

        return result

    def close_position(
        self,
        position_id: int,
        close_quantity: Optional[Decimal] = None,
        reason: str = 'manual',
        close_price: Optional[Decimal] = None,
        market_type: str = 'test'
    ) -> Dict:
        """
        平仓

        Args:
            position_id: 持仓ID
            close_quantity: 平仓数量
            reason: 平仓原因
            close_price: 平仓价格
            market_type: 市场类型

        Returns:
            平仓结果
        """
        engine = self._get_engine(market_type)

        log_prefix = "[实盘]" if market_type == 'live' else "[模拟]"
        logger.info(f"{log_prefix} 平仓请求: position_id={position_id} reason={reason}")

        result = engine.close_position(
            position_id=position_id,
            close_quantity=close_quantity,
            reason=reason,
            close_price=close_price
        )

        if result.get('success'):
            pnl = result.get('realized_pnl', 0)
            logger.info(f"{log_prefix} 平仓成功: PnL={pnl}")
        else:
            logger.error(f"{log_prefix} 平仓失败: {result.get('error', '')}")

        return result

    def get_open_positions(self, account_id: int, market_type: str = 'test') -> List[Dict]:
        """
        获取持仓

        Args:
            account_id: 账户ID
            market_type: 市场类型

        Returns:
            持仓列表
        """
        engine = self._get_engine(market_type)

        if market_type == 'live':
            # 实盘直接从币安获取
            return engine.get_open_positions(account_id)
        else:
            return engine.get_open_positions(account_id)

    # ==================== 模拟引擎专用方法 ====================
    # 这些方法保持与原 FuturesTradingEngine 兼容

    def get_account(self, account_id: int = None):
        """获取模拟账户信息"""
        return self._paper_engine.get_account(account_id)

    def update_positions_value(self, account_id: int):
        """更新持仓市值（模拟）"""
        return self._paper_engine.update_positions_value(account_id)

    def check_stop_loss_take_profit(self, position: Dict, current_price: Decimal) -> Optional[str]:
        """检查止损止盈（模拟）"""
        return self._paper_engine.check_stop_loss_take_profit(position, current_price)

    def calculate_liquidation_price(self, entry_price: Decimal, position_side: str,
                                   leverage: int, maintenance_margin_rate: Decimal = None):
        """计算强平价格"""
        return self._paper_engine.calculate_liquidation_price(
            entry_price, position_side, leverage, maintenance_margin_rate
        )


def create_unified_engine(db_config: dict, trade_notifier=None) -> UnifiedTradingEngine:
    """
    创建统一交易引擎

    Args:
        db_config: 数据库配置
        trade_notifier: Telegram通知服务（可选）
    """
    return UnifiedTradingEngine(db_config, trade_notifier=trade_notifier)
