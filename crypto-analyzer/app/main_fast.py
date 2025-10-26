"""
快速启动版本 - 完全禁用分析模块初始化
只保留 Paper Trading 和基本功能
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from loguru import logger
import yaml

# 全局配置
config = {}

# 全局模块（设置为 None，不初始化）
price_collector = None
news_aggregator = None
technical_analyzer = None
sentiment_analyzer = None
signal_generator = None
enhanced_dashboard = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（快速启动版本）"""
    # 启动时初始化
    logger.info("🚀 启动加密货币交易分析系统（快速模式）...")

    global config

    # 加载配置
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        logger.info("✅ 配置文件加载成功")
    else:
        logger.warning("⚠️  config.yaml 不存在，使用默认配置")
        config = {
            'exchanges': {
                'binance': {'enabled': True}
            },
            'symbols': ['BTC/USDT', 'ETH/USDT']
        }

    # 不初始化任何分析模块，直接完成启动
    logger.info("⚡ 快速启动模式：跳过所有分析模块")
    logger.info("✅ Paper Trading 已就绪")
    logger.info("🎉 系统启动完成！")

    yield

    # 关闭时清理
    logger.info("👋 关闭系统...")


# 创建FastAPI应用
app = FastAPI(
    title="加密货币交易分析系统 - 快速模式",
    description="Paper Trading 专用版本",
    version="1.0.0-fast",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception as e:
    logger.warning(f"静态文件目录挂载失败: {e}")


# ==================== 路由 ====================

@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "加密货币交易分析系统 - 快速模式",
        "status": "running",
        "features": {
            "paper_trading": "enabled",
            "dashboard": "disabled",
            "signals": "disabled"
        }
    }


@app.get("/paper_trading")
def paper_trading_page():
    """
    模拟交易页面（同步函数，避免阻塞）
    """
    trading_path = Path("templates/paper_trading.html")

    if not trading_path.exists():
        # 尝试绝对路径
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        trading_path = Path(base_dir) / "templates" / "paper_trading.html"

    if trading_path.exists():
        return FileResponse(str(trading_path))
    else:
        raise HTTPException(status_code=404, detail=f"Paper trading page not found at {trading_path}")


@app.get("/api/config")
async def get_config_api():
    """获取当前配置"""
    return {
        "symbols": config.get('symbols', []),
        "exchanges": list(config.get('exchanges', {}).keys())
    }


# 挂载 Paper Trading API 路由
from app.api.paper_trading_api import router as paper_trading_router
app.include_router(paper_trading_router)


# ==================== 错误处理 ====================

@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={"error": "Not Found", "path": str(request.url)}
    )


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    logger.error(f"Internal Server Error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error"}
    )


# ==================== 启动服务 ====================

if __name__ == "__main__":
    import uvicorn

    logger.info("启动 FastAPI 服务器（快速模式）...")

    uvicorn.run(
        "app.main_fast:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
