#!/usr/bin/env python3
"""
简单启动脚本 - 直接运行main.py
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 导入并运行main
if __name__ == "__main__":
    from app import main
    import uvicorn
    
    print("=" * 60)
    print("  加密货币智能分析系统 - Web服务器")
    print("=" * 60)
    print()
    print("访问地址:")
    print("  - 仪表盘: http://localhost:8000/dashboard")
    print("  - API文档: http://localhost:8000/docs")
    print("  - 健康检查: http://localhost:8000/health")
    print()
    print("按 Ctrl+C 停止服务器")
    print("=" * 60)
    print()
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
