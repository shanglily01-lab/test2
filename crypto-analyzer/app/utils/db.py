"""
数据库连接管理模块
统一的数据库连接创建和管理
"""

import pymysql
from typing import Dict, Optional
from loguru import logger
from contextlib import contextmanager


def create_connection(db_config: Dict) -> pymysql.Connection:
    """
    创建数据库连接

    Args:
        db_config: 数据库配置字典

    Returns:
        pymysql连接对象
    """
    return pymysql.connect(
        **db_config,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30
    )


@contextmanager
def get_connection(db_config: Dict):
    """
    获取数据库连接的上下文管理器
    自动处理连接的创建和关闭

    Usage:
        with get_connection(db_config) as conn:
            cursor = conn.cursor()
            cursor.execute(...)

    Args:
        db_config: 数据库配置字典

    Yields:
        pymysql连接对象
    """
    conn = None
    try:
        conn = create_connection(db_config)
        yield conn
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@contextmanager
def get_cursor(db_config: Dict, commit: bool = False):
    """
    获取数据库游标的上下文管理器
    自动处理连接和游标的创建、提交和关闭

    Usage:
        with get_cursor(db_config, commit=True) as cursor:
            cursor.execute(...)

    Args:
        db_config: 数据库配置字典
        commit: 是否在退出时自动提交

    Yields:
        pymysql游标对象
    """
    conn = None
    cursor = None
    try:
        conn = create_connection(db_config)
        cursor = conn.cursor()
        yield cursor
        if commit:
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def execute_with_retry(db_config: Dict, sql: str, params: tuple = None,
                       max_retries: int = 2, commit: bool = False) -> Optional[list]:
    """
    带重试的SQL执行

    Args:
        db_config: 数据库配置
        sql: SQL语句
        params: SQL参数
        max_retries: 最大重试次数
        commit: 是否提交

    Returns:
        查询结果列表，或None（如果是写操作）
    """
    last_error = None

    for attempt in range(max_retries):
        conn = None
        cursor = None
        try:
            conn = create_connection(db_config)
            cursor = conn.cursor()
            cursor.execute(sql, params)

            if commit:
                conn.commit()
                return None
            else:
                return cursor.fetchall()

        except pymysql.err.OperationalError as e:
            last_error = e
            if attempt < max_retries - 1:
                logger.warning(f"数据库操作失败，重试中({attempt+1}/{max_retries}): {e}")
                continue
            else:
                logger.error(f"数据库操作重试失败: {e}")
                raise

        except Exception as e:
            logger.error(f"数据库操作出错: {e}")
            raise

        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    raise last_error
