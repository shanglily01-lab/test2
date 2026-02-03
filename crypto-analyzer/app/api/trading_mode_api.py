"""
交易模式API
提供模式切换、配置和查询接口
"""

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.strategies.mode_switcher import TradingModeSwitcher
from app.strategies.range_market_detector import RangeMarketDetector
import pymysql

router = APIRouter(prefix="/api/trading-mode", tags=["trading-mode"])

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}


# ============================================================================
# Pydantic模型
# ============================================================================

class ModeSwitchRequest(BaseModel):
    """模式切换请求"""
    account_id: int
    trading_type: str  # usdt_futures / coin_futures
    new_mode: str  # trend / range / auto
    trigger: str = 'manual'  # manual / auto / schedule
    reason: Optional[str] = ''
    switched_by: str = 'web_user'


class ModeParametersUpdate(BaseModel):
    """模式参数更新"""
    account_id: int
    trading_type: str
    range_min_score: Optional[int] = None
    range_position_size: Optional[float] = None
    range_max_positions: Optional[int] = None
    range_take_profit: Optional[float] = None
    range_stop_loss: Optional[float] = None
    range_max_hold_hours: Optional[int] = None
    auto_switch_enabled: Optional[bool] = None
    switch_cooldown_minutes: Optional[int] = None


# ============================================================================
# API路由
# ============================================================================

