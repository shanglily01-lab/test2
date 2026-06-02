"""
系统配置管理API
提供V1/V2策略切换、Big4过滤器等系统配置的读取和更新
"""
from app.utils.config_loader import get_db_config
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Optional
import pymysql
import hashlib
import os
from loguru import logger

router = APIRouter(prefix="/api/system", tags=["System Settings"])


def _upsert_bool_setting(cursor, key: str, enabled: bool, description: str) -> None:
    value = '1' if enabled else '0'
    cursor.execute(
        """
        INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
        VALUES (%s, %s, %s, 'web_ui', NOW())
        ON DUPLICATE KEY UPDATE
            setting_value = VALUES(setting_value),
            updated_by = 'web_ui',
            updated_at = NOW()
        """,
        (key, value, description),
    )


def _set_open_advisor_pair(cursor, enabled: bool) -> None:
    desc_gemini = 'Gemini 模拟开仓顾问 (1=开仓前审核, reject 不开仓)'
    desc_deepseek = 'DeepSeek 模拟开仓顾问: 1=开仓前审核, 不通过则不开仓'
    _upsert_bool_setting(cursor, 'gemini_open_advisor_enabled', enabled, desc_gemini)
    _upsert_bool_setting(cursor, 'deepseek_open_advisor_enabled', enabled, desc_deepseek)


def _set_position_advisor_pair(cursor, enabled: bool) -> None:
    desc_gemini = (
        'Gemini 模拟持仓顾问 (1=启用). 非 deepseek 仓 ≥30min 每15min hold/observe/sell'
    )
    desc_deepseek = (
        'DeepSeek 模拟持仓顾问: deepseek_* 仓 ≥30min 每15min hold/observe/sell'
    )
    _upsert_bool_setting(cursor, 'gemini_position_advisor_enabled', enabled, desc_gemini)
    _upsert_bool_setting(cursor, 'deepseek_position_advisor_enabled', enabled, desc_deepseek)


class SystemSetting(BaseModel):
    """系统配置模型"""
    setting_key: str
    setting_value: str
    description: Optional[str] = None



class Big4FilterUpdate(BaseModel):
    """Big4过滤器更新"""
    enabled: bool


class TradingDirectionUpdate(BaseModel):
    """交易方向更新"""
    allow_long: Optional[bool] = None
    allow_short: Optional[bool] = None


class TradingServicesUpdate(BaseModel):
    """交易服务状态更新"""
    usdt_futures_enabled: Optional[bool] = None
    live_trading_enabled: Optional[bool] = None
    live_close_enabled: Optional[bool] = None       # 2026-05-22 实盘平仓独立开关
    spot_trading_enabled: Optional[bool] = None      # 2026-05-23 现货交易开关
    spot_close_enabled: Optional[bool] = None         # 2026-05-23 现货自动卖出开关
    predictor_enabled: Optional[bool] = None
    btc_momentum_enabled: Optional[bool] = None
    u_coin_style_enabled: Optional[bool] = None
    signal_confirmation_enabled: Optional[bool] = None
    trend_following_enabled: Optional[bool] = None
    gemini_explore_enabled: Optional[bool] = None   # 2026-05-27 Gemini 探索
    gemini_predict_enabled: Optional[bool] = None   # 2026-05-27 Gemini 预测
    deepseek_explore_enabled: Optional[bool] = None   # DeepSeek 探索
    deepseek_predict_enabled: Optional[bool] = None   # DeepSeek 预测
    gpt_explore_enabled: Optional[bool] = None        # GPT 探索
    s1_early_long_enabled: Optional[bool] = None     # 2026-05-27 S1 早期做多
    s5_large_oversold_enabled: Optional[bool] = None   # 2026-05-27 S5 大币超卖
    s6_vol_spike_enabled: Optional[bool] = None         # 2026-05-27 S6 小币量能异动
    s9_gemini_ai_enabled: Optional[bool] = None      # 2026-05-16 S9 Gemini AI 抄底反转
    gemini_position_advisor_enabled: Optional[bool] = None  # 兼容；同步 DeepSeek
    gemini_open_advisor_enabled: Optional[bool] = None      # 兼容；同步 DeepSeek
    gpt_position_advisor_enabled: Optional[bool] = None
    gpt_open_advisor_enabled: Optional[bool] = None
    open_advisor_enabled: Optional[bool] = None             # Gemini+DeepSeek 开仓顾问
    position_advisor_enabled: Optional[bool] = None         # Gemini+DeepSeek 持仓顾问
    smart_exit_enabled: Optional[bool] = None              # 智能平仓 (趋势反转/移动止盈/最优价等)
    blacklist_level3_enabled: Optional[bool] = None      # 黑名单3级禁止开仓
    live_top50_required: Optional[bool] = None           # TOP50 内可开实仓
    live_whitelist_enabled: Optional[bool] = None        # 白名单可开实仓 (与 TOP50 为或关系)
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None


