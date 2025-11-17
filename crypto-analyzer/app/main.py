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

# 配置日志文件（按天轮转）
log_dir = project_root / "logs"
log_dir.mkdir(exist_ok=True)

# 移除默认的控制台处理器，避免重复输出
logger.remove()

# 添加控制台输出（INFO级别以上，带颜色）
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
    colorize=True
)

# 添加文件输出（按天轮转，保留30天）
logger.add(
    log_dir / "main_{time:YYYY-MM-DD}.log",
    rotation="00:00",  # 每天午夜轮转
    retention="30 days",  # 保留30天的日志
    level="DEBUG",  # 文件记录DEBUG级别以上的所有日志
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    encoding="utf-8",
    enqueue=True,  # 异步写入，提高性能
    backtrace=True,  # 记录异常堆栈
    diagnose=True  # 记录变量值
)

# 延迟导入：注释掉模块级别的import，改为在使用时才导入
# 避免某些模块在导入时的初始化代码导致Windows崩溃
# from app.collectors.price_collector import MultiExchangeCollector
# from app.collectors.mock_price_collector import MockPriceCollector
# from app.collectors.news_collector import NewsAggregator
# from app.analyzers.technical_indicators import TechnicalIndicators
# from app.analyzers.sentiment_analyzer import SentimentAnalyzer
# from app.analyzers.signal_generator import SignalGenerator
# from app.api.enhanced_dashboard_cached import EnhancedDashboardCached as EnhancedDashboard
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
pending_order_executor = None  # 待成交订单自动执行器（现货限价单）
futures_limit_order_executor = None  # 合约限价单自动执行器
futures_monitor_service = None  # 合约止盈止损监控服务


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    logger.info("🚀 启动加密货币交易分析系统...")

    global config, price_collector, news_aggregator
    global technical_analyzer, sentiment_analyzer, signal_generator, enhanced_dashboard, price_cache_service
    global pending_order_executor, futures_limit_order_executor, futures_monitor_service

    # 加载配置
    config_path = project_root / "config.yaml"
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

    # 使用延迟导入，避免模块级别的初始化代码
    try:
        from app.collectors.price_collector import MultiExchangeCollector
        from app.collectors.mock_price_collector import MockPriceCollector
        from app.collectors.news_collector import NewsAggregator
        from app.analyzers.technical_indicators import TechnicalIndicators
        from app.analyzers.sentiment_analyzer import SentimentAnalyzer
        from app.analyzers.signal_generator import SignalGenerator
        # 启用缓存版Dashboard，提升性能
        from app.api.enhanced_dashboard_cached import EnhancedDashboardCached as EnhancedDashboard

        logger.info("🔄 开始初始化分析模块...")

        # 初始化价格采集器
        # 使用真实API从Binance和Gate.io获取数据
        USE_REAL_API = True  # True=真实API, False=模拟数据

        if USE_REAL_API:
            try:
                price_collector = MultiExchangeCollector(config)
                logger.info("✅ 价格采集器初始化成功（真实API模式 - Binance + Gate.io）")
            except Exception as e:
                logger.error(f"❌ 真实API初始化失败: {e}，切换到模拟模式")
                price_collector = MockPriceCollector('binance_demo', config)
                logger.info("✅ 价格采集器初始化成功（模拟模式 - 降级）")
        else:
            price_collector = MockPriceCollector('binance_demo', config)
            logger.info("✅ 价格采集器初始化成功（模拟模式）")

        # 初始化新闻采集器（可能在Windows上导致问题）
        try:
            news_aggregator = NewsAggregator(config)
            logger.info("✅ 新闻采集器初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  新闻采集器初始化失败: {e}")
            news_aggregator = None

        # 初始化技术分析器
        try:
            technical_analyzer = TechnicalIndicators(config)
            logger.info("✅ 技术分析器初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  技术分析器初始化失败: {e}")
            technical_analyzer = None

        # 初始化情绪分析器
        try:
            sentiment_analyzer = SentimentAnalyzer()
            logger.info("✅ 情绪分析器初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  情绪分析器初始化失败: {e}")
            sentiment_analyzer = None

        # 初始化信号生成器
        try:
            signal_generator = SignalGenerator(config)
            logger.info("✅ 信号生成器初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  信号生成器初始化失败: {e}")
            signal_generator = None

        # 初始化 EnhancedDashboard（缓存版）
        try:
            # EnhancedDashboard 需要完整的 config（它内部会提取 database 部分）
            enhanced_dashboard = EnhancedDashboard(config, price_collector=price_collector)
            logger.info("✅ EnhancedDashboard（缓存版）初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  EnhancedDashboard初始化失败: {e}")
            enhanced_dashboard = None

        # 初始化价格缓存服务
        try:
            db_config = config.get('database', {})
            price_cache_service = init_global_price_cache(db_config, update_interval=3)
            logger.info("✅ 价格缓存服务初始化成功（每3秒更新）")
        except Exception as e:
            logger.warning(f"⚠️  价格缓存服务初始化失败: {e}")
            price_cache_service = None

        # 初始化待成交订单自动执行器（现货交易）
        try:
            from app.services.pending_order_executor import PendingOrderExecutor
            from app.trading.paper_trading_engine import PaperTradingEngine
            
            db_config = config.get('database', {}).get('mysql', {})
            trading_engine = PaperTradingEngine(db_config, price_cache_service=price_cache_service)
            pending_order_executor = PendingOrderExecutor(
                db_config=db_config,
                trading_engine=trading_engine,
                price_cache_service=price_cache_service
            )
            logger.info("✅ 待成交订单自动执行服务初始化成功（现货交易）")
        except Exception as e:
            logger.warning(f"⚠️  待成交订单自动执行服务初始化失败: {e}")
            import traceback
            traceback.print_exc()
            pending_order_executor = None

        # 初始化合约限价单自动执行器
        try:
            from app.services.futures_limit_order_executor import FuturesLimitOrderExecutor
            from app.trading.futures_trading_engine import FuturesTradingEngine
            
            db_config = config.get('database', {}).get('mysql', {})
            futures_engine = FuturesTradingEngine(db_config)
            futures_limit_order_executor = FuturesLimitOrderExecutor(
                db_config=db_config,
                trading_engine=futures_engine,
                price_cache_service=price_cache_service
            )
            logger.info("✅ 合约限价单自动执行服务初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  合约限价单自动执行服务初始化失败: {e}")
            import traceback
            traceback.print_exc()
            futures_limit_order_executor = None

        # 初始化合约止盈止损监控服务
        try:
            from app.trading.futures_monitor_service import FuturesMonitorService
            
            futures_monitor_service = FuturesMonitorService(config_path=str(project_root / 'config.yaml'))
            futures_monitor_service.start_monitor()
            logger.info("✅ 合约止盈止损监控服务初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  合约止盈止损监控服务初始化失败: {e}")
            import traceback
            traceback.print_exc()
            futures_monitor_service = None

        logger.info("🎉 分析模块初始化完成！")

    except Exception as e:
        logger.error(f"❌ 模块初始化失败: {e}")
        import traceback
        traceback.print_exc()
        # 降级模式：所有模块设为None
        price_collector = None
        news_aggregator = None
        technical_analyzer = None
        sentiment_analyzer = None
        signal_generator = None
        enhanced_dashboard = None
        price_cache_service = None
        pending_order_executor = None
        futures_limit_order_executor = None
        logger.warning("⚠️  系统以降级模式运行")

    logger.info("🚀 FastAPI 启动完成")
    
    # 在异步上下文中启动后台任务
    if pending_order_executor:
        try:
            import asyncio
            pending_order_executor.task = asyncio.create_task(pending_order_executor.run_loop(interval=5))
            logger.info("✅ 待成交订单自动执行服务已启动（每5秒检查，现货交易）")
        except Exception as e:
            logger.warning(f"⚠️  启动待成交订单自动执行任务失败: {e}")
            pending_order_executor = None

    # 启动合约限价单自动执行服务
    if futures_limit_order_executor:
        try:
            import asyncio
            futures_limit_order_executor.task = asyncio.create_task(futures_limit_order_executor.run_loop(interval=5))
            logger.info("✅ 合约限价单自动执行服务已启动（每5秒检查）")
        except Exception as e:
            logger.warning(f"⚠️  启动合约限价单自动执行任务失败: {e}")
            futures_limit_order_executor = None

    # 启动合约止盈止损监控服务
    if futures_monitor_service:
        try:
            import asyncio
            async def monitor_futures_positions_loop():
                """合约止盈止损监控循环（每5秒）"""
                while True:
                    try:
                        futures_monitor_service.monitor_positions()
                    except Exception as e:
                        logger.error(f"合约止盈止损监控出错: {e}")
                    await asyncio.sleep(5)
            
            asyncio.create_task(monitor_futures_positions_loop())
            logger.info("✅ 合约止盈止损监控服务已启动（每5秒检查）")
        except Exception as e:
            logger.warning(f"⚠️  启动合约止盈止损监控任务失败: {e}")
            futures_monitor_service = None

    yield

    # 关闭时的清理工作
    logger.info("👋 关闭系统...")

    # 停止待成交订单自动执行器
    if pending_order_executor:
        try:
            pending_order_executor.stop()
            logger.info("✅ 待成交订单自动执行服务已停止")
        except Exception as e:
            logger.warning(f"⚠️  停止待成交订单自动执行服务失败: {e}")

    # 停止合约限价单自动执行器
    if futures_limit_order_executor:
        try:
            futures_limit_order_executor.stop()
            logger.info("✅ 合约限价单自动执行服务已停止")
        except Exception as e:
            logger.warning(f"⚠️  停止合约限价单自动执行服务失败: {e}")
    
    # 停止合约止盈止损监控服务
    if futures_monitor_service:
        try:
            futures_monitor_service.stop_monitor()
            logger.info("✅ 合约止盈止损监控服务已停止")
        except Exception as e:
            logger.warning(f"⚠️  停止合约止盈止损监控服务失败: {e}")

    # 停止价格缓存服务
    if price_cache_service:
        try:
            stop_global_price_cache()
        except Exception as e:
            logger.warning(f"停止价格缓存服务失败: {e}")

    # Windows兼容性：简化关闭逻辑，不调用可能阻塞的close()方法
    # 让Python的垃圾回收机制自动清理资源
    logger.info("🎉 FastAPI 已关闭")


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

