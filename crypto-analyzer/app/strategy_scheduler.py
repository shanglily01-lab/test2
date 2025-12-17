"""
交易策略自动执行调度器（实时监控模式）
独立运行，实时监控并执行启用的交易策略

执行策略：
- 每5秒检查一次启用的策略（实时监控）
- 根据策略配置的买入/卖出信号自动执行交易
- 支持EMA、MA/EMA等多种信号类型
- 实时响应市场变化，快速捕捉交易机会
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import signal
import yaml
from datetime import datetime
from loguru import logger
from typing import Optional

from app.services.strategy_executor import StrategyExecutor
from app.services.strategy_executor_v2 import StrategyExecutorV2
from app.services.strategy_test_service import StrategyTestService
from app.services.position_validator import PositionValidator
from app.trading.futures_trading_engine import FuturesTradingEngine
from app.trading.binance_futures_engine import BinanceFuturesEngine
from app.analyzers.technical_indicators import TechnicalIndicators


class StrategyScheduler:
    """交易策略自动执行调度器"""

    def __init__(self, config_path: str = 'config.yaml', use_v2: bool = True):
        """
        初始化调度器

        Args:
            config_path: 配置文件路径
            use_v2: 是否使用V2策略执行器（默认True，简化版）
        """
        self.use_v2 = use_v2

        # 加载配置（支持环境变量）
        from app.utils.config_loader import load_config
        config_file = Path(config_path)
        if not config_file.exists():
            config_file = project_root / 'config.yaml'
        self.config = load_config(config_file)

        # 初始化数据库配置
        db_config = self.config.get('database', {}).get('mysql', {})
        if not db_config:
            raise ValueError("数据库配置未找到，请检查config.yaml")

        # 初始化Telegram通知服务
        from app.services.trade_notifier import init_trade_notifier
        trade_notifier = init_trade_notifier(self.config)

        # 初始化合约交易引擎（模拟盘）
        logger.info("初始化合约交易引擎...")
        self.futures_engine = FuturesTradingEngine(db_config, trade_notifier=trade_notifier)

        # 初始化实盘交易引擎
        logger.info("初始化实盘交易引擎...")
        try:
            self.live_engine = BinanceFuturesEngine(db_config)
            logger.info("  ✓ 实盘交易引擎初始化成功")
            # 绑定live_engine到futures_engine，使模拟盘平仓能同步到实盘
            self.futures_engine.live_engine = self.live_engine
            logger.info("  ✓ 已绑定实盘引擎到模拟盘引擎")
        except Exception as e:
            logger.warning(f"  ⚠️ 实盘交易引擎初始化失败: {e}")
            self.live_engine = None

        # 初始化策略执行器
        if use_v2:
            logger.info("初始化策略执行器 V2（简化版）...")
            self.strategy_executor = StrategyExecutorV2(
                db_config=db_config,
                futures_engine=self.futures_engine,
                live_engine=self.live_engine
            )
            logger.info("  ✓ 策略执行器 V2 初始化成功")
        else:
            logger.info("初始化策略执行器...")
            self.strategy_executor = StrategyExecutor(
                db_config=db_config,
                futures_engine=self.futures_engine
            )
            logger.info("  ✓ 策略执行器初始化成功")
        
        # 初始化技术分析器（用于策略测试）
        logger.info("初始化技术分析器...")
        try:
            technical_analyzer = TechnicalIndicators(self.config)
            logger.info("  ✓ 技术分析器初始化成功")
        except Exception as e:
            logger.warning(f"  ⚠️ 技术分析器初始化失败: {e}")
            technical_analyzer = None
        
        # 初始化策略测试服务
        logger.info("初始化策略测试服务...")
        self.test_service = StrategyTestService(
            db_config=db_config,
            technical_analyzer=technical_analyzer
        )
        logger.info("  ✓ 策略测试服务初始化成功")

        # 初始化开单自检服务
        logger.info("初始化开单自检服务...")
        self.position_validator = PositionValidator(
            db_config=db_config,
            futures_engine=self.futures_engine,
            trade_notifier=trade_notifier,
            strategy_executor=self.strategy_executor
        )
        logger.info("  ✓ 开单自检服务初始化成功")

        # 运行状态
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.validator_task: Optional[asyncio.Task] = None
        self.interval = 5  # 默认5秒检查一次（实时监控）
        self.last_check_time = {}  # 记录每个策略的最后检查时间

        logger.info("策略调度器初始化完成")

    async def check_for_new_klines(self) -> bool:
        """
        检查是否有新K线生成（用于实时触发策略检查）
        
        Returns:
            bool: 如果有新K线返回True，否则返回False
        """
        try:
            import pymysql
            db_config = self.config.get('database', {}).get('mysql', {})
            connection = pymysql.connect(**db_config)
            cursor = connection.cursor(pymysql.cursors.DictCursor)
            
            try:
                # 检查最近1分钟内是否有新K线生成（1分钟K线）
                cursor.execute("""
                    SELECT COUNT(*) as count, MAX(timestamp) as latest_time
                    FROM kline_data
                    WHERE timeframe = '1m'
                    AND timestamp >= DATE_SUB(NOW(), INTERVAL 1 MINUTE)
                """)
                result = cursor.fetchone()
                
                if result and result.get('count', 0) > 0:
                    latest_time = result.get('latest_time')
                    # 检查这个时间是否比上次检查时更新
                    if latest_time:
                        latest_time_str = latest_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(latest_time, 'strftime') else str(latest_time)
                        last_check = self.last_check_time.get('kline_check', '')
                        if latest_time_str != last_check:
                            self.last_check_time['kline_check'] = latest_time_str
                            return True
                
                return False
            finally:
                cursor.close()
                connection.close()
        except Exception as e:
            logger.debug(f"检查新K线时出错: {e}")
            return False

    async def run_loop(self, interval: int = 5):
        """
        运行策略执行循环（实时监控模式）

        Args:
            interval: 检查间隔（秒），默认5秒（实时监控）
        """
        self.running = True
        self.interval = interval
        logger.info(f"🔄 策略实时监控服务已启动（间隔: {interval}秒，实时响应新K线）")

        try:
            loop_count = 0
            while self.running:
                try:
                    loop_count += 1
                    logger.debug(f"策略检查循环 #{loop_count} 开始...")
                    
                    # 检查是否有新K线生成，如果有则立即执行策略检查
                    has_new_kline = await self.check_for_new_klines()
                    
                    if has_new_kline:
                        logger.info("🆕 检测到新K线，立即执行策略检查")
                    else:
                        logger.debug("未检测到新K线，按计划执行策略检查")
                    
                    # 执行策略检查（无论是否有新K线，都按间隔检查以确保不遗漏）
                    await self.strategy_executor.check_and_execute_strategies()
                    
                    logger.debug(f"策略检查循环 #{loop_count} 完成，等待 {interval} 秒...")
                except Exception as e:
                    logger.error(f"策略执行循环出错: {e}")
                    import traceback
                    traceback.print_exc()

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("策略执行服务已取消")
            raise
        except Exception as e:
            logger.error(f"策略执行循环异常退出: {e}")
            import traceback
            traceback.print_exc()

    def start(self, interval: int = 5):
        """启动策略执行服务（实时监控模式）"""
        if self.running:
            logger.warning("策略执行器已在运行")
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        self.task = loop.create_task(self.run_loop(interval))
        logger.info(f"策略实时监控服务已启动（每{interval}秒检查）")

        # 启动开单自检服务
        self.validator_task = loop.create_task(self.position_validator.start())
        logger.info("开单自检服务已启动")

    def stop(self):
        """停止策略执行服务"""
        logger.info("正在停止策略执行服务...")
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()

        # 停止开单自检服务
        if self.validator_task and not self.validator_task.done():
            self.validator_task.cancel()
            asyncio.create_task(self.position_validator.stop())

        logger.info("策略执行服务已停止")

    async def test_strategy(self, strategy_config: dict) -> dict:
        """
        测试交易策略（回测2天数据）
        
        Args:
            strategy_config: 策略配置字典，包含：
                - symbols: 交易对列表
                - buyDirection: 交易方向 ['long', 'short']
                - leverage: 交易倍数
                - buySignals: 买入EMA信号 (ema_5m, ema_15m, ema_1h)
                - sellSignals: 卖出EMA信号
                - positionSize: 仓位大小 (%)
                - 等其他策略参数...
        
        Returns:
            测试结果，包含交易记录和盈亏统计
        """
        logger.info("开始测试交易策略...")
        try:
            result = await self.test_service.test_strategy(strategy_config)
            logger.info("策略测试完成")
            return result
        except Exception as e:
            logger.error(f"策略测试失败: {e}", exc_info=True)
            raise
    
    async def run_forever(self, interval: int = 5):
        """
        持续运行策略执行服务（阻塞，实时监控模式）

        Args:
            interval: 检查间隔（秒），默认5秒（实时监控）
        """
        # 设置信号处理
        def signal_handler(signum, frame):
            logger.info(f"收到信号 {signum}，正在停止...")
            self.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # 启动服务
        self.start(interval)

        try:
            # 等待任务完成（实际上会一直运行）
            await self.task
        except asyncio.CancelledError:
            logger.info("策略执行服务已取消")
        except Exception as e:
            logger.error(f"策略执行服务异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.stop()


def main():
    """主函数 - 用于直接运行调度器"""
    import argparse
    import json

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
        log_dir / "strategy_scheduler_{time:YYYY-MM-DD}.log",
        rotation="00:00",  # 每天午夜轮转
        retention="30 days",  # 保留30天的日志
        level="DEBUG",  # 文件记录DEBUG级别以上的所有日志
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        encoding="utf-8",
        enqueue=True,  # 异步写入，提高性能
        backtrace=True,  # 记录异常堆栈
        diagnose=True  # 记录变量值
    )

    parser = argparse.ArgumentParser(description='交易策略自动执行调度器')
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='配置文件路径（默认: config.yaml）'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='检查间隔（秒），默认5秒（实时监控模式）'
    )
    parser.add_argument(
        '--test',
        type=str,
        help='测试策略：传入策略配置JSON文件路径或JSON字符串'
    )
    parser.add_argument(
        '--test-strategy-id',
        type=int,
        help='测试策略：从配置文件加载指定ID的策略进行测试'
    )
    parser.add_argument(
        '--v2',
        action='store_true',
        default=True,
        help='使用V2策略执行器（简化版，新逻辑）- 默认启用'
    )
    args = parser.parse_args()

    # 创建调度器
    scheduler = StrategyScheduler(config_path=args.config, use_v2=args.v2)

    if args.v2:
        logger.info("🆕 使用 V2 策略执行器（简化版）")

    # 如果指定了测试参数，执行测试
    if args.test or args.test_strategy_id:
        async def run_test():
            strategy_config = None
            
            if args.test_strategy_id:
                # 从配置文件加载策略
                import json as json_lib
                from pathlib import Path
                strategies_file = Path('config/strategies/futures_strategies.json')
                if strategies_file.exists():
                    with open(strategies_file, 'r', encoding='utf-8') as f:
                        strategies = json_lib.load(f)
                        for strategy in strategies:
                            if strategy.get('id') == args.test_strategy_id:
                                strategy_config = strategy
                                break
                    if not strategy_config:
                        logger.error(f"未找到ID为 {args.test_strategy_id} 的策略")
                        return
                else:
                    logger.error("策略配置文件不存在: config/strategies/futures_strategies.json")
                    return
            elif args.test:
                # 从文件或JSON字符串加载策略配置
                import json as json_lib
                test_path = Path(args.test)
                if test_path.exists():
                    with open(test_path, 'r', encoding='utf-8') as f:
                        strategy_config = json_lib.load(f)
                else:
                    # 尝试作为JSON字符串解析
                    try:
                        strategy_config = json_lib.loads(args.test)
                    except:
                        logger.error(f"无法解析策略配置: {args.test}")
                        return
            
            if strategy_config:
                logger.info("=" * 60)
                logger.info("开始测试策略...")
                logger.info("=" * 60)
                result = await scheduler.test_strategy(strategy_config)
                
                # 打印测试结果
                print("\n" + "=" * 60)
                print("策略测试结果")
                print("=" * 60)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                print("=" * 60)
            else:
                logger.error("未指定有效的策略配置")
        
        # 运行测试
        try:
            asyncio.run(run_test())
        except Exception as e:
            logger.error(f"测试执行失败: {e}")
            import traceback
            traceback.print_exc()
        return

    # 运行服务
    try:
        asyncio.run(scheduler.run_forever(interval=args.interval))
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
        scheduler.stop()
    except Exception as e:
        logger.error(f"调度器运行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()

