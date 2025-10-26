"""
修复 cache_update_service.py 中的数据库连接调用
将 get_connection() 改为 get_session() 并使用 SQLAlchemy text()
"""

import re

def fix_file():
    file_path = "app/services/cache_update_service.py"

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 模式1: 替换 conn = self.db_service.get_connection() 为 session = self.db_service.get_session()
    content = re.sub(
        r'conn = self\.db_service\.get_connection\(\)',
        'session = self.db_service.get_session()',
        content
    )

    # 模式2: 替换 cursor = conn.cursor(dictionary=True) 为使用 session.execute
    # 这个需要手动处理每个读取方法

    # 模式3: 替换所有的 %(param)s 为 :param (SQLAlchemy 参数格式)
    # 在所有的 SQL 字符串中
    def replace_sql_params(match):
        sql = match.group(1)
        # 替换 %(name)s 为 :name
        sql = re.sub(r'%\((\w+)\)s', r':\1', sql)
        return f'"""' + sql + '"""'

    content = re.sub(
        r'sql = """(.*?)"""',
        replace_sql_params,
        content,
        flags=re.DOTALL
    )

    # 添加 text() 包装
    content = re.sub(
        r'sql = """',
        'sql = text("""',
        content
    )

    # 替换 cursor.execute 为 session.execute
    content = re.sub(
        r'cursor\.execute\(sql, kwargs\)',
        'session.execute(sql, kwargs)',
        content
    )

    # 替换 conn.commit() 为 session.commit()
    content = re.sub(
        r'conn\.commit\(\)',
        'session.commit()',
        content
    )

    # 替换 cursor.close() 为 session.close()
    content = re.sub(
        r'cursor\.close\(\)',
        'session.close()',
        content
    )

    # 替换 cursor = conn.cursor() (在写入方法中)
    content = re.sub(
        r'cursor = conn\.cursor\(\)',
        '',
        content
    )

    # 修复读取方法中的 cursor.fetchone()
    content = re.sub(
        r'result = cursor\.fetchone\(\)',
        'result = session.execute(sql, params).fetchone()',
        content
    )

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print("✅ 文件修复完成！")

if __name__ == "__main__":
    fix_file()
