"""
策略管理API
提供策略的增删改查、激活、对比等功能
"""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Optional
from pydantic import BaseModel
import logging

from app.strategies.strategy_config import (
    get_strategy_manager,
    InvestmentStrategy,
    DimensionWeights,
    RiskProfile,
    TradingRules,
    TechnicalIndicatorConfig
)
from app.strategies.strategy_analyzer import StrategyBasedAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


# Pydantic模型定义
class DimensionWeightsModel(BaseModel):
    technical: float = 40.0
    hyperliquid: float = 20.0
    news: float = 15.0
    funding_rate: float = 15.0
    ethereum: float = 10.0


class RiskProfileModel(BaseModel):
    level: str = "balanced"
    max_position_size: float = 20.0
    stop_loss: float = 5.0
    take_profit: float = 15.0
    min_signal_strength: float = 60.0
    allow_short: bool = False
    max_leverage: float = 2.0


class TradingRulesModel(BaseModel):
    hyperliquid_min_wallets: int = 3
    hyperliquid_min_amount: float = 50000.0
    hyperliquid_min_net_flow: float = 100000.0
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    news_sentiment_threshold: float = 0.6
    news_min_count: int = 3
    funding_rate_extreme: float = 0.01
    min_dimensions_agree: int = 3


class StrategyCreateModel(BaseModel):
    name: str
    description: str = ""
    dimension_weights: DimensionWeightsModel
    risk_profile: RiskProfileModel
    trading_rules: Optional[TradingRulesModel] = None
    tags: List[str] = []


class StrategyUpdateModel(BaseModel):
    description: Optional[str] = None
    dimension_weights: Optional[DimensionWeightsModel] = None
    risk_profile: Optional[RiskProfileModel] = None
    trading_rules: Optional[TradingRulesModel] = None
    tags: Optional[List[str]] = None


class TestScoresModel(BaseModel):
    technical: float
    hyperliquid: float
    news: float
    funding_rate: float
    ethereum: float


# API路由

@router.get("/list")
async def list_strategies():
    """获取所有策略列表"""
    try:
        manager = get_strategy_manager()
        strategy_names = manager.list_strategies()
        active = manager.get_active_strategy()

        strategies = []
        for name in strategy_names:
            strategy = manager.load_strategy(name)
            if strategy:
                strategies.append({
                    'name': strategy.name,
                    'description': strategy.description,
                    'risk_level': strategy.risk_profile.level,
                    'tags': strategy.tags,
                    'is_active': active and active.name == name,
                    'created_at': strategy.created_at.isoformat(),
                    'updated_at': strategy.updated_at.isoformat()
                })

        return {
            'success': True,
            'strategies': strategies,
            'active_strategy': active.name if active else None
        }

    except Exception as e:
        logger.error(f"获取策略列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail/{name}")
