#!/usr/bin/env python3
"""
自动修复 cache_update_service.py 中的数据库调用
"""

import re

# 读取文件
with open('app/services/cache_update_service.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 修复1: 替换所有剩余的 get_connection() 为 get_session()
content = content.replace('conn = self.db_service.get_connection()', 'session = self.db_service.get_session()')

# 修复2: 替换 cursor = conn.cursor() 和 cursor = conn.cursor(dictionary=True)
content = content.replace('cursor = conn.cursor(dictionary=True)', '')
content = content.replace('cursor = conn.cursor()', '')

# 修复3: 在所有 _upsert 方法中，添加 text() 包装和参数格式转换
# 查找所有的 sql = """ 并替换为 sql = text("""
def fix_sql_statements(content):
    # 匹配 sql = """...""" 模式
    pattern = r'(\s+)(sql = )(""".*?""")'

    def replace_sql(match):
        indent = match.group(1)
        sql_prefix = match.group(2)
        sql_content = match.group(3)

        # 将 %(name)s 替换为 :name
        sql_content = re.sub(r'%\((\w+)\)s', r':\1', sql_content)

        # 添加 text() 包装
        return f'{indent}{sql_prefix}text({sql_content})'

    return re.sub(pattern, replace_sql, content, flags=re.DOTALL)

content = fix_sql_statements(content)

# 修复4: 替换 cursor.execute 为 session.execute
content = content.replace('cursor.execute(sql, kwargs)', 'session.execute(sql, kwargs)')
content = content.replace('cursor.execute(sql, params)', 'session.execute(sql, params)')

# 修复5: 替换 conn.commit() 为 session.commit()
content = content.replace('conn.commit()', 'session.commit()')

# 修复6: 删除 cursor.close()
content = content.replace('cursor.close()\n', '')
content = content.replace('cursor.close()', '')

# 修复7: 替换 conn.close() 为 session.close()
content = content.replace('conn.close()', 'session.close()')

# 修复8: 修复读取方法中的 fetchone()
content = re.sub(
    r'result = cursor\.fetchone\(\)',
    'result = result_proxy.fetchone() if result_proxy else None',
    content
)

# 修复9: 添加 session = None 和 try/finally 块
# 这个需要更复杂的逻辑，这里只能手动修复剩余的3个方法

# 写回文件
with open('app/services/cache_update_service.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ 批量修复完成！")
print("⚠️ 请手动检查以下方法的 try/finally 块：")
print("  - _upsert_news_sentiment")
print("  - _upsert_funding_rate_stats")
print("  - _upsert_recommendation")
print("  - 所有 _get_cached_* 方法")
