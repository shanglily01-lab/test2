"""
最小化FastAPI测试 - 排查Windows崩溃问题
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Minimal Test")

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/test")
async def test():
    return {"test": "success"}

if __name__ == "__main__":
    print("启动最小化测试服务器...")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,  # 使用8001端口避免冲突
        reload=False,
        log_level="info"
    )
