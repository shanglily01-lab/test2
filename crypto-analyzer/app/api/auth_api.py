"""
认证API路由
提供用户注册、登录、令牌刷新等接口
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, EmailStr, Field
from loguru import logger

from ..auth.auth_service import get_auth_service
from ..auth.dependencies import get_current_user


router = APIRouter(prefix="/auth", tags=["认证"])


# ==================== 请求/响应模型 ====================

class RegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: str = Field(..., description="邮箱")
    password: str = Field(..., min_length=6, description="密码")


class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")


class RefreshTokenRequest(BaseModel):
    """刷新令牌请求"""
    refresh_token: str = Field(..., description="刷新令牌")


class ChangePasswordRequest(BaseModel):
    """修改密码请求"""
    old_password: str = Field(..., description="原密码")
    new_password: str = Field(..., min_length=6, description="新密码")


class TokenResponse(BaseModel):
    """令牌响应"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # 访问令牌过期秒数


class UserResponse(BaseModel):
    """用户信息响应"""
    id: int
    username: str
    email: str
    role: str


# ==================== API端点 ====================

@router.post("/register", summary="用户注册")
async def register(request: RegisterRequest):
    """
    注册新用户

    - **username**: 用户名 (3-50字符)
    - **email**: 邮箱地址
    - **password**: 密码 (至少6字符)
    """
    auth_service = get_auth_service()
    result = auth_service.register_user(
        username=request.username,
        email=request.email,
        password=request.password
    )

    if not result['success']:
        raise HTTPException(status_code=400, detail=result['error'])

    return {
        "success": True,
        "message": "注册成功",
        "user_id": result['user_id']
    }


@router.post("/login", response_model=dict, summary="用户登录")
async def login(request: LoginRequest, req: Request):
    """
    用户登录

    支持用户名或邮箱登录，返回访问令牌和刷新令牌

    - **username**: 用户名或邮箱
    - **password**: 密码
    """
    auth_service = get_auth_service()

    # 获取客户端信息
    ip_address = req.client.host if req.client else None
    user_agent = req.headers.get('user-agent')

    # 验证用户
    result = auth_service.authenticate_user(
        username=request.username,
        password=request.password,
        ip_address=ip_address,
        user_agent=user_agent
    )

    if not result['success']:
        raise HTTPException(status_code=401, detail=result['error'])

    user = result['user']

    # 生成令牌
    access_token = auth_service.create_access_token(
        user_id=user['id'],
        username=user['username'],
        role=user['role']
    )

    refresh_token, expires_at = auth_service.create_refresh_token(
        user_id=user['id'],
        device_info=user_agent[:255] if user_agent else None,
        ip_address=ip_address
    )

    return {
        "success": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": auth_service.access_token_expire_minutes * 60,
        "user": {
            "id": user['id'],
            "username": user['username'],
            "email": user['email'],
            "role": user['role']
        }
    }


@router.post("/refresh", summary="刷新令牌")
async def refresh_token(request: RefreshTokenRequest, req: Request):
    """
    使用刷新令牌获取新的访问令牌

    - **refresh_token**: 刷新令牌
    """
    auth_service = get_auth_service()

    # 验证刷新令牌
    result = auth_service.verify_refresh_token(request.refresh_token)

    if not result:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")

    # 生成新的访问令牌
    access_token = auth_service.create_access_token(
        user_id=result['user_id'],
        username=result['username'],
        role=result['role']
    )

    return {
        "success": True,
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": auth_service.access_token_expire_minutes * 60
    }


@router.post("/logout", summary="退出登录")
async def logout(request: RefreshTokenRequest):
    """
    退出登录，撤销刷新令牌

    - **refresh_token**: 要撤销的刷新令牌
    """
    auth_service = get_auth_service()
    auth_service.revoke_refresh_token(request.refresh_token)

    return {
        "success": True,
        "message": "已退出登录"
    }


@router.post("/logout-all", summary="退出所有设备")
async def logout_all(current_user: dict = Depends(get_current_user)):
    """
    退出所有设备，撤销该用户的所有刷新令牌

    需要登录
    """
    auth_service = get_auth_service()
    count = auth_service.revoke_all_user_tokens(current_user['user_id'])

    return {
        "success": True,
        "message": f"已退出 {count} 个设备"
    }


@router.get("/me", summary="获取当前用户信息")
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    获取当前登录用户的信息

    需要登录
    """
    auth_service = get_auth_service()
    user = auth_service.get_user_by_id(current_user['user_id'])

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "success": True,
        "user": {
            "id": user['id'],
            "username": user['username'],
            "email": user['email'],
            "role": user['role'],
            "status": user['status'],
            "last_login": user['last_login'].isoformat() if user['last_login'] else None,
            "created_at": user['created_at'].isoformat() if user['created_at'] else None
        }
    }


@router.post("/change-password", summary="修改密码")
async def change_password(
    request: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    修改当前用户的密码

    修改成功后会撤销所有刷新令牌，需要重新登录

    - **old_password**: 原密码
    - **new_password**: 新密码 (至少6字符)
    """
    auth_service = get_auth_service()
    result = auth_service.change_password(
        user_id=current_user['user_id'],
        old_password=request.old_password,
        new_password=request.new_password
    )

    if not result['success']:
        raise HTTPException(status_code=400, detail=result['error'])

    return {
        "success": True,
        "message": "密码修改成功，请重新登录"
    }


@router.get("/verify", summary="验证令牌")
async def verify_token(current_user: dict = Depends(get_current_user)):
    """
    验证当前令牌是否有效

    用于前端检查登录状态
    """
    return {
        "success": True,
        "valid": True,
        "user": current_user
    }
