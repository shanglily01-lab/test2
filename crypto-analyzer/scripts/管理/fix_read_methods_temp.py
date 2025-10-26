#!/usr/bin/env python3
"""
临时修复读取方法 - 让它们简单地返回 None
这样系统会回退到原有的实时计算逻辑
"""

import re

# 读取文件
with open('app/services/cache_update_service.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 修复所有 _get_cached_* 方法
# 将它们简化为直接返回 None

def_patterns = [
    '_get_cached_technical_data',
    '_get_cached_news_data',
    '_get_cached_funding_data',
    '_get_cached_hyperliquid_data',
    '_get_cached_price_stats'
]

for method_name in def_patterns:
    # 查找方法定义
    pattern = rf'(    def {method_name}\([^)]+\)[^:]*:.*?)(    def |\Z)'

    def replace_method(match):
        # 保留方法签名和文档字符串
        method_start = match.group(1)
        next_def = match.group(2)

        # 提取方法签名
        sig_match = re.search(rf'(    def {method_name}\([^)]+\)[^:]*:)\s*("""[^"]*""")?', method_start)
        if sig_match:
            signature = sig_match.group(1)
            docstring = sig_match.group(2) if sig_match.group(2) else ''

            # 创建简化版本
            new_method = f'''{signature}
        {docstring}
        return None  # 临时禁用缓存读取，使用实时计算

    {next_def}'''
            return new_method
        return match.group(0)

    content = re.sub(pattern, replace_method, content, flags=re.DOTALL)

# 写回文件
with open('app/services/cache_update_service.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ 读取方法已临时禁用")
print("   系统将使用实时计算作为后备")
print("   缓存写入功能不受影响")
