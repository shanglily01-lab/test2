"""
回测运行器
使用采集的历史数据模拟实时行情，运行策略并记录结果

用法:
    python scripts/backtest/backtest_runner.py --session bt_20241208_120000 --strategy 1
"""

import sys
import argparse
import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from decimal import Decimal
import pymysql
import yaml

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 确保控制台输出使用UTF-8编码 (Windows兼容)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def load_config() -> Dict:
    """加载配置文件"""
    config_path = project_root / 'config.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


class BacktestDataProvider:
    """
    回测数据提供器
    从回测数据表中读取数据，模拟实时数据推送
    """

    def __init__(self, db_config: Dict, session_id: str):
        self.db_config = db_config
        self.session_id = session_id
        self.connection = None

        # 缓存
        self._session_info = None
        self._kline_cache = {}  # {symbol_timeframe: [klines]}
        self._price_cache = {}  # {symbol: [prices]}
        self._current_index = {}  # {symbol: price_index}

    def connect(self):
        """连接数据库"""
        self.connection = pymysql.connect(
            host=self.db_config.get('host', '13.212.252.171'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'admin'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def get_session_info(self) -> Optional[Dict]:
        """获取会话信息"""
        if self._session_info:
            return self._session_info

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM backtest_sessions WHERE session_id = %s
        """, (self.session_id,))
        self._session_info = cursor.fetchone()
        cursor.close()

        if self._session_info:
            self._session_info['symbols'] = json.loads(self._session_info['symbols'])
            self._session_info['timeframes'] = json.loads(self._session_info['timeframes'])

        return self._session_info

    def load_klines(self, symbol: str, timeframe: str) -> List[Dict]:
        """加载K线数据到内存"""
        cache_key = f"{symbol}_{timeframe}"

        if cache_key in self._kline_cache:
            return self._kline_cache[cache_key]

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM backtest_kline_data
            WHERE session_id = %s AND symbol = %s AND timeframe = %s
            ORDER BY timestamp ASC
        """, (self.session_id, symbol, timeframe))

        klines = cursor.fetchall()
        cursor.close()

        # 转换Decimal类型
        for k in klines:
            for key in ['open_price', 'high_price', 'low_price', 'close_price', 'volume']:
                if k.get(key) is not None:
                    k[key] = float(k[key])

        self._kline_cache[cache_key] = klines
        return klines

    def load_prices(self, symbol: str) -> List[Dict]:
        """加载价格数据到内存"""
        if symbol in self._price_cache:
            return self._price_cache[symbol]

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM backtest_price_data
            WHERE session_id = %s AND symbol = %s
            ORDER BY timestamp ASC
        """, (self.session_id, symbol))

        prices = cursor.fetchall()
        cursor.close()

        # 转换Decimal类型
        for p in prices:
            for key in ['price', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']:
                if p.get(key) is not None:
                    p[key] = float(p[key])

        self._price_cache[symbol] = prices
        self._current_index[symbol] = 0

        return prices

    def get_klines_at_time(self, symbol: str, timeframe: str,
                           current_time: datetime, limit: int = 100) -> List[Dict]:
        """
        获取某个时间点之前的K线数据
        模拟策略在该时间点能看到的历史数据
        """
        all_klines = self.load_klines(symbol, timeframe)

        # 过滤出当前时间之前的K线
        available_klines = [k for k in all_klines if k['timestamp'] <= current_time]

        # 返回最近的limit条
        return available_klines[-limit:] if len(available_klines) > limit else available_klines

    def get_price_at_time(self, symbol: str, current_time: datetime) -> Optional[Dict]:
        """获取某个时间点的价格"""
        prices = self.load_prices(symbol)

        for p in prices:
            if p['timestamp'] <= current_time:
                last_price = p
            else:
                break

        return last_price if 'last_price' in dir() else (prices[0] if prices else None)

    def get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格（用于模拟实时价格）"""
        prices = self.load_prices(symbol)
        idx = self._current_index.get(symbol, 0)

        if idx < len(prices):
            return prices[idx]['price']
        return None

    def advance_time(self, symbol: str) -> Optional[datetime]:
        """推进时间到下一个价格点"""
        prices = self.load_prices(symbol)
        idx = self._current_index.get(symbol, 0)

        if idx < len(prices) - 1:
            self._current_index[symbol] = idx + 1
            return prices[idx + 1]['timestamp']
        return None

    def get_all_timestamps(self, symbol: str) -> List[datetime]:
        """获取所有价格时间点"""
        prices = self.load_prices(symbol)
        return [p['timestamp'] for p in prices]

    def close(self):
        """关闭连接"""
        if self.connection:
            self.connection.close()


class BacktestEngine:
    """
    回测引擎
    模拟市场环境，调用策略执行器，记录交易结果
    """

    def __init__(self, config: Dict, session_id: str, strategy_id: int = None):
        self.config = config
        self.session_id = session_id
        self.strategy_id = strategy_id

        db_config = config.get('database', {}).get('mysql', {})
        self.data_provider = BacktestDataProvider(db_config, session_id)

        # 回测状态
        self.current_time = None
        self.positions = {}  # {symbol: position}
        self.trades = []
        self.balance = 10000.0  # 初始资金
        self.initial_balance = 10000.0

        # 统计信息
        self.stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0,
            'peak_balance': 10000.0
        }

    def init(self):
        """初始化回测环境"""
        self.data_provider.connect()
        session_info = self.data_provider.get_session_info()

        if not session_info:
            raise ValueError(f"会话 {self.session_id} 不存在")

        if session_info['status'] != 'ready':
            raise ValueError(f"会话状态不是 ready: {session_info['status']}")

        print(f"\n{'='*60}")
        print(f"📊 回测引擎初始化")
        print(f"  会话ID: {self.session_id}")
        print(f"  时间范围: {session_info['start_time']} ~ {session_info['end_time']}")
        print(f"  交易对: {', '.join(session_info['symbols'])}")
        print(f"  K线数据: {session_info['kline_count']} 条")
        print(f"  价格数据: {session_info['price_count']} 条")
        print(f"{'='*60}\n")

        self.current_time = session_info['start_time']
        self.session_info = session_info

        # 预加载数据
        for symbol in session_info['symbols']:
            self.data_provider.load_prices(symbol)
            for tf in session_info['timeframes']:
                self.data_provider.load_klines(symbol, tf)

        print("✅ 数据预加载完成")

    def get_klines(self, symbol: str, timeframe: str, limit: int = 100) -> List[Dict]:
        """获取K线数据（策略调用接口）"""
        return self.data_provider.get_klines_at_time(symbol, timeframe, self.current_time, limit)

    def get_current_price(self, symbol: str) -> float:
        """获取当前价格（策略调用接口）"""
        price_data = self.data_provider.get_price_at_time(symbol, self.current_time)
        return price_data['price'] if price_data else 0.0

    def open_position(self, symbol: str, direction: str, quantity: float,
                      entry_price: float = None, stop_loss: float = None,
                      take_profit: float = None) -> Dict:
        """
        开仓

        Args:
            symbol: 交易对
            direction: 方向 (long/short)
            quantity: 数量
            entry_price: 入场价格，None则使用当前价格
            stop_loss: 止损价
            take_profit: 止盈价

        Returns:
            持仓信息
        """
        if entry_price is None:
            entry_price = self.get_current_price(symbol)

        position = {
            'symbol': symbol,
            'direction': direction,
            'quantity': quantity,
            'entry_price': entry_price,
            'entry_time': self.current_time,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'unrealized_pnl': 0.0
        }

        self.positions[symbol] = position

        print(f"  📈 开仓: {symbol} {direction.upper()} {quantity} @ {entry_price:.4f}")
        print(f"     止损: {stop_loss}, 止盈: {take_profit}")

        return position

    def close_position(self, symbol: str, exit_price: float = None,
                       reason: str = 'manual') -> Optional[Dict]:
        """
        平仓

        Args:
            symbol: 交易对
            exit_price: 出场价格，None则使用当前价格
            reason: 平仓原因

        Returns:
            交易记录
        """
        if symbol not in self.positions:
            return None

        position = self.positions[symbol]

        if exit_price is None:
            exit_price = self.get_current_price(symbol)

        # 计算盈亏
        if position['direction'] == 'long':
            pnl = (exit_price - position['entry_price']) * position['quantity']
        else:
            pnl = (position['entry_price'] - exit_price) * position['quantity']

        # 记录交易
        trade = {
            'symbol': symbol,
            'direction': position['direction'],
            'quantity': position['quantity'],
            'entry_price': position['entry_price'],
            'entry_time': position['entry_time'],
            'exit_price': exit_price,
            'exit_time': self.current_time,
            'pnl': pnl,
            'pnl_pct': pnl / (position['entry_price'] * position['quantity']) * 100,
            'reason': reason
        }

        self.trades.append(trade)

        # 更新统计
        self.stats['total_trades'] += 1
        self.stats['total_pnl'] += pnl
        self.balance += pnl

        if pnl > 0:
            self.stats['winning_trades'] += 1
        else:
            self.stats['losing_trades'] += 1

        # 更新最大回撤
        if self.balance > self.stats['peak_balance']:
            self.stats['peak_balance'] = self.balance
        drawdown = (self.stats['peak_balance'] - self.balance) / self.stats['peak_balance'] * 100
        if drawdown > self.stats['max_drawdown']:
            self.stats['max_drawdown'] = drawdown

        # 删除持仓
        del self.positions[symbol]

        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        print(f"  {pnl_emoji} 平仓: {symbol} @ {exit_price:.4f}, 盈亏: {pnl:.2f} ({trade['pnl_pct']:.2f}%), 原因: {reason}")

        return trade

    def check_stop_loss_take_profit(self, symbol: str):
        """检查止损止盈"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]
        current_price = self.get_current_price(symbol)

        if position['direction'] == 'long':
            # 做多: 价格低于止损或高于止盈
            if position['stop_loss'] and current_price <= position['stop_loss']:
                self.close_position(symbol, current_price, 'stop_loss')
            elif position['take_profit'] and current_price >= position['take_profit']:
                self.close_position(symbol, current_price, 'take_profit')
        else:
            # 做空: 价格高于止损或低于止盈
            if position['stop_loss'] and current_price >= position['stop_loss']:
                self.close_position(symbol, current_price, 'stop_loss')
            elif position['take_profit'] and current_price <= position['take_profit']:
                self.close_position(symbol, current_price, 'take_profit')

    def update_unrealized_pnl(self):
        """更新未实现盈亏"""
        for symbol, position in self.positions.items():
            current_price = self.get_current_price(symbol)

            if position['direction'] == 'long':
                position['unrealized_pnl'] = (current_price - position['entry_price']) * position['quantity']
            else:
                position['unrealized_pnl'] = (position['entry_price'] - current_price) * position['quantity']

    async def run_with_strategy(self, strategy_callback: Callable):
        """
        使用策略回调运行回测

        Args:
            strategy_callback: 策略回调函数，签名: (engine, symbol, current_time) -> None
        """
        symbols = self.session_info['symbols']

        # 获取所有时间点
        all_timestamps = set()
        for symbol in symbols:
            timestamps = self.data_provider.get_all_timestamps(symbol)
            all_timestamps.update(timestamps)

        sorted_timestamps = sorted(all_timestamps)

        print(f"\n📈 开始回测，共 {len(sorted_timestamps)} 个时间点...")
        print(f"   开始时间: {sorted_timestamps[0]}")
        print(f"   结束时间: {sorted_timestamps[-1]}")

        progress_interval = len(sorted_timestamps) // 20  # 每5%报告一次

        for i, timestamp in enumerate(sorted_timestamps):
            self.current_time = timestamp

            # 检查止损止盈
            for symbol in list(self.positions.keys()):
                self.check_stop_loss_take_profit(symbol)

            # 执行策略
            for symbol in symbols:
                try:
                    await strategy_callback(self, symbol, timestamp)
                except Exception as e:
                    print(f"  ❌ 策略执行错误 {symbol}: {e}")

            # 更新未实现盈亏
            self.update_unrealized_pnl()

            # 进度报告
            if progress_interval > 0 and i > 0 and i % progress_interval == 0:
                progress = i / len(sorted_timestamps) * 100
                print(f"  ⏳ 进度: {progress:.0f}%, 时间: {timestamp}, 余额: {self.balance:.2f}")

        # 平掉所有持仓
        print("\n📊 回测结束，平掉剩余持仓...")
        for symbol in list(self.positions.keys()):
            self.close_position(symbol, reason='backtest_end')

        self.print_results()

    def print_results(self):
        """打印回测结果"""
        print(f"\n{'='*60}")
        print(f"📊 回测结果")
        print(f"{'='*60}")
        print(f"  初始资金: {self.initial_balance:.2f}")
        print(f"  最终资金: {self.balance:.2f}")
        print(f"  总收益: {self.stats['total_pnl']:.2f} ({(self.balance/self.initial_balance-1)*100:.2f}%)")
        print(f"  最大回撤: {self.stats['max_drawdown']:.2f}%")
        print(f"\n  交易统计:")
        print(f"    总交易数: {self.stats['total_trades']}")
        print(f"    盈利交易: {self.stats['winning_trades']}")
        print(f"    亏损交易: {self.stats['losing_trades']}")

        if self.stats['total_trades'] > 0:
            win_rate = self.stats['winning_trades'] / self.stats['total_trades'] * 100
            print(f"    胜率: {win_rate:.1f}%")

        if self.trades:
            print(f"\n  最近交易:")
            for trade in self.trades[-5:]:
                pnl_emoji = "🟢" if trade['pnl'] > 0 else "🔴"
                print(f"    {pnl_emoji} {trade['symbol']} {trade['direction']} "
                      f"{trade['entry_price']:.4f} → {trade['exit_price']:.4f} "
                      f"盈亏: {trade['pnl']:.2f} ({trade['pnl_pct']:.2f}%) [{trade['reason']}]")

        print(f"{'='*60}\n")

    def save_results(self):
        """保存回测结果到数据库"""
        cursor = self.data_provider.connection.cursor()

        # 更新会话状态
        cursor.execute("""
            UPDATE backtest_sessions
            SET status = 'completed'
            WHERE session_id = %s
        """, (self.session_id,))

        # 保存交易记录
        if self.trades:
            insert_sql = """
            INSERT INTO backtest_trades
            (session_id, strategy_id, symbol, direction, quantity,
             entry_price, entry_time, exit_price, exit_time, pnl, pnl_pct, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            for trade in self.trades:
                cursor.execute(insert_sql, (
                    self.session_id, self.strategy_id, trade['symbol'],
                    trade['direction'], trade['quantity'], trade['entry_price'],
                    trade['entry_time'], trade['exit_price'], trade['exit_time'],
                    trade['pnl'], trade['pnl_pct'], trade['reason']
                ))

        self.data_provider.connection.commit()
        cursor.close()

    def close(self):
        """关闭引擎"""
        self.data_provider.close()


# ===================== 示例策略 =====================

# 策略全局状态（记录每个交易对的最后交易时间）
_last_trade_time = {}  # {symbol: datetime}
_last_signal = {}  # {symbol: 'long'/'short'/None}


async def simple_ema_strategy(engine: BacktestEngine, symbol: str, current_time: datetime):
    """
    优化版EMA交叉策略 v3.0

    改进点:
    1. 增加入场冷却期 - 平仓后15分钟内不再开仓
    2. 两种入场模式:
       - 交叉入场: 金叉/死叉发生时立即入场
       - 趋势入场: 趋势已确立(EMA差值>0.3%) + RSI确认
    3. 增加RSI过滤 - 避免超买超卖区域逆势入场
    4. 调整止损止盈比例 - 止损2.5%, 止盈7.5% (1:3盈亏比)
    5. 加入趋势确认 - 价格需要在EMA方向上
    """
    global _last_trade_time, _last_signal

    # ===== 策略参数 =====
    COOLDOWN_MINUTES = 15       # 入场冷却期(分钟) - 缩短冷却时间
    MIN_EMA_DIFF_PCT = 0.3      # 趋势入场的最小EMA差值百分比 - 降低阈值
    STOP_LOSS_PCT = 0.025       # 止损百分比 2.5%
    TAKE_PROFIT_PCT = 0.075     # 止盈百分比 7.5%
    POSITION_SIZE_PCT = 0.10    # 仓位大小 10%
    RSI_OVERSOLD = 35           # RSI超卖 - 放宽条件
    RSI_OVERBOUGHT = 65         # RSI超买 - 放宽条件

    # 获取15分钟K线
    klines = engine.get_klines(symbol, '15m', limit=50)

    if len(klines) < 30:
        return

    # 计算EMA
    closes = [k['close_price'] for k in klines]

    def calc_ema(data, period):
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def calc_rsi(data, period=14):
        """计算RSI"""
        if len(data) < period + 1:
            return 50  # 默认中性
        gains = []
        losses = []
        for i in range(1, len(data)):
            change = data[i] - data[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    ema9 = calc_ema(closes, 9)
    ema26 = calc_ema(closes, 26)
    rsi = calc_rsi(closes)

    # 前一根K线的EMA
    prev_closes = closes[:-1]
    prev_ema9 = calc_ema(prev_closes, 9)
    prev_ema26 = calc_ema(prev_closes, 26)

    # 前两根K线的EMA (用于信号确认)
    prev2_closes = closes[:-2]
    prev2_ema9 = calc_ema(prev2_closes, 9)
    prev2_ema26 = calc_ema(prev2_closes, 26)

    if not all([ema9, ema26, prev_ema9, prev_ema26, prev2_ema9, prev2_ema26]):
        return

    current_price = engine.get_current_price(symbol)

    # 计算EMA差值百分比
    ema_diff_pct = (ema9 - ema26) / ema26 * 100

    # 检查冷却期
    last_trade = _last_trade_time.get(symbol)
    in_cooldown = False
    if last_trade:
        cooldown_delta = timedelta(minutes=COOLDOWN_MINUTES)
        if current_time - last_trade < cooldown_delta:
            in_cooldown = True

    # 检查是否已有持仓
    has_position = symbol in engine.positions

    # ===== 信号判断 =====
    # 金叉: EMA9上穿EMA26
    golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26
    # 死叉: EMA9下穿EMA26
    death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26

    # 多头趋势: EMA9 > EMA26 且差值足够大
    bullish_trend = ema9 > ema26 and ema_diff_pct > MIN_EMA_DIFF_PCT
    # 空头趋势: EMA9 < EMA26 且差值足够大
    bearish_trend = ema9 < ema26 and ema_diff_pct < -MIN_EMA_DIFF_PCT

    # RSI过滤
    rsi_ok_for_long = rsi < RSI_OVERBOUGHT and rsi > RSI_OVERSOLD
    rsi_ok_for_short = rsi > RSI_OVERSOLD and rsi < RSI_OVERBOUGHT

    # 价格确认
    price_above_ema = current_price > ema26
    price_below_ema = current_price < ema26

    # ===== 做多信号 =====
    # 方式1: 金叉入场 (交叉发生时立即入场，不需要EMA差值)
    # 方式2: 趋势入场 (趋势已确立，差值足够大)
    long_signal = (golden_cross and rsi_ok_for_long) or \
                  (bullish_trend and rsi_ok_for_long and price_above_ema and _last_signal.get(symbol) != 'long')

    # ===== 做空信号 =====
    short_signal = (death_cross and rsi_ok_for_short) or \
                   (bearish_trend and rsi_ok_for_short and price_below_ema and _last_signal.get(symbol) != 'short')

    # ===== 交易逻辑 =====

    # 做多
    if long_signal:
        if has_position:
            # 如果持有空仓，先平仓
            if engine.positions[symbol]['direction'] == 'short':
                engine.close_position(symbol, reason='signal_reverse')
                _last_trade_time[symbol] = current_time
                has_position = False

        if not has_position and not in_cooldown:
            # 计算仓位
            position_value = engine.balance * POSITION_SIZE_PCT
            quantity = position_value / current_price

            # 止损止盈
            stop_loss = current_price * (1 - STOP_LOSS_PCT)
            take_profit = current_price * (1 + TAKE_PROFIT_PCT)

            engine.open_position(symbol, 'long', quantity,
                                 stop_loss=stop_loss, take_profit=take_profit)
            _last_trade_time[symbol] = current_time
            _last_signal[symbol] = 'long'

    # 做空
    elif short_signal:
        if has_position:
            # 如果持有多仓，先平仓
            if engine.positions[symbol]['direction'] == 'long':
                engine.close_position(symbol, reason='signal_reverse')
                _last_trade_time[symbol] = current_time
                has_position = False

        if not has_position and not in_cooldown:
            position_value = engine.balance * POSITION_SIZE_PCT
            quantity = position_value / current_price

            stop_loss = current_price * (1 + STOP_LOSS_PCT)
            take_profit = current_price * (1 - TAKE_PROFIT_PCT)

            engine.open_position(symbol, 'short', quantity,
                                 stop_loss=stop_loss, take_profit=take_profit)
            _last_trade_time[symbol] = current_time
            _last_signal[symbol] = 'short'


async def main():
    parser = argparse.ArgumentParser(description='回测运行器')
    parser.add_argument('--session', type=str, required=True, help='回测会话ID')
    parser.add_argument('--strategy', type=int, default=None, help='策略ID (可选)')
    parser.add_argument('--initial-balance', type=float, default=10000, help='初始资金')

    args = parser.parse_args()

    # 加载配置
    config = load_config()

    # 创建回测引擎
    engine = BacktestEngine(config, args.session, args.strategy)
    engine.balance = args.initial_balance
    engine.initial_balance = args.initial_balance

    try:
        # 初始化
        engine.init()

        # 运行回测
        await engine.run_with_strategy(simple_ema_strategy)

    except Exception as e:
        print(f"❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        engine.close()


if __name__ == '__main__':
    asyncio.run(main())
