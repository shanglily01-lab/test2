"""
个人投资策略配置系统
允许用户自定义分析维度权重、风险偏好、交易规则等
"""
import json
import logging
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class DimensionWeights:
    """五维度分析权重"""
    technical: float = 40.0      # 技术指标权重
    hyperliquid: float = 20.0    # Hyperliquid聪明钱权重
    news: float = 15.0           # 新闻情绪权重
    funding_rate: float = 15.0   # 资金费率权重
    ethereum: float = 10.0       # 以太坊链上数据权重

    def validate(self) -> bool:
        """验证权重总和为100"""
        total = self.technical + self.hyperliquid + self.news + self.funding_rate + self.ethereum
        return abs(total - 100.0) < 0.01

    def normalize(self):
        """标准化权重使总和为100"""
        total = self.technical + self.hyperliquid + self.news + self.funding_rate + self.ethereum
        if total > 0:
            self.technical = (self.technical / total) * 100
            self.hyperliquid = (self.hyperliquid / total) * 100
            self.news = (self.news / total) * 100
            self.funding_rate = (self.funding_rate / total) * 100
            self.ethereum = (self.ethereum / total) * 100


@dataclass
class RiskProfile:
    """风险偏好配置"""
    # 风险等级: conservative(保守), balanced(平衡), aggressive(激进)
    level: str = "balanced"

    # 最大单笔投资比例 (%)
    max_position_size: float = 20.0

    # 止损比例 (%)
    stop_loss: float = 5.0

    # 止盈比例 (%)
    take_profit: float = 15.0

    # 最小信号强度 (0-100)
    min_signal_strength: float = 60.0

    # 是否允许做空
    allow_short: bool = False

    # 最大杠杆倍数
    max_leverage: float = 1.0


@dataclass
class TradingRules:
    """交易规则配置"""
    # Hyperliquid规则
    hyperliquid_min_wallets: int = 3           # 最少聪明钱包数量
    hyperliquid_min_amount: float = 50000.0    # 最小交易金额(USD)
    hyperliquid_min_net_flow: float = 100000.0 # 最小净流入(USD)

    # 技术指标规则
    rsi_oversold: float = 30.0                 # RSI超卖阈值
    rsi_overbought: float = 70.0               # RSI超买阈值

    # 新闻情绪规则
    news_sentiment_threshold: float = 0.6      # 新闻情绪阈值
    news_min_count: int = 3                    # 最少新闻数量

    # 资金费率规则
    funding_rate_extreme: float = 0.01         # 极端资金费率阈值

    # 综合信号规则
    min_dimensions_agree: int = 3              # 最少同意的维度数


@dataclass
class TechnicalIndicatorConfig:
    """技术指标偏好配置"""
    # 指标权重
    rsi_weight: float = 25.0
    macd_weight: float = 25.0
    bollinger_weight: float = 20.0
    ema_weight: float = 15.0
    volume_weight: float = 15.0

    # RSI参数
    rsi_period: int = 14

    # MACD参数
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # EMA参数
    ema_short: int = 20
    ema_long: int = 50

    # 布林带参数
    bb_period: int = 20
    bb_std: float = 2.0

    def validate(self) -> bool:
        """验证技术指标权重总和"""
        total = self.rsi_weight + self.macd_weight + self.bollinger_weight + self.ema_weight + self.volume_weight
        return abs(total - 100.0) < 0.01


