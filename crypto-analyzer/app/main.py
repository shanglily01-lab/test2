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
import threading
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi import Query
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

# 技术信号页面API缓存配置（5分钟缓存）
_technical_signals_cache = None
_technical_signals_cache_time = None
_technical_signals_cache_lock = threading.Lock()

_trend_analysis_cache = None
_trend_analysis_cache_time = None
_trend_analysis_cache_lock = threading.Lock()

_futures_signals_cache = None
_futures_signals_cache_time = None
_futures_signals_cache_lock = threading.Lock()

TECHNICAL_SIGNALS_CACHE_TTL = 300  # 5分钟缓存


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 禁用 uvicorn 访问日志（无论通过何种方式启动都生效）
    import logging
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # 启动时初始化
    logger.info("🚀 启动加密货币交易分析系统...")

    global config, price_collector, news_aggregator
    global technical_analyzer, sentiment_analyzer, signal_generator, enhanced_dashboard, price_cache_service
    global pending_order_executor, futures_limit_order_executor, futures_monitor_service, live_order_monitor

    # 加载配置（支持环境变量）
    from app.utils.config_loader import load_config, get_config_summary
    config = load_config(project_root / "config.yaml")

    if not config:
        logger.warning("⚠️ config.yaml 不存在，使用默认配置")
        config = {
            'exchanges': {
                'binance': {'enabled': True}
            },
            'symbols': ['BTC/USDT', 'ETH/USDT']
        }
    else:
        # 输出配置摘要（敏感信息已掩码）
        summary = get_config_summary(config)
        logger.debug(f"配置摘要: {summary}")

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
                logger.info("✅ 价格采集器初始化成功（真实API模式 - Binance ）")
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

        # 待成交订单自动执行器已停用（现货交易，系统使用合约交易）
        # 当前系统使用 smart_trader_service.py 进行合约自动交易，不需要现货限价单服务
        pending_order_executor = None

        # 初始化实盘交易引擎（需要在限价单执行器之前初始化）
        live_engine = None
        try:
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            db_config = config.get('database', {}).get('mysql', {})
            live_engine = BinanceFuturesEngine(db_config)
            logger.info("✅ 实盘交易引擎初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  实盘交易引擎初始化失败: {e}")
            import traceback
            traceback.print_exc()

        # 初始化Telegram通知服务（需要先初始化，供其他服务使用）
        try:
            from app.services.trade_notifier import init_trade_notifier
            trade_notifier = init_trade_notifier(config)
            logger.info("✅ Telegram通知服务初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  Telegram通知服务初始化失败: {e}")
            trade_notifier = None

        # 合约限价单自动执行器已移除（archived）
        futures_limit_order_executor = None

        # 合约止盈止损监控服务已停用（平仓逻辑已统一到SmartExitOptimizer）
        # 所有止盈止损、超时平仓逻辑现在由 smart_trader_service.py 中的 SmartExitOptimizer 统一处理
        futures_monitor_service = None

        # 初始化实盘订单监控服务（限价单成交后自动设置止损止盈）
        try:
            from app.services.live_order_monitor import init_live_order_monitor

            db_config = config.get('database', {}).get('mysql', {})
            live_order_monitor = init_live_order_monitor(db_config, live_engine)
            logger.info("✅ 实盘订单监控服务初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  实盘订单监控服务初始化失败: {e}")
            import traceback
            traceback.print_exc()
            live_order_monitor = None

        # Telegram通知服务已在前面初始化

        # 初始化用户认证服务
        try:
            from app.auth.auth_service import init_auth_service
            db_config = config.get('database', {}).get('mysql', {})
            jwt_config = config.get('auth', {})
            init_auth_service(db_config, jwt_config)
            logger.info("✅ 用户认证服务初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  用户认证服务初始化失败: {e}")
            import traceback
            traceback.print_exc()

        # 初始化API密钥管理服务
        try:
            from app.services.api_key_service import init_api_key_service
            db_config = config.get('database', {}).get('mysql', {})
            init_api_key_service(db_config)
            logger.info("✅ API密钥管理服务初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  API密钥管理服务初始化失败: {e}")
            import traceback
            traceback.print_exc()

        # 初始化用户交易引擎管理器
        try:
            from app.services.user_trading_engine_manager import init_engine_manager
            db_config = config.get('database', {}).get('mysql', {})
            init_engine_manager(db_config)
            logger.info("✅ 用户交易引擎管理器初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  用户交易引擎管理器初始化失败: {e}")
            import traceback
            traceback.print_exc()

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
        futures_monitor_service = None
        live_order_monitor = None
        logger.warning("⚠️  系统以降级模式运行")

    logger.info("🚀 FastAPI 启动完成")
    
    # 限价单自动执行服务已停用（系统使用合约市价单交易）
    # 当前系统通过 smart_trader_service.py 使用市价单进行合约交易，不需要限价单服务
    # if pending_order_executor:
    #     try:
    #         import asyncio
    #         pending_order_executor.task = asyncio.create_task(pending_order_executor.run_loop(interval=5))
    #         logger.info("✅ 待成交订单自动执行服务已启动（每5秒检查，现货交易）")
    #     except Exception as e:
    #         logger.warning(f"⚠️  启动待成交订单自动执行任务失败: {e}")
    #         pending_order_executor = None
    #
    # if futures_limit_order_executor:
    #     try:
    #         import asyncio
    #         futures_limit_order_executor.task = asyncio.create_task(futures_limit_order_executor.run_loop(interval=5))
    #         logger.info("✅ 合约限价单自动执行服务已启动（每5秒检查）")
    #     except Exception as e:
    #         logger.warning(f"⚠️  启动合约限价单自动执行任务失败: {e}")
    #         futures_limit_order_executor = None

    # 合约止盈止损监控服务已停用（平仓逻辑已统一到SmartExitOptimizer）
    # 所有止盈止损、超时平仓逻辑现在由 smart_trader_service.py 中的 SmartExitOptimizer 统一处理
    # if futures_monitor_service:
    #     try:
    #         import asyncio
    #         async def monitor_futures_positions_loop():
    #             """合约止盈止损监控循环（每5秒）"""
    #             while True:
    #                 try:
    #                     await asyncio.to_thread(futures_monitor_service.monitor_positions)
    #                 except Exception as e:
    #                     logger.error(f"合约止盈止损监控出错: {e}")
    #                 await asyncio.sleep(5)
    #
    #         asyncio.create_task(monitor_futures_positions_loop())
    #         logger.info("✅ 合约止盈止损监控服务已启动（每5秒检查）")
    #     except Exception as e:
    #         logger.warning(f"⚠️  启动合约止盈止损监控任务失败: {e}")
    #         futures_monitor_service = None

    # 启动实盘订单监控服务（限价单成交后自动设置止损止盈）
    if live_order_monitor:
        try:
            live_order_monitor.start()
            logger.info("✅ 实盘订单监控服务已启动（每10秒检查限价单成交状态）")
        except Exception as e:
            logger.warning(f"⚠️  启动实盘订单监控任务失败: {e}")
            live_order_monitor = None

    # 启动信号分析后台服务（每6小时执行一次）
    signal_analysis_service = None
    try:
        from app.services.signal_analysis_background_service import SignalAnalysisBackgroundService
        signal_analysis_service = SignalAnalysisBackgroundService()
        asyncio.create_task(signal_analysis_service.run_loop(interval_hours=6))
        logger.info("✅ 信号分析后台服务已启动（每6小时执行一次）")
    except Exception as e:
        logger.warning(f"⚠️  启动信号分析后台服务失败: {e}")
        import traceback
        traceback.print_exc()
        signal_analysis_service = None

    # 启动每日优化服务（每天凌晨1点执行）
    daily_optimizer_task = None
    try:
        import schedule
        from app.services.auto_parameter_optimizer import AutoParameterOptimizer

        # 配置数据库（从 mysql 子配置读取）
        mysql_config = config['database']['mysql']
        db_config = {
            'host': mysql_config['host'],
            'port': mysql_config['port'],
            'user': mysql_config['user'],
            'password': mysql_config['password'],
            'database': mysql_config['database']
        }

        # 定义优化任务
        def run_daily_optimization():
            """执行超级大脑自我优化（每4小时）"""
            try:
                import subprocess
                import json
                from pathlib import Path

                logger.info("=" * 80)
                logger.info("🧠 开始执行超级大脑自我优化...")
                logger.info("=" * 80)

                # 1. 运行24小时信号分析
                logger.info("📊 分析最近24小时信号盈亏...")
                result = subprocess.run(
                    ['python', 'analyze_24h_signals.py'],
                    capture_output=True,
                    text=True,
                    timeout=300  # 5分钟超时
                )

                if result.returncode != 0:
                    logger.error(f"❌ 信号分析失败: {result.stderr}")
                    return

                logger.info("✅ 信号分析完成")

                # 2. 检查是否有优化建议
                optimization_file = Path('optimization_actions.json')
                if not optimization_file.exists():
                    logger.info("ℹ️  未发现需要优化的信号")
                    return

                # 读取优化建议
                with open(optimization_file, 'r', encoding='utf-8') as f:
                    optimization_data = json.load(f)

                actions = optimization_data.get('actions', [])
                if not actions:
                    logger.info("ℹ️  没有需要执行的优化操作")
                    return

                logger.info(f"📋 发现 {len(actions)} 个优化操作待执行")

                # 3. 执行优化
                logger.info("🔧 执行优化操作...")
                result = subprocess.run(
                    ['python', 'execute_brain_optimization.py'],
                    capture_output=True,
                    text=True,
                    timeout=300
                )

                if result.returncode != 0:
                    logger.error(f"❌ 优化执行失败: {result.stderr}")
                    return

                logger.info("✅ 超级大脑优化完成")

                # 4. 输出优化结果摘要
                blacklisted = [a for a in actions if a['action'] == 'BLACKLIST_SIGNAL']
                threshold_raised = [a for a in actions if a['action'] == 'RAISE_THRESHOLD']

                if blacklisted:
                    logger.info(f"  🚫 已禁用 {len(blacklisted)} 个低质量信号")
                if threshold_raised:
                    logger.info(f"  ⬆️  已提高 {len(threshold_raised)} 个信号阈值")

                logger.info("=" * 80)

            except subprocess.TimeoutExpired:
                logger.error("❌ 优化任务超时（超过5分钟）")
            except Exception as e:
                logger.error(f"❌ 超级大脑优化失败: {e}")
                import traceback
                logger.error(traceback.format_exc())

        # 配置定时任务：每4小时执行一次
        schedule.every(4).hours.do(run_daily_optimization)

        # 创建后台任务运行调度器
        async def schedule_runner():
            """运行调度器"""
            while True:
                schedule.run_pending()
                await asyncio.sleep(60)  # 每分钟检查一次

        daily_optimizer_task = asyncio.create_task(schedule_runner())
        logger.info("✅ 超级大脑自我优化服务已启动（每4小时执行一次）")

    except Exception as e:
        logger.warning(f"⚠️  启动超级大脑优化服务失败: {e}")
        import traceback
        traceback.print_exc()

    yield

    # 关闭时的清理工作
    logger.info("👋 关闭系统...")

    # 限价单自动执行服务已停用（系统使用合约市价单交易）
    # if pending_order_executor:
    #     try:
    #         pending_order_executor.stop()
    #         logger.info("✅ 待成交订单自动执行服务已停止")
    #     except Exception as e:
    #         logger.warning(f"⚠️  停止待成交订单自动执行服务失败: {e}")
    #
    # if futures_limit_order_executor:
    #     try:
    #         futures_limit_order_executor.stop()
    #         logger.info("✅ 合约限价单自动执行服务已停止")
    #     except Exception as e:
    #         logger.warning(f"⚠️  停止合约限价单自动执行服务失败: {e}")
    
    # 停止合约止盈止损监控服务
    if futures_monitor_service:
        try:
            futures_monitor_service.stop_monitor()
            logger.info("✅ 合约止盈止损监控服务已停止")
        except Exception as e:
            logger.warning(f"⚠️  停止合约止盈止损监控服务失败: {e}")

    # 停止实盘订单监控服务
    if live_order_monitor:
        try:
            live_order_monitor.stop()
            logger.info("✅ 实盘订单监控服务已停止")
        except Exception as e:
            logger.warning(f"⚠️  停止实盘订单监控服务失败: {e}")

    # 停止超级大脑优化服务
    if daily_optimizer_task:
        try:
            daily_optimizer_task.cancel()
            logger.info("✅ 超级大脑优化服务已停止")
        except Exception as e:
            logger.warning(f"⚠️  停止超级大脑优化服务失败: {e}")

    # 停止信号分析后台服务
    if signal_analysis_service:
        try:
            signal_analysis_service.stop()
            logger.info("✅ 信号分析后台服务已停止")
        except Exception as e:
            logger.warning(f"⚠️  停止信号分析后台服务失败: {e}")

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

