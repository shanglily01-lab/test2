"""
配置加载工具
支持从环境变量读取敏感信息，覆盖yaml配置文件中的值
"""

import os
import re
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv
from loguru import logger


def load_env_file(env_path: Optional[Path] = None) -> None:
    """
    加载.env文件中的环境变量

    Args:
        env_path: .env文件路径，默认为项目根目录下的.env
    """
    if env_path is None:
        env_path = Path(__file__).parent.parent.parent / ".env"

    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"✅ 已加载环境变量文件: {env_path}")
    else:
        logger.debug(f"环境变量文件不存在: {env_path}，将使用系统环境变量")


def get_env(key: str, default: Any = None) -> Any:
    """
    获取环境变量，支持类型转换

    Args:
        key: 环境变量名
        default: 默认值

    Returns:
        环境变量值，如果不存在则返回默认值
    """
    value = os.environ.get(key)
    if value is None:
        return default

    # 尝试类型转换
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


def substitute_env_vars(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    递归替换配置中的环境变量占位符
    支持格式: ${ENV_VAR} 或 ${ENV_VAR:default}

    Args:
        config: 配置字典

    Returns:
        替换后的配置字典
    """
    env_pattern = re.compile(r'^\$\{([^}:]+)(?::([^}]*))?\}$')  # 完整匹配

    def try_convert_type(value: str) -> Any:
        """尝试将字符串转换为合适的类型"""
        if not value:
            return value
        # 尝试转换为整数
        try:
            return int(value)
        except ValueError:
            pass
        # 尝试转换为浮点数
        try:
            return float(value)
        except ValueError:
            pass
        # 布尔值
        if value.lower() in ('true', 'yes', 'on'):
            return True
        if value.lower() in ('false', 'no', 'off'):
            return False
        return value

    def substitute_value(value: Any) -> Any:
        if isinstance(value, str):
            match = env_pattern.match(value)
            if match:
                # 完整的环境变量占位符
                env_name = match.group(1)
                default = match.group(2) if match.group(2) is not None else ''
                env_value = os.environ.get(env_name, default)
                return try_convert_type(env_value)
            else:
                # 可能是部分替换（字符串中包含环境变量）
                partial_pattern = re.compile(r'\$\{([^}:]+)(?::([^}]*))?\}')
                def replace_match(m):
                    env_name = m.group(1)
                    default = m.group(2) if m.group(2) is not None else ''
                    return os.environ.get(env_name, default)
                return partial_pattern.sub(replace_match, value)
        elif isinstance(value, dict):
            return {k: substitute_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [substitute_value(item) for item in value]
        else:
            return value

    return substitute_value(config)


def apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    应用环境变量覆盖配置
    环境变量优先级高于yaml配置文件

    Args:
        config: 原始配置字典

    Returns:
        应用环境变量后的配置字典
    """
    # 数据库配置
    if 'database' in config and 'mysql' in config['database']:
        mysql_config = config['database']['mysql']
        mysql_config['host'] = get_env('DB_HOST', mysql_config.get('host', 'localhost'))
        mysql_config['port'] = get_env('DB_PORT', mysql_config.get('port', 3306))
        mysql_config['user'] = get_env('DB_USER', mysql_config.get('user', 'root'))
        mysql_config['password'] = get_env('DB_PASSWORD', mysql_config.get('password', ''))
        mysql_config['database'] = get_env('DB_NAME', mysql_config.get('database', 'binance-data'))

    # JWT认证配置
    if 'auth' not in config:
        config['auth'] = {}
    config['auth']['secret_key'] = get_env('JWT_SECRET_KEY', config['auth'].get('secret_key', ''))
    config['auth']['algorithm'] = get_env('JWT_ALGORITHM', config['auth'].get('algorithm', 'HS256'))
    config['auth']['access_token_expire_minutes'] = get_env(
        'JWT_ACCESS_TOKEN_EXPIRE_MINUTES',
        config['auth'].get('access_token_expire_minutes', 60)
    )
    config['auth']['refresh_token_expire_days'] = get_env(
        'JWT_REFRESH_TOKEN_EXPIRE_DAYS',
        config['auth'].get('refresh_token_expire_days', 30)
    )

    # 交易所API配置
    if 'exchanges' in config:
        if 'binance' in config['exchanges']:
            config['exchanges']['binance']['api_key'] = get_env(
                'BINANCE_API_KEY',
                config['exchanges']['binance'].get('api_key', '')
            )
            config['exchanges']['binance']['api_secret'] = get_env(
                'BINANCE_API_SECRET',
                config['exchanges']['binance'].get('api_secret', '')
            )

        if 'gate' in config['exchanges']:
            config['exchanges']['gate']['api_key'] = get_env(
                'GATE_API_KEY',
                config['exchanges']['gate'].get('api_key', '')
            )
            config['exchanges']['gate']['api_secret'] = get_env(
                'GATE_API_SECRET',
                config['exchanges']['gate'].get('api_secret', '')
            )

    # 新闻数据源
    if 'news' in config and 'cryptopanic' in config['news']:
        config['news']['cryptopanic']['api_key'] = get_env(
            'CRYPTOPANIC_API_KEY',
            config['news']['cryptopanic'].get('api_key', '')
        )

    # Reddit配置
    if 'news' in config and 'reddit' in config['news']:
        config['news']['reddit']['client_id'] = get_env(
            'REDDIT_CLIENT_ID',
            config['news']['reddit'].get('client_id', '')
        )
        config['news']['reddit']['client_secret'] = get_env(
            'REDDIT_CLIENT_SECRET',
            config['news']['reddit'].get('client_secret', '')
        )

    # Twitter配置
    if 'news' in config and 'twitter' in config['news']:
        config['news']['twitter']['bearer_token'] = get_env(
            'TWITTER_BEARER_TOKEN',
            config['news']['twitter'].get('bearer_token', '')
        )
        config['news']['twitter']['api_key'] = get_env(
            'TWITTER_API_KEY',
            config['news']['twitter'].get('api_key', '')
        )
        config['news']['twitter']['api_secret'] = get_env(
            'TWITTER_API_SECRET',
            config['news']['twitter'].get('api_secret', '')
        )

    # Telegram通知配置
    if 'notifications' in config and 'telegram' in config['notifications']:
        config['notifications']['telegram']['bot_token'] = get_env(
            'TELEGRAM_BOT_TOKEN',
            config['notifications']['telegram'].get('bot_token', '')
        )
        config['notifications']['telegram']['chat_id'] = get_env(
            'TELEGRAM_CHAT_ID',
            config['notifications']['telegram'].get('chat_id', '')
        )

    # 区块链API
    if 'smart_money' in config:
        config['smart_money']['etherscan_api_key'] = get_env(
            'ETHERSCAN_API_KEY',
            config['smart_money'].get('etherscan_api_key', '')
        )
        config['smart_money']['bscscan_api_key'] = get_env(
            'BSCSCAN_API_KEY',
            config['smart_money'].get('bscscan_api_key', '')
        )

    # 代理配置
    http_proxy = get_env('HTTP_PROXY', '')
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
    加载完整配置
    1. 先加载.env文件
    2. 读取yaml配置文件
    3. 替换配置中的环境变量占位符
    4. 应用环境变量覆盖

    Args:
        config_path: yaml配置文件路径

    Returns:
        完整配置字典
    """
    # 加载.env文件
    load_env_file()

    # 读取yaml配置
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"

    config = {}
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        logger.info(f"✅ 配置文件加载成功: {config_path}")
    else:
        logger.warning(f"⚠️ 配置文件不存在: {config_path}")

    # 替换环境变量占位符
    config = substitute_env_vars(config)

    # 应用环境变量覆盖
    config = apply_env_overrides(config)

    return config


def mask_sensitive_value(value: str, show_chars: int = 4) -> str:
    """
    掩码敏感值，只显示前几个字符

    Args:
        value: 原始值
        show_chars: 显示的字符数

    Returns:
        掩码后的值
    """
    if not value or len(value) <= show_chars:
        return '*' * len(value) if value else ''
    return value[:show_chars] + '*' * (len(value) - show_chars)


def get_config_summary(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    获取配置摘要（敏感信息已掩码）
    用于日志输出

    Args:
        config: 完整配置

    Returns:
        掩码后的配置摘要
    """
    summary = {}

    # 数据库配置
    if 'database' in config and 'mysql' in config['database']:
        mysql = config['database']['mysql']
        summary['database'] = {
            'host': mysql.get('host'),
            'port': mysql.get('port'),
            'user': mysql.get('user'),
            'password': mask_sensitive_value(mysql.get('password', '')),
            'database': mysql.get('database')
        }

    # 交易所配置
    if 'exchanges' in config:
        summary['exchanges'] = {}
        for exchange, cfg in config['exchanges'].items():
            summary['exchanges'][exchange] = {
                'enabled': cfg.get('enabled'),
                'api_key': mask_sensitive_value(cfg.get('api_key', '')),
                'has_secret': bool(cfg.get('api_secret'))
            }

    # 认证配置
    if 'auth' in config:
        summary['auth'] = {
            'secret_key': mask_sensitive_value(config['auth'].get('secret_key', '')),
            'algorithm': config['auth'].get('algorithm'),
            'access_token_expire_minutes': config['auth'].get('access_token_expire_minutes'),
            'refresh_token_expire_days': config['auth'].get('refresh_token_expire_days')
        }

    return summary