class InvestmentStrategy:
    """投资策略类"""

    def __init__(self, name: str = "default"):
        """
        初始化投资策略

        Args:
            name: 策略名称
        """
        self.name = name
        self.description = ""
        self.created_at = datetime.now()
        self.updated_at = datetime.now()

        # 五维度权重
        self.dimension_weights = DimensionWeights()

        # 风险偏好
        self.risk_profile = RiskProfile()

        # 交易规则
        self.trading_rules = TradingRules()

        # 技术指标配置
        self.technical_config = TechnicalIndicatorConfig()

        # 自定义标签
        self.tags: List[str] = []

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'dimension_weights': asdict(self.dimension_weights),
            'risk_profile': asdict(self.risk_profile),
            'trading_rules': asdict(self.trading_rules),
            'technical_config': asdict(self.technical_config),
            'tags': self.tags
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'InvestmentStrategy':
        """从字典创建策略"""
        strategy = cls(name=data.get('name', 'default'))
        strategy.description = data.get('description', '')

        if 'created_at' in data:
            strategy.created_at = datetime.fromisoformat(data['created_at'])
        if 'updated_at' in data:
            strategy.updated_at = datetime.fromisoformat(data['updated_at'])

        # 加载维度权重
        if 'dimension_weights' in data:
            strategy.dimension_weights = DimensionWeights(**data['dimension_weights'])

        # 加载风险偏好
        if 'risk_profile' in data:
            strategy.risk_profile = RiskProfile(**data['risk_profile'])

        # 加载交易规则
        if 'trading_rules' in data:
            strategy.trading_rules = TradingRules(**data['trading_rules'])

        # 加载技术指标配置
        if 'technical_config' in data:
            strategy.technical_config = TechnicalIndicatorConfig(**data['technical_config'])

        strategy.tags = data.get('tags', [])

        return strategy

    def validate(self) -> bool:
        """验证策略配置"""
        try:
            # 验证维度权重
            if not self.dimension_weights.validate():
                logger.warning(f"策略 {self.name}: 维度权重总和不为100，自动标准化")
                self.dimension_weights.normalize()

            # 验证技术指标权重
            if not self.technical_config.validate():
                logger.warning(f"策略 {self.name}: 技术指标权重总和不为100")
                return False

            # 验证风险配置
            if self.risk_profile.max_position_size <= 0 or self.risk_profile.max_position_size > 100:
                logger.error(f"策略 {self.name}: 最大仓位比例必须在0-100之间")
                return False

            if self.risk_profile.stop_loss <= 0 or self.risk_profile.stop_loss > 50:
                logger.error(f"策略 {self.name}: 止损比例必须在0-50之间")
                return False

            return True

        except Exception as e:
            logger.error(f"验证策略 {self.name} 失败: {e}")
            return False

    def get_summary(self) -> str:
        """获取策略摘要"""
        return f"""
策略名称: {self.name}
描述: {self.description or '无描述'}
风险等级: {self.risk_profile.level}
创建时间: {self.created_at.strftime('%Y-%m-%d %H:%M')}

维度权重:
  - 技术指标: {self.dimension_weights.technical}%
  - Hyperliquid: {self.dimension_weights.hyperliquid}%
  - 新闻情绪: {self.dimension_weights.news}%
  - 资金费率: {self.dimension_weights.funding_rate}%
  - 以太坊链上: {self.dimension_weights.ethereum}%

风险控制:
  - 最大仓位: {self.risk_profile.max_position_size}%
  - 止损: {self.risk_profile.stop_loss}%
  - 止盈: {self.risk_profile.take_profit}%
  - 最小信号强度: {self.risk_profile.min_signal_strength}
  - 允许做空: {'是' if self.risk_profile.allow_short else '否'}
  - 最大杠杆: {self.risk_profile.max_leverage}x
"""


