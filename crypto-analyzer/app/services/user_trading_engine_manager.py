# -*- coding: utf-8 -*-
"""
用户交易引擎管理器
为每个用户管理独立的交易引擎实例（使用各自的API密钥）
"""

from typing import Dict, Optional
from loguru import logger
import threading
import time


class UserTradingEngineManager:
    """用户交易引擎管理器"""

    def __init__(self, db_config: Dict):
        """
        初始化管理器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self._engines: Dict[str, any] = {}  # {user_id_exchange: engine}
        self._engine_last_used: Dict[str, float] = {}  # 最后使用时间
        self._lock = threading.Lock()
        self._cleanup_interval = 1800  # 30分钟清理一次
        self._engine_ttl = 3600  # 引擎闲置1小时后清理

    def _get_engine_key(self, user_id: int, exchange: str = 'binance') -> str:
        """生成引擎缓存键"""
        return f"{user_id}_{exchange}"

    def get_engine(self, user_id: int, exchange: str = 'binance'):
        """
        获取用户的交易引擎

        Args:
            user_id: 用户ID
            exchange: 交易所

        Returns:
            交易引擎实例，如果用户没有配置API密钥则返回None
        """
        key = self._get_engine_key(user_id, exchange)

        with self._lock:
            # 检查缓存
            if key in self._engines:
                self._engine_last_used[key] = time.time()
                return self._engines[key]

            # 创建新引擎
            engine = self._create_engine(user_id, exchange)
            if engine:
                self._engines[key] = engine
                self._engine_last_used[key] = time.time()

            return engine

    def _create_engine(self, user_id: int, exchange: str = 'binance'):
        """
        为用户创建交易引擎

        Args:
            user_id: 用户ID
            exchange: 交易所

        Returns:
            交易引擎实例，如果用户没有配置API密钥则返回None
        """
        try:
            # 获取用户的API密钥
            from app.services.api_key_service import get_api_key_service
            api_key_service = get_api_key_service()

            if not api_key_service:
                logger.warning(f"API密钥服务未初始化，无法为用户 {user_id} 创建交易引擎")
                return None

            api_keys = api_key_service.get_api_key(user_id, exchange)
            if not api_keys:
                logger.debug(f"用户 {user_id} 未配置 {exchange} API密钥")
                return None

            # 根据交易所创建引擎
            if exchange == 'binance':
                from app.trading.binance_futures_engine import BinanceFuturesEngine
                engine = BinanceFuturesEngine(
                    self.db_config,
                    api_key=api_keys['api_key'],
                    api_secret=api_keys['api_secret']
                )
                logger.info(f"为用户 {user_id} 创建了 {exchange} 交易引擎")
                return engine

            elif exchange == 'gate':
                # TODO: 实现Gate交易引擎
                logger.warning(f"暂不支持 {exchange} 交易所")
                return None

            else:
                logger.warning(f"不支持的交易所: {exchange}")
                return None

        except Exception as e:
            logger.error(f"为用户 {user_id} 创建交易引擎失败: {e}")
            return None

    def invalidate_engine(self, user_id: int, exchange: str = 'binance'):
        """
        使用户的引擎缓存失效（当用户更新API密钥时调用）

        Args:
            user_id: 用户ID
            exchange: 交易所
        """
        key = self._get_engine_key(user_id, exchange)

        with self._lock:
            if key in self._engines:
                del self._engines[key]
                del self._engine_last_used[key]
                logger.info(f"已清除用户 {user_id} 的 {exchange} 交易引擎缓存")

    def cleanup_idle_engines(self):
        """清理闲置的引擎"""
        current_time = time.time()

        with self._lock:
            keys_to_remove = []
            for key, last_used in self._engine_last_used.items():
                if current_time - last_used > self._engine_ttl:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._engines[key]
                del self._engine_last_used[key]
                logger.debug(f"清理闲置引擎: {key}")

            if keys_to_remove:
                logger.info(f"清理了 {len(keys_to_remove)} 个闲置交易引擎")

    def get_default_engine(self, exchange: str = 'binance'):
        """
        获取默认交易引擎（使用配置文件中的API密钥）
        用于没有登录的场景或后台服务

        Args:
            exchange: 交易所

        Returns:
            默认交易引擎实例
        """
        key = f"default_{exchange}"

        with self._lock:
            if key in self._engines:
                self._engine_last_used[key] = time.time()
                return self._engines[key]

            # 创建默认引擎
            try:
                if exchange == 'binance':
                    from app.trading.binance_futures_engine import BinanceFuturesEngine
                    engine = BinanceFuturesEngine(self.db_config)  # 使用配置文件中的密钥
                    self._engines[key] = engine
                    self._engine_last_used[key] = time.time()
                    logger.info(f"创建了默认 {exchange} 交易引擎")
                    return engine
            except Exception as e:
                logger.error(f"创建默认交易引擎失败: {e}")
                return None

        return None


# 全局实例
_engine_manager: Optional[UserTradingEngineManager] = None


def get_engine_manager() -> Optional[UserTradingEngineManager]:
    """获取引擎管理器实例"""
    return _engine_manager


def init_engine_manager(db_config: Dict) -> UserTradingEngineManager:
    """初始化引擎管理器"""
    global _engine_manager
    _engine_manager = UserTradingEngineManager(db_config)
    return _engine_manager
