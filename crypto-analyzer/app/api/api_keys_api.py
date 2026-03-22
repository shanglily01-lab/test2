# -*- coding: utf-8 -*-
"""
API密钥管理接口
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from loguru import logger

from app.api.auth_api import get_current_user
from app.services.api_key_service import get_api_key_service

router = APIRouter(prefix="/api/api-keys", tags=["API密钥管理"])


# ==================== 请求模型 ====================

class SaveAPIKeyRequest(BaseModel):
    """保存API密钥请求"""
    exchange: str = Field(default='binance', description='交易所')
    account_name: str = Field(..., min_length=1, max_length=100, description='账户名称')
    api_key: str = Field(..., min_length=10, description='API Key')
    api_secret: str = Field(..., min_length=10, description='API Secret')
    permissions: str = Field(default='spot,futures', description='权限')
    is_testnet: bool = Field(default=False, description='是否测试网')
    max_position_value: float = Field(default=1000.0, ge=0, description='最大持仓价值')
    max_daily_loss: float = Field(default=100.0, ge=0, description='最大日亏损')
    max_leverage: int = Field(default=10, ge=1, le=125, description='最大杠杆')


class DeleteAPIKeyRequest(BaseModel):
    """删除API密钥请求"""
    api_key_id: int = Field(..., description='API密钥ID')


class UpdateRiskRequest(BaseModel):
    """更新风控参数"""
    api_key_id: int
    max_leverage: int = Field(ge=1, le=125)
    max_position_value: float = Field(ge=0)
    max_daily_loss: float = Field(ge=0)


# ==================== 接口 ====================

@router.get("/list")
async def list_api_keys(current_user: dict = Depends(get_current_user)):
    """
    获取当前用户的所有API密钥（不含密钥内容）
    """
    try:
        service = get_api_key_service()
        if not service:
            logger.error("API密钥服务未初始化")
            raise HTTPException(status_code=500, detail="API密钥服务未初始化，请检查服务器日志")

        keys = service.get_user_api_keys(current_user['user_id'])
        return {
            'success': True,
            'api_keys': keys
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取API密钥列表失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save")
async def save_api_key(
    request: SaveAPIKeyRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    保存API密钥（新增或更新）
    """
    try:
        service = get_api_key_service()
        if not service:
            logger.error("API密钥服务未初始化")
            raise HTTPException(status_code=500, detail="API密钥服务未初始化，请检查服务器日志")

        result = service.save_api_key(
            user_id=current_user['user_id'],
            exchange=request.exchange,
            account_name=request.account_name,
            api_key=request.api_key,
            api_secret=request.api_secret,
            permissions=request.permissions,
            is_testnet=request.is_testnet,
            max_position_value=request.max_position_value,
            max_daily_loss=request.max_daily_loss,
            max_leverage=request.max_leverage
        )

        if result['success']:
            return {
                'success': True,
                'message': 'API密钥保存成功',
                'api_key_id': result['api_key_id']
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error', '保存失败'))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存API密钥失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete")
async def delete_api_key(
    request: DeleteAPIKeyRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    删除API密钥
    """
    service = get_api_key_service()
    if not service:
        raise HTTPException(status_code=500, detail="API密钥服务未初始化")

    try:
        result = service.delete_api_key(
            user_id=current_user['user_id'],
            api_key_id=request.api_key_id
        )

        if result['success']:
            return {'success': True, 'message': 'API密钥已删除'}
        else:
            raise HTTPException(status_code=400, detail=result.get('error', '删除失败'))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除API密钥失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify")
async def verify_api_key(
    exchange: str = 'binance',
    current_user: dict = Depends(get_current_user)
):
    """
    验证API密钥是否有效
    """
    service = get_api_key_service()
    if not service:
        raise HTTPException(status_code=500, detail="API密钥服务未初始化")

    try:
        result = service.verify_api_key(
            user_id=current_user['user_id'],
            exchange=exchange
        )

        return result

    except Exception as e:
        logger.error(f"验证API密钥失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class VerifyRawRequest(BaseModel):
    """直接验证原始 API 密钥（用于保存前测试）"""
    exchange: str = Field(default='binance', description='交易所')
    api_key: str = Field(..., min_length=10, description='API Key')
    api_secret: str = Field(..., min_length=10, description='API Secret')


@router.post("/verify-raw")
async def verify_api_key_raw(
    request: VerifyRawRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    验证原始 API Key / Secret 是否有效（不需要先保存，用于填写后即时测试）
    """
    try:
        if request.exchange == 'binance':
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            from app.api.live_trading_api import get_db_config
            temp_engine = BinanceFuturesEngine(
                get_db_config(),
                api_key=request.api_key,
                api_secret=request.api_secret
            )
            balance = temp_engine.get_account_balance()
            if balance and balance.get('success'):
                return {
                    'success': True,
                    'balance': {
                        'balance': float(balance.get('balance', 0)),
                        'available': float(balance.get('available', 0))
                    }
                }
            else:
                return {'success': False, 'error': balance.get('error', '无法获取账户信息，请检查 API 权限')}
        else:
            return {'success': False, 'error': f'暂不支持 {request.exchange}'}
    except Exception as e:
        logger.error(f"验证原始API密钥失败: {e}")
        return {'success': False, 'error': str(e)}


@router.get("/has-key")
async def has_api_key(
    exchange: str = 'binance',
    current_user: dict = Depends(get_current_user)
):
    """
    检查用户是否已配置API密钥
    """
    service = get_api_key_service()
    if not service:
        raise HTTPException(status_code=500, detail="API密钥服务未初始化")

    try:
        api_key = service.get_api_key(current_user['user_id'], exchange)
        return {
            'success': True,
            'has_key': api_key is not None,
            'exchange': exchange
        }
    except Exception as e:
        logger.error(f"检查API密钥失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update-risk")
async def update_risk(request: UpdateRiskRequest, current_user: dict = Depends(get_current_user)):
    """更新指定API Key的风控参数（杠杆/持仓/日亏损限额）"""
    import pymysql, os
    try:
        conn = pymysql.connect(
            host=os.getenv('DB_HOST', 'localhost'), port=int(os.getenv('DB_PORT', 3306)),
            user=os.getenv('DB_USER', 'root'), password=os.getenv('DB_PASSWORD', ''),
            database=os.getenv('DB_NAME', 'binance-data'), cursorclass=pymysql.cursors.DictCursor
        )
        cur = conn.cursor()
        # 只允许更新自己的key（admin可更新任意）
        if current_user.get('role') == 'admin':
            cur.execute("SELECT id FROM user_api_keys WHERE id=%s", (request.api_key_id,))
        else:
            cur.execute("SELECT id FROM user_api_keys WHERE id=%s AND user_id=%s",
                        (request.api_key_id, current_user['user_id']))
        if not cur.fetchone():
            raise HTTPException(status_code=403, detail="无权限或密钥不存在")
        cur.execute("""UPDATE user_api_keys
            SET max_leverage=%s, max_position_value=%s, max_daily_loss=%s, updated_at=NOW()
            WHERE id=%s""",
            (request.max_leverage, request.max_position_value, request.max_daily_loss, request.api_key_id))
        conn.commit(); cur.close(); conn.close()
        return {'success': True, 'message': '风控参数已更新'}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新风控参数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