@router.get("/status/{account_id}/{trading_type}")
async def get_mode_status(account_id: int, trading_type: str):
    """
    获取当前交易模式状态

    Args:
        account_id: 账户ID (2=U本位, 3=币本位)
        trading_type: 交易类型 (usdt_futures/coin_futures)

    Returns:
        模式配置信息
    """
    try:
        switcher = TradingModeSwitcher(DB_CONFIG)
        config = switcher.get_current_mode(account_id, trading_type)

        if not config:
            raise HTTPException(status_code=404, detail="未找到模式配置")

        return {
            "account_id": config['account_id'],
            "trading_type": config['trading_type'],
            "mode_type": config['mode_type'],
            "is_active": bool(config['is_active']),
            "range_config": {
                "min_score": config['range_min_score'],
                "position_size": float(config['range_position_size']),
                "max_positions": config['range_max_positions'],
                "take_profit": float(config['range_take_profit']),
                "stop_loss": float(config['range_stop_loss']),
                "max_hold_hours": config['range_max_hold_hours']
            },
            "auto_switch": {
                "enabled": bool(config['auto_switch_enabled']),
                "cooldown_minutes": config['switch_cooldown_minutes']
            },
            "last_switch_time": config['last_switch_time'].isoformat() if config['last_switch_time'] else None,
            "updated_by": config['updated_by'],
            "updated_at": config['updated_at'].isoformat() if config['updated_at'] else None
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模式状态失败: {str(e)}")


@router.post("/switch")
async def switch_mode(request: ModeSwitchRequest):
    """
    切换交易模式

    Body:
        {
            "account_id": 2,
            "trading_type": "usdt_futures",
            "new_mode": "range",
            "trigger": "manual",
            "reason": "手动切换到震荡模式",
            "switched_by": "web_user"
        }

    Returns:
        切换结果
    """
    try:
        # 验证参数
        if request.new_mode not in ['trend', 'range', 'auto']:
            raise HTTPException(status_code=400, detail="无效的模式类型")

        if request.trading_type not in ['usdt_futures', 'coin_futures']:
            raise HTTPException(status_code=400, detail="无效的交易类型")

        # 执行切换
        switcher = TradingModeSwitcher(DB_CONFIG)

        # 获取Big4信号(用于记录)
        big4_signal, big4_strength = get_latest_big4_signal()

        success = switcher.switch_mode(
            account_id=request.account_id,
            trading_type=request.trading_type,
            new_mode=request.new_mode,
            trigger=request.trigger,
            reason=request.reason,
            big4_signal=big4_signal,
            big4_strength=big4_strength,
            switched_by=request.switched_by
        )

        if not success:
            raise HTTPException(status_code=400, detail="模式切换失败")

        # 返回更新后的状态
        config = switcher.get_current_mode(request.account_id, request.trading_type)

        return {
            "success": True,
            "message": f"成功切换到{request.new_mode}模式",
            "mode_type": config['mode_type'],
            "switched_at": config['updated_at'].isoformat() if config['updated_at'] else None
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"切换模式失败: {str(e)}")


@router.post("/update-parameters")
async def update_parameters(request: ModeParametersUpdate):
    """
    更新模式参数

    Body:
        {
            "account_id": 2,
            "trading_type": "usdt_futures",
            "range_min_score": 55,
            "range_position_size": 3.5,
            "range_take_profit": 3.0,
            "auto_switch_enabled": true
        }

    Returns:
        更新结果
    """
    try:
        switcher = TradingModeSwitcher(DB_CONFIG)

        # 提取非None的参数
        parameters = {
            k: v for k, v in request.dict().items()
            if v is not None and k not in ['account_id', 'trading_type']
        }

        if not parameters:
            raise HTTPException(status_code=400, detail="未提供任何参数")

        success = switcher.update_mode_parameters(
            account_id=request.account_id,
            trading_type=request.trading_type,
            parameters=parameters
        )

        if not success:
            raise HTTPException(status_code=400, detail="参数更新失败")

        return {
            "success": True,
            "message": "参数更新成功",
            "updated_parameters": parameters
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新参数失败: {str(e)}")


@router.get("/statistics/{account_id}/{trading_type}")
async def get_mode_statistics(account_id: int, trading_type: str, days: int = 7):
    """
    获取模式切换统计

    Args:
        account_id: 账户ID
        trading_type: 交易类型
        days: 统计天数(默认7天)

    Returns:
        统计信息
    """
    try:
        switcher = TradingModeSwitcher(DB_CONFIG)
        stats = switcher.get_mode_statistics(account_id, trading_type, days)

        return {
            "account_id": account_id,
            "trading_type": trading_type,
            "period_days": days,
            "statistics": stats
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")


@router.get("/zones/{symbol}")
async def get_active_zone(symbol: str):
    """
    获取币种的当前震荡区间

    Args:
        symbol: 交易对

    Returns:
        震荡区间信息
    """
    try:
        detector = RangeMarketDetector(DB_CONFIG)
        zone = detector.get_active_zone(symbol)

        if not zone:
            return {
                "symbol": symbol,
                "has_zone": False,
                "message": "当前无有效震荡区间"
            }

        return {
            "symbol": symbol,
            "has_zone": True,
            "zone": {
                "support_price": float(zone['support_price']),
                "resistance_price": float(zone['resistance_price']),
                "range_pct": float(zone['range_pct']),
                "touch_count": zone['touch_count'],
                "confidence_score": float(zone['confidence_score']),
                "detected_at": zone['detected_at'].isoformat() if zone['detected_at'] else None,
                "expires_at": zone['expires_at'].isoformat() if zone['expires_at'] else None
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取区间失败: {str(e)}")


@router.get("/market-status")
async def get_market_status():
    """
    获取当前市场状态(Big4信号)

    Returns:
        市场状态信息
    """
    try:
        big4_signal, big4_strength = get_latest_big4_signal()

        detector = RangeMarketDetector(DB_CONFIG)
        is_ranging = detector.is_ranging_market(big4_signal, big4_strength)

        return {
            "big4_signal": big4_signal,
            "big4_strength": big4_strength,
            "is_ranging_market": is_ranging,
            "recommended_mode": "range" if is_ranging else "trend"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取市场状态失败: {str(e)}")


# ============================================================================
# 辅助函数
# ============================================================================

def get_latest_big4_signal() -> tuple:
    """
    获取最新的Big4信号

    Returns:
        (signal, strength) 元组
    """
    try:
        conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT overall_signal, signal_strength
            FROM big4_trend_history
            ORDER BY created_at DESC
            LIMIT 1
        """)

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if result:
            return result['overall_signal'], float(result['signal_strength'])
        else:
            return 'NEUTRAL', 0.0

    except Exception as e:
        return 'NEUTRAL', 0.0
