#!/usr/bin/env python3
"""
通用存储过程调用脚本 —— 由 main.py 调度器以子进程方式启动，
与 FastAPI 主进程完全隔离，不占用主进程线程池。

用法:
    python scripts/call_proc.py <存储过程名>
"""
import sys
import os

# 确保项目根目录在 sys.path
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

import pymysql
from app.utils.config_loader import load_config

def main():
    if len(sys.argv) < 2:
        print("Usage: call_proc.py <proc_name>", file=sys.stderr)
        sys.exit(1)

    proc_name = sys.argv[1]
    config = load_config()
    db = config['database']['mysql']

    conn = pymysql.connect(
        host=db['host'],
        port=db.get('port', 3306),
        user=db['user'],
        password=db['password'],
        database=db['database'],
        charset='utf8mb4',
        connect_timeout=10,
        read_timeout=300,
        write_timeout=300,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(f"CALL {proc_name}()")
        conn.commit()
        cursor.close()
    finally:
        conn.close()

if __name__ == '__main__':
    main()
