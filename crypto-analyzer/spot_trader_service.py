#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易服务启动脚本
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.spot_trader_service import main

if __name__ == "__main__":
    main()
