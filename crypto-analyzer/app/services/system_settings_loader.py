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
                                cursorclass=pymysql.cursors.DictCursor)
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
                                cursorclass=pymysql.cursors.DictCursor)
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


def get_smart_exit_enabled() -> bool:
    """智能平仓开关 (system_settings.smart_exit_enabled). 未配置时回退 config.yaml."""
    settings = _reload_cache()
    val = settings.get('smart_exit_enabled')
    if val is not None:
        if isinstance(val, str):
            return val.lower() in ('1', 'true', 'yes')
        return bool(val)
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[2] / 'config.yaml'
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        return bool(cfg.get('signals', {}).get('smart_exit', {}).get('enabled', False))
    except Exception:
        return False


def get_setting(key: str, default: Any = None) -> Any:
    """获取单个配置项。"""
    settings = _reload_cache()
    return settings.get(key, default)


def get_blacklist_level3_enabled() -> bool:
    """黑名单3级禁止开仓 (默认 True)."""
    from app.services.trading_gates import is_blacklist_level3_enforced
    return is_blacklist_level3_enforced()


def get_live_top50_required() -> bool:
    """TOP50 内是否允许开实仓 (默认 True)."""
    from app.services.trading_gates import is_live_top50_required
    return is_live_top50_required()


def get_live_whitelist_enabled() -> bool:
    """白名单是否允许开实仓 (默认 True)."""
    from app.services.trading_gates import is_live_whitelist_enabled
    return is_live_whitelist_enabled()


_DEFAULT_SL_DECIMAL = 0.03
_DEFAULT_TP_DECIMAL = 0.05


def get_sl_tp_decimal() -> tuple[float, float]:
    """从 system_settings 读取止损/止盈比例（小数，0.03=3%）。"""
    try:
        sl = float(get_setting('stop_loss_pct', str(_DEFAULT_SL_DECIMAL)))
        tp = float(get_setting('take_profit_pct', str(_DEFAULT_TP_DECIMAL)))
        return sl, tp
    except Exception as e:
        logger.warning(f"[settings_loader] 读取 SL/TP 失败，使用默认 3%/5%: {e}")
        return _DEFAULT_SL_DECIMAL, _DEFAULT_TP_DECIMAL


def get_sl_tp_pct_points() -> tuple[float, float]:
    """开仓用百分点（3.0=3%），与 paper_limit_entry / futures_trading_engine 一致。"""
    sl, tp = get_sl_tp_decimal()
    return sl * 100.0, tp * 100.0


_DEFAULT_MAX_HOLD_HOURS = 4
_DEFAULT_MAX_POSITIONS = 50
_MIN_HOLD_HOURS = 2
_MAX_HOLD_HOURS = 8


def get_max_hold_hours() -> int:
    """最大持仓时间 + AI 探索/预测调度周期（小时），读 max_hold_hours，范围 2~8。"""
    try:
        hours = int(float(get_setting('max_hold_hours', str(_DEFAULT_MAX_HOLD_HOURS))))
        return max(_MIN_HOLD_HOURS, min(_MAX_HOLD_HOURS, hours))
    except Exception as e:
        logger.warning(f"[settings_loader] 读取 max_hold_hours 失败，使用默认 {_DEFAULT_MAX_HOLD_HOURS}h: {e}")
        return _DEFAULT_MAX_HOLD_HOURS


def get_max_positions() -> int:
    """模拟盘最大持仓数量（account 级总槽位）。"""
    try:
        return max(1, int(float(get_setting('max_positions', str(_DEFAULT_MAX_POSITIONS)))))
    except Exception as e:
        logger.warning(f"[settings_loader] 读取 max_positions 失败，使用默认 {_DEFAULT_MAX_POSITIONS}: {e}")
        return _DEFAULT_MAX_POSITIONS


def get_strategy_open_params() -> dict:
    """Web/AI 状态页展示用：SL/TP/持仓时长/最大持仓数。"""
    sl_d, tp_d = get_sl_tp_decimal()
    return {
        "max_positions": get_max_positions(),
        "hold_hours": get_max_hold_hours(),
        "sl_pct": round(sl_d * 100, 2),
        "tp_pct": round(tp_d * 100, 2),
    }
