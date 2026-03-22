"""
系统配置管理API
提供V1/V2策略切换、Big4过滤器等系统配置的读取和更新
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Optional
import pymysql
import hashlib
import os
from loguru import logger

router = APIRouter(prefix="/api/system", tags=["System Settings"])


def get_db_config():
    """获取数据库配置"""
    return {
        'host': os.getenv('DB_HOST', '13.212.252.171'),
        'user': os.getenv('DB_USER', 'admin'),
        'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
        'database': os.getenv('DB_NAME', 'binance-data'),
        'charset': 'utf8mb4'
    }


class SystemSetting(BaseModel):
    """系统配置模型"""
    setting_key: str
    setting_value: str
    description: Optional[str] = None


class BatchEntryStrategyUpdate(BaseModel):
    """分批建仓策略更新"""
    strategy: str  # 'kline_pullback' or 'price_percentile'


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
    coin_futures_enabled: Optional[bool] = None
    live_trading_enabled: Optional[bool] = None


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


@router.put("/batch-entry-strategy")
async def update_batch_entry_strategy(data: BatchEntryStrategyUpdate):
    """
    更新分批建仓策略

    Args:
        data: 策略更新数据

    Returns:
        更新结果
    """
    if data.strategy not in ['kline_pullback', 'price_percentile']:
        raise HTTPException(
            status_code=400,
            detail="策略必须是 'kline_pullback' (V2) 或 'price_percentile' (V1)"
        )

    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE system_settings
            SET setting_value = %s,
                updated_by = 'web_ui',
                updated_at = CURRENT_TIMESTAMP
            WHERE setting_key = 'batch_entry_strategy'
        """, (data.strategy,))

        conn.commit()
        cursor.close()
        conn.close()

        strategy_name = 'V2 K线回调策略' if data.strategy == 'kline_pullback' else 'V1 价格分位数策略'
        logger.info(f"✅ 分批建仓策略已更新为: {strategy_name} ({data.strategy})")

        return {
            'success': True,
            'message': f'策略已更新为 {strategy_name}',
            'data': {
                'strategy': data.strategy,
                'strategy_name': strategy_name,
                'note': '需要重启服务才能生效'
            }
        }

    except Exception as e:
        logger.error(f"更新分批建仓策略失败: {e}")
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
        logger.info(f"✅ Big4过滤器{status_text}")

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


@router.put("/top30-filter")
async def update_top30_filter(data: Big4FilterUpdate):
    """开关 Top 30 交易对过滤器（实盘时启用）"""
    try:
        conn = pymysql.connect(**get_db_config())
        cursor = conn.cursor()
        setting_value = 'true' if data.enabled else 'false'
        cursor.execute("""
            INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
            VALUES ('top30_filter_enabled', %s, 'Top30交易对过滤', 'web_ui', CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE setting_value=%s, updated_by='web_ui', updated_at=CURRENT_TIMESTAMP
        """, (setting_value, setting_value))
        conn.commit()
        cursor.close()
        conn.close()
        status_text = '已启用' if data.enabled else '已禁用'
        logger.info(f"✅ Top30过滤器{status_text}")
        return {'success': True, 'message': f'Top30过滤器{status_text}'}
    except Exception as e:
        logger.error(f"更新Top30过滤器失败: {e}")
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
        logger.info(f"✅ 交易方向已更新: {update_msg}")

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

        logger.info(f"✅ 配置项 {key} 已更新为: {data.setting_value}")

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
            WHERE setting_key IN ('u_futures_trading_enabled', 'coin_futures_trading_enabled', 'live_trading_enabled')
        """)

        settings = cursor.fetchall()
        cursor.close()
        conn.close()

        # 转换为字典格式（键名映射为前端使用的格式）
        key_mapping = {
            'u_futures_trading_enabled': 'usdt_futures_enabled',
            'coin_futures_trading_enabled': 'coin_futures_enabled',
            'live_trading_enabled': 'live_trading_enabled'
        }

        result = {
            'usdt_futures_enabled': True,  # 默认值
            'coin_futures_enabled': True,
            'live_trading_enabled': True
        }

        for setting in settings:
            db_key = setting['setting_key']
            frontend_key = key_mapping.get(db_key, db_key)
            result[frontend_key] = int(float(setting['setting_value'])) == 1

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
            status = '✅ 启动' if data.usdt_futures_enabled else '⏸️ 暂停'
            updates.append(f"U本位合约: {status}")

        if data.coin_futures_enabled is not None:
            value = '1' if data.coin_futures_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('coin_futures_trading_enabled', %s, '币本位合约开仓开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '✅ 启动' if data.coin_futures_enabled else '⏸️ 暂停'
            updates.append(f"币本位合约: {status}")

        if data.live_trading_enabled is not None:
            value = '1' if data.live_trading_enabled else '0'
            cursor.execute("""
                INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
                VALUES ('live_trading_enabled', %s, '实盘交易开关 (1=启用, 0=禁用)', 'web_ui', NOW())
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by = 'web_ui',
                    updated_at = NOW()
            """, (value,))
            status = '✅ 启动' if data.live_trading_enabled else '⏸️ 暂停'
            updates.append(f"实盘合约服务: {status}")

        conn.commit()
        cursor.close()
        conn.close()

        update_msg = ', '.join(updates)
        logger.info(f"✅ 交易服务状态已更新: {update_msg}")

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
        logger.info(f"✅ max_hold_hours 已更新为: {hours}小时")
        return {
            'success': True,
            'message': f'最大持仓时间已更新为 {hours} 小时',
            'data': {'hours': hours, 'note': '下次开仓时生效，无需重启'}
        }
    except Exception as e:
        logger.error(f"更新max_hold_hours失败: {e}")
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
        logger.info("✅ 管理员密码已更新")
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
        resp = JSONResponse({'success': True, 'role': user['role'], 'username': user['username']})
        resp.set_cookie('mobile_session', token, max_age=86400 * 7, httponly=True, samesite='lax')
        return resp
    except Exception as e:
        logger.error(f"mobile登录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
