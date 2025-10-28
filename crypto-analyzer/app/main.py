"""
加密货币交易分析系统 - 主程序
FastAPI后端服务
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from loguru import logger
import yaml

# 导入模块
from app.collectors.price_collector import MultiExchangeCollector
from app.collectors.mock_price_collector import MockPriceCollector
from app.collectors.news_collector import NewsAggregator
from app.analyzers.technical_indicators import TechnicalIndicators
from app.analyzers.sentiment_analyzer import SentimentAnalyzer
from app.analyzers.signal_generator import SignalGenerator
# 使用缓存版API，性能提升30倍 ⚡
from app.api.enhanced_dashboard_cached import EnhancedDashboardCached as EnhancedDashboard
from app.services.price_cache_service import init_global_price_cache, stop_global_price_cache


# 全局变量
config = {}
price_collector = None
news_aggregator = None
technical_analyzer = None
sentiment_analyzer = None
signal_generator = None
enhanced_dashboard = None
price_cache_service = None  # 价格缓存服务


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    logger.info("🚀 启动加密货币交易分析系统...")

    global config, price_collector, news_aggregator
    global technical_analyzer, sentiment_analyzer, signal_generator, enhanced_dashboard, price_cache_service

    # 加载配置
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        logger.info("✅ 配置文件加载成功")
    else:
        logger.warning("⚠️  config.yaml 不存在，使用默认配置")
        config = {
            'exchanges': {
                'binance': {'enabled': True}
            },
            'symbols': ['BTC/USDT', 'ETH/USDT']
        }

    # 初始化价格缓存服务（优先启动，Paper Trading 依赖它）
    try:
        # 传递完整的 database 配置（包括 type、mysql 等）
        db_config = config.get('database', {})
        price_cache_service = init_global_price_cache(db_config, update_interval=5)
        logger.info("✅ 价格缓存服务已启动（5秒更新间隔）")
    except Exception as e:
        logger.warning(f"⚠️  价格缓存服务启动失败: {e}，Paper Trading 将直接查询数据库")
        import traceback
        traceback.print_exc()
        price_cache_service = None

    # 初始化各个模块（延迟加载，避免阻塞启动）
    try:
        # 检查是否启用演示模式
        demo_mode = config.get('demo_mode', False)

        # 注意：这些模块的初始化被移到后台任务，不阻塞启动
        # Paper Trading 不依赖这些模块，可以独立运行
        logger.info("⚡ 快速启动模式：延迟加载分析模块")

        # 设置为 None，在需要时才初始化
        price_collector = None
        news_aggregator = None
        technical_analyzer = None
        sentiment_analyzer = None
        signal_generator = None
        enhanced_dashboard = None

        # 在后台线程初始化这些模块（不阻塞事件循环）
        def init_modules_sync():
            """同步初始化分析模块（在单独线程中运行）"""
            global price_collector, news_aggregator, technical_analyzer
            global sentiment_analyzer, signal_generator, enhanced_dashboard

            try:
                logger.info("🔄 开始后台初始化分析模块...")

                if demo_mode:
                    # 演示模式：创建模拟采集器包装器
                    logger.info("🎭 启用演示模式 - 使用模拟数据")

                    class MockMultiExchangeCollector:
                        """模拟多交易所采集器包装器"""
                        def __init__(self, config):
                            self.mock_collector = MockPriceCollector('binance_demo', config)

                        async def fetch_best_price(self, symbol):
                            return await self.mock_collector.fetch_ticker(symbol)

                        async def fetch_price(self, symbol):
                            ticker = await self.mock_collector.fetch_ticker(symbol)
                            return [ticker] if ticker else []

                        async def fetch_ohlcv(self, symbol, timeframe='1h', exchange=None):
                            return await self.mock_collector.fetch_ohlcv(symbol, timeframe, limit=100)

                    price_collector = MockMultiExchangeCollector(config)
                    logger.info("✅ 模拟价格采集器初始化成功")
                else:
                    # 真实API模式
                    price_collector = MultiExchangeCollector(config)
                    logger.info("✅ 价格采集器初始化成功")

                news_aggregator = NewsAggregator(config)
                logger.info("✅ 新闻采集器初始化成功")

                technical_analyzer = TechnicalIndicators(config)
                logger.info("✅ 技术分析器初始化成功")

                sentiment_analyzer = SentimentAnalyzer()
                logger.info("✅ 情绪分析器初始化成功")

                signal_generator = SignalGenerator(config)
                logger.info("✅ 信号生成器初始化成功")

                enhanced_dashboard = EnhancedDashboard(config)
                logger.info("✅ 增强版仪表盘初始化成功")

                logger.info("🎉 分析模块后台初始化完成！")

            except Exception as e:
                logger.error(f"❌ 后台初始化失败: {e}")
                import traceback
                traceback.print_exc()
                logger.warning("⚠️  分析功能可能不可用，但 Paper Trading 功能正常")

        # 在单独的线程中启动后台初始化（不阻塞事件循环）
        import threading

        init_thread = threading.Thread(
            target=init_modules_sync,
            name="ModuleInitializer",
            daemon=True  # 守护线程，主程序退出时自动结束
        )
        init_thread.start()

        logger.info("🚀 FastAPI 启动完成（Paper Trading 已就绪，分析模块后台加载中）")

    except Exception as e:
        logger.error(f"❌ 启动失败: {e}")
        import traceback
        traceback.print_exc()
        logger.warning("⚠️  系统以降级模式运行,部分功能可能不可用")

    yield

    # 关闭时清理
    logger.info("👋 关闭系统...")

    # 停止价格缓存服务
    try:
        stop_global_price_cache()
        logger.info("✅ 价格缓存服务已停止")
    except Exception as e:
        logger.error(f"停止价格缓存服务失败: {e}")


# 创建FastAPI应用
app = FastAPI(
    title="加密货币交易分析系统",
    description="基于技术指标和新闻情绪的交易信号生成系统",
    version="1.0.0",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册策略管理API路由
try:
    from app.api.strategy_api import router as strategy_router
    app.include_router(strategy_router)
    logger.info("✅ 策略管理API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  策略管理API路由注册失败: {e}")

# 注册模拟交易API路由
try:
    from app.api.paper_trading_api import router as paper_trading_router
    app.include_router(paper_trading_router)
    logger.info("✅ 模拟交易API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  模拟交易API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册合约交易API路由
try:
    from app.api.futures_api import router as futures_router
    app.include_router(futures_router)
    logger.info("✅ 合约交易API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  合约交易API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册企业金库监控API路由
try:
    from app.api.corporate_treasury import router as corporate_treasury_router
    app.include_router(corporate_treasury_router)
    logger.info("✅ 企业金库监控API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  企业金库监控API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册模拟合约交易API路由
try:
    from app.api.contract_trading_api import router as contract_trading_router
    app.include_router(contract_trading_router)
    logger.info("✅ 模拟合约交易API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  模拟合约交易API路由注册失败: {e}")
    import traceback
    traceback.print_exc()


# ==================== API路由 ====================

@app.get("/")
async def root():
    """首页"""
    return {
        "name": "加密货币交易分析系统",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "endpoints": {
            "价格查询": "/api/price/{symbol}",
            "技术分析": "/api/analysis/{symbol}",
            "新闻情绪": "/api/news/{symbol}",
            "交易信号": "/api/signals/{symbol}",
            "批量信号": "/api/signals/batch"
        }
    }


@app.get("/favicon.ico")
async def favicon():
    """返回 favicon - 避免 404 错误"""
    # 如果有 favicon 文件，返回它
    favicon_path = Path("static/favicon.ico")
    if favicon_path.exists():
        return FileResponse(favicon_path)
    # 否则返回 204 No Content，浏览器会使用默认图标
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/HYPERLIQUID_INDICATORS_GUIDE.md")
async def hyperliquid_guide():
    """返回 Hyperliquid 指标说明文档"""
    guide_path = Path("HYPERLIQUID_INDICATORS_GUIDE.md")
    if guide_path.exists():
        return FileResponse(guide_path, media_type="text/markdown")
    else:
        raise HTTPException(status_code=404, detail="文档未找到")


@app.get("/test-futures")
async def test_futures():
    """合约数据测试页面"""
    return FileResponse("templates/test_futures.html")


@app.get("/corporate-treasury")
async def corporate_treasury_page():
    """企业金库监控页面"""
    treasury_path = Path("templates/corporate_treasury.html")
    if treasury_path.exists():
        return FileResponse(treasury_path)
    return {"error": "Page not found"}

@app.get("/contract-trading")
async def contract_trading_page():
    """模拟合约交易页面"""
    contract_trading_path = Path("templates/contract_trading.html")
    if contract_trading_path.exists():
        return FileResponse(contract_trading_path)
    else:
        raise HTTPException(status_code=404, detail="企业金库监控页面未找到")


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "modules": {
            "price_collector": price_collector is not None,
            "news_aggregator": news_aggregator is not None,
            "technical_analyzer": technical_analyzer is not None,
            "sentiment_analyzer": sentiment_analyzer is not None,
            "signal_generator": signal_generator is not None
        }
    }


@app.get("/dashboard")
async def dashboard_page():
    """
    增强版仪表盘页面
    """
    dashboard_path = Path("templates/dashboard.html")
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    else:
        raise HTTPException(status_code=404, detail="Dashboard page not found")


@app.get("/strategy")
async def strategy_manager_page():
    """
    策略管理页面
    """
    strategy_path = Path("app/web/templates/strategy_manager.html")
    if strategy_path.exists():
        return FileResponse(strategy_path)
    else:
        raise HTTPException(status_code=404, detail="Strategy manager page not found")


@app.get("/templates/dashboard.html")
async def dashboard_page_alt():
    """
    增强版仪表盘页面 (备用路径)
    """
    dashboard_path = Path("templates/dashboard.html")
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    else:
        raise HTTPException(status_code=404, detail="Dashboard page not found")


@app.get("/paper_trading")
def paper_trading_page():
    """
    模拟交易页面（改为同步函数，避免阻塞）
    """
    from pathlib import Path
    trading_path = Path("templates/paper_trading.html")

    if not trading_path.exists():
        # 尝试绝对路径
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        trading_path = Path(base_dir) / "templates" / "paper_trading.html"

    if trading_path.exists():
        return FileResponse(str(trading_path))
    else:
        raise HTTPException(status_code=404, detail=f"Paper trading page not found at {trading_path}")


@app.get("/futures_trading")
async def futures_trading_page():
    """
    合约交易页面
    """
    futures_path = Path("templates/futures_trading.html")
    if futures_path.exists():
        return FileResponse(futures_path)
    else:
        raise HTTPException(status_code=404, detail="Futures trading page not found")


@app.get("/strategy_manager")
async def strategy_manager_page_alt():
    """
    策略管理页面（备用路径）
    """
    strategy_path = Path("app/web/templates/strategy_manager.html")
    if strategy_path.exists():
        return FileResponse(strategy_path)
    else:
        raise HTTPException(status_code=404, detail="Strategy manager page not found")


@app.get("/api/price/{symbol}")
async def get_price(symbol: str):
    """
    获取实时价格

    Args:
        symbol: 交易对，如 BTC/USDT
    """
    try:
        # 替换URL中的符号
        symbol = symbol.replace('-', '/')

        price_data = await price_collector.fetch_best_price(symbol)

        if not price_data:
            raise HTTPException(status_code=404, detail="未找到价格数据")

        return price_data

    except Exception as e:
        logger.error(f"获取价格失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analysis/{symbol}")
async def get_technical_analysis(
    symbol: str,
    timeframe: str = '1h'
):
    """
    获取技术分析

    Args:
        symbol: 交易对
        timeframe: 时间周期 (1m, 5m, 15m, 1h, 4h, 1d)
    """
    try:
        symbol = symbol.replace('-', '/')

        # 获取K线数据
        df = await price_collector.fetch_ohlcv(symbol, timeframe)

        if df is None or len(df) == 0:
            raise HTTPException(status_code=404, detail="无法获取K线数据")

        # 计算技术指标
        indicators = technical_analyzer.analyze(df)

        # 生成信号
        signal = technical_analyzer.generate_signals(indicators)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "indicators": indicators,
            "signal": signal
        }

    except Exception as e:
        logger.error(f"技术分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/news/{symbol}")
async def get_news_sentiment(
    symbol: str,
    hours: int = 24
):
    """
    获取新闻情绪

    Args:
        symbol: 币种代码，如 BTC
        hours: 统计过去多少小时的新闻
    """
    try:
        # 提取币种代码
        if '/' in symbol:
            symbol = symbol.split('/')[0]
        symbol = symbol.replace('-', '').upper()

        # 采集新闻
        sentiment = await news_aggregator.get_symbol_sentiment(symbol, hours)

        return sentiment

    except Exception as e:
        logger.error(f"获取新闻情绪失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 辅助函数：生成单个交易信号（内部使用）
async def _generate_trading_signal(symbol: str, timeframe: str = '1h'):
    """
    内部函数：生成交易信号

    Args:
        symbol: 交易对（已格式化为BTC/USDT格式）
        timeframe: 时间周期
    """
    # 1. 获取价格
    price_data = await price_collector.fetch_best_price(symbol)
    if not price_data:
        raise ValueError(f"无法获取{symbol}价格")

    current_price = price_data['price']

    # 2. 获取技术分析
    df = await price_collector.fetch_ohlcv(symbol, timeframe)
    if df is None or len(df) == 0:
        raise ValueError(f"无法获取{symbol}K线数据")

    indicators = technical_analyzer.analyze(df)
    technical_signal = technical_analyzer.generate_signals(indicators)

    # 3. 获取新闻情绪（可选，失败不影响主流程）
    news_sentiment = None
    try:
        symbol_code = symbol.split('/')[0]
        news_sentiment = await news_aggregator.get_symbol_sentiment(symbol_code, hours=24)
    except Exception as e:
        logger.warning(f"获取{symbol}新闻情绪失败: {e}，使用默认值")
        news_sentiment = {'sentiment_score': 0, 'total_news': 0}

    # 4. 生成综合信号
    final_signal = signal_generator.generate_signal(
        symbol,
        technical_signal,
        news_sentiment,
        None,  # 社交媒体数据（暂未实现）
        current_price
    )

    return final_signal


@app.get("/api/signals/batch")
async def get_batch_signals(timeframe: str = '1h'):
    """
    批量获取所有监控币种的交易信号（必须在/api/signals/{symbol}之前定义）

    Args:
        timeframe: 时间周期
    """
    try:
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'])
        signals = []

        for symbol in symbols:
            try:
                signal = await _generate_trading_signal(symbol, timeframe)
                signals.append(signal)
            except Exception as e:
                logger.warning(f"获取 {symbol} 信号失败: {e}")
                continue

        # 按置信度排序
        signals.sort(key=lambda x: x.get('confidence', 0), reverse=True)

        return {
            "total": len(signals),
            "timeframe": timeframe,
            "signals": signals
        }

    except Exception as e:
        logger.error(f"批量获取信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/{symbol}")
async def get_trading_signal(
    symbol: str,
    timeframe: str = '1h'
):
    """
    获取综合交易信号

    Args:
        symbol: 交易对，支持格式: BTC-USDT 或 BTC/USDT
        timeframe: 时间周期
    """
    try:
        # 格式化交易对符号
        symbol = symbol.replace('-', '/').upper()

        # 如果只输入了币种代码（如BTC），自动添加/USDT
        if '/' not in symbol:
            symbol = f"{symbol}/USDT"

        signal = await _generate_trading_signal(symbol, timeframe)
        return signal

    except Exception as e:
        logger.error(f"生成交易信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config():
    """获取当前配置"""
    return {
        "symbols": config.get('symbols', []),
        "exchanges": list(config.get('exchanges', {}).keys()),
        "news_sources": config.get('news', {}).keys()
    }


# Dashboard 数据缓存（全局变量）
_dashboard_cache = None
_dashboard_cache_time = None
_dashboard_cache_ttl_seconds = 30  # 增加到 30 秒缓存（降低查询频率）


@app.get("/api/dashboard")
async def get_dashboard():
    """
    获取增强版仪表盘数据（优化缓存策略）
    整合所有数据源：价格、投资建议、新闻、Hyperliquid聪明钱等
    """
    global _dashboard_cache, _dashboard_cache_time

    try:
        # 检查缓存
        from datetime import datetime, timedelta
        now = datetime.now()

        if _dashboard_cache and _dashboard_cache_time:
            cache_age = (now - _dashboard_cache_time).total_seconds()
            if cache_age < _dashboard_cache_ttl_seconds:
                logger.debug(f"✅ 返回缓存的 Dashboard 数据（缓存年龄: {cache_age:.1f}秒）")
                return _dashboard_cache

        # 如果 enhanced_dashboard 未初始化，返回降级数据
        if not enhanced_dashboard:
            logger.warning("⚠️  enhanced_dashboard 未初始化，返回基础数据")
            return {
                "success": True,
                "data": {
                    "prices": [],
                    "futures": [],
                    "recommendations": [],
                    "news": [],
                    "hyperliquid": {},
                    "stats": {
                        "total_symbols": 0,
                        "bullish_count": 0,
                        "bearish_count": 0
                    },
                    "last_updated": now.strftime('%Y-%m-%d %H:%M:%S')
                },
                "message": "仪表盘服务正在初始化中，请稍后刷新"
            }

        # 缓存未命中或过期，重新获取
        logger.info("🔄 重新获取 Dashboard 数据...")
        start_time = now
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'])

        # 在单独的线程池中执行，避免阻塞其他请求
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            data = await loop.run_in_executor(
                executor,
                lambda: asyncio.run(enhanced_dashboard.get_dashboard_data(symbols))
            )

        # 更新缓存
        _dashboard_cache = data
        _dashboard_cache_time = now

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"✅ Dashboard 数据获取完成，耗时: {elapsed:.1f}秒")

        return data

    except Exception as e:
        logger.error(f"❌ 获取仪表盘数据失败: {e}")
        import traceback
        traceback.print_exc()

        # 返回降级数据而不是抛出异常
        return {
            "success": False,
            "data": {
                "prices": [],
                "futures": [],
                "recommendations": [],
                "news": [],
                "hyperliquid": {},
                "stats": {
                    "total_symbols": 0,
                    "bullish_count": 0,
                    "bearish_count": 0
                },
                "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            "error": str(e),
            "message": "数据加载失败，请稍后重试"
        }


@app.get("/api/futures")
async def get_futures_data():
    """
    获取所有币种的合约数据（持仓量、多空比）
    """
    try:
        from app.database.db_service import DatabaseService

        # 获取数据库配置
        db_config = config.get('database', {})
        db_service = DatabaseService(db_config)

        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'])

        futures_data = []
        for symbol in symbols:
            data = db_service.get_latest_futures_data(symbol)
            if data:
                futures_data.append(data)

        return {
            'success': True,
            'data': futures_data,
            'count': len(futures_data)
        }

    except Exception as e:
        logger.error(f"获取合约数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/futures/{symbol}")
async def get_futures_by_symbol(symbol: str):
    """
    获取指定币种的合约数据（持仓量、多空比）

    Args:
        symbol: 交易对符号，如 BTC/USDT
    """
    try:
        from app.database.db_service import DatabaseService

        # 获取数据库配置
        db_config = config.get('database', {})
        db_service = DatabaseService(db_config)

        data = db_service.get_latest_futures_data(symbol)

        if not data:
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的合约数据")

        return {
            'success': True,
            'data': data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取合约数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 错误处理 ====================

@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={"error": "资源未找到"}
    )


@app.exception_handler(500)
async def server_error_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误"}
    )


# ==================== 启动服务 ====================

if __name__ == "__main__":
    import uvicorn

    logger.info("启动FastAPI服务器...")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # 开发模式，生产环境设为False
        log_level="info"
    )