@router.get("/settings")
async def get_system_settings():
    """
    获取所有系统配置

    Returns:
        所有系统配置的字典
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT setting_key, setting_value, description
            FROM system_settings
            ORDER BY setting_key
        """)

        settings = cursor.fetchall()
        cursor.close()
        conn.close()

        # 转换为字典格式
        result = {}
        for setting in settings:
            result[setting['setting_key']] = {
                'value': setting['setting_value'],
                'description': setting['description']
            }

        return {
            'success': True,
            'data': result
        }

    except Exception as e:
        logger.error(f"获取系统配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/{key}")
async def get_setting(key: str):
    """
    获取单个配置项

    Args:
        key: 配置键名

    Returns:
        配置项详情
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT setting_key, setting_value, description
            FROM system_settings
            WHERE setting_key = %s
        """, (key,))

        setting = cursor.fetchone()
        cursor.close()
        conn.close()

        if not setting:
            raise HTTPException(status_code=404, detail=f"配置项 {key} 不存在")

        return {
            'success': True,
            'data': {
                'key': setting['setting_key'],
                'value': setting['setting_value'],
                'description': setting['description']
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取配置项 {key} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.put("/big4-filter")
async def update_big4_filter(data: Big4FilterUpdate):
    """
    更新Big4过滤器状态

    Args:
        data: Big4过滤器更新数据

    Returns:
        更新结果
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        setting_value = 'true' if data.enabled else 'false'

        cursor.execute("""
            UPDATE system_settings
            SET setting_value = %s,
                updated_by = 'web_ui',
                updated_at = CURRENT_TIMESTAMP
            WHERE setting_key = 'big4_filter_enabled'
        """, (setting_value,))

        conn.commit()
        cursor.close()
        conn.close()

        status_text = '已启用' if data.enabled else '已禁用'
        logger.info(f"[OK] Big4过滤器{status_text}")

        return {
            'success': True,
            'message': f'Big4过滤器{status_text}',
            'data': {
                'enabled': data.enabled,
                'note': '配置将在5分钟内自动生效，无需重启服务'
            }
        }

    except Exception as e:
        logger.error(f"更新Big4过滤器失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/trading-direction")
async def get_trading_direction():
    """
    获取交易方向设置

    Returns:
        交易方向设置
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT setting_key, setting_value, description
            FROM system_settings
            WHERE setting_key IN ('allow_long', 'allow_short')
            ORDER BY setting_key
        """)

        settings = cursor.fetchall()
        cursor.close()
        conn.close()

        # 转换为字典格式
        result = {}
        for setting in settings:
            result[setting['setting_key']] = {
                'value': int(float(setting['setting_value'])) == 1,
                'description': setting['description']
            }

        # 如果没有找到设置，使用默认值
        if 'allow_long' not in result:
            result['allow_long'] = {'value': True, 'description': '是否允许做多'}
        if 'allow_short' not in result:
            result['allow_short'] = {'value': True, 'description': '是否允许做空'}

        return {
            'success': True,
            'data': result
        }

    except Exception as e:
        logger.error(f"获取交易方向设置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trading-direction")
async def update_trading_direction(data: TradingDirectionUpdate):
    """
    更新交易方向设置

    Args:
        data: 交易方向更新数据

    Returns:
        更新结果
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        updates = []
        if data.allow_long is not None:
            value = '1' if data.allow_long else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('allow_long', %s, '是否允许做多 (1=允许, 0=禁止)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"做多: {'允许' if data.allow_long else '禁止'}")

        if data.allow_short is not None:
            value = '1' if data.allow_short else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('allow_short', %s, '是否允许做空 (1=允许, 0=禁止)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"做空: {'允许' if data.allow_short else '禁止'}")

        conn.commit()
        cursor.close()
        conn.close()

        update_msg = ', '.join(updates)
        logger.info(f"[OK] 交易方向已更新: {update_msg}")

        return {
            'success': True,
            'message': f'交易方向已更新',
            'data': {
                'allow_long': data.allow_long,
                'allow_short': data.allow_short,
                'updates': updates,
                'note': '配置实时生效，最长延迟5分钟（缓存刷新）'
            }
        }

    except Exception as e:
        logger.error(f"更新交易方向设置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings/{key}")
async def update_setting(key: str, data: SystemSetting):
    """
    更新单个配置项（通用接口）

    Args:
        key: 配置键名
        data: 配置数据

    Returns:
        更新结果
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO system_settings (setting_key, setting_value, description, updated_by)
            VALUES (%s, %s, %s, 'web_ui')
            ON DUPLICATE KEY UPDATE
                setting_value = VALUES(setting_value),
                description = VALUES(description),
                updated_by = 'web_ui',
                updated_at = CURRENT_TIMESTAMP
        """, (key, data.setting_value, data.description))

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"[OK] 配置项 {key} 已更新为: {data.setting_value}")

        return {
            'success': True,
            'message': f'配置项 {key} 更新成功',
            'data': {
                'key': key,
                'value': data.setting_value
            }
        }

    except Exception as e:
        logger.error(f"更新配置项 {key} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trading-services")
async def get_trading_services():
    """
    获取交易服务状态

    Returns:
        各交易服务的启停状态
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT setting_key, setting_value
            FROM system_settings
            WHERE setting_key IN ('u_futures_trading_enabled',
                                  'live_trading_enabled', 'live_close_enabled',
                                  'spot_trading_enabled', 'spot_close_enabled',
                                  'predictor_enabled',
                                  'btc_momentum_enabled', 'u_coin_style_enabled',
                                  'signal_confirmation_enabled', 'trend_following_enabled',
                                  'gemini_explore_enabled', 'gemini_predict_enabled',
                                  'deepseek_explore_enabled', 'deepseek_predict_enabled',
                                  'gpt_explore_enabled',
                                  's1_early_long_enabled',
                                  's5_large_oversold_enabled', 's6_vol_spike_enabled',
                                  's9_gemini_ai_enabled',
                                  'gemini_position_advisor_enabled',
                                  'gemini_open_advisor_enabled',
                                  'deepseek_position_advisor_enabled',
                                  'deepseek_open_advisor_enabled',
                                  'gpt_position_advisor_enabled',
                                  'gpt_open_advisor_enabled',
                                  'smart_exit_enabled',
                                  'blacklist_level3_enabled',
                                  'live_top50_required',
                                  'live_whitelist_enabled',
                                  'stop_loss_pct', 'take_profit_pct')
        """)

        settings = cursor.fetchall()
        cursor.close()
        conn.close()

        bool_keys = {
            'u_futures_trading_enabled': 'usdt_futures_enabled',
            'live_trading_enabled': 'live_trading_enabled',
            'live_close_enabled': 'live_close_enabled',
            'spot_trading_enabled': 'spot_trading_enabled',
            'spot_close_enabled': 'spot_close_enabled',
            'predictor_enabled': 'predictor_enabled',
            'btc_momentum_enabled': 'btc_momentum_enabled',
            'u_coin_style_enabled': 'u_coin_style_enabled',
            'signal_confirmation_enabled': 'signal_confirmation_enabled',
            'trend_following_enabled': 'trend_following_enabled',
            'gemini_explore_enabled': 'gemini_explore_enabled',
            'gemini_predict_enabled': 'gemini_predict_enabled',
            'deepseek_explore_enabled': 'deepseek_explore_enabled',
            'deepseek_predict_enabled': 'deepseek_predict_enabled',
            'gpt_explore_enabled': 'gpt_explore_enabled',
            's9_gemini_ai_enabled': 's9_gemini_ai_enabled',
            'gemini_position_advisor_enabled': 'gemini_position_advisor_enabled',
            'gemini_open_advisor_enabled': 'gemini_open_advisor_enabled',
            'deepseek_position_advisor_enabled': 'deepseek_position_advisor_enabled',
            'deepseek_open_advisor_enabled': 'deepseek_open_advisor_enabled',
            'gpt_position_advisor_enabled': 'gpt_position_advisor_enabled',
            'gpt_open_advisor_enabled': 'gpt_open_advisor_enabled',
            'smart_exit_enabled': 'smart_exit_enabled',
            'blacklist_level3_enabled': 'blacklist_level3_enabled',
            'live_top50_required': 'live_top50_required',
            'live_whitelist_enabled': 'live_whitelist_enabled',
            's1_early_long_enabled': 's1_early_long_enabled',
            's5_large_oversold_enabled': 's5_large_oversold_enabled',
            's6_vol_spike_enabled': 's6_vol_spike_enabled',
        }

        result = {
            'usdt_futures_enabled': True,
            'live_trading_enabled': True,
            'live_close_enabled': True,
            'spot_trading_enabled': True,
            'spot_close_enabled': True,
            'predictor_enabled': True,
            'btc_momentum_enabled': True,
            'u_coin_style_enabled': False,
            'signal_confirmation_enabled': False,
            'trend_following_enabled': False,
            'gemini_explore_enabled': False,
            'gemini_predict_enabled': True,
            'deepseek_explore_enabled': False,
            'deepseek_predict_enabled': False,
            'gpt_explore_enabled': False,
            's9_gemini_ai_enabled': False,
            'gemini_position_advisor_enabled': True,
            'gemini_open_advisor_enabled': True,
            'deepseek_position_advisor_enabled': True,
            'deepseek_open_advisor_enabled': True,
            'gpt_position_advisor_enabled': True,
            'gpt_open_advisor_enabled': True,
            'smart_exit_enabled': False,
            'blacklist_level3_enabled': True,
            'live_top50_required': True,
            'live_whitelist_enabled': True,
            's1_early_long_enabled': False,
            's5_large_oversold_enabled': False,
            's6_vol_spike_enabled': False,
            'stop_loss_pct': 0.02,
            'take_profit_pct': 0.05,
        }

        for setting in settings:
            db_key = setting['setting_key']
            if db_key in bool_keys:
                result[bool_keys[db_key]] = int(float(setting['setting_value'])) == 1
            elif db_key in ('stop_loss_pct', 'take_profit_pct'):
                result[db_key] = float(setting['setting_value'])

        # DB 无 smart_exit_enabled 行时与 smart_trader 运行时一致 (回退 config.yaml)
        if not any(s['setting_key'] == 'smart_exit_enabled' for s in settings):
            from app.services.system_settings_loader import get_smart_exit_enabled
            result['smart_exit_enabled'] = get_smart_exit_enabled()

        result['open_advisor_enabled'] = (
            result['gemini_open_advisor_enabled']
            and result['deepseek_open_advisor_enabled']
        )
        result['position_advisor_enabled'] = (
            result['gemini_position_advisor_enabled']
            and result['deepseek_position_advisor_enabled']
        )

        return {
            'success': True,
            'data': result
        }

    except Exception as e:
        logger.error(f"获取交易服务状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trading-services")
async def update_trading_services(data: TradingServicesUpdate):
    """
    更新交易服务状态

    Args:
        data: 交易服务状态更新数据

    Returns:
        更新结果
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        updates = []

        if data.usdt_futures_enabled is not None:
            value = '1' if data.usdt_futures_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('u_futures_trading_enabled', %s, 'U本位合约开仓开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.usdt_futures_enabled else '暂停'
            updates.append(f"U本位合约: {status}")

        if data.live_trading_enabled is not None:
            value = '1' if data.live_trading_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('live_trading_enabled', %s, '实盘开仓开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.live_trading_enabled else '暂停'
            updates.append(f"实盘开仓服务: {status}")

        if data.live_close_enabled is not None:
            value = '1' if data.live_close_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('live_close_enabled', %s, '实盘平仓开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.live_close_enabled else '暂停'
            updates.append(f"实盘平仓服务: {status}")

        if data.spot_trading_enabled is not None:
            value = '1' if data.spot_trading_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('spot_trading_enabled', %s, '现货交易开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.spot_trading_enabled else '暂停'
            updates.append(f"现货交易: {status}")

        if data.spot_close_enabled is not None:
            value = '1' if data.spot_close_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('spot_close_enabled', %s, '现货自动卖出开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.spot_close_enabled else '暂停'
            updates.append(f"现货卖出: {status}")

        if data.predictor_enabled is not None:
            value = '1' if data.predictor_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('predictor_enabled', %s, '市场预测器开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.predictor_enabled else '暂停'
            updates.append(f"市场预测器: {status}")

        if data.btc_momentum_enabled is not None:
            value = '1' if data.btc_momentum_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('btc_momentum_enabled', %s, 'BTC动量跟随开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.btc_momentum_enabled else '暂停'
            updates.append(f"BTC动量跟随: {status}")

        if data.u_coin_style_enabled is not None:
            value = '1' if data.u_coin_style_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('u_coin_style_enabled', %s, 'U本位破位策略开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '启动' if data.u_coin_style_enabled else '暂停'
            updates.append(f"U本位破位策略: {status}")

        if data.signal_confirmation_enabled is not None:
            value = '1' if data.signal_confirmation_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('signal_confirmation_enabled', %s, '信号确认模式开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"信号确认: {'启用' if data.signal_confirmation_enabled else '禁用'}")

        if data.trend_following_enabled is not None:
            value = '1' if data.trend_following_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('trend_following_enabled', %s, '趋势跟随模式开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"趋势跟随: {'启用' if data.trend_following_enabled else '禁用'}")

        if data.s1_early_long_enabled is not None:
            value = '1' if data.s1_early_long_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('s1_early_long_enabled', %s,
                        'S1 早期做多开关 (1=启用, 0=禁用)',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"S1早期做多: {'启用' if data.s1_early_long_enabled else '禁用'}")

        if data.s5_large_oversold_enabled is not None:
            value = '1' if data.s5_large_oversold_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('s5_large_oversold_enabled', %s,
                        'S5 大币4H超卖反弹做多开关 (1=启用, 0=禁用)',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"S5大币超卖: {'启用' if data.s5_large_oversold_enabled else '禁用'}")

        if data.s6_vol_spike_enabled is not None:
            value = '1' if data.s6_vol_spike_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('s6_vol_spike_enabled', %s,
                        'S6 小币量能异动做多开关 (1=启用, 0=禁用)',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"S6量能异动: {'启用' if data.s6_vol_spike_enabled else '禁用'}")

        if data.gemini_explore_enabled is not None:
            value = '1' if data.gemini_explore_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('gemini_explore_enabled', %s, 'Gemini 探索开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"Gemini探索: {'启用' if data.gemini_explore_enabled else '禁用'}")

        if data.gemini_predict_enabled is not None:
            value = '1' if data.gemini_predict_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('gemini_predict_enabled', %s, 'Gemini 预测开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"Gemini预测: {'启用' if data.gemini_predict_enabled else '禁用'}")

        if data.deepseek_explore_enabled is not None:
            value = '1' if data.deepseek_explore_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('deepseek_explore_enabled', %s, 'DeepSeek 探索开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"DeepSeek探索: {'启用' if data.deepseek_explore_enabled else '禁用'}")

        if data.deepseek_predict_enabled is not None:
            value = '1' if data.deepseek_predict_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('deepseek_predict_enabled', %s, 'DeepSeek 预测开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"DeepSeek预测: {'启用' if data.deepseek_predict_enabled else '禁用'}")

        if data.gpt_explore_enabled is not None:
            value = '1' if data.gpt_explore_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('gpt_explore_enabled', %s, 'GPT 探索开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"GPT探索: {'启用' if data.gpt_explore_enabled else '禁用'}")

        if data.gpt_position_advisor_enabled is not None:
            value = '1' if data.gpt_position_advisor_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('gpt_position_advisor_enabled', %s, 'GPT 模拟持仓顾问: gpt_* 仓 ≥30min 每15min hold/observe/sell', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"GPT持仓顾问: {'启用' if data.gpt_position_advisor_enabled else '禁用'}")

        if data.gpt_open_advisor_enabled is not None:
            value = '1' if data.gpt_open_advisor_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('gpt_open_advisor_enabled', %s, 'GPT 模拟开仓顾问: 1=开仓前审核, 不通过则不开仓', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"GPT开仓顾问: {'启用' if data.gpt_open_advisor_enabled else '禁用'}")

        if data.s9_gemini_ai_enabled is not None:
            value = '1' if data.s9_gemini_ai_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('s9_gemini_ai_enabled', %s,
                        'S9 Gemini AI 抄底反转 LONG 开关 (1=启用, 0=禁用). 默认 0, Gemini API 收费',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"S9_Gemini: {'启用' if data.s9_gemini_ai_enabled else '禁用'}")

        if data.position_advisor_enabled is not None:
            _set_position_advisor_pair(cursor, data.position_advisor_enabled)
            updates.append(
                f"持仓顾问: {'启用' if data.position_advisor_enabled else '禁用'}"
            )
        elif data.gemini_position_advisor_enabled is not None:
            _set_position_advisor_pair(cursor, data.gemini_position_advisor_enabled)
            updates.append(
                f"持仓顾问: {'启用' if data.gemini_position_advisor_enabled else '禁用'}"
            )

        if data.open_advisor_enabled is not None:
            _set_open_advisor_pair(cursor, data.open_advisor_enabled)
            updates.append(
                f"开仓顾问: {'启用' if data.open_advisor_enabled else '禁用'}"
            )
        elif data.gemini_open_advisor_enabled is not None:
            _set_open_advisor_pair(cursor, data.gemini_open_advisor_enabled)
            updates.append(
                f"开仓顾问: {'启用' if data.gemini_open_advisor_enabled else '禁用'}"
            )

        if data.smart_exit_enabled is not None:
            value = '1' if data.smart_exit_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('smart_exit_enabled', %s,
                        '智能平仓开关 (1=启用, 0=禁用). 关闭时仅保留到期/SL/TP 平仓',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"智能平仓: {'启用' if data.smart_exit_enabled else '禁用'}")

        if data.blacklist_level3_enabled is not None:
            value = '1' if data.blacklist_level3_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('blacklist_level3_enabled', %s,
                        '黑名单3级禁止开仓 (1=启用, 0=禁用). 关闭后 L3 交易对可开仓',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"黑名单3级: {'启用' if data.blacklist_level3_enabled else '禁用'}")

        if data.live_top50_required is not None:
            value = '1' if data.live_top50_required else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('live_top50_required', %s,
                        '实盘TOP50闸门 (1=TOP50内可开实仓, 0=关闭). 与白名单为或关系, 两者都关则不开实盘',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"实盘TOP50: {'启用' if data.live_top50_required else '禁用'}")

        if data.live_whitelist_enabled is not None:
            value = '1' if data.live_whitelist_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('live_whitelist_enabled', %s,
                        '实盘白名单闸门 (1=rating_level=0可开实仓, 0=关闭). 与TOP50为或关系, 两者都关则不开实盘',
                        'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            updates.append(f"实盘白名单: {'启用' if data.live_whitelist_enabled else '禁用'}")

        if data.stop_loss_pct is not None:
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('stop_loss_pct', %s, '止损比例（小数，如0.02表示2%%）', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (str(data.stop_loss_pct),))
            updates.append(f"止损: {data.stop_loss_pct*100:.1f}%")

        if data.take_profit_pct is not None:
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('take_profit_pct', %s, '止盈比例（小数，如0.05表示5%%）', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (str(data.take_profit_pct),))
            updates.append(f"止盈: {data.take_profit_pct*100:.1f}%")

        conn.commit()
        cursor.close()
        conn.close()

        # 立即同步 data_cache，避免策略侧最多60秒的延迟
        try:
            from app.services.data_cache_service import sync_settings_cache
            sync_settings_cache()
        except Exception as e:
            logger.warning(f"[settings] data_cache 同步失败(不影响主库): {e}")

        update_msg = ', '.join(updates)
        logger.info(f"[OK] 交易服务状态已更新: {update_msg}")

        return {
            'success': True,
            'message': '交易服务状态已更新',
            'data': {
                'updates': updates,
                'note': '配置实时生效'
            }
        }

    except Exception as e:
        logger.error(f"更新交易服务状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class MaxHoldHoursUpdate(BaseModel):
    """最大持仓时间更新"""
    hours: int  # 3~8


class S1S9MaxHoldHoursUpdate(BaseModel):
    """S1~S9 最大持仓时间更新"""
    hours: int  # 3~12


@router.get("/max-hold-hours")
async def get_max_hold_hours():
    """获取最大持仓时间（小时）"""
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
        cursor = conn.cursor()
        cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'max_hold_hours'")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        hours = int(row['setting_value']) if row else 3
        return {'success': True, 'data': {'hours': hours}}
    except Exception as e:
        logger.error(f"获取max_hold_hours失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/max-hold-hours")
async def update_max_hold_hours(data: MaxHoldHoursUpdate):
    """更新最大持仓时间（3~8小时，实时生效，下次开仓时读取）"""
    hours = max(3, min(8, data.hours))
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
            VALUES ('max_hold_hours', %s, '最大持仓时间（小时），范围3~8', 'web_ui', NOW())
            ON DUPLICATE KEY UPDATE setting_value = %s, updated_by = 'web_ui', updated_at = NOW()
        """, (str(hours), str(hours)))
        cursor.close()
        conn.close()
        logger.info(f"[OK] max_hold_hours 已更新为: {hours}小时")
        return {
            'success': True,
            'message': f'最大持仓时间已更新为 {hours} 小时',
            'data': {'hours': hours, 'note': '下次开仓时生效，无需重启'}
        }
    except Exception as e:
        logger.error(f"更新max_hold_hours失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/s1s9-max-hold-hours")