# 挂载静态文件目录（必须在这里挂载，因为通过 uvicorn -m 启动时 if __name__ == "__main__" 不会执行）
try:
    static_dir = project_root / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    logger.info(f"✅ 静态文件目录已挂载: /static -> {static_dir}")
except Exception as e:
    logger.error(f"❌ 静态文件挂载失败: {e}")
    import traceback
    traceback.print_exc()

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
ENABLE_CORPORATE_TREASURY = True  # 启用企业金库API

if ENABLE_CORPORATE_TREASURY:
    try:
        from app.api.corporate_treasury import router as corporate_treasury_router
        app.include_router(corporate_treasury_router)
        logger.info("✅ 企业金库监控API路由已注册")
    except Exception as e:
        logger.warning(f"⚠️  企业金库监控API路由注册失败: {e}")
        import traceback
        traceback.print_exc()
else:
    logger.warning("⚠️  企业金库监控API已禁用（ENABLE_CORPORATE_TREASURY=False）")

# 注册ETF数据API路由
try:
    from app.api.etf_api import router as etf_router
    app.include_router(etf_router)
    logger.info("✅ ETF数据API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  ETF数据API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册区块链Gas统计API路由
try:
    from app.api.blockchain_gas_api import router as blockchain_gas_router
    app.include_router(blockchain_gas_router)
    logger.info("✅ 区块链Gas统计API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  区块链Gas统计API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册数据管理API路由
try:
    from app.api.data_management_api import router as data_management_router
    app.include_router(data_management_router)
    logger.info("✅ 数据管理API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  数据管理API路由注册失败: {e}")
    import traceback
    traceback.print_exc()


# 注册主API路由（包含价格、分析等通用接口）
try:
    from app.api.routes import router as main_router
    app.include_router(main_router)
    logger.info("✅ 主API路由已注册（/api/prices, /api/analysis等）")
except Exception as e:
    logger.warning(f"⚠️  主API路由注册失败: {e}")
    import traceback
    traceback.print_exc()


# ==================== API路由 ====================

@app.get("/")
async def root():
    """首页 - 返回主页HTML"""
    index_path = project_root / "templates" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    else:
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
    """返回 favicon - 使用AlphaFlow Logo"""
    # 优先使用Logo作为favicon
    logo_path = project_root / "static" / "images" / "logo" / "alphaflow-logo-minimal.svg"
    if logo_path.exists():
        return FileResponse(str(logo_path), media_type="image/svg+xml")
    # 如果有 favicon.ico 文件，返回它
    favicon_path = project_root / "static" / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(str(favicon_path))
    # 否则返回 204 No Content，浏览器会使用默认图标
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/HYPERLIQUID_INDICATORS_GUIDE.md")
async def hyperliquid_guide():
    """返回 Hyperliquid 指标说明文档"""
    guide_path = project_root / "HYPERLIQUID_INDICATORS_GUIDE.md"
    if guide_path.exists():
        return FileResponse(str(guide_path), media_type="text/markdown")
    else:
        raise HTTPException(status_code=404, detail="文档未找到")


@app.get("/test-futures")
async def test_futures():
    """合约数据测试页面"""
    return FileResponse(str(project_root / "templates" / "test_futures.html"))


@app.get("/corporate-treasury")
@app.get("/corporate_treasury")
async def corporate_treasury_page():
    """企业金库监控页面"""
    treasury_path = project_root / "templates" / "corporate_treasury.html"
    if treasury_path.exists():
        return FileResponse(str(treasury_path))
    return {"error": "Page not found"}

@app.get("/blockchain-gas")
@app.get("/blockchain_gas")
async def blockchain_gas_page():
    """区块链Gas统计页面"""
    gas_path = project_root / "templates" / "blockchain_gas.html"
    if gas_path.exists():
        return FileResponse(str(gas_path))
    return {"error": "Page not found"}



@app.get("/data_management")
@app.get("/data-management")
async def data_management_page():
    """数据管理页面"""
    data_management_path = project_root / "templates" / "data_management.html"
    if data_management_path.exists():
        return FileResponse(str(data_management_path))
    else:
        raise HTTPException(status_code=404, detail="数据管理页面未找到")


@app.get("/strategies")
async def strategies_page():
    """投资策略管理页面"""
    # 优先使用templates/strategies.html（新版本）
    strategies_path = project_root / "templates" / "strategies.html"
    if strategies_path.exists():
        return FileResponse(str(strategies_path))
    # 备用：app/web/templates下的页面
    strategies_path_backup = project_root / "app" / "web" / "templates" / "strategy_manager.html"
    if strategies_path_backup.exists():
        return FileResponse(str(strategies_path_backup))
    # 备用：templates目录下的旧文件
    strategies_path_backup2 = project_root / "templates" / "strategy_manager.html"
    if strategies_path_backup2.exists():
        return FileResponse(str(strategies_path_backup2))
    else:
        raise HTTPException(status_code=404, detail="投资策略页面未找到")


@app.get("/auto-trading")
async def auto_trading_page():
    """自动合约交易页面 - futures_trading.html"""
    auto_trading_path = project_root / "templates" / "futures_trading.html"
    if auto_trading_path.exists():
        return FileResponse(str(auto_trading_path))
    else:
        raise HTTPException(status_code=404, detail="自动合约交易页面未找到")


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
    dashboard_path = project_root / "templates" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    else:
        raise HTTPException(status_code=404, detail="Dashboard page not found")




@app.get("/dashboard_new")
async def dashboard_new_page():
    """
    新版仪表盘页面（Gate.io风格）
    """
    dashboard_path = project_root / "templates" / "dashboard_new.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    else:
        raise HTTPException(status_code=404, detail="New dashboard page not found")


@app.get("/contract_trading_new")
async def contract_trading_new_page():
    """
    新版模拟合约交易页面（Gate.io风格）
    """
    contract_trading_path = project_root / "templates" / "contract_trading_new.html"
    if contract_trading_path.exists():
        return FileResponse(str(contract_trading_path))
    else:
        raise HTTPException(status_code=404, detail="New contract trading page not found")


@app.get("/futures_trading_new")
async def futures_trading_new_page():
    """
    新版真实合约交易页面（Gate.io风格）
    """
    futures_trading_path = project_root / "templates" / "futures_trading_new.html"
    if futures_trading_path.exists():
        return FileResponse(str(futures_trading_path))
    else:
        raise HTTPException(status_code=404, detail="New futures trading page not found")


@app.get("/paper_trading_new")
async def paper_trading_new_page():
    """
    新版模拟现货交易页面（Gate.io风格）
    """
    paper_trading_path = project_root / "templates" / "paper_trading_new.html"
    if paper_trading_path.exists():
        return FileResponse(str(paper_trading_path))
    else:
        raise HTTPException(status_code=404, detail="New paper trading page not found")


@app.get("/etf-data")
@app.get("/etf_data")
async def etf_data_page():
    """
    ETF数据监控页面
    """
    etf_path = project_root / "templates" / "etf_data.html"
    if etf_path.exists():
        return FileResponse(str(etf_path))
    else:
        raise HTTPException(status_code=404, detail="ETF data page not found")


@app.get("/strategy")
async def strategy_manager_page():
    """
    策略管理页面
    """
    strategy_path = project_root / "app" / "web" / "templates" / "strategy_manager.html"
    if strategy_path.exists():
        return FileResponse(str(strategy_path))
    else:
        raise HTTPException(status_code=404, detail="Strategy manager page not found")


@app.get("/technical-signals")
async def technical_signals_page():
    """技术信号页面"""
    signals_path = project_root / "templates" / "technical_signals.html"
    if signals_path.exists():
        return FileResponse(str(signals_path))
    else:
        raise HTTPException(status_code=404, detail="Technical signals page not found")


@app.get("/templates/dashboard.html")
async def dashboard_page_alt():
    """
    增强版仪表盘页面 (备用路径)
    """
    dashboard_path = project_root / "templates" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    else:
        raise HTTPException(status_code=404, detail="Dashboard page not found")


@app.get("/paper_trading")
def paper_trading_page():
    """
    模拟交易页面（改为同步函数，避免阻塞）
    """
    trading_path = project_root / "templates" / "paper_trading.html"

    if trading_path.exists():
        return FileResponse(str(trading_path))
    else:
        raise HTTPException(status_code=404, detail=f"Paper trading page not found at {trading_path}")


@app.get("/futures_trading")
async def futures_trading_page():
    """
    合约交易页面
    """
    futures_path = project_root / "templates" / "futures_trading.html"
    if futures_path.exists():
        return FileResponse(str(futures_path))
    else:
        raise HTTPException(status_code=404, detail="Futures trading page not found")


@app.get("/strategy_manager")
async def strategy_manager_page_alt():
    """
    策略管理页面（备用路径）
    """
    strategy_path = project_root / "app" / "web" / "templates" / "strategy_manager.html"
    if strategy_path.exists():
        return FileResponse(str(strategy_path))
    else:
        raise HTTPException(status_code=404, detail="Strategy manager page not found")


@app.get("/api/price/{symbol:path}")
async def get_price(symbol: str):
    """
    获取实时价格

    Args:
        symbol: 交易对，如 BTC/USDT 或 BTC-USDT（支持URL编码的斜杠）
    """
    try:
        # URL解码，然后替换URL中的符号
        from urllib.parse import unquote
        symbol = unquote(symbol)
        symbol = symbol.replace('-', '/')

        if not price_collector:
            raise HTTPException(status_code=503, detail="价格采集器未初始化")

        price_data = await price_collector.fetch_best_price(symbol)

        if not price_data:
            raise HTTPException(status_code=404, detail=f"未找到价格数据: {symbol}")

        return price_data

    except HTTPException:
        raise
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


# 辅助函数：将numpy类型转换为Python原生类型
def convert_numpy_types(obj):
    """
    递归地将numpy类型转换为Python原生类型，以便JSON序列化
    
    Args:
        obj: 要转换的对象（可以是dict, list, 或基本类型）
    
    Returns:
        转换后的对象
    """
    import numpy as np
    
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    elif hasattr(obj, 'isoformat'):  # datetime对象
        return obj.isoformat()
    else:
        return obj


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

    # 5. 转换numpy类型为Python原生类型，以便JSON序列化
    final_signal = convert_numpy_types(final_signal)

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


@app.get("/api/signals/{symbol:path}")
async def get_trading_signal(
    symbol: str,
    timeframe: str = '1h'
):
    """
    获取综合交易信号

    Args:
        symbol: 交易对，支持格式: BTC-USDT 或 BTC/USDT（支持URL编码的斜杠）
        timeframe: 时间周期
    """
    try:
        # URL解码，然后格式化交易对符号
        from urllib.parse import unquote
        symbol = unquote(symbol)
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
    try:
        # 确保 symbols 是列表
        symbols = config.get('symbols', [])
        if not isinstance(symbols, list):
            symbols = list(symbols) if symbols else []
        
        # 确保 exchanges 是字典
        exchanges = config.get('exchanges', {})
        if not isinstance(exchanges, dict):
            exchanges = {}
        exchange_list = list(exchanges.keys()) if exchanges else []
        
        # 确保 news 是字典
        news = config.get('news', {})
        if not isinstance(news, dict):
            news = {}
        news_sources = list(news.keys()) if news else []
        
        return {
            "symbols": symbols,
            "exchanges": exchange_list,
            "news_sources": news_sources
        }
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        # 返回安全的默认值
        return {
            "symbols": [],
            "exchanges": [],
            "news_sources": []
        }


@app.get("/api/technical-indicators")
async def get_technical_indicators(symbol: str = None, timeframe: str = '1h'):
    """
    获取技术指标数据
    
    Args:
        symbol: 交易对（可选，不指定则返回所有）
        timeframe: 时间周期
    """
    try:
        import pymysql
        
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            if symbol:
                # 格式化交易对符号
                symbol = symbol.replace('-', '/').upper()
                if '/' not in symbol:
                    symbol = f"{symbol}/USDT"
                
                sql = """
                    SELECT * FROM technical_indicators_cache 
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY updated_at DESC LIMIT 1
                """
                cursor.execute(sql, (symbol, timeframe))
                result = cursor.fetchone()
                
                if not result:
                    raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的技术指标数据")
                
                return {
                    "symbol": result['symbol'],
                    "timeframe": result['timeframe'],
                    "rsi": {
                        "value": float(result['rsi_value']) if result.get('rsi_value') else None,
                        "signal": result.get('rsi_signal')
                    },
                    "macd": {
                        "value": float(result['macd_value']) if result.get('macd_value') else None,
                        "signal_line": float(result['macd_signal_line']) if result.get('macd_signal_line') else None,
                        "histogram": float(result['macd_histogram']) if result.get('macd_histogram') else None,
                        "trend": result.get('macd_trend')
                    },
                    "bollinger_bands": {
                        "upper": float(result['bb_upper']) if result.get('bb_upper') else None,
                        "middle": float(result['bb_middle']) if result.get('bb_middle') else None,
                        "lower": float(result['bb_lower']) if result.get('bb_lower') else None,
                        "position": result.get('bb_position'),
                        "width": float(result['bb_width']) if result.get('bb_width') else None
                    },
                    "ema": {
                        "short": float(result['ema_short']) if result.get('ema_short') else None,
                        "long": float(result['ema_long']) if result.get('ema_long') else None,
                        "trend": result.get('ema_trend')
                    },
                    "kdj": {
                        "k": float(result['kdj_k']) if result.get('kdj_k') else None,
                        "d": float(result['kdj_d']) if result.get('kdj_d') else None,
                        "j": float(result['kdj_j']) if result.get('kdj_j') else None,
                        "signal": result.get('kdj_signal')
                    },
                    "volume": {
                        "volume_24h": float(result['volume_24h']) if result.get('volume_24h') else None,
                        "volume_avg": float(result['volume_avg']) if result.get('volume_avg') else None,
                        "volume_ratio": float(result['volume_ratio']) if result.get('volume_ratio') else None,
                        "signal": result.get('volume_signal')
                    },
                    "technical_score": float(result['technical_score']) if result.get('technical_score') else None,
                    "technical_signal": result.get('technical_signal'),
                    "updated_at": result['updated_at'].isoformat() if result.get('updated_at') else None
                }
            else:
                # 返回所有交易对的技术指标
                sql = """
                    SELECT t1.* FROM technical_indicators_cache t1
                    INNER JOIN (
                        SELECT symbol, MAX(updated_at) as max_updated_at
                        FROM technical_indicators_cache
                        WHERE timeframe = %s
                        GROUP BY symbol
                    ) t2 ON t1.symbol = t2.symbol AND t1.updated_at = t2.max_updated_at
                    WHERE t1.timeframe = %s
                    ORDER BY t1.technical_score DESC
                """
                cursor.execute(sql, (timeframe, timeframe))
                results = cursor.fetchall()
                
                indicators_list = []
                for result in results:
                    indicators_list.append({
                        "symbol": result['symbol'],
                        "technical_score": float(result['technical_score']) if result.get('technical_score') else None,
                        "technical_signal": result.get('technical_signal'),
                        "rsi_value": float(result['rsi_value']) if result.get('rsi_value') else None,
                        "macd_trend": result.get('macd_trend'),
                        "ema_trend": result.get('ema_trend'),
                        "updated_at": result['updated_at'].isoformat() if result.get('updated_at') else None
                    })
                
                return {
                    "timeframe": timeframe,
                    "total": len(indicators_list),
                    "indicators": indicators_list
                }
        finally:
            cursor.close()
            connection.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取技术指标失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ema-signals")
async def get_ema_signals(symbol: str = None, signal_type: str = None, limit: int = 50):
    """
    获取EMA信号历史
    
    Args:
        symbol: 交易对（可选）
        signal_type: 信号类型 BUY/SELL（可选）
        limit: 返回数量限制
    """
    try:
        import pymysql
        
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            where_clauses = []
            params = []
            
            if symbol:
                symbol = symbol.replace('-', '/').upper()
                if '/' not in symbol:
                    symbol = f"{symbol}/USDT"
                where_clauses.append("symbol = %s")
                params.append(symbol)
            
            if signal_type:
                where_clauses.append("signal_type = %s")
                params.append(signal_type.upper())
            
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            params.append(limit)
            
            sql = f"""
                SELECT * FROM ema_signals
                WHERE {where_sql}
                ORDER BY timestamp DESC
                LIMIT %s
            """
            
            cursor.execute(sql, params)
            results = cursor.fetchall()
            
            signals = []
            for result in results:
                signals.append({
                    "id": result['id'],
                    "symbol": result['symbol'],
                    "timeframe": result['timeframe'],
                    "signal_type": result['signal_type'],
                    "signal_strength": result['signal_strength'],
                    "timestamp": result['timestamp'].isoformat() if result.get('timestamp') else None,
                    "price": float(result['price']) if result.get('price') else None,
                    "short_ema": float(result['short_ema']) if result.get('short_ema') else None,
                    "long_ema": float(result['long_ema']) if result.get('long_ema') else None,
                    "ema_config": result.get('ema_config'),
                    "volume_ratio": float(result['volume_ratio']) if result.get('volume_ratio') else None,
                    "price_change_pct": float(result['price_change_pct']) if result.get('price_change_pct') else None,
                    "ema_distance_pct": float(result['ema_distance_pct']) if result.get('ema_distance_pct') else None
                })
            
            return {
                "total": len(signals),
                "signals": signals
            }
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        logger.error(f"获取EMA信号失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# Dashboard 数据缓存（全局变量）
_dashboard_cache = None
_dashboard_cache_time = None
_dashboard_cache_ttl_seconds = 30  # 增加到 30 秒缓存（降低查询频率）


@app.get("/api/dashboard")
async def get_dashboard():
    """
    获取增强版仪表盘数据（使用缓存版本，性能提升30倍）
    """
    from datetime import datetime

    try:
        # 如果 enhanced_dashboard 已初始化，使用缓存版本
        if enhanced_dashboard:
            # 减少日志输出，提升性能
            # logger.debug("🚀 使用缓存版Dashboard获取数据...")
            symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'])

            # 从缓存获取数据（超快速）
            data = await enhanced_dashboard.get_dashboard_data(symbols)
            # logger.debug("✅ 缓存版Dashboard数据获取成功")
            return data

        # 降级方案：enhanced_dashboard 未初始化时使用简化版本
        logger.warning("⚠️  enhanced_dashboard 未初始化，使用降级方案")
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'])
        prices_data = []

        # 获取价格数据
        if price_collector:
            for symbol in symbols:
                try:
                    price_info = await price_collector.fetch_best_price(symbol)
                    if price_info:
                        # 从details数组的第一个元素获取详细信息
                        details = price_info.get('details', [])
                        first_detail = details[0] if details else {}

                        prices_data.append({
                            "symbol": symbol,
                            "price": price_info.get('price'),
                            "change_24h": first_detail.get('change_24h', 0),
                            "volume": price_info.get('total_volume', 0),
                            "high": price_info.get('max_price', 0),
                            "low": price_info.get('min_price', 0),
                            "exchanges": price_info.get('exchanges', 1)
                        })
                except Exception as e:
                    logger.warning(f"获取 {symbol} 价格失败: {e}")
                    continue

        # 统计
        bullish = sum(1 for p in prices_data if p.get('change_24h', 0) > 0)
        bearish = sum(1 for p in prices_data if p.get('change_24h', 0) < 0)

        return {
            "success": True,
            "data": {
                "prices": prices_data,
                "futures": [],
                "recommendations": [],
                "news": [],
                "hyperliquid": {},
                "stats": {
                    "total_symbols": len(prices_data),
                    "bullish_count": bullish,
                    "bearish_count": bearish
                },
                "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            "message": "降级模式：仅显示价格数据"
        }

    except Exception as e:
        logger.error(f"Dashboard数据获取失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # 确保总是返回有效的响应
        try:
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
        except Exception as e2:
            # 如果连返回响应都失败，记录错误并返回最小响应
            logger.error(f"返回错误响应失败: {e2}")
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": "服务器内部错误",
                    "message": "数据加载失败"
                }
            )

    # 以下代码暂时不执行
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

        # 临时禁用：enhanced_dashboard在Windows上导致崩溃
        ENABLE_ENHANCED_DASHBOARD = False  # 设置为True启用完整dashboard

        if not enhanced_dashboard or not ENABLE_ENHANCED_DASHBOARD:
            logger.warning("⚠️  enhanced_dashboard 已禁用或未初始化，返回基础数据")
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
                "message": "仪表盘服务临时禁用，正在修复Windows兼容性问题"
            }

        # 缓存未命中或过期，重新获取
        logger.info("🔄 重新获取 Dashboard 数据...")
        start_time = now
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'])

        # 添加超时保护，防止长时间阻塞
        try:
            data = await asyncio.wait_for(
                enhanced_dashboard.get_dashboard_data(symbols),
                timeout=30.0  # 30秒超时
            )
        except asyncio.TimeoutError:
            logger.error("❌ Dashboard数据获取超时(30秒)")
            raise HTTPException(status_code=504, detail="数据获取超时，请稍后重试")

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
    """


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


@app.get("/api/futures/data/{symbol}")
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
    import traceback
    error_detail = str(exc)
    error_traceback = traceback.format_exc()
    logger.error(f"500错误: {error_detail}\n{error_traceback}")

    return JSONResponse(
        status_code=500,
        content={
            "error": "服务器内部错误",
            "detail": error_detail,
            "type": type(exc).__name__,
            "traceback": error_traceback
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """捕获所有未处理的异常"""
    import traceback
    error_detail = str(exc)
    error_traceback = traceback.format_exc()
    error_type = type(exc).__name__

    logger.error(f"🔥 全局异常捕获 - {error_type}: {error_detail}\n{error_traceback}")

    return JSONResponse(
        status_code=500,
        content={
            "error": "服务器内部错误",
            "detail": error_detail,
            "type": error_type,
            "traceback": error_traceback,
            "path": str(request.url)
        }
    )


# ==================== 启动服务 ====================

if __name__ == "__main__":
    import uvicorn

    # 挂载静态文件目录（在所有路由注册之后）
    try:
        static_dir = project_root / "static"
        logger.info(f"📁 静态文件目录: {static_dir}")
        logger.info(f"📁 目录存在: {static_dir.exists()}")
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        logger.info("✅ 静态文件目录已挂载: /static")
    except Exception as e:
        logger.error(f"❌ 静态文件挂载失败: {e}")
        import traceback
        traceback.print_exc()

    logger.info("启动FastAPI服务器...")

    uvicorn.run(
        app,  # 直接传递app对象，而不是字符串
        host="0.0.0.0",
        port=9020,  # 改为9020端口，避免8000端口冲突
        reload=False,
        log_level="info"
    )