# 注册用户认证API路由
try:
    from app.api.auth_api import router as auth_router
    app.include_router(auth_router, prefix="/api")
    logger.info("✅ 用户认证API路由已注册 (/api/auth)")
except Exception as e:
    logger.warning(f"⚠️  用户认证API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册API密钥管理路由
try:
    from app.api.api_keys_api import router as api_keys_router
    app.include_router(api_keys_router)
    logger.info("✅ API密钥管理路由已注册 (/api/api-keys)")
except Exception as e:
    logger.warning(f"⚠️  API密钥管理路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册模拟交易API路由
try:
    from app.api.paper_trading_api import router as paper_trading_router
    app.include_router(paper_trading_router)
    logger.info("✅ 模拟交易API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  模拟交易API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册U本位合约交易API路由
try:
    from app.api.futures_api import router as futures_router
    app.include_router(futures_router)
    logger.info("✅ U本位合约交易API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  U本位合约交易API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册币本位合约交易API路由
try:
    from app.api.coin_futures_api import router as coin_futures_router
    app.include_router(coin_futures_router)
    logger.info("✅ 币本位合约交易API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  币本位合约交易API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册实盘交易API路由
try:
    from app.api.live_trading_api import router as live_trading_router
    app.include_router(live_trading_router)
    logger.info("✅ 实盘交易API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  实盘交易API路由注册失败: {e}")
    import traceback
    traceback.print_exc()

# 注册复盘合约API路由
try:
    from app.api.futures_review_api import router as futures_review_router
    app.include_router(futures_review_router)
    logger.info("✅ 复盘合约API路由已注册")
except Exception as e:
    logger.warning(f"⚠️  复盘合约API路由注册失败: {e}")
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

# 注册行情识别API路由
try:
    from app.api.market_regime_api import router as market_regime_router
    app.include_router(market_regime_router)
    logger.info("✅ 行情识别API路由已注册（/api/market-regime）")
except Exception as e:
    logger.warning(f"⚠️  行情识别API路由注册失败: {e}")
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


@app.get("/login")
async def login_page():
    """登录页面"""
    login_path = project_root / "templates" / "login.html"
    if login_path.exists():
        return FileResponse(str(login_path))
    else:
        raise HTTPException(status_code=404, detail="登录页面未找到")


@app.get("/register")
async def register_page():
    """注册页面"""
    register_path = project_root / "templates" / "register.html"
    if register_path.exists():
        return FileResponse(str(register_path))
    else:
        raise HTTPException(status_code=404, detail="注册页面未找到")


@app.get("/api-keys")
async def api_keys_page():
    """API密钥管理页面"""
    page_path = project_root / "templates" / "api-keys.html"
    if page_path.exists():
        return FileResponse(str(page_path))
    else:
        raise HTTPException(status_code=404, detail="API密钥管理页面未找到")


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
@app.get("/trading-strategies")
async def trading_strategies_page():
    """交易策略页面（新版本：包含现货和合约策略）"""
    # 优先使用新的交易策略页面
    trading_strategies_path = project_root / "templates" / "trading_strategies.html"
    if trading_strategies_path.exists():
        return FileResponse(str(trading_strategies_path))
    # 备用：旧的策略管理页面
    strategies_path = project_root / "templates" / "strategies.html"
    if strategies_path.exists():
        return FileResponse(str(strategies_path))
    # 备用：app/web/templates下的页面
    strategies_path_backup = project_root / "app" / "web" / "templates" / "strategy_manager.html"
    if strategies_path_backup.exists():
        return FileResponse(str(strategies_path_backup))
    else:
        raise HTTPException(status_code=404, detail="交易策略页面未找到")


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
    U本位合约交易页面
    """
    futures_path = project_root / "templates" / "futures_trading.html"
    if futures_path.exists():
        return FileResponse(str(futures_path))
    else:
        raise HTTPException(status_code=404, detail="Futures trading page not found")


@app.get("/coin_futures_trading")
async def coin_futures_trading_page():
    """
    币本位合约交易页面
    """
    coin_futures_path = project_root / "templates" / "coin_futures_trading.html"
    if coin_futures_path.exists():
        return FileResponse(str(coin_futures_path))
    else:
        raise HTTPException(status_code=404, detail="Coin futures trading page not found")


@app.get("/live_trading")
async def live_trading_page():
    """
    实盘合约交易页面
    """
    live_path = project_root / "templates" / "live_trading.html"
    if live_path.exists():
        return FileResponse(str(live_path))
    else:
        raise HTTPException(status_code=404, detail="Live trading page not found")


@app.get("/futures_review")
async def futures_review_page():
    """
    复盘合约(24H)页面
    """
    review_path = project_root / "templates" / "futures_review.html"
    if review_path.exists():
        return FileResponse(str(review_path))
    else:
        raise HTTPException(status_code=404, detail="Futures review page not found")


@app.get("/market_regime")
async def market_regime_page():
    """
    行情识别与策略自适应页面
    """
    regime_path = project_root / "templates" / "market_regime.html"
    if regime_path.exists():
        return FileResponse(str(regime_path))
    else:
        raise HTTPException(status_code=404, detail="Market regime page not found")


@app.get("/strategy_analyzer")
async def strategy_analyzer_page():
    """
    48小时策略分析与参数优化页面
    """
    analyzer_path = project_root / "templates" / "strategy_analyzer.html"
    if analyzer_path.exists():
        return FileResponse(str(analyzer_path))
    else:
        raise HTTPException(status_code=404, detail="Strategy analyzer page not found")


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


@app.get("/api/strategies")
async def get_strategies():
    """获取所有策略（从localStorage，暂时返回空列表，由前端管理）"""
    # 策略目前存储在localStorage，前端自己管理
    # 后续可以改为从数据库加载
    return {
        'success': True,
        'data': [],
        'message': '策略由前端localStorage管理，请使用前端API'
    }

@app.post("/api/strategy/execute")
async def execute_strategy(request: dict):
    """
    执行单个策略（功能已禁用）
    
    注意：策略自动执行功能已被移除，此端点仅用于兼容性
    """
    return {
        'success': False,
        'message': '策略自动执行功能已禁用。策略由前端localStorage管理，请使用策略测试功能进行回测。'
    }

@app.get("/api/strategy/execution/list")
async def get_strategy_execution_list(
    market_type: Optional[str] = Query(None, description="市场类型: spot, futures, all"),
    action_type: Optional[str] = Query(None, description="操作类型: buy, sell, all"),
    status: Optional[str] = Query(None, description="订单状态: FILLED, PENDING, CANCELLED, all"),
    symbol: Optional[str] = Query(None, description="交易对"),
    strategy_id: Optional[int] = Query(None, description="策略ID"),
    time_range: str = Query("30d", description="时间范围: 1h, 24h, 7d, 30d, all"),
    limit: int = Query(100, description="返回数量限制")
):
    """
    获取策略执行清单（从 strategy_trade_records 表获取）
    
    返回所有策略执行的买入、平仓等交易记录
    """
    try:
        from datetime import datetime, timedelta
        import pymysql
        
        # 计算时间范围（如果没有指定或为空，默认查询最近30天）
        now = datetime.utcnow()
        time_delta_map = {
            '1h': timedelta(hours=1),
            '24h': timedelta(hours=24),
            '7d': timedelta(days=7),
            '30d': timedelta(days=30),
            'all': None  # all表示查询所有数据，不限制时间
        }
        # 如果没有指定时间范围或为空，默认查询最近30天
        if not time_range or time_range == '':
            time_range = '30d'
        start_time = None if time_range == 'all' else (now - time_delta_map.get(time_range, timedelta(days=30)))
        
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            # 先检查表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = 'strategy_trade_records'
            """)
            table_exists = cursor.fetchone()['count'] > 0
            
            if not table_exists:
                return {
                    'success': True,
                    'data': [],
                    'total': 0,
                    'message': 'strategy_trade_records 表不存在，请先运行测试或执行策略'
                }
            
            # 构建查询SQL
            sql = """
                SELECT 
                    id,
                    strategy_id,
                    strategy_name,
                    account_id,
                    symbol,
                    action,
                    direction,
                    position_side,
                    entry_price,
                    exit_price,
                    quantity,
                    leverage,
                    margin,
                    total_value,
                    fee,
                    realized_pnl,
                    position_id,
                    order_id,
                    signal_id,
                    reason,
                    trade_time,
                    created_at
                FROM strategy_trade_records
                WHERE 1=1
            """
            params = []
            
            # 时间范围筛选
            if start_time is not None:
                sql += " AND trade_time >= %s"
                params.append(start_time)
            
            # 交易对筛选
            if symbol:
                sql += " AND symbol = %s"
                params.append(symbol)
            
            # 策略ID筛选
            if strategy_id:
                sql += " AND strategy_id = %s"
                params.append(strategy_id)
            
            # 操作类型筛选
            if action_type and action_type != 'all':
                if action_type == 'buy':
                    sql += " AND action IN ('BUY', 'OPEN')"
                elif action_type == 'sell':
                    sql += " AND action IN ('SELL', 'CLOSE')"
            
            # 市场类型筛选（根据account_id判断：0=测试，其他=实盘）
            if market_type and market_type != 'all':
                if market_type == 'test':
                    sql += " AND account_id = 0"
                elif market_type == 'live':
                    sql += " AND account_id > 0"
            
            sql += " ORDER BY trade_time DESC LIMIT %s"
            params.append(limit)
            
            cursor.execute(sql, params)
            records = cursor.fetchall()
            
            # 转换数据格式
            all_executions = []
            for record in records:
                action = record['action'].lower()
                if action == 'buy' or action == 'open':
                    action_display = 'buy'
                elif action == 'sell' or action == 'close':
                    action_display = 'sell'
                else:
                    action_display = action
                
                # 确定价格（买入用entry_price，卖出用exit_price或entry_price）
                price = None
                if record['action'] in ('BUY', 'OPEN'):
                    price = float(record['entry_price']) if record['entry_price'] else None
                else:
                    price = float(record['exit_price']) if record['exit_price'] else (float(record['entry_price']) if record['entry_price'] else None)
                
                # 确定金额
                amount = float(record['total_value']) if record['total_value'] else None
                if not amount and price and record['quantity']:
                    amount = float(price) * float(record['quantity'])
                
                # 判断是测试还是实盘
                is_test = record['account_id'] == 0
                market_type_display = 'test' if is_test else 'futures'
                
                all_executions.append({
                    'id': record['id'],
                    'strategy_id': record['strategy_id'],
                    'strategy_name': record['strategy_name'] or '未知策略',
                    'market_type': market_type_display,
                    'action': action_display,
                    'symbol': record['symbol'],
                    'price': price,
                    'quantity': float(record['quantity']) if record['quantity'] else None,
                    'amount': amount,
                    'fee': float(record['fee']) if record['fee'] else 0,
                    'status': 'FILLED',  # 策略交易记录都是已成交的
                    'created_at': record['trade_time'].strftime('%Y-%m-%d %H:%M:%S') if record['trade_time'] else (record['created_at'].strftime('%Y-%m-%d %H:%M:%S') if record['created_at'] else None),
                    'order_source': '策略执行' if not is_test else '策略测试',
                    'leverage': int(record['leverage']) if record['leverage'] else None,
                    'pnl': float(record['realized_pnl']) if record['realized_pnl'] is not None else None,
                    'direction': record['direction'],
                    'position_side': record['position_side'],
                    'reason': record['reason'],
                    'entry_price': float(record['entry_price']) if record['entry_price'] else None,
                    'exit_price': float(record['exit_price']) if record['exit_price'] else None
                })
            
            # 如果没有任何数据，返回提示信息
            if len(all_executions) == 0:
                return {
                    'success': True,
                    'data': [],
                    'total': 0,
                    'message': f'在最近{time_range}内没有找到策略执行记录。可能的原因：1) 数据库中确实没有策略执行记录 2) 时间范围筛选太严格（当前：{time_range}）3) 筛选条件不匹配。建议：尝试扩大时间范围或清除筛选条件。'
                }
            
            return {
                'success': True,
                'data': all_executions,
                'total': len(all_executions)
            }
            
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        logger.error(f"获取策略执行清单失败: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'data': [],
            'total': 0
        }

@app.post("/api/strategy/test")
async def test_strategy(request: dict):
    """
    测试策略：模拟48小时的EMA合约交易下单并计算盈亏
    测试结果会自动保存到数据库中
    
    Args:
        request: 包含策略配置的字典
            - symbols: 交易对列表
            - buyDirection: 交易方向 ['long', 'short']
            - leverage: 交易倍数
            - buySignals: 买入EMA信号 (ema_5m, ema_15m, ema_1h)
            - buyVolumeEnabled: 是否启用买入成交量条件
            - buyVolume: 买入成交量条件
            - sellSignals: 卖出EMA信号
            - sellVolumeEnabled: 是否启用卖出成交量条件
            - sellVolume: 卖出成交量条件
            - positionSize: 仓位大小 (%)
            - longPrice: 做多价格类型
            - shortPrice: 做空价格类型
    
    Returns:
        测试结果，包含交易记录和盈亏统计
    """
    try:
        # 使用策略测试服务
        from app.services.strategy_test_service import StrategyTestService
        
        db_config = config.get('database', {}).get('mysql', {})
        test_service = StrategyTestService(db_config=db_config, technical_analyzer=technical_analyzer)
        return await test_service.test_strategy(request)
        
    except Exception as e:
        logger.error(f"策略测试失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/strategy/test/history")
async def get_strategy_test_history(
    strategy_id: Optional[int] = Query(None, description="策略ID"),
    strategy_name: Optional[str] = Query(None, description="策略名称"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量")
):
    """获取策略测试历史记录"""
    try:
        import pymysql
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            where_conditions = []
            params = []
            
            if strategy_id:
                where_conditions.append("strategy_id = %s")
                params.append(strategy_id)
            
            if strategy_name:
                where_conditions.append("strategy_name LIKE %s")
                params.append(f"%{strategy_name}%")
            
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            
            cursor.execute(f"SELECT COUNT(*) as total FROM strategy_test_results {where_clause}", params)
            total = cursor.fetchone()['total']
            
            offset = (page - 1) * page_size
            cursor.execute(f"""
                SELECT * FROM strategy_test_results 
                {where_clause}
                ORDER BY created_at DESC 
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            
            results = cursor.fetchall()
            
            for r in results:
                for key, value in r.items():
                    if isinstance(value, datetime):
                        r[key] = value.strftime('%Y-%m-%d %H:%M:%S')
            
            return {
                'success': True,
                'data': results,
                'pagination': {
                    'page': page,
                    'page_size': page_size,
                    'total': total,
                    'total_pages': (total + page_size - 1) // page_size
                }
            }
            
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        logger.error(f"获取测试历史失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/strategy/test/history/{test_result_id}")
async def get_strategy_test_detail(test_result_id: int):
    """获取策略测试详细结果"""
    try:
        import pymysql
        import json
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            cursor.execute("SELECT * FROM strategy_test_results WHERE id = %s", (test_result_id,))
            main_result = cursor.fetchone()
            
            if not main_result:
                raise HTTPException(status_code=404, detail="测试结果不存在")
            
            cursor.execute("SELECT * FROM strategy_test_result_details WHERE test_result_id = %s", (test_result_id,))
            details = cursor.fetchall()
            
            for key, value in main_result.items():
                if isinstance(value, datetime):
                    main_result[key] = value.strftime('%Y-%m-%d %H:%M:%S')
                elif key == 'strategy_config' and value:
                    try:
                        main_result[key] = json.loads(value) if isinstance(value, str) else value
                    except:
                        pass
            
            for detail in details:
                for key, value in detail.items():
                    if isinstance(value, datetime):
                        detail[key] = value.strftime('%Y-%m-%d %H:%M:%S')
                    elif key == 'test_result_data' and value:
                        try:
                            detail[key] = json.loads(value) if isinstance(value, str) else value
                        except:
                            pass
                    elif key == 'debug_info' and value:
                        try:
                            # 解析调试信息JSON
                            detail[key] = json.loads(value) if isinstance(value, str) else value
                        except:
                            # 如果解析失败，保持原值
                            pass
            
            return {
                'success': True,
                'data': {
                    'main': main_result,
                    'details': details
                }
            }
            
        finally:
            cursor.close()
            connection.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取测试详情失败: {e}", exc_info=True)
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
                        "timeframe": result.get('timeframe', timeframe),  # 确保包含timeframe字段
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


# 已删除重复的 /api/technical-signals 端点定义（第二个版本），请使用第2300行的版本


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
                        "timeframe": result.get('timeframe', timeframe),  # 确保包含timeframe字段
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


# 已删除重复的 /api/technical-signals 端点定义（第二个版本），请使用第2300行的版本


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
                        "timeframe": result.get('timeframe', timeframe),  # 确保包含timeframe字段
                        "technical_score": float(result['technical_score']) if result.get('technical_score') else None,
                        "technical_signal": result.get('technical_signal'),
                        "rsi_value": float(result['rsi_value']) if result.get('rsi_value') else None,
                        "macd_trend": result.get('macd_trend'),
                        "ema_trend": result.get('ema_trend'),
                        "updated_at": result['updated_at'].isoformat() if result.get('updated_at') else None
                    })
                
                return {
                    "timeframe": timeframe,
                    "indicators": indicators_list
                }
        finally:
            cursor.close()
            connection.close()
    except Exception as e:
        logger.error(f"获取技术指标失败: {e}", exc_info=True)
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
    
    Returns:
        技术指标数据
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            if symbol:
                # 格式化交易对符号
                symbol = symbol.replace('-', '/').upper()
                if '/' not in symbol:
                    symbol = f"{symbol}/USDT"
                
                sql = """
                SELECT * FROM technical_indicators 
                WHERE symbol = %s AND timeframe = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """
                cursor.execute(sql, (symbol, timeframe))
                result = cursor.fetchone()
                
                if not result:
                    raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的技术指标数据")
                
                return {
                    "symbol": result['symbol'],
                    "timeframe": result['timeframe'],
                    "ema_short": result.get('ema_short'),
                    "ema_long": result.get('ema_long'),
                    "ma10": result.get('ma10'),
                    "ema10": result.get('ema10'),
                    "ma5": result.get('ma5'),
                    "ema5": result.get('ema5'),
                    "volume_ratio": result.get('volume_ratio'),
                    "rsi_value": result.get('rsi_value'),
                    "updated_at": result['updated_at'].isoformat() if result.get('updated_at') else None
                }
            else:
                # 返回所有交易对的技术指标
                sql = """
                SELECT * FROM technical_indicators 
                WHERE timeframe = %s
                ORDER BY updated_at DESC
                """
                cursor.execute(sql, (timeframe,))
                results = cursor.fetchall()
                
                return {
                    "timeframe": timeframe,
                    "data": [
                        {
                            "symbol": r['symbol'],
                            "ema_short": r.get('ema_short'),
                            "ema_long": r.get('ema_long'),
                            "ma10": r.get('ma10'),
                            "ema10": r.get('ema10'),
                            "ma5": r.get('ma5'),
                            "ema5": r.get('ema5'),
                            "volume_ratio": r.get('volume_ratio'),
                            "rsi_value": r.get('rsi_value'),
                            "updated_at": r['updated_at'].isoformat() if r.get('updated_at') else None
                        }
                        for r in results
                    ]
                }
        finally:
            cursor.close()
            connection.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取技术指标失败: {e}", exc_info=True)
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
                        "timeframe": result.get('timeframe', timeframe),  # 确保包含timeframe字段
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


@app.get("/api/technical-signals")
async def get_technical_signals():
    """
    获取所有交易对的技术信号（15m, 1h, 1d）
    包含 EMA, MACD, RSI, BOLL 等技术指标和技术评分

    Returns:
        各交易对在不同时间周期的技术指标数据
    """
    global _technical_signals_cache, _technical_signals_cache_time

    # 检查缓存
    with _technical_signals_cache_lock:
        if _technical_signals_cache is not None and _technical_signals_cache_time is not None:
            cache_age = (datetime.utcnow() - _technical_signals_cache_time).total_seconds()
            if cache_age < TECHNICAL_SIGNALS_CACHE_TTL:
                logger.debug(f"✅ 使用缓存的技术信号数据 (缓存年龄: {cache_age:.0f}秒)")
                return _technical_signals_cache

    try:
        import pymysql
        
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            timeframes = ['15m', '1h', '1d']

            # 🚀 优化: 使用单次批量查询替代嵌套循环
            # 使用子查询获取每个(symbol, timeframe)组合的最新记录
            cursor.execute("""
                SELECT t1.*
                FROM technical_indicators_cache t1
                INNER JOIN (
                    SELECT symbol, timeframe, MAX(updated_at) as max_updated
                    FROM technical_indicators_cache
                    WHERE timeframe IN ('15m', '1h', '1d')
                    GROUP BY symbol, timeframe
                ) t2 ON t1.symbol = t2.symbol
                    AND t1.timeframe = t2.timeframe
                    AND t1.updated_at = t2.max_updated
                ORDER BY t1.symbol, t1.timeframe
            """)

            all_results = cursor.fetchall()

            # 组织数据结构
            symbols_data = {}
            for result in all_results:
                symbol = result['symbol']
                timeframe = result['timeframe']

                if symbol not in symbols_data:
                    symbols_data[symbol] = {}

                symbols_data[symbol][timeframe] = {
                    'rsi_value': float(result['rsi_value']) if result.get('rsi_value') else None,
                    'rsi_signal': result.get('rsi_signal'),
                    'macd_value': float(result['macd_value']) if result.get('macd_value') else None,
                    'macd_signal_line': float(result['macd_signal_line']) if result.get('macd_signal_line') else None,
                    'macd_histogram': float(result['macd_histogram']) if result.get('macd_histogram') else None,
                    'macd_trend': result.get('macd_trend'),
                    'ema_short': float(result['ema_short']) if result.get('ema_short') else None,
                    'ema_long': float(result['ema_long']) if result.get('ema_long') else None,
                    'ema_trend': result.get('ema_trend'),
                    'bb_upper': float(result['bb_upper']) if result.get('bb_upper') else None,
                    'bb_middle': float(result['bb_middle']) if result.get('bb_middle') else None,
                    'bb_lower': float(result['bb_lower']) if result.get('bb_lower') else None,
                    'bb_position': result.get('bb_position'),
                    'bb_width': float(result['bb_width']) if result.get('bb_width') else None,
                    'kdj_k': float(result['kdj_k']) if result.get('kdj_k') else None,
                    'kdj_d': float(result['kdj_d']) if result.get('kdj_d') else None,
                    'kdj_j': float(result['kdj_j']) if result.get('kdj_j') else None,
                    'kdj_signal': result.get('kdj_signal'),
                    'technical_score': float(result['technical_score']) if result.get('technical_score') else None,
                    'technical_signal': result.get('technical_signal'),
                    'volume_ratio': float(result['volume_ratio']) if result.get('volume_ratio') else None,
                    'updated_at': result['updated_at'].isoformat() if result.get('updated_at') else None
                }
            
            # 转换为列表格式，便于前端显示
            # 只返回至少有一个时间周期有数据的交易对
            signals_list = []
            for symbol, timeframes_data in symbols_data.items():
                # 至少有一个时间周期有数据才添加到列表
                if timeframes_data.get('15m') or timeframes_data.get('1h') or timeframes_data.get('1d'):
                    signals_list.append({
                        'symbol': symbol,
                        '15m': timeframes_data.get('15m'),
                        '1h': timeframes_data.get('1h'),
                        '1d': timeframes_data.get('1d')
                    })
            
            # 批量获取价格数据
            if signals_list:
                symbols_list = [item['symbol'] for item in signals_list]
                placeholders = ','.join(['%s'] * len(symbols_list))
                cursor.execute(
                    f"""SELECT symbol, current_price, change_24h, updated_at
                    FROM price_stats_24h 
                    WHERE symbol IN ({placeholders})""",
                    symbols_list
                )
                price_data = cursor.fetchall()
                price_map = {row['symbol']: row for row in price_data}
                
                # 将价格数据添加到每个交易对
                for item in signals_list:
                    price_info = price_map.get(item['symbol'])
                    if price_info:
                        item['current_price'] = float(price_info['current_price']) if price_info.get('current_price') else None
                        item['change_24h'] = float(price_info['change_24h']) if price_info.get('change_24h') else None
                    else:
                        item['current_price'] = None
                        item['change_24h'] = None

            result = {
                'success': True,
                'data': signals_list,
                'total': len(signals_list)
            }

            # 更新缓存
            with _technical_signals_cache_lock:
                _technical_signals_cache = result
                _technical_signals_cache_time = datetime.utcnow()
                logger.debug(f"✅ 技术信号数据已缓存 ({len(signals_list)} 条记录)")

            return result
            
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        logger.error(f"获取技术信号失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trend-analysis")
async def get_trend_analysis():
    """
    获取所有交易对的趋势分析（5m, 15m, 1h, 1d）

    优化版本：使用批量查询减少数据库往返次数

    Returns:
        各交易对在不同时间周期的趋势评估
    """
    global _trend_analysis_cache, _trend_analysis_cache_time

    # 检查缓存
    with _trend_analysis_cache_lock:
        if _trend_analysis_cache is not None and _trend_analysis_cache_time is not None:
            cache_age = (datetime.utcnow() - _trend_analysis_cache_time).total_seconds()
            if cache_age < TECHNICAL_SIGNALS_CACHE_TTL:
                logger.debug(f"✅ 使用缓存的趋势分析数据 (缓存年龄: {cache_age:.0f}秒)")
                return _trend_analysis_cache

    try:
        import pymysql

        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        try:
            timeframes = ['5m', '15m', '1h', '1d']
            symbols_data = {}

            # 1. 批量获取所有交易对的最新技术指标（使用窗口函数或子查询）
            cursor.execute("""
                SELECT t1.* FROM technical_indicators_cache t1
                INNER JOIN (
                    SELECT symbol, timeframe, MAX(updated_at) as max_updated
                    FROM technical_indicators_cache
                    WHERE timeframe IN ('5m', '15m', '1h', '1d')
                    GROUP BY symbol, timeframe
                ) t2 ON t1.symbol = t2.symbol
                    AND t1.timeframe = t2.timeframe
                    AND t1.updated_at = t2.max_updated
            """)
            all_indicators = cursor.fetchall()

            # 构建指标查找字典 {(symbol, timeframe): indicator_data}
            indicator_map = {}
            all_symbols = set()
            for row in all_indicators:
                key = (row['symbol'], row['timeframe'])
                indicator_map[key] = row
                all_symbols.add(row['symbol'])

            # 2. 批量获取K线数据（每个symbol-timeframe组合的最新2条）
            # 使用子查询获取每组最新2条记录
            kline_map = {}
            if all_symbols:
                symbols_list = list(all_symbols)
                placeholders = ','.join(['%s'] * len(symbols_list))
                cursor.execute(f"""
                    SELECT k.symbol, k.timeframe, k.open_price, k.high_price,
                           k.low_price, k.close_price, k.volume, k.timestamp
                    FROM kline_data k
                    INNER JOIN (
                        SELECT symbol, timeframe, MAX(timestamp) as max_ts
                        FROM kline_data
                        WHERE symbol IN ({placeholders})
                        AND timeframe IN ('5m', '15m', '1h', '1d')
                        GROUP BY symbol, timeframe
                    ) latest ON k.symbol = latest.symbol
                        AND k.timeframe = latest.timeframe
                        AND k.timestamp >= latest.max_ts - INTERVAL 1 DAY
                    ORDER BY k.symbol, k.timeframe, k.timestamp DESC
                """, symbols_list)

                kline_rows = cursor.fetchall()
                # 按 (symbol, timeframe) 分组，每组取前2条
                for row in kline_rows:
                    key = (row['symbol'], row['timeframe'])
                    if key not in kline_map:
                        kline_map[key] = []
                    if len(kline_map[key]) < 2:
                        kline_map[key].append(row)

            # 3. 批量获取EMA历史数据（用于金叉检测，只需要5m/15m/1h）
            ema_history_map = {}
            if all_symbols:
                symbols_list = list(all_symbols)
                placeholders = ','.join(['%s'] * len(symbols_list))
                cursor.execute(f"""
                    SELECT symbol, timeframe, ema_short, ema_long, updated_at,
                           ROW_NUMBER() OVER (PARTITION BY symbol, timeframe ORDER BY updated_at DESC) as rn
                    FROM technical_indicators_cache
                    WHERE symbol IN ({placeholders})
                    AND timeframe IN ('5m', '15m', '1h')
                    AND ema_short IS NOT NULL AND ema_long IS NOT NULL
                """, symbols_list)

                ema_rows = cursor.fetchall()
                # 按 (symbol, timeframe) 分组，每组取前10条
                for row in ema_rows:
                    if row['rn'] <= 10:  # 只取前10条
                        key = (row['symbol'], row['timeframe'])
                        if key not in ema_history_map:
                            ema_history_map[key] = []
                        ema_history_map[key].append(row)

            # 4. 处理每个交易对的每个时间周期
            for symbol in all_symbols:
                symbols_data[symbol] = {}

                for timeframe in timeframes:
                    key = (symbol, timeframe)
                    result = indicator_map.get(key)

                    if result:
                        # 验证timeframe
                        result_timeframe = result.get('timeframe')
                        if result_timeframe and result_timeframe != timeframe:
                            symbols_data[symbol][timeframe] = None
                            continue

                        # 获取K线数据
                        klines = kline_map.get(key, [])

                        # 处理EMA交叉信息
                        ema_cross_info = None
                        if timeframe in ['5m', '15m', '1h']:
                            ema_history = ema_history_map.get(key, [])
                            ema_cross_info = _process_ema_cross(ema_history, result)

                        # 分析趋势
                        trend_analysis = _analyze_trend_from_indicators(result, klines, timeframe, ema_cross_info)
                        symbols_data[symbol][timeframe] = trend_analysis
                    else:
                        symbols_data[symbol][timeframe] = None

            # 5. 转换为列表格式
            trend_list = []
            for symbol, timeframes_data in symbols_data.items():
                trend_list.append({
                    'symbol': symbol,
                    '5m': timeframes_data.get('5m'),
                    '15m': timeframes_data.get('15m'),
                    '1h': timeframes_data.get('1h'),
                    '1d': timeframes_data.get('1d')
                })

            # 6. 批量获取价格数据
            if trend_list:
                symbols_list = [item['symbol'] for item in trend_list]
                placeholders = ','.join(['%s'] * len(symbols_list))
                cursor.execute(
                    f"""SELECT symbol, current_price, change_24h, updated_at
                    FROM price_stats_24h
                    WHERE symbol IN ({placeholders})""",
                    symbols_list
                )
                price_data = cursor.fetchall()
                price_map = {row['symbol']: row for row in price_data}

                for item in trend_list:
                    price_info = price_map.get(item['symbol'])
                    if price_info:
                        item['current_price'] = float(price_info['current_price']) if price_info.get('current_price') else None
                        item['change_24h'] = float(price_info['change_24h']) if price_info.get('change_24h') else None
                        item['price_updated_at'] = price_info['updated_at'].isoformat() if price_info.get('updated_at') else None
                    else:
                        item['current_price'] = None
                        item['change_24h'] = None
                        item['price_updated_at'] = None

            result = {
                'success': True,
                'data': trend_list,
                'total': len(trend_list)
            }

            # 更新缓存
            with _trend_analysis_cache_lock:
                _trend_analysis_cache = result
                _trend_analysis_cache_time = datetime.utcnow()
                logger.debug(f"✅ 趋势分析数据已缓存 ({len(trend_list)} 条记录)")

            return result

        finally:
            cursor.close()
            connection.close()

    except Exception as e:
        logger.error(f"获取趋势分析失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _process_ema_cross(ema_history: list, current_result: dict) -> dict:
    """处理EMA交叉信息"""
    if len(ema_history) >= 2:
        curr_ema_short = float(ema_history[0].get('ema_short', 0)) if ema_history[0].get('ema_short') else 0
        curr_ema_long = float(ema_history[0].get('ema_long', 0)) if ema_history[0].get('ema_long') else 0

        if curr_ema_short > 0 and curr_ema_long > 0:
            is_golden_cross = False
            is_death_cross = False

            for i in range(len(ema_history) - 1):
                curr_short = float(ema_history[i].get('ema_short', 0)) if ema_history[i].get('ema_short') else 0
                curr_long = float(ema_history[i].get('ema_long', 0)) if ema_history[i].get('ema_long') else 0
                prev_short = float(ema_history[i+1].get('ema_short', 0)) if ema_history[i+1].get('ema_short') else 0
                prev_long = float(ema_history[i+1].get('ema_long', 0)) if ema_history[i+1].get('ema_long') else 0

                if prev_short > 0 and prev_long > 0 and curr_short > 0 and curr_long > 0:
                    if prev_short <= prev_long and curr_short > curr_long:
                        is_golden_cross = True
                        break
                    elif prev_short >= prev_long and curr_short < curr_long:
                        is_death_cross = True
                        break

            return {
                'is_golden_cross': is_golden_cross,
                'is_death_cross': is_death_cross,
                'ema_short': curr_ema_short,
                'ema_long': curr_ema_long,
                'is_bullish': curr_ema_short > curr_ema_long,
                'is_bearish': curr_ema_short < curr_ema_long
            }

    # 历史数据不足时，从当前记录获取
    curr_ema_short = float(current_result.get('ema_short', 0)) if current_result.get('ema_short') else 0
    curr_ema_long = float(current_result.get('ema_long', 0)) if current_result.get('ema_long') else 0
    if curr_ema_short > 0 and curr_ema_long > 0:
        return {
            'is_golden_cross': False,
            'is_death_cross': False,
            'ema_short': curr_ema_short,
            'ema_long': curr_ema_long,
            'is_bullish': curr_ema_short > curr_ema_long,
            'is_bearish': curr_ema_short < curr_ema_long
        }

    return None


def _analyze_trend_from_indicators(indicator_data: dict, klines: list = None, timeframe: str = '1h', ema_cross_info: dict = None) -> dict:
    """
    基于价格和成交量变化分析趋势（不使用简单的阳线/阴线数量）
    
    重要：技术指标必须与timeframe匹配
    - 1d趋势必须使用1d的RSI、MACD、EMA等技术指标
    - 1h趋势必须使用1h的技术指标
    - 15m趋势必须使用15m的技术指标
    
    Args:
        indicator_data: 技术指标数据（必须是对应timeframe的数据）
        klines: K线数据列表（用于价格和成交量趋势分析，必须是对应timeframe的数据）
        timeframe: 时间周期（'5m', '15m', '1h', '1d'）
        
    Returns:
        趋势分析结果
    """
    # 验证技术指标的timeframe（关键验证：确保不会混用不同timeframe的技术指标）
    indicator_timeframe = indicator_data.get('timeframe')
    if indicator_timeframe and indicator_timeframe != timeframe:
        logger.error(f"❌ 技术指标timeframe不匹配: 期望{timeframe}, 实际{indicator_timeframe}。"
                    f"1h趋势必须用1h指标，15m趋势必须用15m指标，1d趋势必须用1d指标！")
        # 如果timeframe不匹配，返回默认值，避免使用错误的技术指标
        # 确保 ema_cross 字段总是存在
        ema_cross_default = None
        if timeframe in ['5m', '15m', '1h']:
            ema_cross_default = {
                'is_golden_cross': False,
                'is_death_cross': False,
                'is_bullish': False,
                'is_bearish': False,
                'ema_short': None,
                'ema_long': None
            }
        return {
            'trend_direction': 'SIDEWAYS',
            'trend_text': '数据错误',
            'trend_class': 'trend-neutral',
            'trend_score': 50.0,
            'confidence': 0.0,
            'rsi_value': 50.0,
            'macd_trend': 'neutral',
            'ema_trend': 'neutral',
            'ema_cross': ema_cross_default,  # 确保字段总是存在
            'technical_score': 50.0,
            'price_change_pct': 0.0,
            'volume_change_pct': 0.0,
            'price_slope_pct': 0.0,
            'volume_slope_pct': 0.0,
            'updated_at': None
        }
    
    rsi_value = float(indicator_data.get('rsi_value', 50)) if indicator_data.get('rsi_value') else 50
    macd_trend = indicator_data.get('macd_trend', 'neutral')
    ema_trend = indicator_data.get('ema_trend', 'neutral')
    bb_position = indicator_data.get('bb_position', 'middle')
    technical_score = float(indicator_data.get('technical_score', 50)) if indicator_data.get('technical_score') else 50
    
    # 趋势评分（0-100，50为中性）
    trend_score = 50.0
    price_trend_score = 50.0  # 价格趋势评分
    volume_trend_score = 50.0  # 成交量趋势评分
    price_change_pct = 0.0
    volume_change_pct = 0.0
    price_slope_pct = 0.0
    volume_slope_pct = 0.0
    
    # 分析价格和成交量变化（直接与前一个数据对比）
    if klines and len(klines) >= 2:
        # K线数据按时间倒序排列（最新的在前）
        # klines[0] 是最新的K线，klines[1] 是前一个K线
        
        # 提取最新和前一个K线的价格和成交量
        current_price = float(klines[0]['close_price'])
        previous_price = float(klines[1]['close_price'])
        current_volume = float(klines[0].get('volume', 0))
        previous_volume = float(klines[1].get('volume', 0))
        
        # ========== 价格变化百分比计算 ==========
        # 直接对比：最新K线收盘价 vs 前一个K线收盘价
        # 公式：(最新价格 - 前一个价格) / 前一个价格 * 100
        price_change_pct = ((current_price - previous_price) / previous_price) * 100 if previous_price > 0 else 0
        
        # ========== 成交量变化百分比计算 ==========
        # 直接对比：最新K线成交量 vs 前一个K线成交量
        # 公式：(最新成交量 - 前一个成交量) / 前一个成交量 * 100
        volume_change_pct = ((current_volume - previous_volume) / previous_volume) * 100 if previous_volume > 0 else 0
        
        # 简化的斜率计算（用于兼容性，实际不再使用）
        price_slope_pct = price_change_pct
        volume_slope_pct = volume_change_pct
        
        # 成交量比率（用于判断成交量是否放大）
        volume_ratio = current_volume / previous_volume if previous_volume > 0 else 1
        
        # 根据时间周期计算趋势评分（简化逻辑，基于价格和成交量变化）
        # 价格趋势评分：基于价格变化百分比，直接映射到0-100分
        # 每1%价格变化 = 10分，50分为中性（价格不变）
        if price_change_pct > 0:
            # 价格上涨：50-100分
            price_trend_score = 50 + min(price_change_pct * 10, 50)  # 每1%涨幅=10分，最高100分
        else:
            # 价格下跌：0-50分
            price_trend_score = 50 + max(price_change_pct * 10, -50)  # 每1%跌幅=-10分，最低0分
        
        # 成交量趋势评分：基于价格变化和成交量比率的配合
        if price_change_pct > 0:
            # 价格上涨时
            if volume_ratio > 1.2:
                volume_trend_score = 70  # 价涨量增，看涨
            elif volume_ratio > 0.8:
                volume_trend_score = 55  # 价涨量平，中性偏涨
            else:
                volume_trend_score = 45  # 价涨量缩，看涨乏力
        elif price_change_pct < 0:
            # 价格下跌时
            if volume_ratio > 1.2:
                volume_trend_score = 30  # 价跌量增，看跌
            elif volume_ratio > 0.8:
                volume_trend_score = 45  # 价跌量平，中性偏跌
            else:
                volume_trend_score = 50  # 价跌量缩，可能反弹
        else:
            # 价格不变
            volume_trend_score = 50
        
        # 价格和成交量趋势综合评分（价格权重70%，成交量权重30%）
        price_trend_score = (price_trend_score * 0.7 + volume_trend_score * 0.3)
    
    # 技术指标评分（权重根据时间周期调整）
    indicator_score = 50.0
    
    # RSI评分
    if rsi_value < 30:
        indicator_score += 15  # 超卖，看涨
    elif rsi_value > 70:
        indicator_score -= 15  # 超买，看跌
    elif 40 <= rsi_value <= 60:
        indicator_score += 3   # 中性区域
    
    # MACD评分
    if macd_trend == 'bullish_cross':
        indicator_score += 12
    elif macd_trend == 'bearish_cross':
        indicator_score -= 12
    
    # EMA趋势评分
    if ema_trend == 'bullish':
        indicator_score += 8
    elif ema_trend == 'bearish':
        indicator_score -= 8
    
    # EMA金叉/死叉评分（仅对5m、15m、1h时间周期）
    if ema_cross_info and timeframe in ['5m', '15m', '1h']:
        if ema_cross_info.get('is_golden_cross'):
            indicator_score += 10  # 金叉加分
        elif ema_cross_info.get('is_death_cross'):
            indicator_score -= 10  # 死叉减分
    
    # 布林带位置评分
    if bb_position == 'below_lower':
        indicator_score += 8  # 价格在下轨，可能反弹
    elif bb_position == 'above_upper':
        indicator_score -= 8  # 价格在上轨，可能回调
    
    # 综合评分（根据时间周期调整权重）
    # 简化逻辑：价格+成交量趋势占主要权重，技术指标作为辅助
    if timeframe == '1d':
        # 1d趋势：价格+成交量趋势80%，技术指标20%（更重视实际价格变化）
        trend_score = (price_trend_score * 0.8 + indicator_score * 0.1 + technical_score * 0.1)
    elif timeframe == '1h':
        # 1h趋势：价格+成交量趋势70%，技术指标30%
        trend_score = (price_trend_score * 0.7 + indicator_score * 0.15 + technical_score * 0.15)
    elif timeframe == '15m':
        # 15m趋势：价格+成交量趋势60%，技术指标40%
        trend_score = (price_trend_score * 0.6 + indicator_score * 0.2 + technical_score * 0.2)
    else:  # 5m
        # 5m趋势：价格+成交量趋势60%，技术指标40%
        trend_score = (price_trend_score * 0.6 + indicator_score * 0.2 + technical_score * 0.2)
    
    trend_score = max(0, min(100, trend_score))
    
    # 判断趋势方向（调整阈值，让震荡范围更窄）
    if trend_score >= 75:
        trend_direction = 'STRONG_UPTREND'
        trend_text = '强烈上涨'
        trend_class = 'trend-strong-up'
    elif trend_score >= 60:
        trend_direction = 'UPTREND'
        trend_text = '上涨'
        trend_class = 'trend-up'
    elif trend_score >= 45:
        trend_direction = 'SIDEWAYS'
        trend_text = '震荡'
        trend_class = 'trend-neutral'
    elif trend_score >= 30:
        trend_direction = 'DOWNTREND'
        trend_text = '下跌'
        trend_class = 'trend-down'
    else:
        trend_direction = 'STRONG_DOWNTREND'
        trend_text = '强烈下跌'
        trend_class = 'trend-strong-down'
    
    # 置信度（基于数据完整性）
    confidence = 80.0
    if not indicator_data.get('rsi_value'):
        confidence -= 20
    if not indicator_data.get('macd_trend'):
        confidence -= 15
    if not indicator_data.get('ema_trend'):
        confidence -= 15
    
    # 构建EMA金叉信息（仅对5m、15m、1h时间周期）
    ema_cross_result = None
    if ema_cross_info and timeframe in ['5m', '15m', '1h']:
        ema_cross_result = {
            'is_golden_cross': ema_cross_info.get('is_golden_cross', False),
            'is_death_cross': ema_cross_info.get('is_death_cross', False),
            'is_bullish': ema_cross_info.get('is_bullish', False),  # 当前多头排列
            'is_bearish': ema_cross_info.get('is_bearish', False),  # 当前空头排列
            'ema_short': ema_cross_info.get('ema_short'),
            'ema_long': ema_cross_info.get('ema_long')
        }
    elif timeframe in ['5m', '15m', '1h']:
        # 即使没有历史数据，也尝试从indicator_data获取当前EMA状态
        ema_short = float(indicator_data.get('ema_short', 0)) if indicator_data.get('ema_short') else 0
        ema_long = float(indicator_data.get('ema_long', 0)) if indicator_data.get('ema_long') else 0
        if ema_short > 0 and ema_long > 0:
            ema_cross_result = {
                'is_golden_cross': False,
                'is_death_cross': False,
                'is_bullish': ema_short > ema_long,
                'is_bearish': ema_short < ema_long,
                'ema_short': ema_short,
                'ema_long': ema_long
            }
    
    # 确保ema_cross字段总是存在（即使为None，也要包含在返回结果中）
    # 对于5m、15m、1h时间周期，如果没有数据，至少返回一个空对象
    if timeframe in ['5m', '15m', '1h'] and ema_cross_result is None:
        # 最后尝试从indicator_data获取（双重保险）
        ema_short = float(indicator_data.get('ema_short', 0)) if indicator_data.get('ema_short') else 0
        ema_long = float(indicator_data.get('ema_long', 0)) if indicator_data.get('ema_long') else 0
        if ema_short > 0 and ema_long > 0:
            ema_cross_result = {
                'is_golden_cross': False,
                'is_death_cross': False,
                'is_bullish': ema_short > ema_long,
                'is_bearish': ema_short < ema_long,
                'ema_short': ema_short,
                'ema_long': ema_long
            }
        else:
            # 即使没有数据，也返回一个空对象，确保字段存在
            ema_cross_result = {
                'is_golden_cross': False,
                'is_death_cross': False,
                'is_bullish': False,
                'is_bearish': False,
                'ema_short': None,
                'ema_long': None
            }
    
    return {
        'trend_direction': trend_direction,
        'trend_text': trend_text,
        'trend_class': trend_class,
        'trend_score': round(trend_score, 2),
        'confidence': round(confidence, 2),
        'rsi_value': rsi_value,
        'macd_trend': macd_trend,
        'ema_trend': ema_trend,
        'ema_cross': ema_cross_result,  # EMA金叉/死叉信息（5m/15m/1h总是有值，1d为None）
        'technical_score': technical_score,
        'price_change_pct': round(price_change_pct, 2),
        'volume_change_pct': round(volume_change_pct, 2),
        'price_slope_pct': round(price_slope_pct, 4),
        'volume_slope_pct': round(volume_slope_pct, 4),
        'updated_at': indicator_data.get('updated_at').isoformat() if indicator_data.get('updated_at') else None
    }


@app.get("/api/realtime-prices")
async def get_realtime_prices(symbols: str = None):
    """
    批量获取实时价格（用于前端实时更新）
    
    Args:
        symbols: 交易对列表，逗号分隔，如 "BTC/USDT,ETH/USDT"（可选，不提供则返回所有）
    
    Returns:
        价格数据字典 {symbol: {price, change_24h, updated_at}}
    """
    try:
        import pymysql
        
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            if symbols:
                # 解析交易对列表
                symbol_list = [s.strip() for s in symbols.split(',')]
                placeholders = ','.join(['%s'] * len(symbol_list))
                cursor.execute(
                    f"""SELECT symbol, current_price, change_24h, updated_at
                    FROM price_stats_24h 
                    WHERE symbol IN ({placeholders})""",
                    symbol_list
                )
            else:
                # 返回所有交易对的价格
                cursor.execute(
                    """SELECT symbol, current_price, change_24h, updated_at
                    FROM price_stats_24h 
                    ORDER BY symbol"""
                )
            
            price_data = cursor.fetchall()
            price_map = {}
            
            for row in price_data:
                price_map[row['symbol']] = {
                    'price': float(row['current_price']) if row.get('current_price') else None,
                    'change_24h': float(row['change_24h']) if row.get('change_24h') else None,
                    'updated_at': row['updated_at'].isoformat() if row.get('updated_at') else None
                }
            
            return {
                'success': True,
                'data': price_map,
                'total': len(price_map)
            }
            
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        logger.error(f"获取实时价格失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/futures-signals")
async def get_futures_signals():
    """
    获取合约交易信号分析

    综合考虑：
    - 资金费率（funding rate）
    - 多空比（long/short ratio）
    - 持仓量变化（open interest）
    - 技术指标（RSI、MACD、EMA等）
    - 价格趋势

    Returns:
        各交易对的合约信号分析
    """
    global _futures_signals_cache, _futures_signals_cache_time

    # 检查缓存
    with _futures_signals_cache_lock:
        if _futures_signals_cache is not None and _futures_signals_cache_time is not None:
            cache_age = (datetime.utcnow() - _futures_signals_cache_time).total_seconds()
            if cache_age < TECHNICAL_SIGNALS_CACHE_TTL:
                logger.debug(f"✅ 使用缓存的合约信号数据 (缓存年龄: {cache_age:.0f}秒)")
                return _futures_signals_cache

    try:
        import pymysql
        
        db_config = config.get('database', {}).get('mysql', {})
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            # 获取所有交易对
            cursor.execute("SELECT DISTINCT symbol FROM technical_indicators_cache WHERE timeframe = '1h'")
            symbols = [row['symbol'] for row in cursor.fetchall()]
            
            futures_signals = []
            
            for symbol in symbols:
                try:
                    # 1. 获取技术指标（5m, 15m, 1h周期）
                    tech_data_5m = None
                    tech_data_15m = None
                    tech_data_1h = None
                    
                    for timeframe in ['5m', '15m', '1h']:
                        cursor.execute(
                            """SELECT * FROM technical_indicators_cache 
                            WHERE symbol = %s AND timeframe = %s
                            ORDER BY updated_at DESC LIMIT 1""",
                            (symbol, timeframe)
                        )
                        result = cursor.fetchone()
                        if timeframe == '5m':
                            tech_data_5m = result
                        elif timeframe == '15m':
                            tech_data_15m = result
                        elif timeframe == '1h':
                            tech_data_1h = result
                    
                    # 使用1h作为主要技术指标（向后兼容）
                    tech_data = tech_data_1h
                    
                    # 2. 获取资金费率
                    cursor.execute(
                        """SELECT current_rate, current_rate_pct, trend, market_sentiment
                        FROM funding_rate_stats 
                        WHERE symbol = %s
                        ORDER BY updated_at DESC LIMIT 1""",
                        (symbol,)
                    )
                    funding_data = cursor.fetchone()
                    
                    # 3. 获取多空比数据
                    symbol_no_slash = symbol.replace('/', '')
                    cursor.execute(
                        """SELECT long_account, short_account, long_short_ratio, timestamp
                        FROM futures_long_short_ratio 
                        WHERE symbol IN (%s, %s)
                        ORDER BY timestamp DESC LIMIT 1""",
                        (symbol, symbol_no_slash)
                    )
                    ls_data = cursor.fetchone()
                    
                    # 4. 获取持仓量数据（用于计算变化）
                    cursor.execute(
                        """SELECT open_interest, timestamp
                        FROM futures_open_interest 
                        WHERE symbol IN (%s, %s)
                        ORDER BY timestamp DESC LIMIT 2""",
                        (symbol, symbol_no_slash)
                    )
                    oi_records = cursor.fetchall()
                    
                    # 5. 获取价格数据（用于计算涨跌幅和显示实时价格）
                    cursor.execute(
                        """SELECT current_price, change_24h, updated_at
                        FROM price_stats_24h 
                        WHERE symbol = %s
                        ORDER BY updated_at DESC LIMIT 1""",
                        (symbol,)
                    )
                    price_data = cursor.fetchone()
                    
                    # 分析合约信号
                    signal_analysis = _analyze_futures_signal(
                        symbol=symbol,
                        tech_data=tech_data,
                        tech_data_5m=tech_data_5m,
                        tech_data_15m=tech_data_15m,
                        tech_data_1h=tech_data_1h,
                        funding_data=funding_data,
                        ls_data=ls_data,
                        oi_records=oi_records,
                        price_data=price_data
                    )
                    
                    if signal_analysis:
                        futures_signals.append(signal_analysis)
                        
                except Exception as e:
                    logger.warning(f"分析{symbol}合约信号失败: {e}")
                    continue
            
            # 按信号强度排序
            futures_signals.sort(key=lambda x: abs(x.get('signal_score', 0)), reverse=True)

            result = {
                'success': True,
                'data': futures_signals,
                'total': len(futures_signals)
            }

            # 更新缓存
            with _futures_signals_cache_lock:
                _futures_signals_cache = result
                _futures_signals_cache_time = datetime.utcnow()
                logger.debug(f"✅ 合约信号数据已缓存 ({len(futures_signals)} 条记录)")

            return result
            
        finally:
            cursor.close()
            connection.close()
            
    except Exception as e:
        logger.error(f"获取合约信号失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _analyze_futures_signal(
    symbol: str,
    tech_data: dict = None,
    tech_data_5m: dict = None,
    tech_data_15m: dict = None,
    tech_data_1h: dict = None,
    funding_data: dict = None,
    ls_data: dict = None,
    oi_records: list = None,
    price_data: dict = None
) -> dict:
    """
    分析合约交易信号
    
    Args:
        symbol: 交易对
        tech_data: 技术指标数据
        funding_data: 资金费率数据
        ls_data: 多空比数据
        oi_records: 持仓量记录
        price_data: 价格数据
        
    Returns:
        合约信号分析结果
    """
    signal_score = 0.0  # 信号评分（-100到+100，正数=做多，负数=做空）
    reasons = []
    
    # 1. 资金费率分析（权重30%）
    funding_score = 0.0
    funding_rate = 0.0
    if funding_data and funding_data.get('current_rate'):
        funding_rate = float(funding_data['current_rate'])
        funding_rate_pct = funding_rate * 100
        
        # 资金费率极高（>0.1%）= 做空机会
        if funding_rate > 0.001:
            funding_score = -30
            reasons.append(f"资金费率极高({funding_rate_pct:.3f}%)，多头过度拥挤，做空机会")
        elif funding_rate > 0.0005:
            funding_score = -15
            reasons.append(f"资金费率较高({funding_rate_pct:.3f}%)，多头占优")
        # 资金费率极低（<-0.1%）= 做多机会
        elif funding_rate < -0.001:
            funding_score = 30
            reasons.append(f"资金费率极低({funding_rate_pct:.3f}%)，空头过度拥挤，做多机会")
        elif funding_rate < -0.0005:
            funding_score = 15
            reasons.append(f"资金费率较低({funding_rate_pct:.3f}%)，空头占优")
    
    signal_score += funding_score * 0.35  # 权重从30%提升到35%
    
    # 2. 多空比分析已移除（因为多空比是用户数的多空比，不是实际仓位的多空比）
    # 原权重25%已重新分配给其他维度
    long_short_ratio = 0.0
    if ls_data and ls_data.get('long_short_ratio'):
        long_short_ratio = float(ls_data['long_short_ratio'])
        # 保留数据用于显示，但不参与评分计算
    
    # 3. 持仓量变化分析（权重从15%提升到20%）
    oi_score = 0.0
    oi_change_pct = 0.0
    if oi_records and len(oi_records) >= 2:
        current_oi = float(oi_records[0]['open_interest'])
        previous_oi = float(oi_records[1]['open_interest'])
        if previous_oi > 0:
            oi_change_pct = ((current_oi - previous_oi) / previous_oi) * 100
            
            # 持仓量增加 + 价格上涨 = 趋势延续（看多）
            # 持仓量增加 + 价格下跌 = 趋势延续（看空）
            # 持仓量减少 = 可能反转
            if price_data and price_data.get('change_24h'):
                price_change = float(price_data['change_24h'])
                if oi_change_pct > 5:
                    if price_change > 0:
                        oi_score = 10  # 持仓量增加+价格上涨=看多
                        reasons.append(f"持仓量增加{oi_change_pct:.1f}%且价格上涨，趋势延续")
                    else:
                        oi_score = -10  # 持仓量增加+价格下跌=看空
                        reasons.append(f"持仓量增加{oi_change_pct:.1f}%且价格下跌，趋势延续")
                elif oi_change_pct < -5:
                    oi_score = 0  # 持仓量减少，可能反转
                    reasons.append(f"持仓量减少{abs(oi_change_pct):.1f}%，可能反转")
    
    signal_score += oi_score * 0.20  # 权重从15%提升到20%
    
    # 4. 技术指标分析（权重从30%提升到35%）
    tech_score = 0.0
    rsi_value = 50.0
    if tech_data:
        rsi_value = float(tech_data.get('rsi_value', 50)) if tech_data.get('rsi_value') else 50
        macd_trend = tech_data.get('macd_trend', 'neutral')
        ema_trend = tech_data.get('ema_trend', 'neutral')
        technical_score = float(tech_data.get('technical_score', 50)) if tech_data.get('technical_score') else 50
        
        # RSI分析
        if rsi_value < 30:
            tech_score += 15
            reasons.append(f"RSI超卖({rsi_value:.1f})，技术面看多")
        elif rsi_value > 70:
            tech_score -= 15
            reasons.append(f"RSI超买({rsi_value:.1f})，技术面看空")
        
        # MACD分析
        if macd_trend == 'bullish_cross':
            tech_score += 10
            reasons.append("MACD金叉，技术面看多")
        elif macd_trend == 'bearish_cross':
            tech_score -= 10
            reasons.append("MACD死叉，技术面看空")
        
        # EMA趋势
        if ema_trend == 'bullish':
            tech_score += 5
        elif ema_trend == 'bearish':
            tech_score -= 5
        
        # 技术评分转换（50为中心，转换为-30到+30）
        tech_score += (technical_score - 50) * 0.6
    
    signal_score += tech_score * 0.35  # 权重从30%提升到35%
    
    # 4.1. 多时间周期技术指标分析（用于前端显示）
    def _analyze_indicator_direction(indicator_data: dict) -> dict:
        """
        分析单个时间周期的技术指标方向
        
        Returns:
            {
                'rsi': {'value': float, 'direction': 'up'/'down'/'neutral'},
                'ema': {'direction': 'up'/'down'/'neutral'},
                'macd': {'direction': 'up'/'down'/'neutral'},
                'boll': {'position': str, 'direction': 'up'/'down'/'neutral'},
                'overall': 'up'/'down'/'neutral'  # 综合方向
            }
        """
        if not indicator_data:
            return None
        
        result = {}
        
        # RSI方向
        rsi_value = float(indicator_data.get('rsi_value', 50)) if indicator_data.get('rsi_value') else 50
        if rsi_value < 30:
            rsi_dir = 'up'  # 超卖，看涨
        elif rsi_value > 70:
            rsi_dir = 'down'  # 超买，看跌
        elif rsi_value > 50:
            rsi_dir = 'up'  # 偏强
        elif rsi_value < 50:
            rsi_dir = 'down'  # 偏弱
        else:
            rsi_dir = 'neutral'
        result['rsi'] = {'value': round(rsi_value, 2), 'direction': rsi_dir}
        
        # EMA方向
        ema_trend = indicator_data.get('ema_trend', 'neutral')
        if ema_trend == 'bullish':
            ema_dir = 'up'
        elif ema_trend == 'bearish':
            ema_dir = 'down'
        else:
            ema_dir = 'neutral'
        result['ema'] = {'direction': ema_dir}
        
        # MACD方向
        macd_trend = indicator_data.get('macd_trend', 'neutral')
        if macd_trend == 'bullish_cross':
            macd_dir = 'up'
        elif macd_trend == 'bearish_cross':
            macd_dir = 'down'
        else:
            macd_dir = 'neutral'
        result['macd'] = {'direction': macd_dir}
        
        # BOLL方向
        bb_position = indicator_data.get('bb_position', 'middle')
        if bb_position == 'below_lower':
            boll_dir = 'up'  # 价格在下轨，可能反弹
        elif bb_position == 'above_upper':
            boll_dir = 'down'  # 价格在上轨，可能回调
        else:
            boll_dir = 'neutral'
        result['boll'] = {'position': bb_position, 'direction': boll_dir}
        
        # 综合方向判断（多数指标向上=向上，多数指标向下=向下）
        up_count = sum([
            1 if rsi_dir == 'up' else 0,
            1 if ema_dir == 'up' else 0,
            1 if macd_dir == 'up' else 0,
            1 if boll_dir == 'up' else 0
        ])
        down_count = sum([
            1 if rsi_dir == 'down' else 0,
            1 if ema_dir == 'down' else 0,
            1 if macd_dir == 'down' else 0,
            1 if boll_dir == 'down' else 0
        ])
        
        if up_count > down_count:
            result['overall'] = 'up'
        elif down_count > up_count:
            result['overall'] = 'down'
        else:
            result['overall'] = 'neutral'
        
        return result
    
    # 分析各时间周期的技术指标
    indicators_5m = _analyze_indicator_direction(tech_data_5m) if tech_data_5m else None
    indicators_15m = _analyze_indicator_direction(tech_data_15m) if tech_data_15m else None
    indicators_1h = _analyze_indicator_direction(tech_data_1h) if tech_data_1h else None
    
    # 5. 价格趋势分析（权重从10%提升到10%，保持不变）
    price_score = 0.0
    price_change_24h = 0.0
    if price_data and price_data.get('change_24h'):
        price_change_24h = float(price_data['change_24h'])
        # 价格变化作为确认信号，权重较低
        if price_change_24h > 3:
            price_score = 5
        elif price_change_24h < -3:
            price_score = -5
    
    signal_score += price_score * 0.10  # 权重保持10%
    
    # 归一化到-100到+100
    signal_score = max(-100, min(100, signal_score))
    
    # 判断信号方向
    if signal_score >= 60:
        signal_direction = 'STRONG_LONG'
        signal_text = '强烈做多'
        signal_class = 'signal-strong-buy'
    elif signal_score >= 30:
        signal_direction = 'LONG'
        signal_text = '做多'
        signal_class = 'signal-buy'
    elif signal_score >= -30:
        signal_direction = 'NEUTRAL'
        signal_text = '观望'
        signal_class = 'signal-hold'
    elif signal_score >= -60:
        signal_direction = 'SHORT'
        signal_text = '做空'
        signal_class = 'signal-sell'
    else:
        signal_direction = 'STRONG_SHORT'
        signal_text = '强烈做空'
        signal_class = 'signal-strong-sell'
    
    # 提取技术指标原始值（用于前端显示）
    def _extract_indicator_values(indicator_data: dict) -> dict:
        """提取技术指标的原始值"""
        if not indicator_data:
            return None
        return {
            'rsi_value': float(indicator_data.get('rsi_value', 0)) if indicator_data.get('rsi_value') else None,
            'ema_short': float(indicator_data.get('ema_short', 0)) if indicator_data.get('ema_short') else None,
            'ema_long': float(indicator_data.get('ema_long', 0)) if indicator_data.get('ema_long') else None,
            'macd_value': float(indicator_data.get('macd_value', 0)) if indicator_data.get('macd_value') else None,
            'macd_signal_line': float(indicator_data.get('macd_signal_line', 0)) if indicator_data.get('macd_signal_line') else None,
            'macd_histogram': float(indicator_data.get('macd_histogram', 0)) if indicator_data.get('macd_histogram') else None,
            'bb_upper': float(indicator_data.get('bb_upper', 0)) if indicator_data.get('bb_upper') else None,
            'bb_middle': float(indicator_data.get('bb_middle', 0)) if indicator_data.get('bb_middle') else None,
            'bb_lower': float(indicator_data.get('bb_lower', 0)) if indicator_data.get('bb_lower') else None,
            'bb_position': indicator_data.get('bb_position', 'middle'),
            'technical_score': float(indicator_data.get('technical_score', 50)) if indicator_data.get('technical_score') else None
        }
    
    # 提取价格数据
    current_price = None
    price_updated_at = None
    if price_data and price_data.get('current_price'):
        current_price = float(price_data['current_price'])
        price_updated_at = price_data['updated_at'].isoformat() if price_data.get('updated_at') else None
    
    # 计算凯利公式建议仓位
    def _calculate_kelly_position(
        signal_score: float,
        current_price: float,
        symbol: str = None,
        price_change_24h: float = None,
        rsi_value: float = None,
        volatility: float = None
    ) -> dict:
        """
        使用凯利公式计算建议仓位
        
        凯利公式: f = (p * b - q) / b
        f: 建议仓位比例
        p: 胜率（0-1）
        b: 盈亏比（盈利/亏损）
        q: 败率 = 1 - p
        
        Args:
            signal_score: 信号评分（-100到+100）
            current_price: 当前价格
            price_change_24h: 24小时涨跌幅（%）
            rsi_value: RSI值
            volatility: 波动率（可选）
            
        Returns:
            {
                'position_pct': 建议仓位比例（%），
                'entry_price': 建议入场价,
                'stop_loss': 止损价,
                'take_profit': 止盈价,
                'kelly_fraction': 凯利分数,
                'win_rate': 胜率,
                'profit_loss_ratio': 盈亏比
            }
        """
        if not current_price or current_price <= 0:
            return None
        
        # 1. 计算胜率（基于信号强度）
        # 信号评分越高，胜率越高
        signal_strength = abs(signal_score) / 100.0  # 0-1
        base_win_rate = 0.5  # 基础胜率50%
        
        # 根据信号强度调整胜率
        if signal_score > 0:
            # 做多信号：信号越强，胜率越高
            win_rate = base_win_rate + signal_strength * 0.3  # 50%-80%
        elif signal_score < 0:
            # 做空信号：信号越强，胜率越高
            win_rate = base_win_rate + signal_strength * 0.3  # 50%-80%
        else:
            win_rate = base_win_rate
        
        # 根据RSI调整胜率
        if rsi_value:
            if rsi_value < 30 and signal_score > 0:
                # 超卖 + 做多信号，提高胜率
                win_rate = min(win_rate + 0.1, 0.85)
            elif rsi_value > 70 and signal_score < 0:
                # 超买 + 做空信号，提高胜率
                win_rate = min(win_rate + 0.1, 0.85)
            elif (rsi_value < 30 and signal_score < 0) or (rsi_value > 70 and signal_score > 0):
                # 信号与RSI矛盾，降低胜率
                win_rate = max(win_rate - 0.15, 0.35)
        
        # 根据价格趋势调整胜率
        if price_change_24h:
            if signal_score > 0 and price_change_24h > 0:
                # 做多 + 价格上涨，提高胜率
                win_rate = min(win_rate + 0.05, 0.85)
            elif signal_score < 0 and price_change_24h < 0:
                # 做空 + 价格下跌，提高胜率
                win_rate = min(win_rate + 0.05, 0.85)
            elif (signal_score > 0 and price_change_24h < -3) or (signal_score < 0 and price_change_24h > 3):
                # 信号与价格趋势严重矛盾，降低胜率
                win_rate = max(win_rate - 0.2, 0.3)
        
        win_rate = max(0.3, min(0.85, win_rate))  # 限制在30%-85%
        lose_rate = 1 - win_rate
        
        # 2. 计算盈亏比（基于信号强度和波动率）
        # 默认盈亏比：做多/做空信号越强，盈亏比越高
        base_profit_loss_ratio = 2.0  # 基础盈亏比 2:1
        
        # 根据信号强度调整盈亏比
        if abs(signal_score) >= 60:
            profit_loss_ratio = base_profit_loss_ratio + 1.0  # 3:1
        elif abs(signal_score) >= 30:
            profit_loss_ratio = base_profit_loss_ratio + 0.5  # 2.5:1
        else:
            profit_loss_ratio = base_profit_loss_ratio  # 2:1
        
        # 根据波动率调整（如果有）
        if volatility:
            if volatility > 0.05:  # 高波动
                profit_loss_ratio = max(profit_loss_ratio - 0.5, 1.5)
            elif volatility < 0.02:  # 低波动
                profit_loss_ratio = min(profit_loss_ratio + 0.5, 4.0)
        
        profit_loss_ratio = max(1.5, min(4.0, profit_loss_ratio))  # 限制在1.5:1到4:1
        
        # 3. 计算凯利分数
        # f = (p * b - q) / b
        # 其中 p = win_rate, b = profit_loss_ratio, q = lose_rate
        kelly_fraction = (win_rate * profit_loss_ratio - lose_rate) / profit_loss_ratio
        
        # 凯利分数限制在0-0.25（最多25%仓位，避免过度杠杆）
        kelly_fraction = max(0, min(0.25, kelly_fraction))
        
        # 4. 根据交易对确定价格精度
        price_decimals = 2  # 默认2位小数
        if symbol:
            symbol_upper = symbol.upper()
            if 'PUMP' in symbol_upper:
                price_decimals = 5  # PUMP保留5位小数
            elif 'DOGE' in symbol_upper:
                price_decimals = 4  # DOGE保留4位小数
        
        # 如果凯利分数为负，不建议开仓
        if kelly_fraction <= 0:
            return {
                'position_pct': 0.0,
                'entry_price': round(current_price, price_decimals),
                'stop_loss': round(current_price, price_decimals),
                'take_profit': round(current_price, price_decimals),
                'kelly_fraction': 0.0,
                'win_rate': round(win_rate * 100, 1),
                'profit_loss_ratio': round(profit_loss_ratio, 2),
                'recommendation': '不建议开仓'
            }
        
        # 5. 计算入场价、止损价、止盈价
        entry_price = current_price
        
        # 根据信号方向计算止损和止盈
        if signal_score > 0:
            # 做多信号
            # 止损：当前价格下方，根据波动率调整
            stop_loss_pct = 0.02 if not volatility else min(volatility * 0.8, 0.05)  # 2%-5%
            stop_loss = current_price * (1 - stop_loss_pct)
            
            # 止盈：根据盈亏比计算
            take_profit = current_price * (1 + stop_loss_pct * profit_loss_ratio)
        else:
            # 做空信号
            # 止损：当前价格上方
            stop_loss_pct = 0.02 if not volatility else min(volatility * 0.8, 0.05)  # 2%-5%
            stop_loss = current_price * (1 + stop_loss_pct)
            
            # 止盈：根据盈亏比计算
            take_profit = current_price * (1 - stop_loss_pct * profit_loss_ratio)
        
        # 5. 计算建议仓位比例（基于凯利分数，但更保守）
        # 使用凯利分数的50%作为实际建议（更保守）
        conservative_fraction = kelly_fraction * 0.5
        position_pct = conservative_fraction * 100  # 转换为百分比
        
        return {
            'position_pct': round(position_pct, 2),
            'entry_price': round(entry_price, price_decimals),
            'stop_loss': round(stop_loss, price_decimals),
            'take_profit': round(take_profit, price_decimals),
            'kelly_fraction': round(kelly_fraction, 4),
            'win_rate': round(win_rate * 100, 1),
            'profit_loss_ratio': round(profit_loss_ratio, 2),
            'recommendation': '建议开仓' if position_pct > 0 else '不建议开仓'
        }
    
    # 计算凯利公式建议
    kelly_advice = None
    if current_price:
        kelly_advice = _calculate_kelly_position(
            signal_score=signal_score,
            current_price=current_price,
            symbol=symbol,  # 传入symbol以确定价格精度
            price_change_24h=price_change_24h if price_change_24h else None,
            rsi_value=rsi_value if rsi_value else None,
            volatility=None  # 可以后续从历史数据计算
        )
    
    return {
        'symbol': symbol,
        'signal_direction': signal_direction,
        'signal_text': signal_text,
        'signal_class': signal_class,
        'signal_score': round(signal_score, 2),
        'funding_rate': round(funding_rate * 100, 4) if funding_rate else None,
        'long_short_ratio': round(long_short_ratio, 2) if long_short_ratio else None,
        'oi_change_pct': round(oi_change_pct, 2) if oi_change_pct else None,
        'rsi_value': round(rsi_value, 2) if rsi_value else None,
        'current_price': current_price,
        'price_change_24h': round(price_change_24h, 2) if price_change_24h else None,
        'price_updated_at': price_updated_at,
        'reasons': reasons[:3],  # 只显示前3个原因
        # 多时间周期技术指标
        'indicators_5m': {
            'directions': indicators_5m,
            'values': _extract_indicator_values(tech_data_5m)
        } if tech_data_5m else None,
        'indicators_15m': {
            'directions': indicators_15m,
            'values': _extract_indicator_values(tech_data_15m)
        } if tech_data_15m else None,
        'indicators_1h': {
            'directions': indicators_1h,
            'values': _extract_indicator_values(tech_data_1h)
        } if tech_data_1h else None,
        'kelly_advice': kelly_advice,  # 凯利公式建议
        'updated_at': datetime.utcnow().isoformat()
    }


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
                "last_updated": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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
                    "last_updated": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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
        now = datetime.utcnow()

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

        elapsed = (datetime.utcnow() - start_time).total_seconds()
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
                "last_updated": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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

    # 配置uvicorn日志，禁用访问日志
    import logging
    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.setLevel(logging.WARNING)  # 只显示WARNING及以上级别，过滤掉INFO级别的访问日志

    uvicorn.run(
        app,  # 直接传递app对象，而不是字符串
        host="0.0.0.0",
        port=9020,  # 改为9020端口，避免8000端口冲突
        reload=False,
        log_level="info",
        access_log=False  # 禁用访问日志
    )
