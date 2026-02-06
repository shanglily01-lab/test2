#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
12小时复盘分析系统
每12小时对市场走势、信号捕捉、交易表现进行全面评估
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pymysql
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import json

class RetrospectiveAnalyzer:
    """12小时复盘分析器"""

    def __init__(self):
        self.db_config = {
            'host': '13.212.252.171',
            'port': 3306,
            'user': 'admin',
            'password': 'Tonny@1000',
            'database': 'binance-data'
        }

    def _get_connection(self):
        return pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)

    def analyze_12h_market_trend(self) -> Dict:
        """
        分析过去12小时的市场走势
        返回: Big4币种的价格、成交量、方向判断
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        big4_symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']
        market_analysis = {}

        for symbol in big4_symbols:
            # 获取12小时的15M K线 (48根)
            cursor.execute("""
                SELECT
                    open_price, high_price, low_price, close_price, volume,
                    open_time
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '15m'
                AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 12 HOUR)) * 1000
                ORDER BY open_time ASC
            """, (symbol,))

            klines = cursor.fetchall()

            if not klines:
                continue

            # 计算关键指标
            first_open = float(klines[0]['open_price'])
            last_close = float(klines[-1]['close_price'])
            period_high = max(float(k['high_price']) for k in klines)
            period_low = min(float(k['low_price']) for k in klines)
            total_volume = sum(float(k['volume']) for k in klines)

            # 价格变化
            price_change = last_close - first_open
            price_change_pct = (price_change / first_open) * 100

            # 波动幅度
            volatility = ((period_high - period_low) / period_low) * 100

            # 阴阳K线统计
            bullish_count = sum(1 for k in klines if float(k['close_price']) > float(k['open_price']))
            bearish_count = len(klines) - bullish_count

            # 方向判断
            if price_change_pct > 3 and bullish_count > bearish_count * 1.2:
                direction = "强势上涨"
            elif price_change_pct > 1:
                direction = "温和上涨"
            elif price_change_pct < -3 and bearish_count > bullish_count * 1.2:
                direction = "强势下跌"
            elif price_change_pct < -1:
                direction = "温和下跌"
            else:
                direction = "震荡盘整"

            # 找到最高点和最低点的时间
            max_idx = next(i for i, k in enumerate(klines) if float(k['high_price']) == period_high)
            min_idx = next(i for i, k in enumerate(klines) if float(k['low_price']) == period_low)

            market_analysis[symbol] = {
                'first_open': first_open,
                'last_close': last_close,
                'period_high': period_high,
                'period_low': period_low,
                'price_change': price_change,
                'price_change_pct': price_change_pct,
                'volatility': volatility,
                'total_volume': total_volume,
                'bullish_count': bullish_count,
                'bearish_count': bearish_count,
                'direction': direction,
                'high_time': klines[max_idx]['open_time'],
                'low_time': klines[min_idx]['open_time'],
                'total_klines': len(klines)
            }

        cursor.close()
        conn.close()

        return market_analysis

    def analyze_big4_signal_capture(self) -> List[Dict]:
        """
        分析过去12小时Big4趋势检测的信号捕捉情况
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                created_at,
                overall_signal,
                signal_strength,
                bullish_count,
                bearish_count,
                recommendation,
                btc_signal, btc_strength,
                eth_signal, eth_strength,
                bnb_signal, bnb_strength,
                sol_signal, sol_strength
            FROM big4_trend_history
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 12 HOUR)
            ORDER BY created_at ASC
        """)

        signals = cursor.fetchall()

        cursor.close()
        conn.close()

        return signals

    def analyze_trading_performance(self) -> Dict:
        """
        分析过去12小时的交易表现
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 查询12小时内开仓并平仓的订单
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                entry_price,
                mark_price,
                quantity,
                realized_pnl,
                unrealized_pnl_pct as pnl_pct,
                entry_signal_type,
                entry_score,
                entry_reason,
                open_time,
                close_time,
                holding_hours,
                notes
            FROM futures_positions
            WHERE open_time >= DATE_SUB(NOW(), INTERVAL 12 HOUR)
            AND status = 'closed'
            ORDER BY open_time ASC
        """)

        trades = cursor.fetchall()

        if not trades:
            cursor.close()
            conn.close()
            return {
                'total_trades': 0,
                'profit_trades': 0,
                'loss_trades': 0,
                'total_pnl': 0,
                'win_rate': 0,
                'trades': []
            }

        total_pnl = 0
        profit_trades = []
        loss_trades = []

        for t in trades:
            pnl = float(t['realized_pnl']) if t['realized_pnl'] else 0
            total_pnl += pnl

            trade_info = {
                'symbol': t['symbol'],
                'side': t['position_side'],
                'entry_price': float(t['entry_price']),
                'pnl': pnl,
                'pnl_pct': float(t['pnl_pct'] or 0),
                'signal': t['entry_signal_type'],
                'score': t['entry_score'],
                'reason': t['entry_reason'],
                'open_time': t['open_time'],
                'close_time': t['close_time'],
                'holding_hours': t['holding_hours']
            }

            if pnl < 0:
                loss_trades.append(trade_info)
            else:
                profit_trades.append(trade_info)

        cursor.close()
        conn.close()

        return {
            'total_trades': len(trades),
            'profit_trades': len(profit_trades),
            'loss_trades': len(loss_trades),
            'total_pnl': total_pnl,
            'win_rate': (len(profit_trades) / len(trades) * 100) if trades else 0,
            'profit_list': profit_trades,
            'loss_list': loss_trades
        }

    def analyze_loss_reasons(self, loss_trades: List[Dict], market_analysis: Dict, big4_signals: List[Dict]) -> List[Dict]:
        """
        深度分析亏损单的原因

        分析维度:
        1. 开仓时机是否合理 (与市场走势对比)
        2. Big4信号是否准确
        3. 持仓时长是否合理
        4. 止损设置是否合理
        """
        loss_analysis = []

        for trade in loss_trades:
            analysis = {
                'trade': trade,
                'issues': [],
                'recommendations': []
            }

            symbol_base = trade['symbol'].split('/')[0]
            side = trade['side']
            open_time = trade['open_time']

            # 1. 检查开仓时机与市场走势的匹配度
            # 找到对应的Big4币种市场分析
            market_key = None
            for key in market_analysis.keys():
                if symbol_base in ['BTC', 'ETH', 'BNB', 'SOL'] and symbol_base in key:
                    market_key = key
                    break

            if market_key:
                market = market_analysis[market_key]

                # SHORT在上涨市场 or LONG在下跌市场
                if side == 'SHORT' and market['direction'] in ['强势上涨', '温和上涨']:
                    analysis['issues'].append(f"逆势做空: 市场方向为{market['direction']},但开了SHORT单")
                    analysis['recommendations'].append("应等待上涨结束或见顶信号再做空")

                elif side == 'LONG' and market['direction'] in ['强势下跌', '温和下跌']:
                    analysis['issues'].append(f"逆势做多: 市场方向为{market['direction']},但开了LONG单")
                    analysis['recommendations'].append("应等待下跌结束或触底信号再做多")

            # 2. 检查Big4信号的准确性
            # 找到开仓时间最接近的Big4信号
            closest_signal = None
            min_time_diff = float('inf')

            for signal in big4_signals:
                time_diff = abs((signal['created_at'] - open_time).total_seconds())
                if time_diff < min_time_diff:
                    min_time_diff = time_diff
                    closest_signal = signal

            if closest_signal and min_time_diff < 600:  # 10分钟内
                big4_signal = closest_signal['overall_signal']
                big4_strength = float(closest_signal['signal_strength'])

                # SHORT时Big4应该是BEARISH
                if side == 'SHORT' and big4_signal != 'BEARISH':
                    analysis['issues'].append(f"Big4信号不支持: 开SHORT时Big4={big4_signal}({big4_strength:.0f})")
                    analysis['recommendations'].append("只在Big4=BEARISH且强度>60时做空")

                # LONG时Big4应该是BULLISH
                elif side == 'LONG' and big4_signal != 'BULLISH':
                    analysis['issues'].append(f"Big4信号不支持: 开LONG时Big4={big4_signal}({big4_strength:.0f})")
                    analysis['recommendations'].append("只在Big4=BULLISH且强度>60时做多")

                # 信号强度不足
                if big4_strength < 50:
                    analysis['issues'].append(f"Big4信号强度不足: {big4_strength:.0f} < 50")
                    analysis['recommendations'].append("避免在Big4信号强度<50时开仓")

            # 3. 分析信号类型是否可靠
            signal_type = trade['signal']
            if signal_type:
                # momentum_down_3pct在反弹中很危险
                if 'momentum_down_3pct' in signal_type and side == 'SHORT':
                    if market_key and 'down' not in market_analysis[market_key]['direction'].lower():
                        analysis['issues'].append("动量信号滞后: momentum_down_3pct在反弹中触发")
                        analysis['recommendations'].append("添加Big4触底保护,避免反弹中做空")

                # breakdown_short在震荡市容易假突破
                if 'breakdown_short' in signal_type and side == 'SHORT':
                    if market_key and '震荡' in market_analysis[market_key]['direction']:
                        analysis['issues'].append("假突破风险: 震荡市中的breakdown信号")
                        analysis['recommendations'].append("震荡市应该使用布林带回归策略")

            # 4. 持仓时长分析
            holding_hours = trade['holding_hours'] or 0
            if holding_hours < 1:
                analysis['issues'].append(f"快速止损: 持仓仅{holding_hours}小时即止损")
                analysis['recommendations'].append("检查是否开仓价格不佳或止损设置过紧")

            loss_analysis.append(analysis)

        return loss_analysis

    def generate_report(self) -> str:
        """生成完整的12小时复盘报告"""

        print("\n" + "=" * 100)
        print(f"12小时复盘分析报告")
        print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"分析区间: {(datetime.now() - timedelta(hours=12)).strftime('%Y-%m-%d %H:%M')} ~ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 100)

        # 1. 市场走势分析
        print("\n[第一部分] 12小时市场走势分析")
        print("-" * 100)

        market_analysis = self.analyze_12h_market_trend()

        for symbol, data in market_analysis.items():
            print(f"\n{symbol}:")
            print(f"  价格变化: {data['first_open']:.2f} → {data['last_close']:.2f} ({data['price_change_pct']:+.2f}%)")
            print(f"  区间高低: {data['period_low']:.2f} - {data['period_high']:.2f} (波动{data['volatility']:.2f}%)")
            print(f"  K线统计: {data['bullish_count']}阳 / {data['bearish_count']}阴 (共{data['total_klines']}根)")
            print(f"  方向判断: {data['direction']}")
            print(f"  成交量: {data['total_volume']:.2f}")

        # 2. Big4信号捕捉分析
        print("\n[第二部分] Big4信号捕捉分析")
        print("-" * 100)

        big4_signals = self.analyze_big4_signal_capture()

        if big4_signals:
            print(f"\n12小时内Big4更新{len(big4_signals)}次 (每5分钟检测):")

            # 统计信号分布
            signal_dist = {'BULLISH': 0, 'BEARISH': 0, 'NEUTRAL': 0}
            for sig in big4_signals:
                signal_dist[sig['overall_signal']] = signal_dist.get(sig['overall_signal'], 0) + 1

            print(f"  信号分布: BULLISH={signal_dist['BULLISH']}次, BEARISH={signal_dist['BEARISH']}次, NEUTRAL={signal_dist['NEUTRAL']}次")

            # 显示最近5次信号
            print(f"\n  最近5次信号:")
            for sig in big4_signals[-5:]:
                print(f"    {sig['created_at']} | {sig['overall_signal']} (强度{sig['signal_strength']:.0f}) | "
                      f"BTC:{sig['btc_signal']} ETH:{sig['eth_signal']} BNB:{sig['bnb_signal']} SOL:{sig['sol_signal']}")

        # 3. 交易表现分析
        print("\n[第三部分] 交易表现分析")
        print("-" * 100)

        performance = self.analyze_trading_performance()

        print(f"\n交易统计:")
        print(f"  总交易数: {performance['total_trades']}笔")
        print(f"  盈利单: {performance['profit_trades']}笔")
        print(f"  亏损单: {performance['loss_trades']}笔")
        print(f"  胜率: {performance['win_rate']:.1f}%")
        print(f"  总盈亏: {performance['total_pnl']:.2f} USDT")

        # 4. 亏损单深度分析
        if performance['loss_trades'] > 0:
            print("\n[第四部分] 亏损单深度分析")
            print("-" * 100)

            loss_analysis = self.analyze_loss_reasons(
                performance['loss_list'],
                market_analysis,
                big4_signals
            )

            print(f"\n分析{len(loss_analysis)}笔亏损单:\n")

            for i, analysis in enumerate(loss_analysis[:10], 1):  # 只显示前10笔
                trade = analysis['trade']
                print(f"{i}. {trade['symbol']} {trade['side']} | 亏损{trade['pnl']:.2f}U ({trade['pnl_pct']:.2f}%)")
                print(f"   开仓: {trade['open_time']} | 信号: {trade['signal'][:50]}")

                if analysis['issues']:
                    print(f"   问题诊断:")
                    for issue in analysis['issues']:
                        print(f"     - {issue}")

                if analysis['recommendations']:
                    print(f"   改进建议:")
                    for rec in analysis['recommendations']:
                        print(f"     → {rec}")
                print()

        # 5. 总结与建议
        print("\n[第五部分] 总结与改进建议")
        print("-" * 100)

        # 综合判断
        overall_market_direction = "震荡"
        bullish_count = sum(1 for m in market_analysis.values() if '上涨' in m['direction'])
        bearish_count = sum(1 for m in market_analysis.values() if '下跌' in m['direction'])

        if bullish_count >= 3:
            overall_market_direction = "多头"
        elif bearish_count >= 3:
            overall_market_direction = "空头"

        print(f"\n市场整体方向: {overall_market_direction}")
        print(f"交易表现评价: ", end="")

        if performance['win_rate'] >= 60:
            print("优秀 (胜率≥60%)")
        elif performance['win_rate'] >= 40:
            print("良好 (胜率40-60%)")
        elif performance['win_rate'] >= 20:
            print("一般 (胜率20-40%)")
        else:
            print("较差 (胜率<20%)")

        print(f"\n关键改进点:")

        # 基于亏损分析提取共性问题
        if performance['loss_trades'] > 0:
            loss_analysis = self.analyze_loss_reasons(
                performance['loss_list'],
                market_analysis,
                big4_signals
            )

            # 统计最常见的问题
            all_issues = []
            for analysis in loss_analysis:
                all_issues.extend(analysis['issues'])

            issue_keywords = {}
            for issue in all_issues:
                if '逆势' in issue:
                    issue_keywords['逆势交易'] = issue_keywords.get('逆势交易', 0) + 1
                if 'Big4信号' in issue:
                    issue_keywords['Big4信号不匹配'] = issue_keywords.get('Big4信号不匹配', 0) + 1
                if '滞后' in issue or '反弹' in issue:
                    issue_keywords['信号滞后'] = issue_keywords.get('信号滞后', 0) + 1
                if '震荡' in issue or '假突破' in issue:
                    issue_keywords['震荡市误判'] = issue_keywords.get('震荡市误判', 0) + 1

            if issue_keywords:
                sorted_issues = sorted(issue_keywords.items(), key=lambda x: x[1], reverse=True)
                for issue, count in sorted_issues[:3]:
                    print(f"  {count}. {issue} (出现{count}次)")

        print("\n" + "=" * 100)

        # 保存到数据库
        self.save_to_database(market_analysis, big4_signals, performance, loss_analysis, overall_market_direction)

        return "Report generated successfully"

    def save_to_database(self, market_analysis, big4_signals, performance, loss_analysis, overall_market_direction):
        """保存分析结果到数据库"""
        conn = None
        try:
            import json

            # 计算时间区间
            period_end = datetime.now()
            period_start = period_end - timedelta(hours=12)

            # 准备统计数据
            counter_trend_count = 0
            signal_mismatch_count = 0
            signal_lag_count = 0
            false_breakout_count = 0

            for analysis in loss_analysis:
                for issue in analysis['issues']:
                    if '逆势' in issue:
                        counter_trend_count += 1
                    if 'Big4信号' in issue:
                        signal_mismatch_count += 1
                    if '滞后' in issue or '反弹' in issue:
                        signal_lag_count += 1
                    if '震荡' in issue or '假突破' in issue:
                        false_breakout_count += 1

            # 确定表现评级
            if performance['win_rate'] >= 60:
                performance_rating = "优秀"
            elif performance['win_rate'] >= 40:
                performance_rating = "良好"
            elif performance['win_rate'] >= 20:
                performance_rating = "一般"
            else:
                performance_rating = "较差"

            # 准备JSON数据
            market_analysis_json = json.dumps(market_analysis, ensure_ascii=False)
            trading_analysis_json = json.dumps({
                'profit_list': [{'symbol': t['symbol'], 'pnl': t['pnl'], 'side': t['side']}
                               for t in performance.get('profit_list', [])[:20]],
                'loss_list': [{'symbol': t['symbol'], 'pnl': t['pnl'], 'side': t['side']}
                             for t in performance.get('loss_list', [])[:20]]
            }, ensure_ascii=False)
            loss_analysis_json = json.dumps({
                'loss_trades': [{
                    'symbol': a['trade']['symbol'],
                    'side': a['trade']['side'],
                    'pnl': a['trade']['pnl'],
                    'pnl_pct': a['trade']['pnl_pct'],
                    'issues': a['issues'],
                    'recommendations': a['recommendations']
                } for a in loss_analysis[:20]]
            }, ensure_ascii=False)

            # 插入数据库
            conn = self._get_connection()
            cursor = conn.cursor()
            sql = """
                INSERT INTO retrospective_analysis (
                    analysis_time, period_start, period_end,
                    btc_price_change_pct, btc_volatility_pct, btc_direction,
                    eth_price_change_pct, eth_volatility_pct, eth_direction,
                    bnb_price_change_pct, bnb_volatility_pct, bnb_direction,
                    sol_price_change_pct, sol_volatility_pct, sol_direction,
                    overall_market_direction,
                    big4_signal_count, big4_bullish_count, big4_bearish_count, big4_neutral_count,
                    total_trades, profit_trades, loss_trades, win_rate, total_pnl,
                    counter_trend_trades, signal_mismatch_trades, signal_lag_trades, false_breakout_trades,
                    performance_rating,
                    market_analysis_json, trading_analysis_json, loss_analysis_json
                ) VALUES (
                    NOW(), %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s,
                    %s, %s, %s
                )
            """

            cursor.execute(sql, (
                period_start, period_end,
                market_analysis['BTC/USDT']['price_change_pct'],
                market_analysis['BTC/USDT']['volatility'],
                market_analysis['BTC/USDT']['direction'],
                market_analysis['ETH/USDT']['price_change_pct'],
                market_analysis['ETH/USDT']['volatility'],
                market_analysis['ETH/USDT']['direction'],
                market_analysis['BNB/USDT']['price_change_pct'],
                market_analysis['BNB/USDT']['volatility'],
                market_analysis['BNB/USDT']['direction'],
                market_analysis['SOL/USDT']['price_change_pct'],
                market_analysis['SOL/USDT']['volatility'],
                market_analysis['SOL/USDT']['direction'],
                overall_market_direction,
                len(big4_signals),
                sum(1 for s in big4_signals if 'BULLISH' in s.get('overall_signal', '')),
                sum(1 for s in big4_signals if 'BEARISH' in s.get('overall_signal', '')),
                sum(1 for s in big4_signals if 'NEUTRAL' in s.get('overall_signal', '')),
                performance['total_trades'],
                performance['profit_trades'],
                performance['loss_trades'],
                performance['win_rate'],
                performance['total_pnl'],
                counter_trend_count,
                signal_mismatch_count,
                signal_lag_count,
                false_breakout_count,
                performance_rating,
                market_analysis_json,
                trading_analysis_json,
                loss_analysis_json
            ))

            conn.commit()
            cursor.close()
            print("\nOK - Analysis saved to database")

        except Exception as e:
            print(f"\nWARNING - Failed to save to database: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass


def main():
    analyzer = RetrospectiveAnalyzer()
    analyzer.generate_report()


if __name__ == '__main__':
    main()
