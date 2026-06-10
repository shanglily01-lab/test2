#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket K线采集服务入口 (独立进程)

替代 fast_collector_service.py 的 REST 轮询，避免 IP 被 ban (-1003)。
fast_collector_service.py 不删，降频到 1 小时做兜底/校准。

Phase 1: 仅 U本位 5m + 15m, ~500 streams, 2-3 个 WS 连接
Phase 2 (本服务后续扩展): 加 1h/1d

启动:
    python ws_kline_collector_service.py
"""
import asyncio
import sys
from pathlib import Path

from dotenv import dotenv_values
from loguru import logger

# 添加项目路径
_project_root = Path(__file__).parent
sys.path.insert(0, str(_project_root))

from app.collectors.smart_futures_collector import SmartFuturesCollector
from app.services.binance_ws_kline_collector import WSKlineCollector
from app.utils.pid_lock import acquire_pid_lock


# Phase 1: 采 U本位 5m 和 15m
# 1h 由 REST 回填兜底 (1h K 线 1 小时才闭一次, WS 订阅只产生僵尸重连)
PHASE1_INTERVALS = ['5m', '15m']
HEARTBEAT_INTERVAL_S = 3600  # 主循环 sleep 间隔


def _setup_logging() -> None:
    """日志配置 (跟 fast_collector 风格一致)"""
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
        level="INFO",
    )
    logger.add(
        "logs/ws_kline_collector_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
        level="INFO",
    )


def _load_db_config() -> dict:
    """从本项目 .env 读 DB 配置 (避免被系统环境变量污染)"""
    _env = dotenv_values(_project_root / '.env')
    return {
        'host':     _env.get('DB_HOST', 'localhost'),
        'port':     int(_env.get('DB_PORT', 3306)),
        'user':     _env.get('DB_USER', 'root'),
        'password': _env.get('DB_PASSWORD', ''),
        'database': _env.get('DB_NAME', 'binance-data'),
    }


async def main_async() -> None:
    db_config = _load_db_config()

    # 用 SmartFuturesCollector 提供的方法读 symbols, 保持一致性
    helper = SmartFuturesCollector(db_config)
    usdt_symbols = helper.get_trading_symbols()

    if not usdt_symbols:
        logger.error("config.yaml 中没有 U本位 symbols, 退出")
        return

    logger.info("=" * 60)
    logger.info("WS K线采集服务启动")
    logger.info(f"U本位 symbols: {len(usdt_symbols)}")
    logger.info(f"周期: {PHASE1_INTERVALS}")
    logger.info("=" * 60)

    collector = WSKlineCollector(
        db_config=db_config,
        usdt_symbols=usdt_symbols,
        intervals=PHASE1_INTERVALS,
    )
    await collector.start()

    # 永跑, WS 连接和 flusher 在后台 task 里跑
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


def main() -> None:
    _setup_logging()
    try:
        # 全局异常钩子: 捕获未处理的后台 task 异常
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(_async_exception_handler)
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        logger.info("收到停止信号, 服务退出")


def _async_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """全局 asyncio 异常处理器 — 记录后台 task 的未处理异常"""
    msg = context.get('message', '未知 asyncio 异常')
    exc = context.get('exception')
    task = context.get('future') or context.get('task', '<无>')
    logger.error(f"[全局异常] task={task}, msg={msg}, exc={exc}")


if __name__ == '__main__':
    # PID 文件锁 - 防止重复启动 (与 fast_collector 同模式)
    acquire_pid_lock('ws_kline_collector_service')
    main()
