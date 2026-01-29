#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为Big4趋势检测器添加数据库记录功能

修改内容:
1. 创建 big4_trend_history 表存储每次检测结果
2. 修改 Big4TrendDetector.detect_market_trend() 方法,在返回结果前存储到数据库
"""

import pymysql
import sys
import io
from dotenv import load_dotenv
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor,
    charset='utf8mb4'
)

cursor = conn.cursor()

print('=' * 100)
print('为Big4趋势检测器添加数据库记录功能')
print('=' * 100)
print()

# 1. 创建 big4_trend_history 表
print('1. 创建 big4_trend_history 表...')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS big4_trend_history (
        id INT AUTO_INCREMENT PRIMARY KEY,
        overall_signal VARCHAR(20),              -- BULLISH, BEARISH, NEUTRAL
        signal_strength DECIMAL(5, 2),           -- 0-100 信号强度
        bullish_count INT,                       -- 看涨数量
        bearish_count INT,                       -- 看跌数量
        recommendation TEXT,                     -- 建议

        -- BTC详情
        btc_signal VARCHAR(20),
        btc_strength DECIMAL(5, 2),
        btc_reason TEXT,
        btc_is_consolidating BOOLEAN,
        btc_price_change_6h DECIMAL(10, 4),
        btc_1h_dominant VARCHAR(20),
        btc_15m_dominant VARCHAR(20),

        -- ETH详情
        eth_signal VARCHAR(20),
        eth_strength DECIMAL(5, 2),
        eth_reason TEXT,
        eth_is_consolidating BOOLEAN,
        eth_price_change_6h DECIMAL(10, 4),
        eth_1h_dominant VARCHAR(20),
        eth_15m_dominant VARCHAR(20),

        -- BNB详情
        bnb_signal VARCHAR(20),
        bnb_strength DECIMAL(5, 2),
        bnb_reason TEXT,
        bnb_is_consolidating BOOLEAN,
        bnb_price_change_6h DECIMAL(10, 4),
        bnb_1h_dominant VARCHAR(20),
        bnb_15m_dominant VARCHAR(20),

        -- SOL详情
        sol_signal VARCHAR(20),
        sol_strength DECIMAL(5, 2),
        sol_reason TEXT,
        sol_is_consolidating BOOLEAN,
        sol_price_change_6h DECIMAL(10, 4),
        sol_1h_dominant VARCHAR(20),
        sol_15m_dominant VARCHAR(20),

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        INDEX idx_created_at (created_at),
        INDEX idx_overall_signal (overall_signal, created_at)
    )
''')
print('   ✓ big4_trend_history 表已创建')
print()

# 2. 修改 big4_trend_detector.py 文件
print('2. 修改 big4_trend_detector.py 添加数据库记录功能...')

big4_file = 'app/services/big4_trend_detector.py'

# 读取原文件
with open(big4_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 在 detect_market_trend 方法的 return 语句之前插入数据库记录代码
insert_code = '''
        # 记录到数据库
        self._save_to_database(result)

'''

# 找到 return 语句的位置 (在 detect_market_trend 方法中)
import_pos = content.find("import mysql.connector")
if import_pos != -1:
    # 添加 os 和 dotenv 导入
    new_imports = """import mysql.connector
from dotenv import load_dotenv
import os

# 加载环境变量
load_dotenv()
"""
    content = content.replace("import mysql.connector", new_imports)

# 找到 return 语句前添加数据库记录
return_pos = content.find("        return {\n            'overall_signal': overall_signal,")
if return_pos != -1:
    # 在 return 之前插入
    result_var = """
        result = {
            'overall_signal': overall_signal,
            'signal_strength': avg_strength,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'details': results,
            'recommendation': recommendation,
            'timestamp': datetime.now()
        }

        # 记录到数据库
        self._save_to_database(result)

        return result
