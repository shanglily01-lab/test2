"""
API路由定义
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict, Optional
from loguru import logger

from app.database.db_service import DatabaseService
from app.services.analysis_service import AnalysisService


router = APIRouter()

# 全局数据库服务
_db_service = None


def get_db_service():
    """获取数据库服务单例"""
    global _db_service
    if _db_service is None:
        import yaml
        from pathlib import Path
        # 使用绝对路径
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / 'config.yaml'
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        _db_service = DatabaseService(config.get('database', {}))
    return _db_service


def get_db_session():
    """获取数据库会话"""
    db_service = get_db_service()
    session = db_service.get_session()
    try:
        yield session
    finally:
        session.close()


@router.get("/api")
async def api_info():
    """API信息"""
    return {
        "name": "Crypto Analyzer API",
        "version": "1.2.0",
        "status": "running",
        "endpoints": [
            "/api/dashboard",
            "/api/prices",
            "/api/analysis/{symbol}",
            "/api/kline/{symbol}",
            "/api/news",
            "/api/sentiment/{symbol}",
            "/api/funding-rate/{symbol}",
            "/api/smart-money/addresses",
            "/api/smart-money/transactions/{token_symbol}",
            "/api/smart-money/signals",
            "/api/smart-money/signals/{token_symbol}",
            "/api/smart-money/dashboard"
        ]
    }


# 注释掉这个端点，因为 main.py 中有更完整的实现（带缓存和enhanced_dashboard）
# @router.get("/api/dashboard")
# async def get_dashboard(session: Session = Depends(get_db_session)):
#     """
#     获取仪表盘数据
#     包含: 最新价格、投资建议、新闻
#     """
#     try:
#         analysis_service = AnalysisService(session)
#         data = analysis_service.get_dashboard_data()
#         return {"success": True, "data": data}
#     except Exception as e:
#         logger.error(f"获取仪表盘数据失败: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/prices")
async def get_prices(limit: int = 20, session: Session = Depends(get_db_session)):
    """获取最新价格列表"""
    try:
        analysis_service = AnalysisService(session)
        prices = analysis_service.get_latest_prices(limit=limit)
        return {"success": True, "data": prices}
    except Exception as e:
        logger.error(f"获取价格失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/analysis/{symbol}")
async def get_analysis(symbol: str, session: Session = Depends(get_db_session)):
    """
    获取指定币种的详细分析
    包含: 技术指标、投资建议、新闻情绪
    """
    try:
        analysis_service = AnalysisService(session)
        advice = analysis_service.generate_investment_advice(symbol)
        return {"success": True, "data": advice}
    except Exception as e:
        logger.error(f"获取分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/kline/{symbol}")
async def get_kline(
    symbol: str,
    timeframe: str = '1h',
    limit: int = 100,
    session: Session = Depends(get_db_session)
):
    """获取K线数据"""
    try:
        analysis_service = AnalysisService(session)
        df = analysis_service.get_kline_data(symbol, timeframe, limit)

        if df.empty:
            return {"success": True, "data": []}

        # 转换为JSON格式
        kline_data = df.to_dict('records')
        # 转换timestamp为字符串
        for item in kline_data:
            if 'timestamp' in item:
                item['timestamp'] = item['timestamp'].strftime('%Y-%m-%d %H:%M:%S')

        return {"success": True, "data": kline_data}
    except Exception as e:
        logger.error(f"获取K线数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/news")
async def get_news(
    symbol: str = None,
    hours: int = 24,
    limit: int = 50,
    session: Session = Depends(get_db_session)
):
    """获取新闻列表"""
    try:
        from app.database.models import NewsData
        from sqlalchemy import desc
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(hours=hours)
        query = session.query(NewsData).filter(
            NewsData.published_datetime >= cutoff_time
        )

        if symbol:
            symbol_code = symbol.split('/')[0] if '/' in symbol else symbol
            query = query.filter(NewsData.symbols.like(f'%{symbol_code}%'))

        news_list = query.order_by(desc(NewsData.published_datetime)).limit(limit).all()

        data = [{
            'title': n.title,
            'source': n.source,
            'sentiment': n.sentiment,
            'symbols': n.symbols,
            'published_at': n.published_datetime.strftime('%Y-%m-%d %H:%M') if n.published_datetime else '',
            'url': n.url,
            'description': n.description
        } for n in news_list]

        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"获取新闻失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/sentiment/{symbol}")
