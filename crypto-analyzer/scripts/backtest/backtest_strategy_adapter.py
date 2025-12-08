"""
回测策略适配器
将回测引擎与现有的 StrategyExecutor 集成，实现用真实策略回测

用法:
    python scripts/backtest/backtest_strategy_adapter.py --session bt_xxx --strategy-id 1
"""

import sys
import argparse
import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
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


from scripts.backtest.backtest_runner import BacktestEngine, BacktestDataProvider


class BacktestStrategyAdapter:
    """
    回测策略适配器
    将回测数据注入到 StrategyExecutor，实现真实策略回测
    """

    def __init__(self, config: Dict, session_id: str, strategy_id: int):
        self.config = config
        self.session_id = session_id
        self.strategy_id = strategy_id

        db_config = config.get('database', {}).get('mysql', {})
        self.db_config = db_config
        self.data_provider = BacktestDataProvider(db_config, session_id)

        # 回测状态
        self.current_time = None
        self.strategy = None
        self.session_info = None

        # 模拟持仓和交易
        self.positions = {}  # {position_id: position}
        self.trades = []
        self.next_position_id = 1

        # 资金管理
        self.initial_balance = 10000.0
        self.balance = 10000.0
        self.frozen_margin = 0.0

        # 统计
        self.stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0,
            'peak_balance': 10000.0
        }

    def init(self):
        """初始化"""
        self.data_provider.connect()

        # 获取会话信息
        self.session_info = self.data_provider.get_session_info()
        if not self.session_info:
            raise ValueError(f"会话 {self.session_id} 不存在")

        # 加载策略配置
        self.strategy = self._load_strategy()
        if not self.strategy:
            raise ValueError(f"策略 {self.strategy_id} 不存在")

        # 设置初始资金
        capital_config = self.strategy.get('capitalManagement', {})
        if capital_config.get('enabled'):
            self.initial_balance = capital_config.get('totalCapital', 10000)
            self.balance = self.initial_balance

        print(f"\n{'='*60}")
        print(f"📊 回测策略适配器初始化")
        print(f"  会话ID: {self.session_id}")
        print(f"  策略ID: {self.strategy_id}")
        print(f"  策略名称: {self.strategy.get('name', 'Unknown')}")
        print(f"  初始资金: {self.initial_balance}")
        print(f"  时间范围: {self.session_info['start_time']} ~ {self.session_info['end_time']}")
        print(f"{'='*60}\n")

        # 预加载数据
        for symbol in self.session_info['symbols']:
            self.data_provider.load_prices(symbol)
            for tf in self.session_info['timeframes']:
                self.data_provider.load_klines(symbol, tf)

        self.current_time = self.session_info['start_time']

    def _load_strategy(self) -> Optional[Dict]:
        """从数据库加载策略配置"""
        cursor = self.data_provider.connection.cursor()
        cursor.execute("""
            SELECT * FROM trading_strategies WHERE id = %s
        """, (self.strategy_id,))
        row = cursor.fetchone()
        cursor.close()

        if row:
            return {
                'id': row['id'],
                'name': row['name'],
                **json.loads(row['config'])
            }
        return None

    def get_klines_for_strategy(self, symbol: str, timeframe: str, limit: int = 100) -> List[Dict]:
        """
        为策略提供K线数据

        返回格式与数据库查询结果兼容
        """
        klines = self.data_provider.get_klines_at_time(symbol, timeframe, self.current_time, limit)

        # 转换为策略执行器期望的格式
        return [{
            'symbol': k['symbol'],
            'timeframe': k['timeframe'],
            'timestamp': k['timestamp'],
            'open_time': k['open_time'],
            'open_price': k['open_price'],
            'high_price': k['high_price'],
            'low_price': k['low_price'],
            'close_price': k['close_price'],
            'volume': k.get('volume', 0)
        } for k in klines]

    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        price_data = self.data_provider.get_price_at_time(symbol, self.current_time)
        return price_data['price'] if price_data else 0.0

    def calculate_indicators(self, klines: List[Dict]) -> Dict:
        """计算技术指标"""
        if not klines or len(klines) < 26:
            return {}

        closes = [k['close_price'] for k in klines]
        highs = [k['high_price'] for k in klines]
        lows = [k['low_price'] for k in klines]

        def calc_ema(data, period):
            if len(data) < period:
                return None
            multiplier = 2 / (period + 1)
            ema = sum(data[:period]) / period
            for price in data[period:]:
                ema = (price - ema) * multiplier + ema
            return ema

        def calc_ma(data, period):
            if len(data) < period:
                return None
            return sum(data[-period:]) / period

        def calc_rsi(data, period=14):
            if len(data) < period + 1:
                return None
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
        ma10 = calc_ma(closes, 10)
        ema10 = calc_ema(closes, 10)
        rsi = calc_rsi(closes)

        # 计算前一根K线的EMA
        prev_closes = closes[:-1]
        prev_ema9 = calc_ema(prev_closes, 9) if len(prev_closes) >= 9 else None
        prev_ema26 = calc_ema(prev_closes, 26) if len(prev_closes) >= 26 else None

        return {
            'ema9': ema9,
            'ema26': ema26,
            'ma10': ma10,
            'ema10': ema10,
            'rsi': rsi,
            'prev_ema9': prev_ema9,
            'prev_ema26': prev_ema26,
            'current_price': closes[-1] if closes else None
        }

    def detect_signal(self, symbol: str, indicators: Dict) -> Optional[str]:
        """
        检测交易信号

        Returns:
            'golden' - 金叉
            'death' - 死叉
            None - 无信号
        """
        ema9 = indicators.get('ema9')
        ema26 = indicators.get('ema26')
        prev_ema9 = indicators.get('prev_ema9')
        prev_ema26 = indicators.get('prev_ema26')

        if not all([ema9, ema26, prev_ema9, prev_ema26]):
            return None

        # 金叉: EMA9 上穿 EMA26
        if prev_ema9 <= prev_ema26 and ema9 > ema26:
            return 'golden'

        # 死叉: EMA9 下穿 EMA26
        if prev_ema9 >= prev_ema26 and ema9 < ema26:
            return 'death'

        return None

    def open_position(self, symbol: str, direction: str, quantity: float,
                      entry_price: float, stop_loss: float = None,
                      take_profit: float = None) -> int:
        """开仓"""
        position_id = self.next_position_id
        self.next_position_id += 1

        # 计算保证金 (假设10倍杠杆)
        leverage = self.strategy.get('leverage', 10)
        margin = (entry_price * quantity) / leverage

        position = {
            'id': position_id,
            'symbol': symbol,
            'direction': direction,
            'quantity': quantity,
            'entry_price': entry_price,
            'entry_time': self.current_time,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'margin': margin,
            'leverage': leverage,
            'unrealized_pnl': 0.0
        }

        self.positions[position_id] = position
        self.frozen_margin += margin
        self.balance -= margin

        print(f"  📈 开仓 #{position_id}: {symbol} {direction.upper()} "
              f"{quantity:.4f} @ {entry_price:.4f}, 保证金: {margin:.2f}")

        return position_id

    def close_position(self, position_id: int, exit_price: float = None,
                       reason: str = 'manual') -> Optional[Dict]:
        """平仓"""
        if position_id not in self.positions:
            return None

        position = self.positions[position_id]

        if exit_price is None:
            exit_price = self.get_current_price(position['symbol'])

        # 计算盈亏
        if position['direction'] == 'long':
            pnl = (exit_price - position['entry_price']) * position['quantity']
        else:
            pnl = (position['entry_price'] - exit_price) * position['quantity']

        # 记录交易
        trade = {
            'position_id': position_id,
            'symbol': position['symbol'],
            'direction': position['direction'],
            'quantity': position['quantity'],
            'entry_price': position['entry_price'],
            'entry_time': position['entry_time'],
            'exit_price': exit_price,
            'exit_time': self.current_time,
            'pnl': pnl,
            'pnl_pct': pnl / position['margin'] * 100 if position['margin'] > 0 else 0,
            'reason': reason
        }
        self.trades.append(trade)

        # 更新资金
        self.frozen_margin -= position['margin']
        self.balance += position['margin'] + pnl

        # 更新统计
        self.stats['total_trades'] += 1
        self.stats['total_pnl'] += pnl
        if pnl > 0:
            self.stats['winning_trades'] += 1
        else:
            self.stats['losing_trades'] += 1

        # 更新最大回撤
        total_equity = self.balance + self.frozen_margin
        if total_equity > self.stats['peak_balance']:
            self.stats['peak_balance'] = total_equity
        drawdown = (self.stats['peak_balance'] - total_equity) / self.stats['peak_balance'] * 100
        if drawdown > self.stats['max_drawdown']:
            self.stats['max_drawdown'] = drawdown

        # 删除持仓
        del self.positions[position_id]

        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        print(f"  {pnl_emoji} 平仓 #{position_id}: {position['symbol']} @ {exit_price:.4f}, "
              f"盈亏: {pnl:.2f} ({trade['pnl_pct']:.2f}%), 原因: {reason}")

        return trade

    def check_stop_loss_take_profit(self):
        """检查所有持仓的止损止盈"""
        for position_id in list(self.positions.keys()):
            position = self.positions[position_id]
            current_price = self.get_current_price(position['symbol'])

            if position['direction'] == 'long':
                if position['stop_loss'] and current_price <= position['stop_loss']:
                    self.close_position(position_id, position['stop_loss'], 'stop_loss')
                elif position['take_profit'] and current_price >= position['take_profit']:
                    self.close_position(position_id, position['take_profit'], 'take_profit')
            else:
                if position['stop_loss'] and current_price >= position['stop_loss']:
                    self.close_position(position_id, position['stop_loss'], 'stop_loss')
                elif position['take_profit'] and current_price <= position['take_profit']:
                    self.close_position(position_id, position['take_profit'], 'take_profit')

    def has_position(self, symbol: str, direction: str = None) -> bool:
        """检查是否有持仓"""
        for pos in self.positions.values():
            if pos['symbol'] == symbol:
                if direction is None or pos['direction'] == direction:
                    return True
        return False

    def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        """获取某个交易对的持仓"""
        for pos in self.positions.values():
            if pos['symbol'] == symbol:
                return pos
        return None

    async def run(self):
        """运行回测"""
        symbols = self.session_info['symbols']
        strategy_symbols = self.strategy.get('symbols', symbols)

        # 只回测策略配置的交易对
        test_symbols = [s for s in symbols if s in strategy_symbols]

        if not test_symbols:
            print("❌ 没有匹配的交易对")
            return

        # 获取所有时间点
        all_timestamps = set()
        for symbol in test_symbols:
            timestamps = self.data_provider.get_all_timestamps(symbol)
            all_timestamps.update(timestamps)

        sorted_timestamps = sorted(all_timestamps)

        # 获取策略参数
        buy_timeframe = self.strategy.get('buyTimeframe', '15m')
        stop_loss_pct = self.strategy.get('stopLoss', 2) / 100
        take_profit_pct = self.strategy.get('takeProfit', 6) / 100
        position_size_pct = self.strategy.get('positionSize', 10) / 100

        print(f"\n📈 开始回测")
        print(f"  交易对: {', '.join(test_symbols)}")
        print(f"  时间周期: {buy_timeframe}")
        print(f"  止损: {stop_loss_pct*100}%, 止盈: {take_profit_pct*100}%")
        print(f"  仓位: {position_size_pct*100}%")
        print(f"  时间点数: {len(sorted_timestamps)}")
        print()

        last_signal_time = {}  # {symbol: datetime} 防止重复信号
        progress_interval = max(1, len(sorted_timestamps) // 20)

        for i, timestamp in enumerate(sorted_timestamps):
            self.current_time = timestamp

            # 检查止损止盈
            self.check_stop_loss_take_profit()

            # 每个交易对处理信号
            for symbol in test_symbols:
                # 获取K线数据
                klines = self.get_klines_for_strategy(symbol, buy_timeframe, 50)
                if len(klines) < 30:
                    continue

                # 计算指标
                indicators = self.calculate_indicators(klines)

                # 检测信号
                signal = self.detect_signal(symbol, indicators)

                if signal:
                    # 防止同一根K线重复信号
                    last_time = last_signal_time.get(symbol)
                    if last_time and (timestamp - last_time).total_seconds() < 60 * 15:
                        continue

                    current_price = self.get_current_price(symbol)
                    position = self.get_position_by_symbol(symbol)

                    if signal == 'golden':
                        # 金叉 -> 做多
                        if position and position['direction'] == 'short':
                            self.close_position(position['id'], current_price, 'signal_reverse')
                            position = None

                        if not position:
                            # 计算仓位
                            position_value = self.balance * position_size_pct
                            quantity = position_value / current_price

                            stop_loss = current_price * (1 - stop_loss_pct)
                            take_profit = current_price * (1 + take_profit_pct)

                            self.open_position(symbol, 'long', quantity, current_price,
                                               stop_loss, take_profit)
                            last_signal_time[symbol] = timestamp

                    elif signal == 'death':
                        # 死叉 -> 做空
                        if position and position['direction'] == 'long':
                            self.close_position(position['id'], current_price, 'signal_reverse')
                            position = None

                        if not position:
                            position_value = self.balance * position_size_pct
                            quantity = position_value / current_price

                            stop_loss = current_price * (1 + stop_loss_pct)
                            take_profit = current_price * (1 - take_profit_pct)

                            self.open_position(symbol, 'short', quantity, current_price,
                                               stop_loss, take_profit)
                            last_signal_time[symbol] = timestamp

            # 进度报告
            if i > 0 and i % progress_interval == 0:
                progress = i / len(sorted_timestamps) * 100
                equity = self.balance + self.frozen_margin
                print(f"  ⏳ {progress:.0f}% | {timestamp} | 权益: {equity:.2f} | 持仓: {len(self.positions)}")

        # 平掉所有持仓
        print("\n📊 回测结束，平掉剩余持仓...")
        for position_id in list(self.positions.keys()):
            self.close_position(position_id, reason='backtest_end')

        self.print_results()
        self.save_results()

    def print_results(self):
        """打印回测结果"""
        final_equity = self.balance + self.frozen_margin
        total_return = (final_equity / self.initial_balance - 1) * 100

        print(f"\n{'='*60}")
        print(f"📊 回测结果 - {self.strategy.get('name', 'Unknown')}")
        print(f"{'='*60}")
        print(f"  初始资金: {self.initial_balance:.2f}")
        print(f"  最终权益: {final_equity:.2f}")
        print(f"  总收益率: {total_return:.2f}%")
        print(f"  总盈亏: {self.stats['total_pnl']:.2f}")
        print(f"  最大回撤: {self.stats['max_drawdown']:.2f}%")
        print(f"\n  交易统计:")
        print(f"    总交易数: {self.stats['total_trades']}")
        print(f"    盈利交易: {self.stats['winning_trades']}")
        print(f"    亏损交易: {self.stats['losing_trades']}")

        if self.stats['total_trades'] > 0:
            win_rate = self.stats['winning_trades'] / self.stats['total_trades'] * 100
            avg_pnl = self.stats['total_pnl'] / self.stats['total_trades']
            print(f"    胜率: {win_rate:.1f}%")
            print(f"    平均盈亏: {avg_pnl:.2f}")

        # 按交易对统计
        symbol_stats = {}
        for trade in self.trades:
            symbol = trade['symbol']
            if symbol not in symbol_stats:
                symbol_stats[symbol] = {'count': 0, 'pnl': 0, 'wins': 0}
            symbol_stats[symbol]['count'] += 1
            symbol_stats[symbol]['pnl'] += trade['pnl']
            if trade['pnl'] > 0:
                symbol_stats[symbol]['wins'] += 1

        if symbol_stats:
            print(f"\n  按交易对统计:")
            for symbol, stats in sorted(symbol_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
                wr = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
                print(f"    {symbol}: {stats['count']}笔, 盈亏: {stats['pnl']:.2f}, 胜率: {wr:.1f}%")

        # 最近交易
        if self.trades:
            print(f"\n  最近5笔交易:")
            for trade in self.trades[-5:]:
                pnl_emoji = "🟢" if trade['pnl'] > 0 else "🔴"
                print(f"    {pnl_emoji} {trade['symbol']} {trade['direction']} "
                      f"{trade['entry_price']:.4f} → {trade['exit_price']:.4f} "
                      f"| {trade['pnl']:.2f} ({trade['pnl_pct']:.1f}%) [{trade['reason']}]")

        print(f"{'='*60}\n")

    def save_results(self):
        """保存回测结果到数据库"""
        cursor = self.data_provider.connection.cursor()

        # 创建回测交易表（如果不存在）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backtest_trades (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(50) NOT NULL,
                strategy_id INT NULL,
                symbol VARCHAR(20) NOT NULL,
                direction VARCHAR(10) NOT NULL,
                quantity DECIMAL(20, 8) NOT NULL,
                entry_price DECIMAL(18, 8) NOT NULL,
                entry_time DATETIME NOT NULL,
                exit_price DECIMAL(18, 8) NOT NULL,
                exit_time DATETIME NOT NULL,
                pnl DECIMAL(18, 8) NOT NULL,
                pnl_pct DECIMAL(10, 4) NOT NULL,
                reason VARCHAR(50) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                KEY idx_session_strategy (session_id, strategy_id),
                KEY idx_symbol (symbol)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 创建回测结果表（如果不存在）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(50) NOT NULL,
                strategy_id INT NULL,
                strategy_name VARCHAR(100) NULL,
                initial_balance DECIMAL(18, 2) NOT NULL,
                final_equity DECIMAL(18, 2) NOT NULL,
                total_return_pct DECIMAL(10, 4) NOT NULL,
                total_pnl DECIMAL(18, 2) NOT NULL,
                max_drawdown_pct DECIMAL(10, 4) NOT NULL,
                total_trades INT NOT NULL,
                winning_trades INT NOT NULL,
                losing_trades INT NOT NULL,
                win_rate DECIMAL(10, 4) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                KEY idx_session (session_id),
                UNIQUE KEY uk_session_strategy (session_id, strategy_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 保存交易记录
        if self.trades:
            for trade in self.trades:
                cursor.execute("""
                    INSERT INTO backtest_trades
                    (session_id, strategy_id, symbol, direction, quantity,
                     entry_price, entry_time, exit_price, exit_time, pnl, pnl_pct, reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.session_id, self.strategy_id, trade['symbol'],
                    trade['direction'], trade['quantity'], trade['entry_price'],
                    trade['entry_time'], trade['exit_price'], trade['exit_time'],
                    trade['pnl'], trade['pnl_pct'], trade['reason']
                ))

        # 保存回测结果
        final_equity = self.balance + self.frozen_margin
        total_return = (final_equity / self.initial_balance - 1) * 100
        win_rate = self.stats['winning_trades'] / self.stats['total_trades'] * 100 \
            if self.stats['total_trades'] > 0 else 0

        cursor.execute("""
            INSERT INTO backtest_results
            (session_id, strategy_id, strategy_name, initial_balance, final_equity,
             total_return_pct, total_pnl, max_drawdown_pct, total_trades,
             winning_trades, losing_trades, win_rate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            final_equity = VALUES(final_equity),
            total_return_pct = VALUES(total_return_pct),
            total_pnl = VALUES(total_pnl),
            max_drawdown_pct = VALUES(max_drawdown_pct),
            total_trades = VALUES(total_trades),
            winning_trades = VALUES(winning_trades),
            losing_trades = VALUES(losing_trades),
            win_rate = VALUES(win_rate)
        """, (
            self.session_id, self.strategy_id, self.strategy.get('name'),
            self.initial_balance, final_equity, total_return, self.stats['total_pnl'],
            self.stats['max_drawdown'], self.stats['total_trades'],
            self.stats['winning_trades'], self.stats['losing_trades'], win_rate
        ))

        # 更新会话状态
        cursor.execute("""
            UPDATE backtest_sessions SET status = 'completed' WHERE session_id = %s
        """, (self.session_id,))

        self.data_provider.connection.commit()
        cursor.close()

        print("✅ 回测结果已保存到数据库")

    def close(self):
        """关闭"""
        self.data_provider.close()


async def main():
    parser = argparse.ArgumentParser(description='回测策略适配器')
    parser.add_argument('--session', type=str, required=True, help='回测会话ID')
    parser.add_argument('--strategy-id', type=int, required=True, help='策略ID')
    parser.add_argument('--initial-balance', type=float, default=None, help='初始资金（覆盖策略配置）')

    args = parser.parse_args()

    # 加载配置
    config = load_config()

    # 创建适配器
    adapter = BacktestStrategyAdapter(config, args.session, args.strategy_id)

    if args.initial_balance:
        adapter.initial_balance = args.initial_balance
        adapter.balance = args.initial_balance

    try:
        adapter.init()
        await adapter.run()
    except Exception as e:
        print(f"❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        adapter.close()


if __name__ == '__main__':
    asyncio.run(main())