class StrategyManager:
    """策略管理器"""

    def __init__(self, config_dir: str = None):
        """
        初始化策略管理器

        Args:
            config_dir: 策略配置目录
        """
        if config_dir is None:
            config_dir = Path(__file__).parent.parent.parent / "config" / "strategies"

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 当前激活的策略
        self.active_strategy: Optional[InvestmentStrategy] = None

        # 加载默认策略
        self._init_default_strategies()

    def _init_default_strategies(self):
        """初始化默认策略"""
        # 检查是否已有策略文件
        if not list(self.config_dir.glob("*.json")):
            logger.info("未找到策略配置，创建默认策略")

            # 创建保守策略
            conservative = self._create_conservative_strategy()
            self.save_strategy(conservative)

            # 创建平衡策略
            balanced = self._create_balanced_strategy()
            self.save_strategy(balanced)

            # 创建激进策略
            aggressive = self._create_aggressive_strategy()
            self.save_strategy(aggressive)

            # 设置平衡策略为默认
            self.set_active_strategy("balanced")

    def _create_conservative_strategy(self) -> InvestmentStrategy:
        """创建保守策略"""
        strategy = InvestmentStrategy(name="conservative")
        strategy.description = "保守型投资策略，注重风险控制，适合稳健投资者"
        strategy.tags = ["保守", "稳健", "低风险"]

        # 维度权重 - 更重视技术面和新闻面
        strategy.dimension_weights.technical = 45.0
        strategy.dimension_weights.hyperliquid = 15.0
        strategy.dimension_weights.news = 20.0
        strategy.dimension_weights.funding_rate = 15.0
        strategy.dimension_weights.ethereum = 5.0

        # 风险配置 - 严格控制
        strategy.risk_profile.level = "conservative"
        strategy.risk_profile.max_position_size = 10.0
        strategy.risk_profile.stop_loss = 3.0
        strategy.risk_profile.take_profit = 10.0
        strategy.risk_profile.min_signal_strength = 75.0
        strategy.risk_profile.allow_short = False
        strategy.risk_profile.max_leverage = 1.0

        # 交易规则 - 严格要求
        strategy.trading_rules.hyperliquid_min_wallets = 5
        strategy.trading_rules.hyperliquid_min_amount = 100000.0
        strategy.trading_rules.hyperliquid_min_net_flow = 200000.0
        strategy.trading_rules.min_dimensions_agree = 4

        return strategy

    def _create_balanced_strategy(self) -> InvestmentStrategy:
        """创建平衡策略（默认）"""
        strategy = InvestmentStrategy(name="balanced")
        strategy.description = "平衡型投资策略，风险收益均衡，适合大多数投资者"
        strategy.tags = ["平衡", "中等风险", "推荐"]

        # 维度权重 - 默认配置
        strategy.dimension_weights.technical = 40.0
        strategy.dimension_weights.hyperliquid = 20.0
        strategy.dimension_weights.news = 15.0
        strategy.dimension_weights.funding_rate = 15.0
        strategy.dimension_weights.ethereum = 10.0

        # 风险配置 - 适中
        strategy.risk_profile.level = "balanced"
        strategy.risk_profile.max_position_size = 20.0
        strategy.risk_profile.stop_loss = 5.0
        strategy.risk_profile.take_profit = 15.0
        strategy.risk_profile.min_signal_strength = 60.0
        strategy.risk_profile.allow_short = False
        strategy.risk_profile.max_leverage = 2.0

        # 交易规则 - 标准要求
        strategy.trading_rules.hyperliquid_min_wallets = 3
        strategy.trading_rules.hyperliquid_min_amount = 50000.0
        strategy.trading_rules.hyperliquid_min_net_flow = 100000.0
        strategy.trading_rules.min_dimensions_agree = 3

        return strategy

    def _create_aggressive_strategy(self) -> InvestmentStrategy:
        """创建激进策略"""
        strategy = InvestmentStrategy(name="aggressive")
        strategy.description = "激进型投资策略，追求高收益，适合风险承受能力强的投资者"
        strategy.tags = ["激进", "高风险高收益", "进阶"]

        # 维度权重 - 重视聪明钱和链上数据
        strategy.dimension_weights.technical = 30.0
        strategy.dimension_weights.hyperliquid = 35.0
        strategy.dimension_weights.news = 10.0
        strategy.dimension_weights.funding_rate = 10.0
        strategy.dimension_weights.ethereum = 15.0

        # 风险配置 - 激进
        strategy.risk_profile.level = "aggressive"
        strategy.risk_profile.max_position_size = 30.0
        strategy.risk_profile.stop_loss = 8.0
        strategy.risk_profile.take_profit = 25.0
        strategy.risk_profile.min_signal_strength = 50.0
        strategy.risk_profile.allow_short = True
        strategy.risk_profile.max_leverage = 5.0

        # 交易规则 - 宽松要求
        strategy.trading_rules.hyperliquid_min_wallets = 2
        strategy.trading_rules.hyperliquid_min_amount = 30000.0
        strategy.trading_rules.hyperliquid_min_net_flow = 50000.0
        strategy.trading_rules.min_dimensions_agree = 2

        return strategy

    def save_strategy(self, strategy: InvestmentStrategy) -> bool:
        """
        保存策略到文件

        Args:
            strategy: 投资策略对象

        Returns:
            bool: 保存成功返回True
        """
        try:
            # 验证策略
            if not strategy.validate():
                logger.error(f"策略 {strategy.name} 验证失败，无法保存")
                return False

            # 更新时间
            strategy.updated_at = datetime.now()

            # 保存到文件
            file_path = self.config_dir / f"{strategy.name}.json"
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(strategy.to_dict(), f, ensure_ascii=False, indent=2)

            logger.info(f"策略 {strategy.name} 已保存到 {file_path}")
            return True

        except Exception as e:
            logger.error(f"保存策略 {strategy.name} 失败: {e}")
            return False

    def load_strategy(self, name: str) -> Optional[InvestmentStrategy]:
        """
        从文件加载策略

        Args:
            name: 策略名称

        Returns:
            InvestmentStrategy: 策略对象，失败返回None
        """
        try:
            file_path = self.config_dir / f"{name}.json"

            if not file_path.exists():
                logger.error(f"策略文件不存在: {file_path}")
                return None

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            strategy = InvestmentStrategy.from_dict(data)

            if not strategy.validate():
                logger.error(f"策略 {name} 验证失败")
                return None

            logger.info(f"成功加载策略: {name}")
            return strategy

        except Exception as e:
            logger.error(f"加载策略 {name} 失败: {e}")
            return None

    def list_strategies(self) -> List[str]:
        """列出所有可用策略"""
        try:
            strategies = []
            for file_path in self.config_dir.glob("*.json"):
                strategies.append(file_path.stem)
            return sorted(strategies)
        except Exception as e:
            logger.error(f"列出策略失败: {e}")
            return []

    def delete_strategy(self, name: str) -> bool:
        """
        删除策略

        Args:
            name: 策略名称

        Returns:
            bool: 删除成功返回True
        """
        try:
            # 不允许删除默认策略
            if name in ['conservative', 'balanced', 'aggressive']:
                logger.warning(f"不能删除默认策略: {name}")
                return False

            file_path = self.config_dir / f"{name}.json"

            if not file_path.exists():
                logger.warning(f"策略不存在: {name}")
                return False

            file_path.unlink()
            logger.info(f"已删除策略: {name}")

            # 如果删除的是当前激活策略，切换到balanced
            if self.active_strategy and self.active_strategy.name == name:
                self.set_active_strategy("balanced")

            return True

        except Exception as e:
            logger.error(f"删除策略 {name} 失败: {e}")
            return False

    def set_active_strategy(self, name: str) -> bool:
        """
        设置激活策略

        Args:
            name: 策略名称

        Returns:
            bool: 设置成功返回True
        """
        strategy = self.load_strategy(name)
        if strategy:
            self.active_strategy = strategy
            logger.info(f"已激活策略: {name}")

            # 保存激活策略配置
            active_file = self.config_dir / "active.txt"
            with open(active_file, 'w') as f:
                f.write(name)

            return True
        return False

    def get_active_strategy(self) -> Optional[InvestmentStrategy]:
        """获取当前激活的策略"""
        if self.active_strategy is None:
            # 尝试从文件加载
            active_file = self.config_dir / "active.txt"
            if active_file.exists():
                with open(active_file, 'r') as f:
                    name = f.read().strip()
                self.set_active_strategy(name)
            else:
                # 默认使用balanced策略
                self.set_active_strategy("balanced")

        return self.active_strategy

    def copy_strategy(self, source_name: str, new_name: str, new_description: str = None) -> bool:
        """
        复制策略

        Args:
            source_name: 源策略名称
            new_name: 新策略名称
            new_description: 新策略描述

        Returns:
            bool: 复制成功返回True
        """
        try:
            # 加载源策略
            source = self.load_strategy(source_name)
            if not source:
                logger.error(f"源策略不存在: {source_name}")
                return False

            # 检查新策略名是否已存在
            if (self.config_dir / f"{new_name}.json").exists():
                logger.error(f"策略已存在: {new_name}")
                return False

            # 创建新策略
            new_strategy = InvestmentStrategy.from_dict(source.to_dict())
            new_strategy.name = new_name
            new_strategy.description = new_description or f"从 {source_name} 复制"
            new_strategy.created_at = datetime.now()
            new_strategy.updated_at = datetime.now()

            # 保存
            return self.save_strategy(new_strategy)

        except Exception as e:
            logger.error(f"复制策略失败: {e}")
            return False


