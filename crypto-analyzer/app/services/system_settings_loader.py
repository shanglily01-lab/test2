"""
系统配置加载器
从 data_cache.settings_cache 读取系统配置（本地 60s TTL），兜底直接查 system_settings 表。
"""
from app.utils.config_loader import get_db_config
import pymysql
import time
from loguru import logger
from typing import Dict, Any, Optional

_local_cache: Dict[str, Any] = {}
_local_cache_time: float = 0
_LOCAL_TTL = 60


def _reload_cache():
    """从 data_cache.settings_cache 加载，失败则查主库 system_settings。"""
    global _local_cache, _local_cache_time
    now = time.time()
    if now - _local_cache_time < _LOCAL_TTL and _local_cache:
        return _local_cache

    cfg = get_db_config()

    # 1) 优先 data_cache
    try:
        cache_cfg = {**cfg, "database": "data_cache"}
        conn = pymysql.connect(**cache_cfg, charset='utf8mb4',
                                cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("SELECT setting_key, setting_value FROM settings_cache")
            rows = cur.fetchall()
        conn.close()
        if rows:
            result = {}
            for r in rows:
                key = r['setting_key']
                val = r['setting_value']
                if val.lower() in ('true', 'false'):
                    result[key] = val.lower() == 'true'
                else:
                    result[key] = val
            _local_cache = result
            _local_cache_time = now
            return result
    except Exception as e:
        logger.warning(f"[settings_loader] data_cache 不可用: {e}")

    # 2) 兜底: 直接查主库 system_settings
    try:
        conn = pymysql.connect(**cfg, charset='utf8mb4',
                                cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("SELECT setting_key, setting_value FROM system_settings")
            rows = cur.fetchall()
        conn.close()
        result = {}
        for r in rows:
            key = r['setting_key']
            val = r['setting_value']
            if val.lower() in ('true', 'false'):
                result[key] = val.lower() == 'true'
            else:
                result[key] = val
        _local_cache = result
        _local_cache_time = now
        return result
    except Exception as e:
        logger.warning(f"[settings_loader] system_settings 读取失败: {e}，返回空配置")
        return {}


def get_system_settings() -> Dict[str, Any]:
    """获取全部系统配置（本地 60s TTL 缓存）。"""
    return _reload_cache()


def get_batch_entry_strategy() -> str:
    """获取分批建仓策略。"""
    settings = _reload_cache()
    return settings.get('batch_entry_strategy', 'kline_pullback')


def get_big4_filter_enabled() -> bool:
    """获取 Big4 过滤器状态。"""
    settings = _reload_cache()
    val = settings.get('big4_filter_enabled', True)
    if isinstance(val, str):
        return val.lower() == 'true'
    return bool(val)


def get_setting(key: str, default: Any = None) -> Any:
    """获取单个配置项。"""
    settings = _reload_cache()
    return settings.get(key, default)
