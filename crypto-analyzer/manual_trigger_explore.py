#!/usr/bin/env python3
"""手动触发 Gemini Explore 一轮 (直接在远程服务器运行)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.gemini_explore_worker import run_explore_round

rid = run_explore_round(triggered_by='manual')
print(f"run_id={rid}")
