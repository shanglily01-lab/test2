#!/usr/bin/env python3
"""
合约监控服务 - 集成到调度器
Futures Monitoring Service - Integrated with Scheduler

将止盈止损监控集成到现有的APScheduler调度器中
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
from loguru import logger
from app.trading.stop_loss_monitor import StopLossMonitor


class FuturesMonitorService:
    """合约监控服务"""

    def __init__(self, config_path: str = None):
        """
        初始化服务

        Args:
            config_path: 配置文件路径
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / 'config.yaml'

        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        self.db_config = self.config['database']['mysql']
        self.monitor = None

        logger.info("FuturesMonitorService initialized")

    def start_monitor(self):
        """启动监控器"""
        if not self.monitor:
            self.monitor = StopLossMonitor(self.db_config)
            logger.info("Stop-loss monitor created")

    def monitor_positions(self):
        """
        监控持仓（供调度器调用）

        这个方法会被APScheduler定期调用
        """
        try:
            if not self.monitor:
                self.start_monitor()

            results = self.monitor.monitor_all_positions()

            # 记录重要事件
            if results['liquidated'] > 0:
                logger.warning(f"⚠️ {results['liquidated']} positions were liquidated")

            if results['stop_loss'] > 0:
                logger.info(f"🛑 {results['stop_loss']} positions hit stop-loss")

            if results['take_profit'] > 0:
                logger.info(f"✅ {results['take_profit']} positions hit take-profit")

            return results

        except Exception as e:
            logger.error(f"Error in futures monitoring: {e}", exc_info=True)
            return None

    def stop_monitor(self):
        """停止监控器"""
        if self.monitor:
            self.monitor.close()
            self.monitor = None
            logger.info("Stop-loss monitor stopped")


# 全局实例（供调度器使用）
_futures_monitor_service = None


def get_futures_monitor_service(config_path: str = None) -> FuturesMonitorService:
    """
    获取合约监控服务单例

    Args:
        config_path: 配置文件路径

    Returns:
        FuturesMonitorService实例
    """
    global _futures_monitor_service

    if _futures_monitor_service is None:
        _futures_monitor_service = FuturesMonitorService(config_path)

    return _futures_monitor_service


def monitor_futures_positions():
    """
    监控合约持仓（供调度器调用的函数）

    在scheduler.py中添加这个任务:
    scheduler.add_job(monitor_futures_positions, 'interval', minutes=1, id='futures_monitor')
    """
    service = get_futures_monitor_service()
    return service.monitor_positions()


if __name__ == '__main__':
    # 测试运行
    service = FuturesMonitorService()
    service.start_monitor()

    # 执行一次监控
    results = service.monitor_positions()

    print("\n监控结果:")
    print(f"  总持仓数: {results['total_positions']}")
    print(f"  监控中: {results['monitoring']}")
    print(f"  止损触发: {results['stop_loss']}")
    print(f"  止盈触发: {results['take_profit']}")
    print(f"  强平触发: {results['liquidated']}")

    service.stop_monitor()