"""
    content = content.replace(
        """        return {
            'overall_signal': overall_signal,
            'signal_strength': avg_strength,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'details': results,
            'recommendation': recommendation,
            'timestamp': datetime.now()
        }""",
        result_var
    )

# 在类的末尾添加 _save_to_database 方法
save_method = '''
    def _save_to_database(self, result: Dict):
        """保存检测结果到数据库"""
        try:
            db_config = {
                'host': os.getenv('DB_HOST'),
                'port': int(os.getenv('DB_PORT', 3306)),
                'user': os.getenv('DB_USER'),
                'password': os.getenv('DB_PASSWORD'),
                'database': os.getenv('DB_NAME')
            }

            import pymysql
            conn = pymysql.connect(**db_config)
            cursor = conn.cursor()

            details = result['details']

            cursor.execute("""
                INSERT INTO big4_trend_history (
                    overall_signal, signal_strength, bullish_count, bearish_count, recommendation,
                    btc_signal, btc_strength, btc_reason, btc_is_consolidating, btc_price_change_6h,
                    btc_1h_dominant, btc_15m_dominant,
                    eth_signal, eth_strength, eth_reason, eth_is_consolidating, eth_price_change_6h,
                    eth_1h_dominant, eth_15m_dominant,
                    bnb_signal, bnb_strength, bnb_reason, bnb_is_consolidating, bnb_price_change_6h,
                    bnb_1h_dominant, bnb_15m_dominant,
                    sol_signal, sol_strength, sol_reason, sol_is_consolidating, sol_price_change_6h,
                    sol_1h_dominant, sol_15m_dominant
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                result['overall_signal'],
                result['signal_strength'],
                result['bullish_count'],
                result['bearish_count'],
                result['recommendation'],
                # BTC
                details['BTC/USDT']['signal'],
                details['BTC/USDT']['strength'],
                details['BTC/USDT']['reason'],
                details['BTC/USDT']['is_consolidating'],
                details['BTC/USDT']['price_change_6h'],
                details['BTC/USDT']['1h_analysis']['dominant'],
                details['BTC/USDT']['15m_analysis']['dominant'],
                # ETH
                details['ETH/USDT']['signal'],
                details['ETH/USDT']['strength'],
                details['ETH/USDT']['reason'],
                details['ETH/USDT']['is_consolidating'],
                details['ETH/USDT']['price_change_6h'],
                details['ETH/USDT']['1h_analysis']['dominant'],
                details['ETH/USDT']['15m_analysis']['dominant'],
                # BNB
                details['BNB/USDT']['signal'],
                details['BNB/USDT']['strength'],
                details['BNB/USDT']['reason'],
                details['BNB/USDT']['is_consolidating'],
                details['BNB/USDT']['price_change_6h'],
                details['BNB/USDT']['1h_analysis']['dominant'],
                details['BNB/USDT']['15m_analysis']['dominant'],
                # SOL
                details['SOL/USDT']['signal'],
                details['SOL/USDT']['strength'],
                details['SOL/USDT']['reason'],
                details['SOL/USDT']['is_consolidating'],
                details['SOL/USDT']['price_change_6h'],
                details['SOL/USDT']['1h_analysis']['dominant'],
                details['SOL/USDT']['15m_analysis']['dominant']
            ))

            conn.commit()
            cursor.close()
            conn.close()

            logger.debug(f"Big4趋势检测结果已保存到数据库: {result['overall_signal']}")

        except Exception as e:
            logger.warning(f"保存Big4趋势检测结果失败: {e}")

'''

# 在最后一个方法后添加新方法
last_method_pos = content.rfind("    def _generate_signal(")
if last_method_pos != -1:
    # 找到这个方法的结束位置
    method_end = content.find("\n\ndef get_big4_detector", last_method_pos)
    if method_end != -1:
        content = content[:method_end] + save_method + content[method_end:]

# 写回文件
with open(big4_file, 'w', encoding='utf-8') as f:
    f.write(content)

print('   ✓ big4_trend_detector.py 已修改')
print()

# 3. 提交数据库更改
conn.commit()

print('=' * 100)
print('✅ Big4趋势检测数据库记录功能已添加!')
print('=' * 100)
print()
print('功能说明:')
print('  1. 每次调用 detect_market_trend() 时自动保存结果到数据库')
print('  2. big4_trend_history 表记录完整的检测结果')
print('  3. 可用于后续分析Big4信号的准确性')
print()
print('下一步:')
print('  1. 重启服务使修改生效')
print('  2. 每次开仓时会自动记录当时的Big4趋势')
print('  3. 可以查询历史数据分析Big4信号效果')
print()
print('查询示例:')
print('  -- 查询最近的Big4趋势')
print('  SELECT * FROM big4_trend_history ORDER BY created_at DESC LIMIT 10;')
print()
print('  -- 分析Big4信号准确性')
print('  SELECT overall_signal, COUNT(*) as count, AVG(signal_strength) as avg_strength')
print('  FROM big4_trend_history')
print('  WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)')
print('  GROUP BY overall_signal;')
print()

cursor.close()
conn.close()
