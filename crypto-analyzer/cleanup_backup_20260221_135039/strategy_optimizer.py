"""
策略自动优化器
使用遗传算法和回测数据来优化策略参数
"""
import random
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio

from app.strategies.strategy_config import (
    InvestmentStrategy, DimensionWeights, RiskProfile, 
    TradingRules, TechnicalIndicatorConfig
)

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """优化结果"""
    strategy: InvestmentStrategy
    score: float
    metrics: Dict
    generation: int


class StrategyOptimizer:
    """策略优化器 - 使用遗传算法"""
    
    def __init__(self, backtest_func, symbol: str, start_date: str, end_date: str):
        """
        初始化优化器
        
        Args:
            backtest_func: 回测函数，接受strategy, symbol, start_date, end_date参数
            symbol: 交易对
            start_date: 开始日期
            end_date: 结束日期
        """
        self.backtest_func = backtest_func
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        
        # 遗传算法参数
        self.population_size = 20  # 种群大小
        self.generations = 10     # 迭代代数
        self.mutation_rate = 0.1  # 变异率
        self.crossover_rate = 0.7  # 交叉率
        self.elite_size = 3      # 精英数量（直接保留到下一代）
    
    def create_random_strategy(self, base_strategy: Optional[InvestmentStrategy] = None) -> InvestmentStrategy:
        """
        创建随机策略
        
        Args:
            base_strategy: 基础策略（可选）
        
        Returns:
            随机生成的策略
        """
        if base_strategy:
            strategy = InvestmentStrategy(base_strategy.name + "_optimized")
            # 基于基础策略进行变异
            strategy.dimension_weights = self._mutate_weights(base_strategy.dimension_weights)
            strategy.risk_profile = self._mutate_risk_profile(base_strategy.risk_profile)
            strategy.trading_rules = self._mutate_trading_rules(base_strategy.trading_rules)
            strategy.technical_config = self._mutate_technical_config(base_strategy.technical_config)
        else:
            strategy = InvestmentStrategy("auto_generated")
            # 完全随机生成
            strategy.dimension_weights = self._random_weights()
            strategy.risk_profile = self._random_risk_profile()
            strategy.trading_rules = self._random_trading_rules()
            strategy.technical_config = self._random_technical_config()
        
        strategy.description = "自动优化生成的策略"
        strategy.tags = ["auto-generated", "optimized"]
        
        return strategy
    
    def _random_weights(self) -> DimensionWeights:
        """生成随机维度权重"""
        weights = [
            random.uniform(20, 50),  # technical
            random.uniform(10, 30),  # hyperliquid
            random.uniform(5, 25),   # news
            random.uniform(5, 25),   # funding_rate
            random.uniform(5, 20),   # ethereum
        ]
        total = sum(weights)
        return DimensionWeights(
            technical=(weights[0] / total) * 100,
            hyperliquid=(weights[1] / total) * 100,
            news=(weights[2] / total) * 100,
            funding_rate=(weights[3] / total) * 100,
            ethereum=(weights[4] / total) * 100,
        )
    
    def _mutate_weights(self, base: DimensionWeights) -> DimensionWeights:
        """变异权重"""
        mutation_range = 10  # 变异范围 ±10%
        return DimensionWeights(
            technical=max(10, min(60, base.technical + random.uniform(-mutation_range, mutation_range))),
            hyperliquid=max(5, min(40, base.hyperliquid + random.uniform(-mutation_range, mutation_range))),
            news=max(5, min(30, base.news + random.uniform(-mutation_range, mutation_range))),
            funding_rate=max(5, min(30, base.funding_rate + random.uniform(-mutation_range, mutation_range))),
            ethereum=max(5, min(20, base.ethereum + random.uniform(-mutation_range, mutation_range))),
        )
    
    def _random_risk_profile(self) -> RiskProfile:
        """生成随机风险配置"""
        level = random.choice(["conservative", "balanced", "aggressive"])
        return RiskProfile(
            level=level,
            max_position_size=random.uniform(10, 30),
            stop_loss=random.uniform(3, 8),
            take_profit=random.uniform(10, 25),
            min_signal_strength=random.uniform(50, 75),
            allow_short=random.random() < 0.3,
            max_leverage=random.uniform(1.0, 3.0) if level == "aggressive" else 1.0
        )
    
    def _mutate_risk_profile(self, base: RiskProfile) -> RiskProfile:
        """变异风险配置"""
        return RiskProfile(
            level=base.level,
            max_position_size=max(5, min(50, base.max_position_size + random.uniform(-5, 5))),
            stop_loss=max(2, min(10, base.stop_loss + random.uniform(-1, 1))),
            take_profit=max(5, min(30, base.take_profit + random.uniform(-3, 3))),
            min_signal_strength=max(40, min(80, base.min_signal_strength + random.uniform(-5, 5))),
            allow_short=base.allow_short if random.random() > 0.2 else not base.allow_short,
            max_leverage=max(1.0, min(5.0, base.max_leverage + random.uniform(-0.5, 0.5)))
        )
    
    def _random_trading_rules(self) -> TradingRules:
        """生成随机交易规则"""
        return TradingRules(
            hyperliquid_min_wallets=random.randint(2, 5),
            hyperliquid_min_amount=random.uniform(30000, 100000),
            hyperliquid_min_net_flow=random.uniform(50000, 200000),
            rsi_oversold=random.uniform(25, 35),
            rsi_overbought=random.uniform(65, 75),
            news_sentiment_threshold=random.uniform(0.5, 0.7),
            news_min_count=random.randint(2, 5),
            funding_rate_extreme=random.uniform(0.005, 0.02),
            min_dimensions_agree=random.randint(2, 4)
        )
    
    def _mutate_trading_rules(self, base: TradingRules) -> TradingRules:
        """变异交易规则"""
        return TradingRules(
            hyperliquid_min_wallets=max(1, min(10, base.hyperliquid_min_wallets + random.randint(-1, 1))),
            hyperliquid_min_amount=max(10000, base.hyperliquid_min_amount * random.uniform(0.8, 1.2)),
            hyperliquid_min_net_flow=max(20000, base.hyperliquid_min_net_flow * random.uniform(0.8, 1.2)),
            rsi_oversold=max(20, min(40, base.rsi_oversold + random.uniform(-3, 3))),
            rsi_overbought=max(60, min(80, base.rsi_overbought + random.uniform(-3, 3))),
            news_sentiment_threshold=max(0.3, min(0.8, base.news_sentiment_threshold + random.uniform(-0.1, 0.1))),
            news_min_count=max(1, min(10, base.news_min_count + random.randint(-1, 1))),
            funding_rate_extreme=max(0.001, min(0.05, base.funding_rate_extreme * random.uniform(0.8, 1.2))),
            min_dimensions_agree=max(2, min(5, base.min_dimensions_agree + random.randint(-1, 1)))
        )
    
    def _random_technical_config(self) -> TechnicalIndicatorConfig:
        """生成随机技术指标配置"""
        weights = [
            random.uniform(15, 35),  # rsi
            random.uniform(15, 35),  # macd
            random.uniform(10, 30),  # bollinger
            random.uniform(10, 25),  # ema
            random.uniform(10, 25),  # volume
        ]
        total = sum(weights)
        return TechnicalIndicatorConfig(
            rsi_weight=(weights[0] / total) * 100,
            macd_weight=(weights[1] / total) * 100,
            bollinger_weight=(weights[2] / total) * 100,
            ema_weight=(weights[3] / total) * 100,
            volume_weight=(weights[4] / total) * 100,
            rsi_period=random.randint(10, 20),
            macd_fast=random.randint(8, 16),
            macd_slow=random.randint(20, 30),
            macd_signal=random.randint(7, 11),
            ema_short=random.randint(15, 25),
            ema_long=random.randint(40, 60),
            bb_period=random.randint(15, 25),
            bb_std=random.uniform(1.5, 2.5)
        )
    
    def _mutate_technical_config(self, base: TechnicalIndicatorConfig) -> TechnicalIndicatorConfig:
        """变异技术指标配置"""
        weights = [
            max(10, min(40, base.rsi_weight + random.uniform(-5, 5))),
            max(10, min(40, base.macd_weight + random.uniform(-5, 5))),
            max(5, min(35, base.bollinger_weight + random.uniform(-5, 5))),
            max(5, min(30, base.ema_weight + random.uniform(-5, 5))),
            max(5, min(30, base.volume_weight + random.uniform(-5, 5))),
        ]
        total = sum(weights)
        return TechnicalIndicatorConfig(
            rsi_weight=(weights[0] / total) * 100,
            macd_weight=(weights[1] / total) * 100,
            bollinger_weight=(weights[2] / total) * 100,
            ema_weight=(weights[3] / total) * 100,
            volume_weight=(weights[4] / total) * 100,
            rsi_period=max(10, min(20, base.rsi_period + random.randint(-2, 2))),
            macd_fast=max(8, min(16, base.macd_fast + random.randint(-2, 2))),
            macd_slow=max(20, min(30, base.macd_slow + random.randint(-2, 2))),
            macd_signal=max(7, min(11, base.macd_signal + random.randint(-1, 1))),
            ema_short=max(15, min(25, base.ema_short + random.randint(-3, 3))),
            ema_long=max(40, min(60, base.ema_long + random.randint(-5, 5))),
            bb_period=max(15, min(25, base.bb_period + random.randint(-2, 2))),
            bb_std=max(1.5, min(2.5, base.bb_std + random.uniform(-0.2, 0.2)))
        )
    
    def calculate_fitness(self, backtest_result: Dict) -> float:
        """
        计算策略适应度（评分）
        
        Args:
            backtest_result: 回测结果
        
        Returns:
            适应度分数（越高越好）
        """
        if not backtest_result or not backtest_result.get('success'):
            return -1000  # 回测失败，给很低的分
        
        result = backtest_result.get('backtest_result', {})
        
        # 提取指标
        total_return = result.get('total_return', 0)
        win_rate = result.get('win_rate', 0)
        max_drawdown = result.get('max_drawdown', 100)
        profit_factor = result.get('profit_factor', 0)
        total_trades = result.get('total_trades', 0)
        
        # 综合评分公式
        # 收益率权重40%，胜率权重30%，最大回撤权重20%，盈亏比权重10%
        score = (
            total_return * 0.4 +                    # 收益率
            win_rate * 0.3 +                        # 胜率
            (100 - max_drawdown) * 0.2 +            # 最大回撤（越小越好，所以用100减去）
            min(profit_factor * 10, 100) * 0.1      # 盈亏比
        )
        
        # 惩罚交易次数过少
        if total_trades < 5:
            score *= 0.5
        
        # 惩罚亏损策略
        if total_return < 0:
            score -= abs(total_return) * 2
        
        return score
    
    def crossover(self, parent1: InvestmentStrategy, parent2: InvestmentStrategy) -> InvestmentStrategy:
        """交叉操作：生成子代策略"""
        child = InvestmentStrategy("crossover_child")
        child.description = "交叉生成的策略"
        
        # 随机选择父代的特征
        if random.random() < 0.5:
            child.dimension_weights = parent1.dimension_weights
        else:
            child.dimension_weights = parent2.dimension_weights
        
        if random.random() < 0.5:
            child.risk_profile = parent1.risk_profile
        else:
            child.risk_profile = parent2.risk_profile
        
        if random.random() < 0.5:
            child.trading_rules = parent1.trading_rules
        else:
            child.trading_rules = parent2.trading_rules
        
        if random.random() < 0.5:
            child.technical_config = parent1.technical_config
        else:
            child.technical_config = parent2.technical_config
        
        # 标准化权重
        child.dimension_weights.normalize()
        child.technical_config.validate()
        
        return child
    
    async def optimize(self, base_strategy: Optional[InvestmentStrategy] = None) -> OptimizationResult:
        """
        执行优化
        
        Args:
            base_strategy: 基础策略（可选，如果提供则基于此策略优化）
        
        Returns:
            优化结果
        """
        logger.info(f"开始策略优化 - 交易对: {self.symbol}, 时间范围: {self.start_date} 至 {self.end_date}")
        
        # 初始化种群
        population = []
        for i in range(self.population_size):
            strategy = self.create_random_strategy(base_strategy)
            strategy.name = f"gen0_strategy_{i}"
            population.append(strategy)
        
        best_result = None
        best_score = float('-inf')
        
        # 迭代优化
        for generation in range(self.generations):
            logger.info(f"第 {generation + 1}/{self.generations} 代优化中...")
            
            # 评估所有策略
            results = []
            for strategy in population:
                try:
                    backtest_result = await self.backtest_func(
                        strategy.name,
                        self.symbol,
                        self.start_date,
                        self.end_date,
                        strategy=strategy
                    )
                    score = self.calculate_fitness(backtest_result)
                    results.append((strategy, score, backtest_result))
                except Exception as e:
                    logger.warning(f"策略 {strategy.name} 回测失败: {e}")
                    results.append((strategy, -1000, None))
            
            # 按分数排序
            results.sort(key=lambda x: x[1], reverse=True)
            
            # 更新最佳结果
            if results[0][1] > best_score:
                best_score = results[0][1]
                best_result = OptimizationResult(
                    strategy=results[0][0],
                    score=best_score,
                    metrics=results[0][2].get('backtest_result', {}) if results[0][2] else {},
                    generation=generation
                )
                logger.info(f"发现更好的策略，分数: {best_score:.2f}")
            
            # 如果是最后一代，直接返回
            if generation == self.generations - 1:
                break
            
            # 选择、交叉、变异生成下一代
            new_population = []
            
            # 保留精英（直接复制）
            for i in range(self.elite_size):
                new_population.append(results[i][0])
            
            # 生成新个体
            while len(new_population) < self.population_size:
                if random.random() < self.crossover_rate:
                    # 交叉
                    parent1 = self._select_parent(results)
                    parent2 = self._select_parent(results)
                    child = self.crossover(parent1, parent2)
                else:
                    # 变异
                    parent = self._select_parent(results)
                    child = self.create_random_strategy(parent)
                
                # 变异
                if random.random() < self.mutation_rate:
                    child.dimension_weights = self._mutate_weights(child.dimension_weights)
                    child.risk_profile = self._mutate_risk_profile(child.risk_profile)
                    child.trading_rules = self._mutate_trading_rules(child.trading_rules)
                    child.technical_config = self._mutate_technical_config(child.technical_config)
                    child.dimension_weights.normalize()
                
                child.name = f"gen{generation+1}_strategy_{len(new_population)}"
                new_population.append(child)
            
            population = new_population
        
        if best_result is None:
            raise ValueError("优化失败，未找到有效策略")
        
        logger.info(f"优化完成，最佳策略分数: {best_result.score:.2f}")
        return best_result
    
    def _select_parent(self, results: List[Tuple]) -> InvestmentStrategy:
        """选择父代（轮盘赌选择）"""
        # 使用线性排名选择
        sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
        ranks = list(range(len(sorted_results), 0, -1))
        total_rank = sum(ranks)
        
        r = random.uniform(0, total_rank)
        cumulative = 0
        for i, rank in enumerate(ranks):
            cumulative += rank
            if r <= cumulative:
                return sorted_results[i][0]
        
        return sorted_results[0][0]

