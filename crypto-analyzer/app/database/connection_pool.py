#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MySQL 连接池管理器
解决长时间运行的服务中连接断开的问题
"""
import pymysql
from pymysql.cursors import DictCursor
from typing import Optional, Dict, Any
from loguru import logger
import threading
import time
from contextlib import contextmanager


class MySQLConnectionPool:
    """MySQL 连接池管理器 - 支持自动重连和连接健康检查"""

    def __init__(self, db_config: Dict[str, Any], pool_size: int = 5):
        """
        初始化连接池

        Args:
            db_config: 数据库配置字典
            pool_size: 连接池大小
        """
        self.db_config = db_config
        self.pool_size = pool_size
        self.connections = []
        self.lock = threading.Lock()
        self._last_check_time = time.time()
        self._check_interval = 300  # 每5分钟检查一次连接健康

        logger.info(f"✅ MySQL连接池初始化 (大小: {pool_size})")

    def _create_connection(self):
        """创建新的数据库连接"""
        try:
            # 复制配置，避免修改原始配置
            config = self.db_config.copy()

            # 设置默认值（如果配置中没有）
            config.setdefault('charset', 'utf8mb4')
            config.setdefault('autocommit', False)
            config.setdefault('connect_timeout', 10)
            config.setdefault('read_timeout', 30)
            config.setdefault('write_timeout', 30)
            config.setdefault('init_command', "SET SESSION wait_timeout=28800")

            # cursorclass 单独设置（不能用 setdefault，因为可能是 None）
            if 'cursorclass' not in config or config['cursorclass'] is None:
                config['cursorclass'] = DictCursor

            conn = pymysql.connect(**config)
            logger.debug("创建新的数据库连接")
            return conn
        except Exception as e:
            logger.error(f"❌ 创建数据库连接失败: {e}")
            raise

    def _is_connection_alive(self, conn) -> bool:
        """检查连接是否存活"""
        try:
            if conn is None:
                return False
            if not conn.open:
                return False
            # 使用 ping 检查连接
            conn.ping(reconnect=False)
            return True
        except:
            return False

    def _get_healthy_connection(self):
        """从池中获取健康的连接"""
        with self.lock:
            # 定期清理无效连接
            current_time = time.time()
            if current_time - self._last_check_time > self._check_interval:
                self._cleanup_dead_connections()
                self._last_check_time = current_time

            # 尝试从池中获取健康连接
            while self.connections:
                conn = self.connections.pop(0)
                if self._is_connection_alive(conn):
                    return conn
                else:
                    try:
                        conn.close()
                    except:
                        pass

            # 池中没有可用连接，创建新连接
            return self._create_connection()

    def _cleanup_dead_connections(self):
        """清理池中的死连接"""
        alive_connections = []
        for conn in self.connections:
            if self._is_connection_alive(conn):
                alive_connections.append(conn)
            else:
                try:
                    conn.close()
                    logger.debug("清理无效连接")
                except:
                    pass
        self.connections = alive_connections

    def _return_connection(self, conn):
        """将连接归还到池中"""
        with self.lock:
            if len(self.connections) < self.pool_size:
                if self._is_connection_alive(conn):
                    self.connections.append(conn)
                else:
                    try:
                        conn.close()
                    except:
                        pass
            else:
                try:
                    conn.close()
                except:
                    pass

    @contextmanager
    def get_connection(self):
        """
        获取数据库连接的上下文管理器

        用法:
            with pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
        """
        conn = None
        try:
            conn = self._get_healthy_connection()
            yield conn
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            raise
        finally:
            if conn:
                self._return_connection(conn)

    def execute_query(self, query: str, params: tuple = None, fetch_one: bool = False,
                     fetch_all: bool = True, commit: bool = False):
        """
        执行查询的便捷方法

        Args:
            query: SQL查询语句
            params: 查询参数
            fetch_one: 是否只获取一条记录
            fetch_all: 是否获取所有记录
            commit: 是否提交事务

        Returns:
            查询结果
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query, params or ())

                if commit:
                    conn.commit()
                    return cursor.lastrowid
                elif fetch_one:
                    return cursor.fetchone()
                elif fetch_all:
                    return cursor.fetchall()
                else:
                    return None
            finally:
                cursor.close()

    def close_all(self):
        """关闭池中所有连接"""
        with self.lock:
            for conn in self.connections:
                try:
                    conn.close()
                except:
                    pass
            self.connections = []
            logger.info("✅ 连接池已关闭")


