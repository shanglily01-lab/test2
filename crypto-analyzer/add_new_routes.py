#!/usr/bin/env python3
"""
添加新页面路由到 main.py
请先停止后端服务，然后运行此脚本
"""

import os

MAIN_PY_PATH = "app/main.py"

# 要添加的路由代码
NEW_ROUTES = '''

@app.get("/dashboard_new")
async def dashboard_new_page():
    """
    新版仪表盘页面（Gate.io风格）
    """
    dashboard_path = project_root / "templates" / "dashboard_new.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    else:
        raise HTTPException(status_code=404, detail="New dashboard page not found")


@app.get("/contract_trading_new")
async def contract_trading_new_page():
    """
    新版模拟合约交易页面（Gate.io风格）
    """
    contract_trading_path = project_root / "templates" / "contract_trading_new.html"
    if contract_trading_path.exists():
        return FileResponse(str(contract_trading_path))
    else:
        raise HTTPException(status_code=404, detail="New contract trading page not found")
'''

def add_routes():
    # 读取 main.py
    with open(MAIN_PY_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查路由是否已存在
    if '/dashboard_new' in content:
        print("✅ 路由已存在，无需添加")
        return

    # 找到 @app.get("/dashboard") 的位置
    dashboard_route = '@app.get("/dashboard")'
    if dashboard_route not in content:
        print("❌ 未找到 /dashboard 路由")
        return

    # 找到下一个路由的位置
    lines = content.split('\n')
    insert_index = None
    in_dashboard_function = False

    for i, line in enumerate(lines):
        if dashboard_route in line:
            in_dashboard_function = True
        elif in_dashboard_function and line.strip().startswith('@app.get'):
            insert_index = i
            break

    if insert_index is None:
        print("❌ 未找到插入位置")
        return

    # 插入新路由
    new_lines = lines[:insert_index] + NEW_ROUTES.split('\n') + lines[insert_index:]
    new_content = '\n'.join(new_lines)

    # 写回文件
    with open(MAIN_PY_PATH, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("✅ 路由添加成功！")
    print("\n新增路由:")
    print("  - http://localhost:9020/dashboard_new")
    print("  - http://localhost:9020/contract_trading_new")
    print("\n请重启后端服务以使更改生效")

if __name__ == '__main__':
    print("正在添加新路由...")
    add_routes()
