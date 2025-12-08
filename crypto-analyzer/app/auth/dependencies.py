"""
FastAPI 认证依赖项
用于保护API端点，获取当前用户信息
"""

from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .auth_service import get_auth_service


# HTTP Bearer 认证方案
security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    获取当前登录用户 (必须登录)

    Args:
        credentials: HTTP Authorization header中的Bearer token

    Returns:
        用户信息字典 {'user_id': int, 'username': str, 'role': str}

    Raises:
        HTTPException 401: 未提供令牌或令牌无效
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"}
        )

    auth_service = get_auth_service()
    payload = auth_service.verify_access_token(credentials.credentials)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return {
        'user_id': int(payload['sub']),
        'username': payload['username'],
        'role': payload['role']
    }


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[dict]:
    """
    获取当前登录用户 (可选，未登录返回None)

    用于支持匿名访问但登录用户有更多权限的场景

    Returns:
        用户信息字典或None
    """
    if credentials is None:
        return None

    auth_service = get_auth_service()
    payload = auth_service.verify_access_token(credentials.credentials)

    if payload is None:
        return None

    return {
        'user_id': int(payload['sub']),
        'username': payload['username'],
        'role': payload['role']
    }


async def require_admin(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """
    要求管理员权限

    Args:
        current_user: 当前用户信息

    Returns:
        用户信息字典

    Raises:
        HTTPException 403: 非管理员用户
    """
    if current_user['role'] != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user


def check_resource_owner(user_id: int, resource_user_id: int, allow_admin: bool = True):
    """
    检查资源所有权

    Args:
        user_id: 当前用户ID
        resource_user_id: 资源所属用户ID
        allow_admin: 是否允许管理员访问

    Raises:
        HTTPException 403: 无权访问
    """
    # 此函数需要配合 current_user 使用
    # 示例: check_resource_owner(current_user['user_id'], strategy.user_id)
    pass


class UserChecker:
    """
    用户权限检查器

    用法:
        @router.get("/resource/{id}")
        async def get_resource(id: int, user: dict = Depends(UserChecker(min_role='user'))):
            ...
    """

    ROLE_HIERARCHY = {
        'viewer': 0,
        'user': 1,
        'admin': 2
    }

    def __init__(self, min_role: str = 'user'):
        """
        Args:
            min_role: 最低要求的角色等级
        """
        self.min_role = min_role

    async def __call__(
        self,
        credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> dict:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="未提供认证令牌",
                headers={"WWW-Authenticate": "Bearer"}
            )

        auth_service = get_auth_service()
        payload = auth_service.verify_access_token(credentials.credentials)

        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="令牌无效或已过期",
                headers={"WWW-Authenticate": "Bearer"}
            )

        user_role = payload.get('role', 'user')
        user_level = self.ROLE_HIERARCHY.get(user_role, 0)
        required_level = self.ROLE_HIERARCHY.get(self.min_role, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {self.min_role} 或更高权限"
            )

        return {
            'user_id': int(payload['sub']),
            'username': payload['username'],
            'role': payload['role']
        }
