"""
价格采样器（滚动窗口）
用于超级大脑智能建仓期间实时采集和分析价格数据
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from decimal import Decimal
from loguru import logger
import numpy as np


class PriceSampler:
    """实时价格采样器（滚动窗口）"""

    def __init__(self, symbol: str, price_service, window_seconds: int = 300):
        """
        初始化采样器

        Args:
            symbol: 交易对符号
            price_service: 价格服务（用于获取实时价格）
            window_seconds: 滚动窗口大小（秒），默认5分钟
        """
        self.symbol = symbol
        self.price_service = price_service
        self.window_seconds = window_seconds  # 滚动窗口: 5分钟

        # 价格样本池（滚动更新）
        self.samples: List[Dict] = []  # [{'price': Decimal, 'timestamp': datetime}, ...]

        # 初始基线和当前基线
        self.baseline: Optional[Dict] = None  # 初始价格基线
        self.sampling_started = False
        self.initial_baseline_built = False

    async def start_background_sampling(self):
        """
        启动后台持续采样（独立协程）

        在整个30分钟建仓期间持续运行
        """
        self.sampling_started = True
        logger.info(f"📊 {self.symbol} 开始后台价格采样（滚动窗口{self.window_seconds}秒）")

        while self.sampling_started:
            try:
                # 获取实时价格
                current_price = await self._get_realtime_price()
                current_time = datetime.now()

                # 添加新样本
                self.samples.append({
                    'price': current_price,
                    'timestamp': current_time
                })

                # 清理超出窗口的旧样本
                cutoff_time = current_time - timedelta(seconds=self.window_seconds)
                self.samples = [
                    s for s in self.samples
                    if s['timestamp'] >= cutoff_time
                ]

                # 前5分钟建立初始基线
                if not self.initial_baseline_built and len(self.samples) >= 10:
                    elapsed = (current_time - self.samples[0]['timestamp']).total_seconds()
                    if elapsed >= 300:  # 5分钟后
                        self.baseline = self._build_baseline()
                        self.initial_baseline_built = True
                        logger.info(
                            f"✅ {self.symbol} 初始基线建立完成: "
                            f"中位数={self.baseline['p50']:.6f}, "
                            f"波动率={self.baseline['volatility']:.4f}%, "
                            f"趋势={self.baseline['trend']['direction']}"
                        )

                await asyncio.sleep(10)  # 每10秒采样一次

            except Exception as e:
                logger.error(f"价格采样异常: {e}")
                await asyncio.sleep(10)

    def stop_sampling(self):
        """停止采样"""
        self.sampling_started = False
        logger.info(f"⏹️ {self.symbol} 停止价格采样，共采集 {len(self.samples)} 个样本")

    async def _get_realtime_price(self) -> Decimal:
        """
        获取实时价格 - 全部委托 BinanceDataHub.

        Hub 内部按 WS -> 缓存 -> DB -> REST 多级降级, 所有 REST 流量受
        rate_guard 熔断 + 令牌桶限速保护.
        """
        from app.services.binance_data_hub import get_global_data_hub
        hub = get_global_data_hub()
        if hub is None:
            logger.error(f"{self.symbol} DataHub 未初始化, 价格采样失败")
            return Decimal('0')
        price = await hub.get_price(self.symbol)
        if price is None:
            logger.warning(f"{self.symbol} 全部取价方法均失败, 本轮采样跳过")
            return Decimal('0')
        return price

    def _build_baseline(self) -> Optional[Dict]:
        """
        根据当前采样数据建立/更新价格基线

        Returns:
            价格基线字典（包含分位数、趋势等）
        """
        if len(self.samples) < 10:
            return None  # 样本不足

        prices = [float(s['price']) for s in self.samples]

        baseline = {
            'signal_price': prices[0] if not self.baseline else self.baseline['signal_price'],  # 保持初始信号价格
            'avg_price': np.mean(prices),
            'max_price': np.max(prices),
            'min_price': np.min(prices),
            'volatility': (np.std(prices) / np.mean(prices)) * 100,  # 波动率%

            # 分位数（基于滚动窗口实时计算）
            'p90': np.percentile(prices, 90),
            'p75': np.percentile(prices, 75),
            'p50': np.percentile(prices, 50),  # 中位数
            'p25': np.percentile(prices, 25),
            'p10': np.percentile(prices, 10),

            # 趋势（基于滚动窗口）
            'trend': self._calculate_trend(prices),

            # 采样元数据
            'sample_count': len(prices),
            'window_seconds': self.window_seconds,
            'time_range': f"{self.samples[0]['timestamp'].strftime('%H:%M:%S')} - {self.samples[-1]['timestamp'].strftime('%H:%M:%S')}",
            'updated_at': datetime.now()
        }

        return baseline

    def get_current_baseline(self) -> Optional[Dict]:
        """
        获取当前实时基线（基于滚动窗口）

        Returns:
            实时更新的价格基线
        """
        if len(self.samples) >= 10:
            return self._build_baseline()
        elif self.baseline:
            return self.baseline  # 返回初始基线
        else:
            return None

    def _calculate_trend(self, prices: List[float]) -> Dict:
        """
        计算趋势方向和强度

        Args:
            prices: 价格列表

        Returns:
            {'direction': 'up'/'down'/'sideways', 'strength': 0-1, 'change_pct': float}
        """
        first_price = prices[0]
        last_price = prices[-1]
        change_pct = (last_price - first_price) / first_price * 100

        if abs(change_pct) < 0.15:
            return {'direction': 'sideways', 'strength': 0.3, 'change_pct': change_pct}
        elif change_pct > 0:
            strength = min(abs(change_pct) / 0.5, 1.0)  # 0.5%变化=100%强度
            return {'direction': 'up', 'strength': strength, 'change_pct': change_pct}
        else:
            strength = min(abs(change_pct) / 0.5, 1.0)
            return {'direction': 'down', 'strength': strength, 'change_pct': change_pct}

    def is_good_long_price(self, current_price: Decimal) -> Dict:
        """
        判断当前价格是否适合做多入场（基于90分位数阈值）

        做多策略: 只在价格 <= 90分位数时买入（买在相对低位）

        Args:
            current_price: 当前价格

        Returns:
            {'suitable': bool, 'score': 0-100, 'reason': str}
        """
        # 获取实时基线（基于滚动窗口）
        baseline = self.get_current_baseline()

        if not baseline:
            return {'suitable': False, 'score': 0, 'reason': '基线未建立'}

        price_float = float(current_price)
        p90 = baseline['p90']

        # 核心判断: 价格必须 <= 90分位数
        if price_float <= p90:
            # 计算价格在分位数中的位置，越低分数越高
            price_pct_in_range = (p90 - price_float) / (p90 - baseline['min_price']) * 100 if p90 > baseline['min_price'] else 0
            score = min(100, 60 + int(price_pct_in_range * 0.4))  # 基础60分，最高100分

            reason = f"✅ 适合做多: 价格{price_float:.6f} <= p90({p90:.6f}), 相对低位{price_pct_in_range:.1f}%"
            suitable = True
        else:
            # 价格高于90分位数，不适合做多
            exceed_pct = (price_float - p90) / p90 * 100
            score = 0
            reason = f"❌ 不适合做多: 价格{price_float:.6f} > p90({p90:.6f}), 高出{exceed_pct:.2f}%"
            suitable = False

        return {
            'suitable': suitable,
            'score': score,
            'reason': reason,
            'current_price': price_float,
            'p90_threshold': p90,
            'baseline_updated_at': baseline['updated_at']
        }

    def is_good_short_price(self, current_price: Decimal) -> Dict:
        """
        判断当前价格是否适合做空入场（基于90分位数阈值）

        做空策略: 只在价格 >= 90分位数时卖出（卖在相对高位）

        Args:
            current_price: 当前价格

        Returns:
            {'suitable': bool, 'score': 0-100, 'reason': str}
        """
        # 获取实时基线（基于滚动窗口）
        baseline = self.get_current_baseline()

        if not baseline:
            return {'suitable': False, 'score': 0, 'reason': '基线未建立'}

        price_float = float(current_price)
        p90 = baseline['p90']

        # 核心判断: 价格必须 >= 90分位数
        if price_float >= p90:
            # 计算价格在分位数中的位置，越高分数越高
            price_pct_in_range = (price_float - p90) / (baseline['max_price'] - p90) * 100 if baseline['max_price'] > p90 else 0
            score = min(100, 60 + int(price_pct_in_range * 0.4))  # 基础60分，最高100分

            reason = f"✅ 适合做空: 价格{price_float:.6f} >= p90({p90:.6f}), 相对高位{price_pct_in_range:.1f}%"
            suitable = True
        else:
            # 价格低于90分位数，不适合做空
            below_pct = (p90 - price_float) / p90 * 100
            score = 0
            reason = f"❌ 不适合做空: 价格{price_float:.6f} < p90({p90:.6f}), 低于{below_pct:.2f}%"
            suitable = False

        return {
            'suitable': suitable,
            'score': score,
            'reason': reason,
            'current_price': price_float,
            'p90_threshold': p90,
            'baseline_updated_at': baseline['updated_at']
        }

    def detect_bottom_signal(self) -> int:
        """
        检测止跌信号（多种方法综合评分）

        Returns:
            信号强度 0-100分
        """
        if len(self.samples) < 10:
            return 0

        score = 0
        prices = [float(s['price']) for s in self.samples]

        # 方法1: 实时价格连续上涨（权重30分）
        recent_prices = prices[-6:]  # 最近6次采样（约1分钟）
        if len(recent_prices) >= 3:
            consecutive_ups = 0
            for i in range(1, len(recent_prices)):
                if recent_prices[i] > recent_prices[i-1]:
                    consecutive_ups += 1

            if consecutive_ups >= 2:
                score += 15
            if consecutive_ups >= 4:
                score += 15  # 连续上涨加强信号

        # 方法2: V型反转检测（权重30分）
        if len(self.samples) >= 30:  # 至少5分钟数据
            recent_5m = prices[-30:]
            min_price = min(recent_5m)
            current_price = recent_5m[-1]
            rebound_pct = (current_price - min_price) / min_price * 100

            if rebound_pct >= 0.15:
                score += 15
            if rebound_pct >= 0.3:
                score += 15  # 强反弹

        return score

    def detect_top_signal(self) -> int:
        """
        检测止涨信号（逻辑镜像止跌检测）

        Returns:
            信号强度 0-100分
        """
        if len(self.samples) < 10:
            return 0

        score = 0
        prices = [float(s['price']) for s in self.samples]

        # 方法1: 实时价格连续下跌
        recent_prices = prices[-6:]
        if len(recent_prices) >= 3:
            consecutive_downs = 0
            for i in range(1, len(recent_prices)):
                if recent_prices[i] < recent_prices[i-1]:
                    consecutive_downs += 1

            if consecutive_downs >= 2:
                score += 15
            if consecutive_downs >= 4:
                score += 15

        # 方法2: 倒V型检测
        if len(self.samples) >= 30:
            recent_5m = prices[-30:]
            max_price = max(recent_5m)
            current_price = recent_5m[-1]
            pullback_pct = (max_price - current_price) / max_price * 100

            if pullback_pct >= 0.15:
                score += 15
            if pullback_pct >= 0.3:
                score += 15

        return score

    def is_good_long_exit_price(self, current_price: Decimal, entry_price: float) -> Dict:
        """
        判断当前价格是否适合做多平仓（基于实时滚动基线）
        目标：在高位卖出

        Args:
            current_price: 当前价格
            entry_price: 开仓价格

        Returns:
            {'score': 0-100, 'reason': str, 'profit_pct': float}
        """
        baseline = self.get_current_baseline()

        if not baseline:
            return {'score': 0, 'reason': '基线未建立', 'profit_pct': 0}

        price_float = float(current_price)
        profit_pct = (price_float - entry_price) / entry_price * 100

        score = 0
        reasons = []

        # 评分标准1: 价格分位数（权重70分）
        if price_float >= baseline['p90']:
            score += 70
            reasons.append(f"顶部10%极佳卖点(p90={baseline['p90']:.6f})")
        elif price_float >= baseline['p75']:
            score += 55
            reasons.append(f"顶部25%优秀卖点(p75={baseline['p75']:.6f})")
        elif price_float >= baseline['p50']:
            score += 35
            reasons.append(f"中位数以上良好卖点(p50={baseline['p50']:.6f})")
        elif price_float >= baseline['p25']:
            score += 20
            reasons.append(f"一般卖点(p25以上)")
        else:
            score += 5
            reasons.append(f"底部区间较差卖点")

        # 评分标准2: 盈利加分（权重30分）
        if profit_pct >= 2.0:
            score += 30
            reasons.append(f"高盈利+{profit_pct:.2f}%")
        elif profit_pct >= 1.0:
            score += 20
            reasons.append(f"中盈利+{profit_pct:.2f}%")
        elif profit_pct > 0:
            score += 10
            reasons.append(f"微盈利+{profit_pct:.2f}%")
        elif profit_pct < -1.0:
            score -= 10
            reasons.append(f"亏损{profit_pct:.2f}%")

        # 评分标准3: 突破最高价加分（+15分）
        if price_float >= baseline['max_price']:
            score += 15
            reasons.append(f"突破滚动最高价({baseline['max_price']:.6f})")

        total_score = max(0, min(100, score))

        return {
            'score': total_score,
            'reason': ' | '.join(reasons),
            'profit_pct': profit_pct,
            'current_price': price_float,
            'baseline_updated_at': baseline['updated_at']
        }

    def is_good_short_exit_price(self, current_price: Decimal, entry_price: float) -> Dict:
        """
        判断当前价格是否适合做空平仓（基于实时滚动基线）
        目标：在低位买入平仓

        Args:
            current_price: 当前价格
            entry_price: 开仓价格

        Returns:
            {'score': 0-100, 'reason': str, 'profit_pct': float}
        """
        baseline = self.get_current_baseline()

        if not baseline:
            return {'score': 0, 'reason': '基线未建立', 'profit_pct': 0}

        price_float = float(current_price)
        profit_pct = (entry_price - price_float) / entry_price * 100

        score = 0
        reasons = []

        # 评分标准1: 价格分位数（权重70分）
        if price_float <= baseline['p10']:
            score += 70
            reasons.append(f"底部10%极佳买点(p10={baseline['p10']:.6f})")
        elif price_float <= baseline['p25']:
            score += 55
            reasons.append(f"底部25%优秀买点(p25={baseline['p25']:.6f})")
        elif price_float <= baseline['p50']:
            score += 35
            reasons.append(f"中位数以下良好买点(p50={baseline['p50']:.6f})")
        elif price_float <= baseline['p75']:
            score += 20
            reasons.append(f"一般买点(p75以下)")
        else:
            score += 5
            reasons.append(f"顶部区间较差买点")

        # 评分标准2: 盈利加分（权重30分）
        if profit_pct >= 2.0:
            score += 30
            reasons.append(f"高盈利+{profit_pct:.2f}%")
        elif profit_pct >= 1.0:
            score += 20
            reasons.append(f"中盈利+{profit_pct:.2f}%")
        elif profit_pct > 0:
            score += 10
            reasons.append(f"微盈利+{profit_pct:.2f}%")
        elif profit_pct < -1.0:
            score -= 10
            reasons.append(f"亏损{profit_pct:.2f}%")

        # 评分标准3: 跌破最低价加分（+15分）
        if price_float <= baseline['min_price']:
            score += 15
            reasons.append(f"跌破滚动最低价({baseline['min_price']:.6f})")

        total_score = max(0, min(100, score))

        return {
            'score': total_score,
            'reason': ' | '.join(reasons),
            'profit_pct': profit_pct,
            'current_price': price_float,
            'baseline_updated_at': baseline['updated_at']
        }
