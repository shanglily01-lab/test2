"""
最小化版本的 main.py - 用于测试 Paper Trading 路由
跳过所有可能阻塞的初始化
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
from loguru import logger
import yaml

# 创建 FastAPI 应用（不使用 lifespan）
app = FastAPI(
    title="加密货币交易分析系统 - 最小版",
    description="Paper Trading 测试版本",
    version="1.0.0-minimal"
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

# 加载配置
config = {}
config_path = Path("config.yaml")
if config_path.exists():
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    logger.info("✅ 配置文件加载成功")


# ==================== Paper Trading 路由 ====================

@app.get("/")
async def root():
    """根路径"""
    return {"message": "Paper Trading API - Minimal Version", "status": "running"}


@app.get("/paper_trading")
def paper_trading_page():
    """
    模拟交易页面（同步函数）
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


# 挂载 Paper Trading API 路由
from app.api.paper_trading_api import router as paper_trading_router
app.include_router(paper_trading_router)


# ==================== 启动服务 ====================

if __name__ == "__main__":
    import uvicorn

    logger.info("启动最小化 FastAPI 服务器（仅 Paper Trading）...")

    uvicorn.run(
        "app.main_minimal:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
