import pymysql

conn = pymysql.connect(
    host='13.212.252.171',
    user='admin',
    password='Tonny@1000',
    database='binance-data',
    charset='utf8mb4'
)
cursor = conn.cursor()

# Create system settings table
create_table_sql = """
CREATE TABLE IF NOT EXISTS system_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    setting_key VARCHAR(100) UNIQUE NOT NULL,
    setting_value TEXT NOT NULL,
    description VARCHAR(255),
    updated_by VARCHAR(100) DEFAULT 'system',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_setting_key (setting_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

cursor.execute(create_table_sql)
conn.commit()
print('Table created')

# Insert default settings
insert_sql = """
INSERT INTO system_settings (setting_key, setting_value, description, updated_by)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    setting_value = VALUES(setting_value),
    description = VALUES(description),
    updated_at = CURRENT_TIMESTAMP
"""

settings = [
    ('batch_entry_strategy', 'kline_pullback', 'V2 (kline_pullback) or V1 (price_percentile)', 'system'),
    ('big4_filter_enabled', 'true', 'Big4 filter status', 'system')
]

for setting in settings:
    cursor.execute(insert_sql, setting)

conn.commit()
print('Default settings inserted')

# Query data
cursor.execute('SELECT setting_key, setting_value, description FROM system_settings')
rows = cursor.fetchall()
print('\nCurrent settings:')
for row in rows:
    print(f'  {row[0]}: {row[1]}')
    print(f'    ({row[2]})')

cursor.close()
conn.close()