class RobustConnection:
    """
    增强的数据库连接包装器
    自动处理连接断开和重连
    """

    def __init__(self, db_config: Dict[str, Any]):
        """
        初始化增强连接

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection: Optional[pymysql.Connection] = None
        self._reconnect_attempts = 3
        self._reconnect_delay = 2

    def _ensure_connection(self):
        """确保连接可用"""
        for attempt in range(self._reconnect_attempts):
            try:
                if self.connection is None or not self.connection.open:
                    self.connection = pymysql.connect(
                        **self.db_config,
                        charset='utf8mb4',
                        connect_timeout=10,
                        read_timeout=30,
                        write_timeout=30
                    )
                    logger.debug(f"数据库连接已建立 (尝试 {attempt + 1}/{self._reconnect_attempts})")
                else:
                    # 使用 ping 检查并重连
                    try:
                        self.connection.ping(reconnect=True)
                    except:
                        self.connection = None
                        continue
                return True
            except Exception as e:
                logger.warning(f"⚠️ 连接失败 (尝试 {attempt + 1}/{self._reconnect_attempts}): {e}")
                if attempt < self._reconnect_attempts - 1:
                    time.sleep(self._reconnect_delay)
                self.connection = None

        logger.error("❌ 无法建立数据库连接")
        return False

    def execute(self, query: str, params: tuple = None, commit: bool = False):
        """
        执行SQL语句（自动重连）

        Args:
            query: SQL语句
            params: 参数
            commit: 是否提交

        Returns:
            执行结果
        """
        if not self._ensure_connection():
            raise Exception("无法连接到数据库")

        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, params or ())

            if commit:
                self.connection.commit()
                return cursor.lastrowid
            else:
                return cursor.fetchall()
        except pymysql.OperationalError as e:
            # 连接相关错误，尝试重连后再试
            logger.warning(f"⚠️ 数据库操作错误，尝试重连: {e}")
            self.connection = None
            if self._ensure_connection():
                # 重试一次
                cursor = self.connection.cursor()
                cursor.execute(query, params or ())
                if commit:
                    self.connection.commit()
                    return cursor.lastrowid
                else:
                    return cursor.fetchall()
            raise
        finally:
            if cursor:
                cursor.close()

    def close(self):
        """关闭连接"""
        if self.connection:
            try:
                self.connection.close()
            except:
                pass
            self.connection = None


# 全局连接池实例（懒加载）
_global_pool: Optional[MySQLConnectionPool] = None
_pool_lock = threading.Lock()


def get_global_pool(db_config: Dict[str, Any] = None, pool_size: int = 10) -> MySQLConnectionPool:
    """
    获取全局连接池实例

    Args:
        db_config: 数据库配置（首次调用时需要）
        pool_size: 连接池大小

    Returns:
        全局连接池实例
    """
    global _global_pool

    if _global_pool is None:
        with _pool_lock:
            if _global_pool is None:
                if db_config is None:
                    raise ValueError("首次调用需要提供 db_config")
                _global_pool = MySQLConnectionPool(db_config, pool_size)

    return _global_pool


def close_global_pool():
    """关闭全局连接池"""
    global _global_pool
    if _global_pool:
        _global_pool.close_all()
        _global_pool = None


# 便捷函数
@contextmanager
def get_db_connection(db_config: Dict[str, Any] = None):
    """
    便捷的数据库连接上下文管理器

    用法:
        with get_db_connection(db_config) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
    """
    pool = get_global_pool(db_config)
    with pool.get_connection() as conn:
        yield conn


if __name__ == '__main__':
    # 测试代码
    import os
    from dotenv import load_dotenv

    load_dotenv()

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'test')
    }

    print("测试连接池...")
    pool = MySQLConnectionPool(db_config, pool_size=3)

    # 测试基本查询
    with pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 as test")
        result = cursor.fetchone()
        print(f"查询结果: {result}")

    # 测试便捷方法
    result = pool.execute_query("SELECT DATABASE() as db", fetch_one=True)
    print(f"当前数据库: {result}")

    pool.close_all()
    print("✅ 测试完成")