async def get_s1s9_max_hold_hours():
    """获取 S1~S9 最大持仓时间（小时）"""
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
        cursor = conn.cursor()
        cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key = 's1_s9_max_hold_hours'")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        hours = int(row['setting_value']) if row else 6
        return {'success': True, 'data': {'hours': hours}}
    except Exception as e:
        logger.error(f"获取s1_s9_max_hold_hours失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/s1s9-max-hold-hours")
async def update_s1s9_max_hold_hours(data: S1S9MaxHoldHoursUpdate):
    """更新 S1~S9 最大持仓时间（3~12小时，实时生效）"""
    hours = max(3, min(12, data.hours))
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
            VALUES ('s1_s9_max_hold_hours', %s, 'S1~S9策略持仓时间（小时），范围3~12', 'web_ui', NOW())
            ON DUPLICATE KEY UPDATE setting_value = %s, updated_by = 'web_ui', updated_at = NOW()
        """, (str(hours), str(hours)))
        cursor.close()
        conn.close()
        logger.info(f"[OK] s1_s9_max_hold_hours 已更新为: {hours}小时")
        return {
            'success': True,
            'message': f'S1~S9 最大持仓时间已更新为 {hours} 小时',
            'data': {'hours': hours, 'note': '下次开仓时生效，无需重启'}
        }
    except Exception as e:
        logger.error(f"更新s1_s9_max_hold_hours失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Big4 trend status ────────────────────────────────────────────────────────

@router.get("/big4-trend")
async def get_big4_trend():
    """获取最新 Big4 趋势状态（读取 big4_trend_history 最新一行）"""
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT overall_signal, signal_strength, bullish_count, bearish_count,
                   btc_signal, btc_strength,
                   eth_signal, eth_strength,
                   bnb_signal, bnb_strength,
                   sol_signal, sol_strength,
                   created_at
            FROM big4_trend_history
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return {'success': True, 'data': None}

        def norm_sig(s):
            if not s:
                return 'NEUTRAL'
            s = s.upper()
            if 'BULL' in s:
                return 'BULLISH'
            if 'BEAR' in s:
                return 'BEARISH'
            return 'NEUTRAL'

        weight_map = {'btc': 0.50, 'eth': 0.30, 'bnb': 0.10, 'sol': 0.10}
        data = {
            'overall_signal': norm_sig(row['overall_signal']),
            'signal_strength': float(row['signal_strength'] or 0),
            'bullish_count': int(row['bullish_count'] or 0),
            'bearish_count': int(row['bearish_count'] or 0),
            'updated_at': row['created_at'].isoformat() if row['created_at'] else None,
            'btc': {'signal': norm_sig(row['btc_signal']), 'score': float(row['btc_strength'] or 0), 'weight': weight_map['btc']},
            'eth': {'signal': norm_sig(row['eth_signal']), 'score': float(row['eth_strength'] or 0), 'weight': weight_map['eth']},
            'bnb': {'signal': norm_sig(row['bnb_signal']), 'score': float(row['bnb_strength'] or 0), 'weight': weight_map['bnb']},
            'sol': {'signal': norm_sig(row['sol_signal']), 'score': float(row['sol_strength'] or 0), 'weight': weight_map['sol']},
        }
        return {'success': True, 'data': data}
    except Exception as e:
        logger.error(f"获取Big4趋势失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Admin password ──────────────────────────────────────────────────────────

def _hash_pwd(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


@router.post("/admin/login")
async def admin_login(request: Request):
    """验证密码并写入 admin_token cookie（供 /m/settings 服务端校验）"""
    data = await request.json()
    password = data.get('password', '')
    try:
        conn = pymysql.connect(**get_db_config())
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key='admin_password'")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return JSONResponse({'success': False, 'needs_setup': True})
        if not password:
            return JSONResponse({'success': False, 'error': '密码不能为空'})
        if _hash_pwd(password) != row['setting_value']:
            return JSONResponse({'success': False, 'error': '密码错误'})
        # 生成 token = sha256(stored_hash)，服务端验证时重新计算
        token = hashlib.sha256(row['setting_value'].encode()).hexdigest()
        resp = JSONResponse({'success': True})
        resp.set_cookie('admin_token', token, max_age=86400 * 7, httponly=True, samesite='lax')
        return resp
    except Exception as e:
        logger.error(f"admin登录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/verify-password")
async def verify_admin_password(request: Request):
    """验证管理员密码"""
    data = await request.json()
    password = data.get('password', '')
    try:
        conn = pymysql.connect(**get_db_config())
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key='admin_password'"
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return {'success': False, 'needs_setup': True}
        if not password:
            return {'success': False, 'error': '密码不能为空'}
        if _hash_pwd(password) == row['setting_value']:
            return {'success': True}
        return {'success': False, 'error': '密码错误'}
    except Exception as e:
        logger.error(f"验证管理员密码失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/set-password")
async def set_admin_password(request: Request):
    """设置或修改管理员密码"""
    data = await request.json()
    new_password = data.get('new_password', '')
    current_password = data.get('current_password', '')
    if len(new_password) < 4:
        return {'success': False, 'error': '密码至少4位'}
    try:
        conn = pymysql.connect(**get_db_config())
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key='admin_password'"
        )
        row = cursor.fetchone()
        if row:
            # 已有密码，必须验证当前密码
            if not current_password:
                cursor.close(); conn.close()
                return {'success': False, 'error': '请输入当前密码'}
            if _hash_pwd(current_password) != row['setting_value']:
                cursor.close(); conn.close()
                return {'success': False, 'error': '当前密码错误'}
        new_hash = _hash_pwd(new_password)
        cursor.execute("""
            INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
            VALUES ('admin_password', %s, '管理员密码(SHA256)', 'web_ui', NOW())
            ON DUPLICATE KEY UPDATE setting_value=%s, updated_by='web_ui', updated_at=NOW()
        """, (new_hash, new_hash))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info("[OK] 管理员密码已更新")
        return {'success': True, 'message': '密码已更新'}
    except Exception as e:
        logger.error(f"设置管理员密码失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mobile/login")
async def mobile_login(request: Request):
    """手机端统一登录：使用 users 表验证，写入 mobile_session cookie"""
    import bcrypt, hmac as _hmac, os as _os
    data = await request.json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return JSONResponse({'success': False, 'error': '用户名和密码不能为空'})
    try:
        conn = pymysql.connect(**get_db_config())
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT id, username, password_hash, role, status FROM users WHERE username=%s OR email=%s",
            (username, username)
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if not user or user['status'] != 'active':
            return JSONResponse({'success': False, 'error': '用户不存在或已禁用'})
        if not bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
            return JSONResponse({'success': False, 'error': '密码错误'})
        # 生成签名 token: hmac(secret, "user_id:role")
        secret = _os.getenv('SECRET_KEY', 'mobile_secret_2026').encode()
        payload = f"{user['id']}:{user['role']}"
        sig = _hmac.new(secret, payload.encode(), 'sha256').hexdigest()
        token = f"{payload}:{sig}"
        # 同时生成 JWT access_token 供实盘API使用
        from app.auth.auth_service import get_auth_service
        auth_service = get_auth_service()
        access_token = auth_service.create_access_token(
            user_id=user['id'], username=user['username'], role=user['role']
        )
        resp = JSONResponse({
            'success': True, 'role': user['role'],
            'username': user['username'], 'access_token': access_token
        })
        resp.set_cookie('mobile_session', token, max_age=86400 * 7, httponly=True, samesite='lax')
        return resp
    except Exception as e:
        logger.error(f"mobile登录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
