"""
日志配置管理
统一管理所有模块的日志输出级别
"""

from loguru import logger
import sys

# 日志级别配置
# 可选值: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVELS = {
    # 核心服务 - 只显示WARNING及以上
    'app.services.cache_update_service': 'WARNING',
    'app.services.price_cache_service': 'WARNING',

    # API层 - 只显示WARNING及以上
    'app.api.enhanced_dashboard_cached': 'WARNING',
    'app.api.enhanced_dashboard': 'WARNING',

    # 调度器 - 只显示重要信息
    'app.scheduler': 'INFO',

    # 采集器 - 只显示ERROR
    'app.collectors.price_collector': 'ERROR',
    'app.collectors.binance_futures_collector': 'ERROR',
    'app.collectors.hyperliquid_collector': 'ERROR',
    'app.collectors.news_collector': 'ERROR',

    # 交易相关 - 只显示重要信息
    'app.trading.auto_futures_trader': 'INFO',
    'app.trading.futures_monitor_service': 'INFO',
    'app.trading.ema_signal_monitor': 'INFO',

    # 主程序 - 显示启动信息
    'app.main': 'INFO',

    # 默认级别
    'default': 'WARNING'
}

def setup_logging(level: str = 'INFO'):
    """
    配置日志系统

    Args:
        level: 默认日志级别
    """
    # 移除默认的logger
    logger.remove()

    # 添加控制台输出
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=level,
        colorize=True
    )

    # 添加文件输出（仅ERROR及以上）
    logger.add(
        "logs/error.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="ERROR",
        rotation="10 MB",
        retention="7 days"
    )

def get_logger(name: str):
    """
    获取指定模块的logger

    Args:
        name: 模块名称

    Returns:
        配置好的logger
    """
    level = LOG_LEVELS.get(name, LOG_LEVELS['default'])
    return logger.bind(name=name, level=level)
