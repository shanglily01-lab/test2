"""动态行情实时分析 API。只读，不下单。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/market-pulse", tags=["market-pulse"])


@router.get("")
async def market_pulse():
    from app.services.market_pulse import build_market_pulse
    return build_market_pulse()
