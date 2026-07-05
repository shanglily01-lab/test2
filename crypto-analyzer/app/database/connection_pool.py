#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MySQL 连接池管理器
解决长时间运行的服务中连接断开的问题
"""
from app.utils.config_loader import DB_SESSION_INIT_COMMAND, get_db_config
import pymysql
from pymysql.cursors import DictCursor
from typing import Optional, Dict, Any
from loguru import logger
import threading
import time
from contextlib import contextmanager


# 池内连接 idle 超过此值直接丢弃，不做 ping（隔夜死连接 ping 会阻塞至 read_timeout）
POOL_CONN_MAX_IDLE_SEC = 1800  # 30min
# 单次 checkout 最多从池内 pop 并丢弃几条，避免一条请求扫光 20 条死连接
MAX_DISCARD_PER_CHECKOUT = 5


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
        self._slot_sem = threading.BoundedSemaphore(pool_size)
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
            config.setdefault('autocommit', True)  # 改为自动提交，避免死锁
            config.setdefault('connect_timeout', 10)
            config.setdefault('read_timeout', 30)
            config.setdefault('write_timeout', 30)
            config.setdefault('init_command', DB_SESSION_INIT_COMMAND)

            # cursorclass 单独设置（不能用 setdefault，因为可能是 None）
            if 'cursorclass' not in config or config['cursorclass'] is None:
                config['cursorclass'] = DictCursor

            conn = pymysql.connect(**config)
            self._apply_optional_session_guards(conn)
            logger.debug("创建新的数据库连接")
            return conn
        except Exception as e:
            logger.error(f"❌ 创建数据库连接失败: {e}")
            raise

    def _apply_optional_session_guards(self, conn) -> None:
        """Best-effort DB guards; unsupported variables must not break connects."""
        try:
            with conn.cursor() as cur:
                for stmt in (
                    "SET SESSION lock_wait_timeout=5",
                    "SET SESSION wait_timeout=28800",
                ):
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        logger.debug(f"DB session guard skipped ({stmt}): {e}")
        except Exception as e:
            logger.debug(f"DB session guard setup skipped: {e}")

    @staticmethod
    def _raw_close(conn) -> None:
        """关闭底层 TCP。禁止调用被 get_api_connection 劫持的 close()（会误 release 信号量）."""
        if conn is None:
            return
        real_close = getattr(conn, "_pymysql_close", None)
        try:
            if real_close is not None:
                real_close()
            elif not getattr(conn, "_pool_managed", False):
                # 尚未被池包装的原始连接
                pymysql.connections.Connection.close(conn)
        except Exception:
            pass

    @staticmethod
    def _pool_idle_seconds(conn) -> Optional[float]:
        returned_at = getattr(conn, "_pool_returned_at", None)
        if returned_at is None:
            return None
        return time.time() - float(returned_at)

    def _discard_pooled_connection(self, conn, reason: str) -> None:
        logger.debug(f"丢弃池内连接 ({reason})")
        self._raw_close(conn)

    def _get_healthy_connection(self):
        """从池中取连接：pop 持锁；过期连接按 idle 时间丢弃，禁止对隔夜死连接 ping.

        旧逻辑对池内每条死连接 ping(reconnect=False)，read_timeout=30s 时
        一条请求可阻塞数分钟并占满信号量，次日首屏表现为整站卡死。
        """
        discarded = 0
        while discarded < MAX_DISCARD_PER_CHECKOUT:
            conn = None
            with self.lock:
                if self.connections:
                    conn = self.connections.pop(0)
            if conn is None:
                return self._create_connection()

            idle_s = self._pool_idle_seconds(conn)
            if idle_s is not None and idle_s > POOL_CONN_MAX_IDLE_SEC:
                self._discard_pooled_connection(conn, f"idle {idle_s:.0f}s")
                discarded += 1
                continue

            if not getattr(conn, "open", True):
                self._discard_pooled_connection(conn, "socket closed")
                discarded += 1
                continue

            return conn

        if discarded >= MAX_DISCARD_PER_CHECKOUT:
            logger.warning(
                f"连接池: 连续丢弃 {discarded} 条过期/失效连接，新建 TCP"
            )
        return self._create_connection()

    def _return_connection(self, conn):
        """将连接归还到池中。归还时不 ping（避免持锁阻塞）；过期连接 checkout 时按 idle 丢弃."""
        try:
            conn.rollback()
        except Exception:
            self._raw_close(conn)
            return

        try:
            conn._pool_returned_at = time.time()
        except Exception:
            pass

        with self.lock:
            if len(self.connections) < self.pool_size:
                self.connections.append(conn)
                return
        # 池已满：关底层连接，禁止调用劫持后的 close()（会误 release 信号量）
        self._raw_close(conn)

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
            pending = list(self.connections)
            self.connections = []
        for conn in pending:
            self._raw_close(conn)
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
                        write_timeout=30,
                        init_command=DB_SESSION_INIT_COMMAND,
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
_api_pool: Optional[MySQLConnectionPool] = None
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


def _get_api_pool() -> MySQLConnectionPool:
    """FastAPI 专用池：与后台服务隔离，短 read_timeout，避免 ping/慢查占满."""
    global _api_pool
    if _api_pool is None:
        with _pool_lock:
            if _api_pool is None:
                cfg = get_db_config().copy()
                cfg.setdefault("read_timeout", 5)
                cfg.setdefault("write_timeout", 5)
                _api_pool = MySQLConnectionPool(cfg, pool_size=20)
    return _api_pool


def get_api_connection(acquire_timeout: float = 5.0):
    """获取一个由 API 连接池管理的连接. conn.close() 实际归还到池中（非关 TCP）."""
    pool = _get_api_pool()
    if not pool._slot_sem.acquire(timeout=acquire_timeout):
        raise TimeoutError(
            f"MySQL 连接池已满 ({pool.pool_size})，{acquire_timeout:.0f}s 内无可用连接"
        )
    try:
        conn = pool._get_healthy_connection()
    except Exception:
        pool._slot_sem.release()
        raise

    # 只包装一次：保留底层 close，丢弃死连接时必须走 _pymysql_close
    if not getattr(conn, "_pool_managed", False):
        conn._pymysql_close = conn.close
        conn._pool_managed = True

        def _pool_close():
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                pool._return_connection(conn)
            except Exception:
                pass
            try:
                pool._slot_sem.release()
            except Exception:
                pass

        conn.close = _pool_close
    return conn


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
        **get_db_config()
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
