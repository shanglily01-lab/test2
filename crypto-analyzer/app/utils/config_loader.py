"""
配置加载工具
支持从 .env 文件读取敏感信息，覆盖 yaml 配置文件中的值。

注意：本模块使用 dotenv_values() 直接读取 .env 文件，
不依赖 os.environ，避免同服务器多版本部署时环境变量互相污染。
"""

import os
import re
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import dotenv_values
from loguru import logger

# 项目根目录（config_loader.py 位于 app/utils/，上两级即根目录）
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _load_env_dict(env_path: Optional[Path] = None) -> Dict[str, str]:
    """
    从 .env 文件读取键值对，返回字典。
    使用 dotenv_values() 而非 load_dotenv()，不写入 os.environ。
    """
    if env_path is None:
        env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        return dict(dotenv_values(env_path))
    logger.debug(f".env 文件不存在: {env_path}，DB 配置将使用默认值")
    return {}


def get_db_config(env_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    直接从本项目 .env 文件构建 MySQL 连接配置字典。
    不读取 os.environ，彻底避免多版本部署时的环境变量污染。

    Returns:
        {'host': ..., 'port': ..., 'user': ..., 'password': ..., 'database': ...}
    """
    env = _load_env_dict(env_path)
    return {
        'host':     env.get('DB_HOST', 'localhost'),
        'port':     int(env.get('DB_PORT', 3306)),
        'user':     env.get('DB_USER', 'root'),
        'password': env.get('DB_PASSWORD', ''),
        'database': env.get('DB_NAME', 'binance-data'),
    }


def get_env(key: str, default: Any = None, env_dict: Optional[Dict] = None) -> Any:
    """
    从 env_dict（优先）或 os.environ（兜底）获取配置值，支持类型转换。

    Args:
        key: 配置键名
        default: 默认值（同时决定类型转换目标类型）
        env_dict: 优先使用的字典，通常来自 dotenv_values()
    """
    source = env_dict if env_dict is not None else os.environ
    value = source.get(key)
    if value is None:
        return default

    if default is not None:
        if isinstance(default, bool):
            return value.lower() in ('true', '1', 'yes', 'on')
        elif isinstance(default, int):
            try:
                return int(value)
            except ValueError:
                return default
        elif isinstance(default, float):
            try:
                return float(value)
            except ValueError:
                return default

    return value


def substitute_env_vars(config: Dict[str, Any], env_dict: Optional[Dict] = None) -> Dict[str, Any]:
    """
    递归替换配置中的环境变量占位符（${ENV_VAR} 或 ${ENV_VAR:default}）。
    优先从 env_dict 取值，不依赖 os.environ。
    """
    env_pattern = re.compile(r'^\$\{([^}:]+)(?::([^}]*))?\}$')

    def try_convert_type(value: str) -> Any:
        if not value:
            return value
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        if value.lower() in ('true', 'yes', 'on'):
            return True
        if value.lower() in ('false', 'no', 'off'):
            return False
        return value

    source = env_dict if env_dict is not None else os.environ

    def substitute_value(value: Any) -> Any:
        if isinstance(value, str):
            match = env_pattern.match(value)
            if match:
                env_name = match.group(1)
                default = match.group(2) if match.group(2) is not None else ''
                env_value = source.get(env_name, default)
                return try_convert_type(env_value)
            else:
                partial_pattern = re.compile(r'\$\{([^}:]+)(?::([^}]*))?\}')
                def replace_match(m):
                    env_name = m.group(1)
                    default = m.group(2) if m.group(2) is not None else ''
                    return source.get(env_name, default)
                return partial_pattern.sub(replace_match, value)
        elif isinstance(value, dict):
            return {k: substitute_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [substitute_value(item) for item in value]
        else:
            return value

    return substitute_value(config)


def apply_env_overrides(config: Dict[str, Any], env_dict: Optional[Dict] = None) -> Dict[str, Any]:
    """
    用 env_dict（来自 .env 文件）覆盖配置中的敏感项。
    不依赖 os.environ。
    """
    def _get(key, default=None):
        return get_env(key, default, env_dict)

    # 数据库配置
    if 'database' in config and 'mysql' in config['database']:
        mysql_config = config['database']['mysql']
        mysql_config['host']     = _get('DB_HOST',     mysql_config.get('host', 'localhost'))
        mysql_config['port']     = _get('DB_PORT',     mysql_config.get('port', 3306))
        mysql_config['user']     = _get('DB_USER',     mysql_config.get('user', 'root'))
        mysql_config['password'] = _get('DB_PASSWORD', mysql_config.get('password', ''))
        mysql_config['database'] = _get('DB_NAME',     mysql_config.get('database', 'binance-data'))

    # JWT 认证配置
    if 'auth' not in config:
        config['auth'] = {}
    config['auth']['secret_key']                    = _get('JWT_SECRET_KEY',                    config['auth'].get('secret_key', ''))
    config['auth']['algorithm']                     = _get('JWT_ALGORITHM',                     config['auth'].get('algorithm', 'HS256'))
    config['auth']['access_token_expire_minutes']   = _get('JWT_ACCESS_TOKEN_EXPIRE_MINUTES',   config['auth'].get('access_token_expire_minutes', 60))
    config['auth']['refresh_token_expire_days']     = _get('JWT_REFRESH_TOKEN_EXPIRE_DAYS',     config['auth'].get('refresh_token_expire_days', 30))

    # 交易所 API
    if 'exchanges' in config:
        if 'binance' in config['exchanges']:
            config['exchanges']['binance']['api_key']    = _get('BINANCE_API_KEY',    config['exchanges']['binance'].get('api_key', ''))
            config['exchanges']['binance']['api_secret'] = _get('BINANCE_API_SECRET', config['exchanges']['binance'].get('api_secret', ''))
        if 'gate' in config['exchanges']:
            config['exchanges']['gate']['api_key']    = _get('GATE_API_KEY',    config['exchanges']['gate'].get('api_key', ''))
            config['exchanges']['gate']['api_secret'] = _get('GATE_API_SECRET', config['exchanges']['gate'].get('api_secret', ''))

    # 新闻数据源
    if 'news' in config and 'cryptopanic' in config['news']:
        config['news']['cryptopanic']['api_key'] = _get('CRYPTOPANIC_API_KEY', config['news']['cryptopanic'].get('api_key', ''))

    # Reddit
    if 'news' in config and 'reddit' in config['news']:
        config['news']['reddit']['client_id']     = _get('REDDIT_CLIENT_ID',     config['news']['reddit'].get('client_id', ''))
        config['news']['reddit']['client_secret'] = _get('REDDIT_CLIENT_SECRET', config['news']['reddit'].get('client_secret', ''))

    # Twitter
    if 'news' in config and 'twitter' in config['news']:
        config['news']['twitter']['bearer_token'] = _get('TWITTER_BEARER_TOKEN', config['news']['twitter'].get('bearer_token', ''))
        config['news']['twitter']['api_key']      = _get('TWITTER_API_KEY',      config['news']['twitter'].get('api_key', ''))
        config['news']['twitter']['api_secret']   = _get('TWITTER_API_SECRET',   config['news']['twitter'].get('api_secret', ''))

    # Telegram
    if 'notifications' in config and 'telegram' in config['notifications']:
        config['notifications']['telegram']['bot_token'] = _get('TELEGRAM_BOT_TOKEN', config['notifications']['telegram'].get('bot_token', ''))
        config['notifications']['telegram']['chat_id']   = _get('TELEGRAM_CHAT_ID',   config['notifications']['telegram'].get('chat_id', ''))

    # 区块链 API
    if 'smart_money' in config:
        config['smart_money']['etherscan_api_key'] = _get('ETHERSCAN_API_KEY', config['smart_money'].get('etherscan_api_key', ''))
        config['smart_money']['bscscan_api_key']   = _get('BSCSCAN_API_KEY',   config['smart_money'].get('bscscan_api_key', ''))

    # 代理
    http_proxy = _get('HTTP_PROXY', '')
    if http_proxy:
        if 'news' in config and 'reddit' in config['news']:
            config['news']['reddit']['proxy'] = http_proxy
        if 'news' in config and 'twitter' in config['news']:
            config['news']['twitter']['proxy'] = http_proxy
        if 'news' in config and 'coingecko' in config['news']:
            config['news']['coingecko']['proxy'] = http_proxy
        if 'smart_money' in config:
            config['smart_money']['proxy'] = http_proxy

    return config


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    加载完整配置：
    1. 用 dotenv_values() 从 .env 文件读取键值（不写入 os.environ）
    2. 读取 yaml 配置文件
    3. 替换配置中的 ${VAR} 占位符
    4. 用 .env 值覆盖敏感配置项
    """
    env_dict = _load_env_dict()

    if config_path is None:
        config_path = _PROJECT_ROOT / "config.yaml"

    config = {}
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        logger.info(f"配置文件加载成功: {config_path}")
    else:
        logger.warning(f"配置文件不存在: {config_path}")

    config = substitute_env_vars(config, env_dict)
    config = apply_env_overrides(config, env_dict)

    return config


def mask_sensitive_value(value: str, show_chars: int = 4) -> str:
    if not value or len(value) <= show_chars:
        return '*' * len(value) if value else ''
    return value[:show_chars] + '*' * (len(value) - show_chars)


def get_config_summary(config: Dict[str, Any]) -> Dict[str, Any]:
    summary = {}
    if 'database' in config and 'mysql' in config['database']:
        mysql = config['database']['mysql']
        summary['database'] = {
            'host': mysql.get('host'),
            'port': mysql.get('port'),
            'user': mysql.get('user'),
            'password': mask_sensitive_value(mysql.get('password', '')),
            'database': mysql.get('database')
        }
    if 'exchanges' in config:
        summary['exchanges'] = {}
        for exchange, cfg in config['exchanges'].items():
            summary['exchanges'][exchange] = {
                'enabled': cfg.get('enabled'),
                'api_key': mask_sensitive_value(cfg.get('api_key', '')),
                'has_secret': bool(cfg.get('api_secret'))
            }
    if 'auth' in config:
        summary['auth'] = {
            'secret_key': mask_sensitive_value(config['auth'].get('secret_key', '')),
            'algorithm': config['auth'].get('algorithm'),
            'access_token_expire_minutes': config['auth'].get('access_token_expire_minutes'),
            'refresh_token_expire_days': config['auth'].get('refresh_token_expire_days')
        }
    return summary
