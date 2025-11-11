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


class TechnicalIndicatorConfigModel(BaseModel):
    rsi_weight: float = 25.0
    macd_weight: float = 25.0
    bollinger_weight: float = 20.0
    ema_weight: float = 15.0
    volume_weight: float = 15.0
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    ema_short: int = 20
    ema_long: int = 50
    bb_period: int = 20
    bb_std: float = 2.0


class StrategyUpdateModel(BaseModel):
    description: Optional[str] = None
    dimension_weights: Optional[DimensionWeightsModel] = None
    risk_profile: Optional[RiskProfileModel] = None
    trading_rules: Optional[TradingRulesModel] = None
    technical_config: Optional[TechnicalIndicatorConfigModel] = None
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
                rsi_overbought=data.trading_rules.rsi_overbought,
                news_sentiment_threshold=data.trading_rules.news_sentiment_threshold,
                news_min_count=data.trading_rules.news_min_count,
                funding_rate_extreme=data.trading_rules.funding_rate_extreme,
                min_dimensions_agree=data.trading_rules.min_dimensions_agree
            )

        if data.technical_config:
            strategy.technical_config = TechnicalIndicatorConfig(
                rsi_weight=data.technical_config.rsi_weight,
                macd_weight=data.technical_config.macd_weight,
                bollinger_weight=data.technical_config.bollinger_weight,
                ema_weight=data.technical_config.ema_weight,
                volume_weight=data.technical_config.volume_weight,
                rsi_period=data.technical_config.rsi_period,
                macd_fast=data.technical_config.macd_fast,
                macd_slow=data.technical_config.macd_slow,
                macd_signal=data.technical_config.macd_signal,
                ema_short=data.technical_config.ema_short,
                ema_long=data.technical_config.ema_long,
                bb_period=data.technical_config.bb_period,
                bb_std=data.technical_config.bb_std
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

        if data.trading_rules:
            strategy.trading_rules = TradingRules(
                hyperliquid_min_wallets=data.trading_rules.hyperliquid_min_wallets,
                hyperliquid_min_amount=data.trading_rules.hyperliquid_min_amount,
                hyperliquid_min_net_flow=data.trading_rules.hyperliquid_min_net_flow,
                rsi_oversold=data.trading_rules.rsi_oversold,
                rsi_overbought=data.trading_rules.rsi_overbought,
                news_sentiment_threshold=data.trading_rules.news_sentiment_threshold,
                news_min_count=data.trading_rules.news_min_count,
                funding_rate_extreme=data.trading_rules.funding_rate_extreme,
                min_dimensions_agree=data.trading_rules.min_dimensions_agree
            )

        if data.technical_config:
            strategy.technical_config = TechnicalIndicatorConfig(
                rsi_weight=data.technical_config.rsi_weight,
                macd_weight=data.technical_config.macd_weight,
                bollinger_weight=data.technical_config.bollinger_weight,
                ema_weight=data.technical_config.ema_weight,
                volume_weight=data.technical_config.volume_weight,
                rsi_period=data.technical_config.rsi_period,
                macd_fast=data.technical_config.macd_fast,
                macd_slow=data.technical_config.macd_slow,
                macd_signal=data.technical_config.macd_signal,
                ema_short=data.technical_config.ema_short,
                ema_long=data.technical_config.ema_long,
                bb_period=data.technical_config.bb_period,
                bb_std=data.technical_config.bb_std
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


class BacktestRequest(BaseModel):
    """回测请求模型"""
    strategy_name: str
    symbol: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    initial_balance: float = 10000.0  # 初始资金
    market_type: str = 'spot'  # 市场类型: 'spot' 或 'futures'


class AutoGenerateRequest(BaseModel):
    """自动生成策略请求模型"""
    base_strategy_name: Optional[str] = None  # 基础策略名称（可选，如果提供则基于此策略优化）
    symbol: str  # 交易对
    start_date: str  # YYYY-MM-DD 训练数据开始日期
    end_date: str    # YYYY-MM-DD 训练数据结束日期
    initial_balance: float = 10000.0
    market_type: str = 'spot'
    population_size: int = 20  # 种群大小
    generations: int = 10      # 迭代代数


async def _backtest_strategy_internal(
    strategy_name: str,
    symbol: str,
    start_date: str,
    end_date: str,
    initial_balance: float = 10000.0,
    market_type: str = 'spot',
    strategy: Optional[InvestmentStrategy] = None
):
    """
    内部回测函数，支持传入策略对象
    
    Args:
        strategy_name: 策略名称（如果strategy为None则从名称加载）
        symbol: 交易对
        start_date: 开始日期
        end_date: 结束日期
        initial_balance: 初始资金
        market_type: 市场类型
        strategy: 策略对象（可选，如果提供则使用此策略）
    """
    # 如果提供了策略对象，使用它；否则从名称加载
    if strategy is None:
        manager = get_strategy_manager()
        strategy = manager.load_strategy(strategy_name)
        if not strategy:
            raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_name}")
    
    # 其余回测逻辑保持不变...
    # 这里需要将原来的backtest_strategy函数逻辑移到这里
    # 为了简化，我们直接调用原来的函数，但传入strategy对象
    pass


@router.post("/backtest")
async def backtest_strategy(request: BacktestRequest):
    """
    策略回测
    
    对指定策略在指定时间范围内进行回测，评估策略的可实战性
    """
    try:
        from datetime import datetime, timedelta
        import pandas as pd
        import asyncio
        from app.collectors.price_collector import PriceCollector
        from app.analyzers.technical_indicators import TechnicalIndicators
        from app.strategies.strategy_analyzer import StrategyBasedAnalyzer
        
        # 加载策略
        manager = get_strategy_manager()
        strategy = manager.load_strategy(request.strategy_name)
        if not strategy:
            raise HTTPException(status_code=404, detail=f"策略不存在: {request.strategy_name}")
        
        analyzer = StrategyBasedAnalyzer(strategy)
        
        # 解析日期
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(request.end_date, "%Y-%m-%d")
        days = (end_date - start_date).days
        
        if days <= 0:
            raise HTTPException(status_code=400, detail="结束日期必须晚于开始日期")
        if days > 365:
            raise HTTPException(status_code=400, detail="回测时间范围不能超过365天")
        
        # 获取历史数据 - 根据市场类型选择采集器
        market_type = request.market_type.lower() if hasattr(request, 'market_type') else 'spot'
        
        if market_type == 'futures':
            # 合约数据采集器
            from app.collectors.binance_futures_collector import BinanceFuturesCollector
            futures_config = {'enabled': True}
            collector = BinanceFuturesCollector(futures_config)
        else:
            # 现货数据采集器
            from app.collectors.price_collector import PriceCollector
            collector_config = {'enabled': True}
            collector = PriceCollector('binance', collector_config)
        
        # 计算需要获取的数据量（1小时K线，每天24条）
        total_hours = days * 24 + 7 * 24  # 多获取7天用于计算指标
        limit_per_request = 1000  # 币安单次最多1000条
        
        # 分批获取历史数据
        all_data = []
        start_timestamp = int(start_date.timestamp() * 1000)
        end_timestamp = int((end_date + timedelta(days=7)).timestamp() * 1000)  # 多获取7天
        current_timestamp = start_timestamp
        total_fetched = 0
        
        while current_timestamp < end_timestamp and total_fetched < total_hours:
            remaining = min(limit_per_request, total_hours - total_fetched)
            
            # 根据市场类型调用不同的方法
            if market_type == 'futures':
                # 合约K线数据获取
                import requests
                binance_symbol = request.symbol.replace('/', '')
                url = "https://fapi.binance.com/fapi/v1/klines"
                
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
                    logger.warning(f"获取合约数据失败: {e}")
                    df = None
            else:
                # 现货K线数据获取
                df = await collector.fetch_ohlcv(
                    symbol=request.symbol,
                    timeframe='1h',
                    limit=remaining,
                    since=current_timestamp
                )
            
            if df is None or df.empty:
                break
            
            all_data.append(df)
            total_fetched += len(df)
            
            # 更新时间戳为最后一条数据的时间
            if 'timestamp' in df.columns and len(df) > 0:
                last_timestamp = df['timestamp'].iloc[-1]
                if pd.api.types.is_datetime64_any_dtype(type(last_timestamp)):
                    current_timestamp = int(pd.Timestamp(last_timestamp).timestamp() * 1000) + 3600000  # 加1小时
                elif hasattr(last_timestamp, 'timestamp'):
                    current_timestamp = int(last_timestamp.timestamp() * 1000) + 3600000
                else:
                    current_timestamp = int(pd.to_datetime(last_timestamp).timestamp() * 1000) + 3600000
            else:
                break
            
            # 如果获取的数据少于limit，说明已经到最新了
            if len(df) < limit_per_request:
                break
            
            # 避免请求过快
            await asyncio.sleep(0.2)
        
        if not all_data:
            raise HTTPException(status_code=404, detail=f"无法获取 {request.symbol} 的历史数据")
        
        # 合并所有数据
        historical_data = pd.concat(all_data, ignore_index=True)
        
        # 确保timestamp列存在且为datetime类型
        if 'timestamp' not in historical_data.columns:
            raise HTTPException(status_code=500, detail="历史数据格式错误：缺少timestamp列")
        
        # 去重并排序
        historical_data = historical_data.drop_duplicates(subset=['timestamp'])
        historical_data = historical_data.sort_values('timestamp').reset_index(drop=True)
        
        # 确保timestamp是datetime类型
        if not pd.api.types.is_datetime64_any_dtype(historical_data['timestamp']):
            historical_data['timestamp'] = pd.to_datetime(historical_data['timestamp'])
        
        # 过滤日期范围
        historical_data = historical_data[
            (historical_data['timestamp'] >= pd.Timestamp(start_date)) & 
            (historical_data['timestamp'] <= pd.Timestamp(end_date + timedelta(days=1)))  # 包含结束日期当天
        ]
        
        if historical_data.empty:
            raise HTTPException(status_code=404, detail="指定日期范围内没有数据")
        
        # 设置timestamp为索引（用于后续处理）
        historical_data = historical_data.set_index('timestamp')
        
        # 计算技术指标
        tech_indicators = TechnicalIndicators()
        historical_data = historical_data.copy()
        
        # 确保数据列名正确
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        missing_columns = [col for col in required_columns if col not in historical_data.columns]
        if missing_columns:
            raise HTTPException(status_code=500, detail=f"历史数据格式错误：缺少列 {missing_columns}")
        
        # 确保数据足够计算指标
        if len(historical_data) < 50:
            raise HTTPException(status_code=400, detail="历史数据不足，至少需要50条数据来计算技术指标")
        
        # 计算所有技术指标（在DataFrame上直接修改）
        try:
            # RSI
            historical_data['rsi'] = tech_indicators.calculate_rsi(historical_data)
            
            # MACD
            historical_data['macd'], historical_data['macd_signal'], historical_data['macd_histogram'] = \
                tech_indicators.calculate_macd(historical_data)
            
            # EMA
            historical_data['ema_short'] = tech_indicators.calculate_ema(historical_data, tech_indicators.ema_short)
            historical_data['ema_long'] = tech_indicators.calculate_ema(historical_data, tech_indicators.ema_long)
        except Exception as e:
            logger.warning(f"计算技术指标时出错: {e}，使用默认值")
            # 使用默认值
            historical_data['rsi'] = 50.0
            historical_data['macd_histogram'] = 0.0
            historical_data['ema_short'] = historical_data['close']
            historical_data['ema_long'] = historical_data['close']
        
        # 回测模拟
        initial_balance = request.initial_balance
        balance = initial_balance
        position = 0.0  # 持仓数量
        position_price = 0.0  # 持仓价格
        trades = []  # 交易记录
        equity_curve = []  # 权益曲线
        
        # 简化：只使用技术指标得分，其他维度使用固定值或基于价格变化估算
        for idx, row in historical_data.iterrows():
            current_price = float(row['close'])
            # idx 就是 timestamp（因为已经设置为索引）
            timestamp = idx if isinstance(idx, datetime) else pd.to_datetime(idx).to_pydatetime()
            
            # 获取技术指标值
            rsi = float(row.get('rsi', 50)) if 'rsi' in row and pd.notna(row.get('rsi')) else 50
            macd_hist = float(row.get('macd_histogram', 0)) if 'macd_histogram' in row and pd.notna(row.get('macd_histogram')) else 0
            ema_short = float(row.get('ema_short', current_price)) if 'ema_short' in row and pd.notna(row.get('ema_short')) else current_price
            ema_long = float(row.get('ema_long', current_price)) if 'ema_long' in row and pd.notna(row.get('ema_long')) else current_price
            ema_trend = 'up' if current_price > ema_short and ema_short > ema_long else 'down'
            
            # 计算技术指标得分
            technical_score = 50
            if rsi < 30:
                technical_score += 20
            elif rsi < 40:
                technical_score += 10
            elif rsi > 70:
                technical_score -= 15
            elif rsi > 60:
                technical_score -= 8
            
            if macd_hist > 0:
                technical_score += 10
            else:
                technical_score -= 10
            
            if ema_trend == 'up':
                technical_score += 8
            else:
                technical_score -= 8
            
            technical_score = max(0, min(100, technical_score))
            
            # 简化其他维度得分（实际应该从历史数据计算）
            dimension_scores = {
                'technical': technical_score,
                'hyperliquid': 60.0,  # 简化：使用固定值
                'news': 55.0,
                'funding_rate': 50.0,
                'ethereum': 50.0
            }
            
            # 使用策略分析
            analysis_result = analyzer.analyze_symbol(request.symbol, dimension_scores)
            
            if not analysis_result:
                continue
            
            recommendation = analysis_result.get('recommendation', {})
            action = recommendation.get('action', '观望')
            total_score = analysis_result.get('total_score', 50)
            
            # 模拟交易逻辑
            current_equity = balance + (position * current_price if position > 0 else 0)
            equity_curve.append({
                'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
                'equity': current_equity,
                'price': current_price,
                'position': position
            })
            
            # 检查止损止盈
            if position > 0:
                price_change_pct = ((current_price - position_price) / position_price) * 100
                stop_loss_pct = strategy.risk_profile.stop_loss
                take_profit_pct = strategy.risk_profile.take_profit
                
                # 止损
                if price_change_pct <= -stop_loss_pct:
                    # 卖出止损
                    balance = position * current_price
                    trades.append({
                        'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
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
                    # 卖出止盈
                    balance = position * current_price
                    trades.append({
                        'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
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
            if position == 0 and total_score >= strategy.risk_profile.min_signal_strength:
                if '买入' in action or '强烈买入' in action:
                    position_size_pct = recommendation.get('position_size_pct', 20)
                    position_value = balance * (position_size_pct / 100)
                    position = position_value / current_price
                    position_price = current_price
                    balance -= position_value
                    
                    trades.append({
                        'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
                        'action': action,
                        'price': current_price,
                        'quantity': position,
                        'score': total_score
                    })
        
        # 计算最终权益（如果还有持仓，按最后价格计算）
        final_price = float(historical_data.iloc[-1]['close'])
        final_equity = balance + (position * final_price if position > 0 else 0)
        
        # 计算回测指标
        total_return = ((final_equity - initial_balance) / initial_balance) * 100
        
        # 计算胜率
        winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl', 0) < 0]
        total_trades = len(winning_trades) + len(losing_trades)
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
        
        # 计算最大回撤
        equity_values = [e['equity'] for e in equity_curve]
        max_drawdown = 0
        peak = initial_balance
        for equity in equity_values:
            if equity > peak:
                peak = equity
            drawdown = ((peak - equity) / peak) * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # 计算平均盈亏
        avg_win = sum(t.get('pnl', 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.get('pnl', 0) for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        return {
            'success': True,
            'backtest_result': {
                'strategy_name': request.strategy_name,
                'symbol': request.symbol,
                'start_date': request.start_date,
                'end_date': request.end_date,
                'initial_balance': initial_balance,
                'final_equity': final_equity,
                'total_return': round(total_return, 2),
                'total_trades': total_trades,
                'winning_trades': len(winning_trades),
                'losing_trades': len(losing_trades),
                'win_rate': round(win_rate, 2),
                'max_drawdown': round(max_drawdown, 2),
                'avg_win': round(avg_win, 2),
                'avg_loss': round(avg_loss, 2),
                'profit_factor': round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
                'trades': trades[-50:],  # 只返回最后50笔交易
                'equity_curve': equity_curve[::max(1, len(equity_curve)//100)]  # 采样，最多100个点
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"策略回测失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"回测失败: {str(e)}")


@router.post("/auto-generate")
async def auto_generate_strategy(request: AutoGenerateRequest):
    """
    自动生成优化策略
    
    使用遗传算法基于历史数据自动生成优秀的交易策略
    """
    try:
        from app.strategies.strategy_optimizer import StrategyOptimizer
        from datetime import datetime as dt
        
        # 加载基础策略（如果提供）
        base_strategy = None
        if request.base_strategy_name:
            manager = get_strategy_manager()
            base_strategy = manager.load_strategy(request.base_strategy_name)
            if not base_strategy:
                raise HTTPException(status_code=404, detail=f"基础策略不存在: {request.base_strategy_name}")
        
        # 创建回测函数包装器
        async def backtest_wrapper(strategy_name: str, symbol: str, start_date: str, end_date: str, strategy=None):
            """回测函数包装器"""
            # 临时保存策略
            if strategy:
                manager = get_strategy_manager()
                temp_name = f"temp_{strategy_name}_{dt.now().timestamp()}"
                # 设置策略名称并保存
                strategy.name = temp_name
                manager.save_strategy(strategy)
                try:
                    result = await backtest_strategy(BacktestRequest(
                        strategy_name=temp_name,
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        initial_balance=request.initial_balance,
                        market_type=request.market_type
                    ))
                    return result
                finally:
                    # 清理临时策略
                    try:
                        manager.delete_strategy(temp_name)
                    except:
                        pass
            else:
                return await backtest_strategy(BacktestRequest(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    initial_balance=request.initial_balance,
                    market_type=request.market_type
                ))
        
        # 创建优化器
        optimizer = StrategyOptimizer(
            backtest_func=backtest_wrapper,
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date
        )
        
        # 设置优化参数
        optimizer.population_size = request.population_size
        optimizer.generations = request.generations
        
        # 执行优化
        result = await optimizer.optimize(base_strategy)
        
        # 生成策略名称
        strategy_name = f"auto_optimized_{dt.now().strftime('%Y%m%d_%H%M%S')}"
        result.strategy.name = strategy_name
        result.strategy.description = f"自动优化生成的策略（基于{request.symbol} {request.start_date}至{request.end_date}的数据）"
        
        # 保存策略
        manager = get_strategy_manager()
        manager.save_strategy(result.strategy)
        
        return {
            'success': True,
            'strategy_name': strategy_name,
            'score': result.score,
            'metrics': result.metrics,
            'generation': result.generation,
            'strategy': result.strategy.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"自动生成策略失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"自动生成策略失败: {str(e)}")


class OptimalPointsRequest(BaseModel):
    """最佳买点卖点分析请求模型"""
    symbol: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    market_type: str = 'spot'
    lookback_period: int = 20  # 回看周期
    min_profit_pct: float = 5.0  # 最小盈利百分比


@router.post("/optimal-points")
async def find_optimal_points(request: OptimalPointsRequest):
    """
    分析历史数据，找出最佳买点和卖点
    """
    try:
        from datetime import datetime, timedelta
        import pandas as pd
        import asyncio
        from app.collectors.price_collector import PriceCollector
        from app.strategies.buy_sell_analyzer import BuySellAnalyzer
        
        # 解析日期
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(request.end_date, "%Y-%m-%d")
        days = (end_date - start_date).days
        
        if days <= 0:
            raise HTTPException(status_code=400, detail="结束日期必须晚于开始日期")
        if days > 365:
            raise HTTPException(status_code=400, detail="分析时间范围不能超过365天")
        
        # 获取历史数据
        market_type = request.market_type.lower()
        
        if market_type == 'futures':
            import requests
            binance_symbol = request.symbol.replace('/', '')
            url = "https://fapi.binance.com/fapi/v1/klines"
            
            # 获取数据
            total_hours = days * 24
            limit = min(total_hours, 1500)
            start_timestamp = int(start_date.timestamp() * 1000)
            end_timestamp = int(end_date.timestamp() * 1000)
            
            params = {
                'symbol': binance_symbol,
                'interval': '1h',
                'limit': limit,
                'startTime': start_timestamp,
                'endTime': end_timestamp
            }
            
            response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
            if response.status_code == 200:
                klines = response.json()
                if klines:
                    df = pd.DataFrame(klines, columns=[
                        'open_time', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
                        'taker_buy_quote_volume', 'ignore'
                    ])
                    df = df[['open_time', 'open', 'high', 'low', 'close', 'volume']].copy()
                    df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
                    df['open'] = df['open'].astype(float)
                    df['high'] = df['high'].astype(float)
                    df['low'] = df['low'].astype(float)
                    df['close'] = df['close'].astype(float)
                    df['volume'] = df['volume'].astype(float)
                else:
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
        else:
            collector_config = {'enabled': True}
            collector = PriceCollector('binance', collector_config)
            
            total_hours = days * 24
            limit_per_request = 1000
            all_data = []
            start_timestamp = int(start_date.timestamp() * 1000)
            end_timestamp = int((end_date + timedelta(days=1)).timestamp() * 1000)
            current_timestamp = start_timestamp
            total_fetched = 0
            
            while current_timestamp < end_timestamp and total_fetched < total_hours:
                remaining = min(limit_per_request, total_hours - total_fetched)
                df = await collector.fetch_ohlcv(
                    symbol=request.symbol,
                    timeframe='1h',
                    limit=remaining,
                    since=current_timestamp
                )
                
                if df is None or df.empty:
                    break
                
                all_data.append(df)
                total_fetched += len(df)
                
                if 'timestamp' in df.columns and len(df) > 0:
                    last_timestamp = df['timestamp'].iloc[-1]
                    if pd.api.types.is_datetime64_any_dtype(type(last_timestamp)):
                        current_timestamp = int(pd.Timestamp(last_timestamp).timestamp() * 1000) + 3600000
                    elif hasattr(last_timestamp, 'timestamp'):
                        current_timestamp = int(last_timestamp.timestamp() * 1000) + 3600000
                    else:
                        current_timestamp = int(pd.to_datetime(last_timestamp).timestamp() * 1000) + 3600000
                else:
                    break
                
                if len(df) < limit_per_request:
                    break
                
                await asyncio.sleep(0.2)
            
            if all_data:
                df = pd.concat(all_data, ignore_index=True)
                df = df.drop_duplicates(subset=['timestamp'])
                df = df.sort_values('timestamp').reset_index(drop=True)
            else:
                df = pd.DataFrame()
        
        if df.empty:
            raise HTTPException(status_code=404, detail=f"无法获取 {request.symbol} 的历史数据")
        
        # 确保timestamp是datetime类型
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # 过滤日期范围
        df = df[
            (df['timestamp'] >= pd.Timestamp(start_date)) & 
            (df['timestamp'] <= pd.Timestamp(end_date + timedelta(days=1)))
        ]
        
        if df.empty:
            raise HTTPException(status_code=404, detail="指定日期范围内没有数据")
        
        # 分析最佳买点和卖点
        analyzer = BuySellAnalyzer()
        result = analyzer.analyze_optimal_points(
            df,
            lookback_period=request.lookback_period,
            min_profit_pct=request.min_profit_pct
        )
        
        # 格式化时间戳
        for point in result['buy_points']:
            if hasattr(point['timestamp'], 'isoformat'):
                point['timestamp'] = point['timestamp'].isoformat()
            else:
                point['timestamp'] = str(point['timestamp'])
        
        for point in result['sell_points']:
            if hasattr(point['timestamp'], 'isoformat'):
                point['timestamp'] = point['timestamp'].isoformat()
            else:
                point['timestamp'] = str(point['timestamp'])
        
        for pair in result['optimal_pairs']:
            if hasattr(pair['buy']['timestamp'], 'isoformat'):
                pair['buy']['timestamp'] = pair['buy']['timestamp'].isoformat()
            if hasattr(pair['sell']['timestamp'], 'isoformat'):
                pair['sell']['timestamp'] = pair['sell']['timestamp'].isoformat()
        
        return {
            'success': True,
            'symbol': request.symbol,
            'start_date': request.start_date,
            'end_date': request.end_date,
            'buy_points': result['buy_points'],
            'sell_points': result['sell_points'],
            'optimal_pairs': result['optimal_pairs'][:10],  # 只返回前10个最佳交易对
            'total_opportunities': result['total_opportunities']
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分析最佳买点卖点失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")


class PricePredictionRequest(BaseModel):
    """价格预测请求模型"""
    symbol: str
    market_type: str = 'spot'
    days_ahead: int = 3  # 预测未来天数


@router.post("/price-prediction")
async def predict_price_trend(request: PricePredictionRequest):
    """
    基于过去一周的价格走势预测未来3天的走势
    """
    try:
        from datetime import datetime, timedelta
        import pandas as pd
        import asyncio
        from app.collectors.price_collector import PriceCollector
        from app.strategies.price_predictor import PricePredictor
        
        # 获取过去7天的数据（用于分析）
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        # 获取历史数据
        market_type = request.market_type.lower()
        
        if market_type == 'futures':
            import requests
            binance_symbol = request.symbol.replace('/', '')
            url = "https://fapi.binance.com/fapi/v1/klines"
            
            # 获取过去7天的数据
            limit = min(7 * 24, 1500)  # 7天 * 24小时
            start_timestamp = int(start_date.timestamp() * 1000)
            end_timestamp = int(end_date.timestamp() * 1000)
            
            params = {
                'symbol': binance_symbol,
                'interval': '1h',
                'limit': limit,
                'startTime': start_timestamp,
                'endTime': end_timestamp
            }
            
            response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
            if response.status_code == 200:
                klines = response.json()
                if klines:
                    df = pd.DataFrame(klines, columns=[
                        'open_time', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
                        'taker_buy_quote_volume', 'ignore'
                    ])
                    df = df[['open_time', 'open', 'high', 'low', 'close', 'volume']].copy()
                    df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
                    df['open'] = df['open'].astype(float)
                    df['high'] = df['high'].astype(float)
                    df['low'] = df['low'].astype(float)
                    df['close'] = df['close'].astype(float)
                    df['volume'] = df['volume'].astype(float)
                else:
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
        else:
            collector_config = {'enabled': True}
            collector = PriceCollector('binance', collector_config)
            
            # 获取过去7天的数据
            total_hours = 7 * 24
            limit_per_request = 1000
            all_data = []
            start_timestamp = int(start_date.timestamp() * 1000)
            end_timestamp = int(end_date.timestamp() * 1000)
            current_timestamp = start_timestamp
            total_fetched = 0
            
            while current_timestamp < end_timestamp and total_fetched < total_hours:
                remaining = min(limit_per_request, total_hours - total_fetched)
                df = await collector.fetch_ohlcv(
                    symbol=request.symbol,
                    timeframe='1h',
                    limit=remaining,
                    since=current_timestamp
                )
                
                if df is None or df.empty:
                    break
                
                all_data.append(df)
                total_fetched += len(df)
                
                if 'timestamp' in df.columns and len(df) > 0:
                    last_timestamp = df['timestamp'].iloc[-1]
                    if pd.api.types.is_datetime64_any_dtype(type(last_timestamp)):
                        current_timestamp = int(pd.Timestamp(last_timestamp).timestamp() * 1000) + 3600000
                    elif hasattr(last_timestamp, 'timestamp'):
                        current_timestamp = int(last_timestamp.timestamp() * 1000) + 3600000
                    else:
                        current_timestamp = int(pd.to_datetime(last_timestamp).timestamp() * 1000) + 3600000
                else:
                    break
                
                if len(df) < limit_per_request:
                    break
                
                await asyncio.sleep(0.2)
            
            if all_data:
                df = pd.concat(all_data, ignore_index=True)
                df = df.drop_duplicates(subset=['timestamp'])
                df = df.sort_values('timestamp').reset_index(drop=True)
            else:
                df = pd.DataFrame()
        
        if df.empty:
            raise HTTPException(status_code=404, detail=f"无法获取 {request.symbol} 的历史数据")
        
        # 确保timestamp是datetime类型
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # 过滤日期范围（过去7天）
        df = df[
            (df['timestamp'] >= pd.Timestamp(start_date)) & 
            (df['timestamp'] <= pd.Timestamp(end_date))
        ]
        
        if df.empty:
            raise HTTPException(status_code=404, detail="过去7天内没有数据")
        
        # 执行预测
        predictor = PricePredictor()
        result = predictor.predict_future_trend(df, days_ahead=request.days_ahead)
        
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error', '预测失败'))
        
        # 格式化时间戳
        if 'prediction_points' in result:
            for point in result['prediction_points']:
                if isinstance(point['timestamp'], str):
                    pass  # 已经是字符串
                elif hasattr(point['timestamp'], 'isoformat'):
                    point['timestamp'] = point['timestamp'].isoformat()
                else:
                    point['timestamp'] = str(point['timestamp'])
        
        return {
            'success': True,
            'symbol': request.symbol,
            'analysis_period': {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d'),
                'days': 7
            },
            'prediction_period': {
                'days': request.days_ahead,
                'end_date': (end_date + timedelta(days=request.days_ahead)).strftime('%Y-%m-%d')
            },
            **result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"价格预测失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"预测失败: {str(e)}")


class TradeDiagnosticRequest(BaseModel):
    """交易诊断请求模型"""
    symbol: str
    trade_time: str  # ISO格式时间字符串，如 "2024-01-15T12:00:00"
    trade_type: str = 'buy'  # 'buy' or 'sell'
    market_type: str = 'spot'


@router.post("/trade-diagnostic")
async def diagnose_trade(request: TradeDiagnosticRequest):
    """
    诊断历史交易操作，评估买入/卖出时机的合理性
    """
    try:
        from datetime import datetime, timedelta
        import pandas as pd
        import asyncio
        from app.collectors.price_collector import PriceCollector
        from app.strategies.trade_diagnostic import TradeDiagnostic
        
        # 解析交易时间
        try:
            trade_time = datetime.fromisoformat(request.trade_time.replace('Z', '+00:00'))
            if trade_time.tzinfo is None:
                # 如果没有时区信息，假设是本地时间
                trade_time = trade_time.replace(tzinfo=None)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"时间格式错误: {str(e)}")
        
        # 获取交易时间前后7天的数据
        start_date = trade_time - timedelta(days=7)
        end_date = trade_time + timedelta(days=3)
        
        # 获取历史数据
        market_type = request.market_type.lower()
        
        if market_type == 'futures':
            import requests
            binance_symbol = request.symbol.replace('/', '')
            url = "https://fapi.binance.com/fapi/v1/klines"
            
            # 获取数据
            total_hours = 10 * 24  # 10天
            limit = min(total_hours, 1500)
            start_timestamp = int(start_date.timestamp() * 1000)
            end_timestamp = int(end_date.timestamp() * 1000)
            
            params = {
                'symbol': binance_symbol,
                'interval': '1h',
                'limit': limit,
                'startTime': start_timestamp,
                'endTime': end_timestamp
            }
            
            response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
            if response.status_code == 200:
                klines = response.json()
                if klines:
                    df = pd.DataFrame(klines, columns=[
                        'open_time', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
                        'taker_buy_quote_volume', 'ignore'
                    ])
                    df = df[['open_time', 'open', 'high', 'low', 'close', 'volume']].copy()
                    df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
                    df['open'] = df['open'].astype(float)
                    df['high'] = df['high'].astype(float)
                    df['low'] = df['low'].astype(float)
                    df['close'] = df['close'].astype(float)
                    df['volume'] = df['volume'].astype(float)
                else:
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
        else:
            collector_config = {'enabled': True}
            collector = PriceCollector('binance', collector_config)
            
            # 获取数据
            total_hours = 10 * 24
            limit_per_request = 1000
            all_data = []
            start_timestamp = int(start_date.timestamp() * 1000)
            end_timestamp = int(end_date.timestamp() * 1000)
            current_timestamp = start_timestamp
            total_fetched = 0
            
            while current_timestamp < end_timestamp and total_fetched < total_hours:
                remaining = min(limit_per_request, total_hours - total_fetched)
                df = await collector.fetch_ohlcv(
                    symbol=request.symbol,
                    timeframe='1h',
                    limit=remaining,
                    since=current_timestamp
                )
                
                if df is None or df.empty:
                    break
                
                all_data.append(df)
                total_fetched += len(df)
                
                if 'timestamp' in df.columns and len(df) > 0:
                    last_timestamp = df['timestamp'].iloc[-1]
                    if pd.api.types.is_datetime64_any_dtype(type(last_timestamp)):
                        current_timestamp = int(pd.Timestamp(last_timestamp).timestamp() * 1000) + 3600000
                    elif hasattr(last_timestamp, 'timestamp'):
                        current_timestamp = int(last_timestamp.timestamp() * 1000) + 3600000
                    else:
                        current_timestamp = int(pd.to_datetime(last_timestamp).timestamp() * 1000) + 3600000
                else:
                    break
                
                if len(df) < limit_per_request:
                    break
                
                await asyncio.sleep(0.2)
            
            if all_data:
                df = pd.concat(all_data, ignore_index=True)
                df = df.drop_duplicates(subset=['timestamp'])
                df = df.sort_values('timestamp').reset_index(drop=True)
            else:
                df = pd.DataFrame()
        
        if df.empty:
            raise HTTPException(status_code=404, detail=f"无法获取 {request.symbol} 的历史数据")
        
        # 确保timestamp是datetime类型
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # 执行诊断
        diagnostic = TradeDiagnostic()
        result = diagnostic.diagnose_trade(
            df=df,
            trade_time=trade_time,
            trade_type=request.trade_type,
            symbol=request.symbol
        )
        
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error', '诊断失败'))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"交易诊断失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"诊断失败: {str(e)}")