async def get_sentiment(
    symbol: str,
    hours: int = 24,
    session: Session = Depends(get_db_session)
):
    """获取新闻情绪分析"""
    try:
        analysis_service = AnalysisService(session)
        sentiment = analysis_service.get_news_sentiment(symbol, hours)
        return {"success": True, "data": sentiment}
    except Exception as e:
        logger.error(f"获取情绪分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/funding-rate/{symbol}")
async def get_funding_rate(
    symbol: str,
    session: Session = Depends(get_db_session)
):
    """获取资金费率数据"""
    try:
        analysis_service = AnalysisService(session)
        funding_rate = analysis_service.get_funding_rate(symbol)

        if not funding_rate:
            return {
                "success": False,
                "message": f"未找到 {symbol} 的资金费率数据"
            }

        return {"success": True, "data": funding_rate}
    except Exception as e:
        logger.error(f"获取资金费率失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 聪明钱监控API ====================

@router.get("/api/smart-money/addresses")
async def get_smart_money_addresses(blockchain: str = None):
    """
    获取监控的聪明钱地址列表

    Args:
        blockchain: 区块链网络(可选): ethereum, bsc

    Returns:
        监控地址列表及统计信息
    """
    try:
        db_service = get_db_service()
        addresses = db_service.get_smart_money_addresses(blockchain=blockchain, active_only=True)

        return {
            "success": True,
            "data": {
                "addresses": addresses,
                "total_count": len(addresses)
            }
        }
    except Exception as e:
        logger.error(f"获取聪明钱地址失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/smart-money/transactions/{token_symbol}")
async def get_smart_money_transactions(
    token_symbol: str,
    hours: int = 24,
    action: str = None,
    limit: int = 100
):
    """
    获取指定代币的聪明钱交易记录

    Args:
        token_symbol: 代币符号(如 BTC, ETH)
        hours: 时间范围(小时)
        action: 交易类型(可选): buy, sell
        limit: 返回数量限制

    Returns:
        交易记录列表
    """
    try:
        db_service = get_db_service()
        transactions = db_service.get_recent_smart_money_transactions(
            token_symbol=token_symbol,
            hours=hours,
            action=action,
            limit=limit
        )

        # 统计买卖情况
        buy_count = sum(1 for tx in transactions if tx['action'] == 'buy')
        sell_count = sum(1 for tx in transactions if tx['action'] == 'sell')
        total_buy_usd = sum(tx['amount_usd'] for tx in transactions if tx['action'] == 'buy')
        total_sell_usd = sum(tx['amount_usd'] for tx in transactions if tx['action'] == 'sell')

        return {
            "success": True,
            "data": {
                "transactions": transactions,
                "statistics": {
                    "total_count": len(transactions),
                    "buy_count": buy_count,
                    "sell_count": sell_count,
                    "total_buy_usd": round(total_buy_usd, 2),
                    "total_sell_usd": round(total_sell_usd, 2),
                    "net_flow_usd": round(total_buy_usd - total_sell_usd, 2)
                }
            }
        }
    except Exception as e:
        logger.error(f"获取聪明钱交易失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/smart-money/signals")
async def get_smart_money_signals(limit: int = 10):
    """
    获取活跃的聪明钱信号列表

    Args:
        limit: 返回数量限制

    Returns:
        聪明钱信号列表,按置信度排序
    """
    try:
        db_service = get_db_service()
        signals = db_service.get_active_smart_money_signals(limit=limit)

        return {
            "success": True,
            "data": {
                "signals": signals,
                "total_count": len(signals)
            }
        }
    except Exception as e:
        logger.error(f"获取聪明钱信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/smart-money/signals/{token_symbol}")
async def get_token_smart_money_signal(token_symbol: str):
    """
    获取指定代币的最新聪明钱信号

    Args:
        token_symbol: 代币符号(如 BTC, ETH)

    Returns:
        聪明钱信号详情
    """
    try:
        db_service = get_db_service()
        signal = db_service.get_smart_money_signal_by_token(token_symbol)

        if not signal:
            return {
                "success": False,
                "message": f"未找到 {token_symbol} 的聪明钱信号"
            }

        return {"success": True, "data": signal}
    except Exception as e:
        logger.error(f"获取代币聪明钱信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/smart-money/dashboard")
async def get_smart_money_dashboard():
    """
    聪明钱监控仪表盘

    Returns:
        聪明钱活动概览,包括活跃信号、最近大额交易、热门代币等
    """
    try:
        db_service = get_db_service()

        # 获取活跃信号
        active_signals = db_service.get_active_smart_money_signals(limit=5)

        # 获取最近大额交易
        recent_transactions = db_service.get_recent_smart_money_transactions(
            hours=24,
            limit=20
        )
        large_transactions = [
            tx for tx in recent_transactions
            if tx.get('is_large_transaction', False)
        ][:10]

        # 获取监控地址数量
        addresses = db_service.get_smart_money_addresses(active_only=True)

        # 统计热门代币(最近24小时交易最多的)
        from collections import Counter
        token_counter = Counter(tx['token_symbol'] for tx in recent_transactions)
        top_tokens = [
            {"token": token, "transaction_count": count}
            for token, count in token_counter.most_common(10)
        ]

        # 统计买卖比例
        buy_count = sum(1 for tx in recent_transactions if tx['action'] == 'buy')
        sell_count = sum(1 for tx in recent_transactions if tx['action'] == 'sell')

        return {
            "success": True,
            "data": {
                "active_signals": active_signals,
                "large_transactions": large_transactions,
                "top_active_tokens": top_tokens,
                "statistics": {
                    "monitored_addresses_count": len(addresses),
                    "total_transactions_24h": len(recent_transactions),
                    "large_transactions_24h": len(large_transactions),
                    "buy_count_24h": buy_count,
                    "sell_count_24h": sell_count,
                    "buy_sell_ratio": round(buy_count / sell_count, 2) if sell_count > 0 else 0
                }
            }
        }
    except Exception as e:
        logger.error(f"获取聪明钱仪表盘失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/ema-signals")
async def get_ema_signals(
    limit: int = 20,
    signal_type: Optional[str] = None,
    session: Session = Depends(get_db_session)
):
    """
    获取EMA信号列表

    Args:
        limit: 返回数量限制
        signal_type: 信号类型过滤 (BUY或SELL)

    Returns:
        EMA信号列表
    """
    try:
        # 构建查询
        query = """
            SELECT
                symbol, timeframe, signal_type, signal_strength,
                timestamp, price, short_ema, long_ema,
                ema_config, volume_ratio, price_change_pct, ema_distance_pct,
                created_at
            FROM ema_signals
        """

        if signal_type:
            query += " WHERE signal_type = :signal_type"

        query += " ORDER BY timestamp DESC LIMIT :limit"

        params = {'limit': limit}
        if signal_type:
            params['signal_type'] = signal_type.upper()

        result = session.execute(text(query), params)
        signals = []

        for row in result:
            signals.append({
                'symbol': row.symbol,
                'timeframe': row.timeframe,
                'signal_type': row.signal_type,
                'signal_strength': row.signal_strength,
                'timestamp': row.timestamp.isoformat() if row.timestamp else None,
                'price': float(row.price),
                'short_ema': float(row.short_ema),
                'long_ema': float(row.long_ema),
                'ema_config': row.ema_config,
                'volume_ratio': float(row.volume_ratio),
                'price_change_pct': float(row.price_change_pct),
                'ema_distance_pct': float(row.ema_distance_pct),
                'created_at': row.created_at.isoformat() if row.created_at else None
            })

        return {
            "success": True,
            "data": signals,
            "count": len(signals)
        }

    except Exception as e:
        logger.error(f"获取EMA信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
