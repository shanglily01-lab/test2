"""动态行情分析 API。15 分钟算一次，只读，不下单。"""
import asyncio

from fastapi import APIRouter

router = APIRouter(prefix="/api/market-pulse", tags=["market-pulse"])


@router.get("")
async def market_pulse():
    from app.services.market_pulse import build_market_pulse
    return await asyncio.to_thread(build_market_pulse)
