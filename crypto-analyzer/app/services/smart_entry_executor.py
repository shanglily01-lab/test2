"""
智能价格采样建仓执行器 V1 (一次性开仓版本)
基于15分钟价格采样找到最优入场点

核心策略：
- 15分钟价格采样窗口
- 做多：找最低价（90分位数以下）
- 做空：找最高价（90分位数以上）
- 找到最优点后立即一次性开仓100%，不分批
"""
import asyncio
import json
import pymysql
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from decimal import Decimal
from loguru import logger
import numpy as np

from app.services.optimization_config import OptimizationConfig


class SmartEntryExecutor:
    """智能价格采样建仓执行器（一次性开仓）"""

    def __init__(self, db_config: dict, live_engine, price_service, account_id=None):
        """
        初始化执行器

        Args:
            db_config: 数据库配置
            live_engine: 交易引擎
            price_service: 价格服务（WebSocket）
            account_id: 账户ID
        """
        self.db_config = db_config
        self.live_engine = live_engine
        self.price_service = price_service
        if account_id is not None:
            self.account_id = account_id
        else:
            self.account_id = getattr(live_engine, 'account_id', 2)

        # 获取brain和opt_config
        self.brain = getattr(live_engine, 'brain', None)
        self.opt_config = getattr(live_engine, 'opt_config', None)

        # 如果仍没有opt_config，创建新实例
        if not self.opt_config:
            self.opt_config = OptimizationConfig(db_config)

        # 价格采样配置
        self.sampling_window_minutes = 15  # 采样窗口15分钟
        self.sample_interval_seconds = 5  # 每5秒采样一次
        self.percentile_threshold = 90  # 90分位数阈值

    def _get_margin_amount(self, symbol: str) -> float:
        """根据交易对评级等级获取保证金金额"""
        rating_level = self.opt_config.get_symbol_rating_level(symbol)

        if rating_level == 0:
            return 200.0  # 白名单
        elif rating_level == 1:
            return 50.0  # 黑名单1级
        elif rating_level == 2:
            return 30.0  # 黑名单2级
        else:
            return 0.0  # 黑名单3级

    def _calculate_stop_take_prices(self, symbol: str, direction: str, current_price: float, signal_components: dict) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """计算止盈止损价格和百分比"""
        if not self.brain or not self.opt_config:
            return None, None, None, None

        # 获取自适应参数
        if direction == 'LONG':
            adaptive_params = self.brain.adaptive_long
        else:
            adaptive_params = self.brain.adaptive_short

        # 计算止损
        base_stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)
        stop_loss_pct = self._calculate_volatility_adjusted_stop_loss(signal_components, base_stop_loss_pct)

        # 计算止盈
        volatility_profile = self.opt_config.get_symbol_volatility_profile(symbol)
        if volatility_profile:
            if direction == 'LONG' and volatility_profile.get('long_fixed_tp_pct'):
                take_profit_pct = float(volatility_profile['long_fixed_tp_pct'])
            elif direction == 'SHORT' and volatility_profile.get('short_fixed_tp_pct'):
                take_profit_pct = float(volatility_profile['short_fixed_tp_pct'])
            else:
                take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)
        else:
            take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)

        # 计算具体价格
        if direction == 'LONG':
            stop_loss_price = current_price * (1 - stop_loss_pct)
            take_profit_price = current_price * (1 + take_profit_pct)
        else:  # SHORT
            stop_loss_price = current_price * (1 + stop_loss_pct)
            take_profit_price = current_price * (1 - take_profit_pct)

        return stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct

    def _calculate_volatility_adjusted_stop_loss(self, signal_components: dict, base_stop_loss_pct: float) -> float:
        """波动率自适应止损"""
        if not signal_components:
            return base_stop_loss_pct

        # 如果包含破位信号，扩大止损
        if any(key.startswith('breakdown_') for key in signal_components.keys()):
            adjusted_pct = base_stop_loss_pct * 1.5
            logger.debug(f"[VOLATILITY_SL] 破位信号，止损扩大1.5倍: {adjusted_pct*100:.2f}%")
            return adjusted_pct

        return base_stop_loss_pct

    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格（优先WebSocket，回退到数据库）"""
        # 优先从WebSocket获取
        if self.price_service:
            price = self.price_service.get_price(symbol)
            if price and price > 0:
                return float(price)

        # 回退到数据库
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT close FROM futures_klines
                WHERE symbol = %s AND interval = '5m' AND exchange = 'binance_futures'
                ORDER BY open_time DESC
                LIMIT 1
            """, (symbol,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return float(result[0])
        except Exception as e:
            logger.error(f"❌ 从数据库获取价格失败: {e}")

        return None

    async def execute_entry(self, signal: Dict) -> Dict:
        """
        执行价格采样建仓

        流程：
        1. 15分钟价格采样窗口
        2. 做多：找最低价（<90分位数）
        3. 做空：找最高价（>90分位数）
        4. 找到最优点后立即一次性开仓100%

        Args:
            signal: 开仓信号 {
                'symbol': str,
                'direction': 'LONG'/'SHORT',
                'leverage': int,
                'signal_time': datetime,
                'trade_params': {...}
            }

        Returns:
            建仓结果 {'success': bool, 'position_id': int, 'price': float}
        """
        symbol = signal['symbol']
        direction = signal['direction']

        # 使用真实的信号触发时间
        signal_time = signal.get('signal_time', datetime.now())
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time)

        # 获取保证金金额
        margin = self._get_margin_amount(symbol)

        if margin == 0:
            rating_level = self.opt_config.get_symbol_rating_level(symbol)
            logger.warning(f"❌ {symbol} 为黑名单{rating_level}级，禁止交易")
            return {'success': False, 'reason': f'黑名单{rating_level}级禁止交易'}

        logger.info(f"🚀 {symbol} 开始价格采样建仓 V1（一次性开仓） | 方向: {direction}")
        logger.info(f"   信号时间: {signal_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"   策略: 15分钟价格采样找最优点 | 90分位数阈值")
        logger.info(f"💰 保证金: {margin}U (评级等级: {self.opt_config.get_symbol_rating_level(symbol)})")

        # 确保symbol已订阅到WebSocket价格服务
        if self.price_service and hasattr(self.price_service, 'subscribe'):
            try:
                await self.price_service.subscribe([symbol])
                logger.debug(f"✅ {symbol} 已订阅到WebSocket价格服务")
            except Exception as e:
                logger.warning(f"⚠️ {symbol} WebSocket订阅失败: {e}，将使用数据库价格")

        try:
            # 价格采样
            price_samples = []
            sampling_start = datetime.now()
            sampling_end = sampling_start + timedelta(minutes=self.sampling_window_minutes)

            logger.info(f"🔍 {symbol} 开始价格采样（15分钟窗口）...")

            while datetime.now() < sampling_end:
                current_price = await self._get_current_price(symbol)
                if current_price:
                    price_samples.append(current_price)
                    elapsed_seconds = (datetime.now() - sampling_start).total_seconds()

                    # 每分钟输出一次状态
                    if int(elapsed_seconds) % 60 == 0 and elapsed_seconds > 0:
                        logger.debug(f"📊 {symbol} 采样进度: {elapsed_seconds/60:.0f}/15分钟 | 已采样:{len(price_samples)}个价格点")

                    # 如果已有足够样本，检查是否达到最优点
                    if len(price_samples) >= 30:  # 至少30个样本
                        optimal, reason = self._check_optimal_entry(price_samples, current_price, direction)
                        if optimal:
                            logger.info(f"✅ {symbol} 找到最优入场点: {reason}")
                            return await self._execute_single_entry(
                                symbol, direction, margin, signal, current_price
                            )

                await asyncio.sleep(self.sample_interval_seconds)

            # 采样结束，使用最后的价格开仓
            if price_samples:
                final_price = price_samples[-1]
                logger.info(f"⏱️ {symbol} 15分钟采样结束，使用最终价格开仓: ${final_price:.4f}")
                return await self._execute_single_entry(
                    symbol, direction, margin, signal, final_price
                )
            else:
                logger.error(f"❌ {symbol} 采样失败，无有效价格")
                return {'success': False, 'error': '无有效价格'}

        except Exception as e:
            logger.error(f"❌ {symbol} 价格采样建仓执行出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'error': str(e)}

    def _check_optimal_entry(self, price_samples: list, current_price: float, direction: str) -> Tuple[bool, str]:
        """
        检查是否达到最优入场点

        Args:
            price_samples: 价格样本列表
            current_price: 当前价格
            direction: 方向 LONG/SHORT

        Returns:
            (是否最优, 原因描述)
        """
        if len(price_samples) < 30:
            return False, "样本不足"

        # 计算90分位数
        percentile_value = np.percentile(price_samples, self.percentile_threshold)

        if direction == 'LONG':
            # 做多：当前价格低于90分位数（价格相对较低）
            if current_price < percentile_value:
                pct_below = (percentile_value - current_price) / percentile_value * 100
                return True, f"价格低于90分位数{pct_below:.2f}%"
        else:  # SHORT
            # 做空：当前价格高于90分位数（价格相对较高）
            percentile_10 = np.percentile(price_samples, 10)
            if current_price > percentile_value:
                pct_above = (current_price - percentile_value) / percentile_value * 100
                return True, f"价格高于90分位数{pct_above:.2f}%"

        return False, "等待更优价格"

    async def _execute_single_entry(
        self, symbol: str, direction: str, margin: float,
        signal: Dict, entry_price: float
    ) -> Dict:
        """
        执行一次性开仓

        Args:
            symbol: 交易对
            direction: 方向
            margin: 保证金金额
            signal: 原始信号
            entry_price: 入场价格

        Returns:
            开仓结果
        """
        try:
            # 计算止盈止损
            signal_components = signal.get('trade_params', {}).get('signal_components', {})
            stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct = \
                self._calculate_stop_take_prices(symbol, direction, entry_price, signal_components)

            # 计算仓位
            leverage = signal.get('leverage', 5)
            quantity = margin * leverage / entry_price
            notional_value = quantity * entry_price

            # 生成信号组合键
            if signal_components:
                sorted_signals = sorted(signal_components.keys())
                signal_combination_key = "TREND_" + " + ".join(sorted_signals)
            else:
                signal_combination_key = "TREND_unknown"

            # 计算超时和计划平仓时间
            max_hold_minutes = 180  # 3小时
            timeout_at = datetime.utcnow() + timedelta(minutes=max_hold_minutes)
            planned_close_time = datetime.now() + timedelta(minutes=max_hold_minutes)

            # 准备数据
            entry_score = signal.get('trade_params', {}).get('entry_score', 0)
            entry_reason = f"V1价格采样 | 评分:{entry_score}"

            # 插入持仓记录
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, entry_reason, entry_score, signal_components, max_hold_minutes, timeout_at,
                 planned_close_time, source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'smart_trader', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, direction, quantity, entry_price, leverage,
                notional_value, margin, stop_loss_price, take_profit_price,
                signal_combination_key, entry_reason, entry_score,
                json.dumps(signal_components) if signal_components else None,
                max_hold_minutes, timeout_at, planned_close_time
            ))

            position_id = cursor.lastrowid

            # 冻结资金
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET available_balance = available_balance - %s,
                    frozen_balance = frozen_balance + %s
                WHERE id = %s
            """, (margin, margin, self.account_id))

            conn.commit()
            conn.close()

            logger.info(f"✅ {symbol} 一次性开仓完成 | 持仓ID:{position_id} | 价格:${entry_price:.4f} | 保证金:{margin}U")

            # 启动智能平仓监控
            if self.live_engine.smart_exit_optimizer:
                try:
                    asyncio.create_task(
                        self.live_engine.smart_exit_optimizer.start_monitoring_position(position_id)
                    )
                    logger.info(f"✅ 持仓{position_id}已加入智能平仓监控")
                except Exception as e:
                    logger.error(f"❌ 持仓{position_id}启动监控失败: {e}")

            return {
                'success': True,
                'position_id': position_id,
                'price': entry_price,
                'margin': margin,
                'quantity': quantity
            }

        except Exception as e:
            logger.error(f"❌ {symbol} 一次性开仓失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'error': str(e)}
