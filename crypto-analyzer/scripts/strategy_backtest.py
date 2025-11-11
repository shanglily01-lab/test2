#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
策略回测脚本
独立运行，不影响系统

使用方法:
    # 现货回测（默认）
    python scripts/strategy_backtest.py --strategy balanced --symbol BNB/USDT --start 2024-10-10 --end 2024-11-10
    
    # 合约回测
    python scripts/strategy_backtest.py --strategy balanced --symbol BNB/USDT --start 2024-10-10 --end 2024-11-10 --market futures
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import asyncio
import requests

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.strategies.strategy_config import get_strategy_manager
from app.strategies.strategy_analyzer import StrategyBasedAnalyzer
from app.collectors.price_collector import PriceCollector
from app.analyzers.technical_indicators import TechnicalIndicators


class StrategyBacktester:
    """策略回测器"""
    
    def __init__(self, strategy_name: str, symbol: str, start_date: str, end_date: str, 
                 initial_balance: float = 10000.0, market_type: str = 'spot'):
        """
        初始化回测器
        
        Args:
            strategy_name: 策略名称
            symbol: 交易对
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            initial_balance: 初始资金
            market_type: 市场类型 ('spot' 现货 或 'futures' 合约)
        """
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        self.initial_balance = initial_balance
        self.market_type = market_type.lower()  # 'spot' 或 'futures'
        
        if self.market_type not in ['spot', 'futures']:
            raise ValueError("market_type 必须是 'spot' (现货) 或 'futures' (合约)")
        
        # 加载策略
        manager = get_strategy_manager()
        self.strategy = manager.load_strategy(strategy_name)
        if not self.strategy:
            raise ValueError(f"策略不存在: {strategy_name}")
        
        self.analyzer = StrategyBasedAnalyzer(self.strategy)
        self.tech_indicators = TechnicalIndicators()
        
        # 初始化采集器
        if self.market_type == 'futures':
            from app.collectors.binance_futures_collector import BinanceFuturesCollector
            futures_config = {'enabled': True}
            self.collector = BinanceFuturesCollector(futures_config)
        else:
            collector_config = {'enabled': True}
            self.collector = PriceCollector('binance', collector_config)
    
    async def fetch_historical_data(self) -> pd.DataFrame:
        """获取历史数据"""
        days = (self.end_date - self.start_date).days
        total_hours = days * 24 + 7 * 24  # 多获取7天用于计算指标
        limit_per_request = 1000
        
        all_data = []
        start_timestamp = int(self.start_date.timestamp() * 1000)
        end_timestamp = int((self.end_date + timedelta(days=7)).timestamp() * 1000)
        current_timestamp = start_timestamp
        total_fetched = 0
        
        market_name = "合约" if self.market_type == 'futures' else "现货"
        print(f"📊 开始获取 {self.symbol} 的{market_name}历史数据...")
        print(f"   时间范围: {self.start_date.strftime('%Y-%m-%d')} 至 {self.end_date.strftime('%Y-%m-%d')}")
        
        while current_timestamp < end_timestamp and total_fetched < total_hours:
            remaining = min(limit_per_request, total_hours - total_fetched)
            
            # 根据市场类型调用不同的方法
            if self.market_type == 'futures':
                # 合约K线数据获取（需要手动实现时间范围查询）
                import requests
                binance_symbol = self.symbol.replace('/', '')
                url = "https://fapi.binance.com/fapi/v1/klines"
                
                # 币安合约API支持startTime和endTime参数
                params = {
                    'symbol': binance_symbol,
                    'interval': '1h',
                    'limit': min(remaining, 1500),
                    'startTime': current_timestamp,
                    'endTime': end_timestamp
                }
                
                try:
                    response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
                    if response.status_code == 200:
                        klines = response.json()
                        if klines:
                            df = pd.DataFrame(klines, columns=[
                                'open_time', 'open', 'high', 'low', 'close', 'volume',
                                'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
                                'taker_buy_quote_volume', 'ignore'
                            ])
                            df = df[['open_time', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']].copy()
                            df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
                            df['open'] = df['open'].astype(float)
                            df['high'] = df['high'].astype(float)
                            df['low'] = df['low'].astype(float)
                            df['close'] = df['close'].astype(float)
                            df['volume'] = df['volume'].astype(float)
                        else:
                            df = None
                    else:
                        df = None
                except Exception as e:
                    print(f"⚠️  获取合约数据失败: {e}")
                    df = None
            else:
                # 现货K线数据获取
                df = await self.collector.fetch_ohlcv(
                    symbol=self.symbol,
                    timeframe='1h',
                    limit=remaining,
                    since=current_timestamp
                )
            
            if df is None or df.empty:
                break
            
            all_data.append(df)
            total_fetched += len(df)
            print(f"   已获取 {total_fetched} 条数据...", end='\r')
            
            # 更新时间戳（继续获取下一批数据）
            if 'timestamp' in df.columns and len(df) > 0:
                last_timestamp = df['timestamp'].iloc[-1]
                if pd.api.types.is_datetime64_any_dtype(type(last_timestamp)):
                    current_timestamp = int(pd.Timestamp(last_timestamp).timestamp() * 1000) + 3600000
                elif hasattr(last_timestamp, 'timestamp'):
                    current_timestamp = int(last_timestamp.timestamp() * 1000) + 3600000
                else:
                    current_timestamp = int(pd.to_datetime(last_timestamp).timestamp() * 1000) + 3600000
                
                # 如果已经超过结束时间，停止获取
                if current_timestamp >= end_timestamp:
                    break
            else:
                break
            
            # 如果获取的数据少于limit，说明已经到最新了
            if len(df) < limit_per_request:
                break
            
            await asyncio.sleep(0.2)
        
        print(f"\n✅ 共获取 {total_fetched} 条历史数据")
        
        if not all_data:
            raise ValueError(f"无法获取 {self.symbol} 的历史数据")
        
        # 合并数据
        historical_data = pd.concat(all_data, ignore_index=True)
        historical_data = historical_data.drop_duplicates(subset=['timestamp'])
        historical_data = historical_data.sort_values('timestamp').reset_index(drop=True)
        
        # 确保timestamp是datetime类型
        if not pd.api.types.is_datetime64_any_dtype(historical_data['timestamp']):
            historical_data['timestamp'] = pd.to_datetime(historical_data['timestamp'])
        
        # 过滤日期范围
        historical_data = historical_data[
            (historical_data['timestamp'] >= pd.Timestamp(self.start_date)) & 
            (historical_data['timestamp'] <= pd.Timestamp(self.end_date + timedelta(days=1)))
        ]
        
        if historical_data.empty:
            raise ValueError("指定日期范围内没有数据")
        
        # 设置timestamp为索引
        historical_data = historical_data.set_index('timestamp')
        
        return historical_data
    
    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        print("📈 计算技术指标...")
        
        try:
            # RSI
            df['rsi'] = self.tech_indicators.calculate_rsi(df)
            
            # MACD
            df['macd'], df['macd_signal'], df['macd_histogram'] = \
                self.tech_indicators.calculate_macd(df)
            
            # EMA
            df['ema_short'] = self.tech_indicators.calculate_ema(df, self.tech_indicators.ema_short)
            df['ema_long'] = self.tech_indicators.calculate_ema(df, self.tech_indicators.ema_long)
        except Exception as e:
            print(f"⚠️  计算技术指标时出错: {e}，使用默认值")
            df['rsi'] = 50.0
            df['macd_histogram'] = 0.0
            df['ema_short'] = df['close']
            df['ema_long'] = df['close']
        
        return df
    
    def calculate_technical_score(self, row: pd.Series) -> float:
        """计算技术指标得分"""
        rsi = float(row.get('rsi', 50)) if pd.notna(row.get('rsi')) else 50
        macd_hist = float(row.get('macd_histogram', 0)) if pd.notna(row.get('macd_histogram')) else 0
        ema_short = float(row.get('ema_short', row['close'])) if pd.notna(row.get('ema_short')) else row['close']
        ema_long = float(row.get('ema_long', row['close'])) if pd.notna(row.get('ema_long')) else row['close']
        
        current_price = float(row['close'])
        ema_trend = 'up' if current_price > ema_short and ema_short > ema_long else 'down'
        
        technical_score = 50
        
        # RSI评分
        if rsi < 30:
            technical_score += 20
        elif rsi < 40:
            technical_score += 10
        elif rsi > 70:
            technical_score -= 15
        elif rsi > 60:
            technical_score -= 8
        
        # MACD评分
        if macd_hist > 0:
            technical_score += 10
        else:
            technical_score -= 10
        
        # EMA趋势评分
        if ema_trend == 'up':
            technical_score += 8
        else:
            technical_score -= 8
        
        return max(0, min(100, technical_score))
    
    async def run_backtest(self) -> dict:
        """执行回测"""
        market_name = "合约" if self.market_type == 'futures' else "现货"
        print(f"\n{'='*80}")
        print(f"策略回测: {self.strategy_name}")
        print(f"交易对: {self.symbol}")
        print(f"市场类型: {market_name}")
        print(f"时间范围: {self.start_date.strftime('%Y-%m-%d')} 至 {self.end_date.strftime('%Y-%m-%d')}")
        print(f"初始资金: {self.initial_balance} USDT")
        print(f"{'='*80}\n")
        
        # 获取历史数据
        historical_data = await self.fetch_historical_data()
        
        # 计算技术指标
        historical_data = self.calculate_technical_indicators(historical_data)
        
        # 回测模拟
        balance = self.initial_balance
        position = 0.0
        position_price = 0.0
        trades = []
        equity_curve = []
        
        print("🔄 开始回测模拟...")
        
        for idx, row in historical_data.iterrows():
            current_price = float(row['close'])
            timestamp = idx if isinstance(idx, datetime) else pd.to_datetime(idx).to_pydatetime()
            
            # 计算技术指标得分
            technical_score = self.calculate_technical_score(row)
            
            # 简化其他维度得分
            dimension_scores = {
                'technical': technical_score,
                'hyperliquid': 60.0,
                'news': 55.0,
                'funding_rate': 50.0,
                'ethereum': 50.0
            }
            
            # 使用策略分析
            analysis_result = self.analyzer.analyze_symbol(self.symbol, dimension_scores)
            
            if not analysis_result:
                continue
            
            recommendation = analysis_result.get('recommendation', {})
            action = recommendation.get('action', '观望')
            total_score = analysis_result.get('total_score', 50)
            
            # 计算当前权益
            current_equity = balance + (position * current_price if position > 0 else 0)
            equity_curve.append({
                'timestamp': timestamp.isoformat(),
                'equity': current_equity,
                'price': current_price,
                'position': position
            })
            
            # 检查止损止盈
            if position > 0:
                price_change_pct = ((current_price - position_price) / position_price) * 100
                stop_loss_pct = self.strategy.risk_profile.stop_loss
                take_profit_pct = self.strategy.risk_profile.take_profit
                
                # 止损
                if price_change_pct <= -stop_loss_pct:
                    balance = position * current_price
                    trades.append({
                        'timestamp': timestamp.isoformat(),
                        'action': '止损卖出',
                        'price': current_price,
                        'quantity': position,
                        'pnl': (current_price - position_price) * position,
                        'pnl_pct': price_change_pct
                    })
                    position = 0
                    position_price = 0
                    continue
                
                # 止盈
                if price_change_pct >= take_profit_pct:
                    balance = position * current_price
                    trades.append({
                        'timestamp': timestamp.isoformat(),
                        'action': '止盈卖出',
                        'price': current_price,
                        'quantity': position,
                        'pnl': (current_price - position_price) * position,
                        'pnl_pct': price_change_pct
                    })
                    position = 0
                    position_price = 0
                    continue
            
            # 买入信号
            if position == 0 and total_score >= self.strategy.risk_profile.min_signal_strength:
                if '买入' in action or '强烈买入' in action:
                    position_size_pct = recommendation.get('position_size_pct', 20)
                    position_value = balance * (position_size_pct / 100)
                    position = position_value / current_price
                    position_price = current_price
                    balance -= position_value
                    
                    trades.append({
                        'timestamp': timestamp.isoformat(),
                        'action': action,
                        'price': current_price,
                        'quantity': position,
                        'score': total_score
                    })
        
        # 计算最终权益
        final_price = float(historical_data.iloc[-1]['close'])
        final_equity = balance + (position * final_price if position > 0 else 0)
        
        # 计算回测指标
        total_return = ((final_equity - self.initial_balance) / self.initial_balance) * 100
        
        # 计算胜率
        winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl', 0) < 0]
        total_trades = len(winning_trades) + len(losing_trades)
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
        
        # 计算最大回撤
        equity_values = [e['equity'] for e in equity_curve]
        max_drawdown = 0
        peak = self.initial_balance
        for equity in equity_values:
            if equity > peak:
                peak = equity
            drawdown = ((peak - equity) / peak) * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # 计算平均盈亏
        avg_win = sum(t.get('pnl', 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.get('pnl', 0) for t in losing_trades) / len(losing_trades) if losing_trades else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        
        return {
            'strategy_name': self.strategy_name,
            'symbol': self.symbol,
            'start_date': self.start_date.strftime('%Y-%m-%d'),
            'end_date': self.end_date.strftime('%Y-%m-%d'),
            'initial_balance': self.initial_balance,
            'final_equity': final_equity,
            'total_return': round(total_return, 2),
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': round(win_rate, 2),
            'max_drawdown': round(max_drawdown, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
            'trades': trades,
            'equity_curve': equity_curve[::max(1, len(equity_curve)//100)]  # 采样
        }
    
    def print_results(self, result: dict):
        """打印回测结果"""
        print(f"\n{'='*80}")
        print("回测结果")
        print(f"{'='*80}\n")
        
        print(f"策略名称: {result['strategy_name']}")
        print(f"交易对: {result['symbol']}")
        print(f"回测时间: {result['start_date']} 至 {result['end_date']}")
        print(f"\n初始资金: {result['initial_balance']:,.2f} USDT")
        print(f"最终权益: {result['final_equity']:,.2f} USDT")
        print(f"总收益率: {result['total_return']:+.2f}%")
        print(f"\n交易统计:")
        print(f"  总交易次数: {result['total_trades']}")
        print(f"  盈利交易: {result['winning_trades']}")
        print(f"  亏损交易: {result['losing_trades']}")
        print(f"  胜率: {result['win_rate']:.2f}%")
        print(f"\n风险指标:")
        print(f"  最大回撤: -{result['max_drawdown']:.2f}%")
        print(f"  平均盈利: {result['avg_win']:+.2f} USDT")
        print(f"  平均亏损: {result['avg_loss']:+.2f} USDT")
        print(f"  盈亏比: {result['profit_factor']:.2f}")
        
        # 打印交易记录（最近20笔）
        if result['trades']:
            print(f"\n最近交易记录 (显示最后20笔):")
            print(f"{'-'*80}")
            print(f"{'时间':<20} {'操作':<15} {'价格':<12} {'盈亏':<20}")
            print(f"{'-'*80}")
            for trade in result['trades'][-20:]:
                timestamp = datetime.fromisoformat(trade['timestamp']).strftime('%Y-%m-%d %H:%M')
                action = trade['action']
                price = f"{trade['price']:.4f}"
                pnl = trade.get('pnl', 0)
                pnl_pct = trade.get('pnl_pct', 0)
                pnl_str = f"{pnl:+.2f} ({pnl_pct:+.2f}%)" if pnl != 0 else "-"
                print(f"{timestamp:<20} {action:<15} {price:<12} {pnl_str:<20}")
        
        print(f"\n{'='*80}")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='策略回测工具')
    parser.add_argument('--strategy', '-s', type=str, required=True, help='策略名称')
    parser.add_argument('--symbol', type=str, required=True, help='交易对 (如: BNB/USDT)')
    parser.add_argument('--start', type=str, required=True, help='开始日期 (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, required=True, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--balance', '-b', type=float, default=10000.0, help='初始资金 (默认: 10000)')
    parser.add_argument('--market', '-m', type=str, default='spot', choices=['spot', 'futures'], 
                       help='市场类型: spot(现货, 默认) 或 futures(合约)')
    
    args = parser.parse_args()
    
    try:
        # 创建回测器
        backtester = StrategyBacktester(
            strategy_name=args.strategy,
            symbol=args.symbol,
            start_date=args.start,
            end_date=args.end,
            initial_balance=args.balance,
            market_type=args.market
        )
        
        # 执行回测
        result = await backtester.run_backtest()
        
        # 打印结果
        backtester.print_results(result)
        
    except Exception as e:
        print(f"❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())