async def get_strategy(name: str):
    """获取策略详情"""
    try:
        manager = get_strategy_manager()
        strategy = manager.load_strategy(name)

        if not strategy:
            raise HTTPException(status_code=404, detail=f"策略不存在: {name}")

        return {
            'success': True,
            'strategy': strategy.to_dict()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取策略详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
async def create_strategy(data: StrategyCreateModel):
    """创建新策略"""
    try:
        manager = get_strategy_manager()

        # 检查名称是否已存在
        if data.name in manager.list_strategies():
            raise HTTPException(status_code=400, detail=f"策略已存在: {data.name}")

        # 创建策略对象
        strategy = InvestmentStrategy(name=data.name)
        strategy.description = data.description
        strategy.tags = data.tags

        # 设置维度权重
        strategy.dimension_weights = DimensionWeights(
            technical=data.dimension_weights.technical,
            hyperliquid=data.dimension_weights.hyperliquid,
            news=data.dimension_weights.news,
            funding_rate=data.dimension_weights.funding_rate,
            ethereum=data.dimension_weights.ethereum
        )

        # 设置风险配置
        strategy.risk_profile = RiskProfile(
            level=data.risk_profile.level,
            max_position_size=data.risk_profile.max_position_size,
            stop_loss=data.risk_profile.stop_loss,
            take_profit=data.risk_profile.take_profit,
            min_signal_strength=data.risk_profile.min_signal_strength,
            allow_short=data.risk_profile.allow_short,
            max_leverage=data.risk_profile.max_leverage
        )

        # 设置交易规则
        if data.trading_rules:
            strategy.trading_rules = TradingRules(
                hyperliquid_min_wallets=data.trading_rules.hyperliquid_min_wallets,
                hyperliquid_min_amount=data.trading_rules.hyperliquid_min_amount,
                hyperliquid_min_net_flow=data.trading_rules.hyperliquid_min_net_flow,
                rsi_oversold=data.trading_rules.rsi_oversold,
                rsi_overbought=data.trading_rules.rsi_overbought
            )

        # 保存策略
        if not manager.save_strategy(strategy):
            raise HTTPException(status_code=500, detail="保存策略失败")

        return {
            'success': True,
            'message': f'策略创建成功: {data.name}',
            'strategy': strategy.to_dict()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/{name}")
async def update_strategy(name: str, data: StrategyUpdateModel):
    """更新策略"""
    try:
        # 不允许修改默认策略
        if name in ['conservative', 'balanced', 'aggressive']:
            raise HTTPException(
                status_code=403,
                detail=f"不能修改默认策略: {name}，请先复制后修改"
            )

        manager = get_strategy_manager()
        strategy = manager.load_strategy(name)

        if not strategy:
            raise HTTPException(status_code=404, detail=f"策略不存在: {name}")

        # 更新字段
        if data.description is not None:
            strategy.description = data.description

        if data.dimension_weights:
            strategy.dimension_weights = DimensionWeights(
                technical=data.dimension_weights.technical,
                hyperliquid=data.dimension_weights.hyperliquid,
                news=data.dimension_weights.news,
                funding_rate=data.dimension_weights.funding_rate,
                ethereum=data.dimension_weights.ethereum
            )

        if data.risk_profile:
            strategy.risk_profile = RiskProfile(
                level=data.risk_profile.level,
                max_position_size=data.risk_profile.max_position_size,
                stop_loss=data.risk_profile.stop_loss,
                take_profit=data.risk_profile.take_profit,
                min_signal_strength=data.risk_profile.min_signal_strength,
                allow_short=data.risk_profile.allow_short,
                max_leverage=data.risk_profile.max_leverage
            )

        if data.tags is not None:
            strategy.tags = data.tags

        # 保存
        if not manager.save_strategy(strategy):
            raise HTTPException(status_code=500, detail="保存策略失败")

        return {
            'success': True,
            'message': f'策略更新成功: {name}',
            'strategy': strategy.to_dict()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/{name}")
async def delete_strategy(name: str):
    """删除策略"""
    try:
        # 不允许删除默认策略
        if name in ['conservative', 'balanced', 'aggressive']:
            raise HTTPException(
                status_code=403,
                detail=f"不能删除默认策略: {name}"
            )

        manager = get_strategy_manager()

        if not manager.delete_strategy(name):
            raise HTTPException(status_code=500, detail="删除策略失败")

        return {
            'success': True,
            'message': f'策略删除成功: {name}'
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/activate/{name}")
async def activate_strategy(name: str):
    """激活策略"""
    try:
        manager = get_strategy_manager()

        if not manager.set_active_strategy(name):
            raise HTTPException(status_code=404, detail=f"策略不存在: {name}")

        return {
            'success': True,
            'message': f'策略已激活: {name}',
            'active_strategy': name
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"激活策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CopyStrategyModel(BaseModel):
    source: str
    dest: str
    description: Optional[str] = None


@router.post("/copy")
async def copy_strategy(data: CopyStrategyModel):
    """复制策略"""
    try:
        manager = get_strategy_manager()

        if data.dest in manager.list_strategies():
            raise HTTPException(status_code=400, detail=f"目标策略已存在: {data.dest}")

        if not manager.copy_strategy(data.source, data.dest, data.description):
            raise HTTPException(status_code=500, detail="复制策略失败")

        return {
            'success': True,
            'message': f'策略复制成功: {data.source} -> {data.dest}'
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"复制策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compare")
async def compare_strategies(strategy_names: List[str]):
    """对比多个策略"""
    try:
        manager = get_strategy_manager()

        comparison = {}
        for name in strategy_names:
            strategy = manager.load_strategy(name)
            if strategy:
                comparison[name] = {
                    'dimension_weights': {
                        'technical': strategy.dimension_weights.technical,
                        'hyperliquid': strategy.dimension_weights.hyperliquid,
                        'news': strategy.dimension_weights.news,
                        'funding_rate': strategy.dimension_weights.funding_rate,
                        'ethereum': strategy.dimension_weights.ethereum
                    },
                    'risk_profile': {
                        'level': strategy.risk_profile.level,
                        'max_position_size': strategy.risk_profile.max_position_size,
                        'stop_loss': strategy.risk_profile.stop_loss,
                        'take_profit': strategy.risk_profile.take_profit,
                        'min_signal_strength': strategy.risk_profile.min_signal_strength,
                        'allow_short': strategy.risk_profile.allow_short,
                        'max_leverage': strategy.risk_profile.max_leverage
                    }
                }

        return {
            'success': True,
            'comparison': comparison
        }

    except Exception as e:
        logger.error(f"对比策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/{name}")
async def test_strategy(name: str, scores: TestScoresModel):
    """测试策略分析结果"""
    try:
        manager = get_strategy_manager()
        strategy = manager.load_strategy(name)

        if not strategy:
            raise HTTPException(status_code=404, detail=f"策略不存在: {name}")

        # 创建分析器
        analyzer = StrategyBasedAnalyzer(strategy)

        # 分析
        dimension_scores = {
            'technical': scores.technical,
            'hyperliquid': scores.hyperliquid,
            'news': scores.news,
            'funding_rate': scores.funding_rate,
            'ethereum': scores.ethereum
        }

        result = analyzer.analyze_symbol("TEST/USDT", dimension_scores)

        return {
            'success': True,
            'result': result
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active")
async def get_active_strategy():
    """获取当前激活的策略"""
    try:
        manager = get_strategy_manager()
        active = manager.get_active_strategy()

        if not active:
            raise HTTPException(status_code=404, detail="没有激活的策略")

        return {
            'success': True,
            'strategy': active.to_dict()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取激活策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