# 全局策略管理器实例
_strategy_manager: Optional[StrategyManager] = None


def get_strategy_manager() -> StrategyManager:
    """获取全局策略管理器实例"""
    global _strategy_manager
    if _strategy_manager is None:
        _strategy_manager = StrategyManager()
    return _strategy_manager


def get_active_strategy() -> InvestmentStrategy:
    """获取当前激活的策略"""
    manager = get_strategy_manager()
    return manager.get_active_strategy()


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("投资策略配置系统测试")
    print("=" * 60)

    # 初始化管理器
    manager = StrategyManager()

    # 列出所有策略
    print("\n📋 可用策略:")
    strategies = manager.list_strategies()
    for name in strategies:
        print(f"  - {name}")

    # 加载并显示每个策略
    print("\n" + "=" * 60)
    for name in strategies:
        strategy = manager.load_strategy(name)
        if strategy:
            print(strategy.get_summary())
            print("=" * 60)

    # 测试激活策略
    print("\n🎯 测试策略激活:")
    manager.set_active_strategy("balanced")
    active = manager.get_active_strategy()
    print(f"当前激活策略: {active.name}")

    # 测试复制策略
    print("\n📋 测试复制策略:")
    success = manager.copy_strategy("balanced", "my_strategy", "我的自定义策略")
    if success:
        print("✅ 策略复制成功")
        my_strategy = manager.load_strategy("my_strategy")
        if my_strategy:
            # 修改一些参数
            my_strategy.dimension_weights.hyperliquid = 30.0
            my_strategy.dimension_weights.technical = 35.0
            my_strategy.dimension_weights.normalize()
            manager.save_strategy(my_strategy)
            print("✅ 自定义策略已保存")

    print("\n✨ 测试完成！")
