"""最近 2 天 Gemini 探索/预测详细分析"""
import pymysql
from app.utils.config_loader import get_db_config
cfg = get_db_config()
conn = pymysql.connect(**cfg, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor, autocommit=True)
cur = conn.cursor()

# 看看表结构
cur.execute("DESCRIBE futures_positions")
print("=== futures_positions columns ===")
for r in cur.fetchall():
    print("  %s  %s" % (r['Field'], r['Type']))

conn.close()
