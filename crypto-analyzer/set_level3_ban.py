#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动将指定交易对设置为 rating_level=3（永久禁止交易）
运行方式：python set_level3_ban.py
"""
import os
import re
import pymysql
import pymysql.cursors

SYMBOLS_TO_BAN = ["AIN/USDT", "FET/USDT", "ARIA/USDT", "FHE/USDT", "ALLO/USDT"]
REASON = "手动加入黑名单3级，永久禁止交易"


def resolve_env(value: str) -> str:
    """解析 ${VAR:default} 格式"""
    match = re.match(r'\$\{(\w+):?(.*)\}', str(value))
    if match:
        var, default = match.group(1), match.group(2)
        return os.environ.get(var, default)
    return str(value)


def get_db_config():
    import yaml
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    mysql = cfg["database"]["mysql"]
    return {
        "host":     resolve_env(mysql["host"]),
        "port":     int(resolve_env(mysql["port"])),
        "user":     resolve_env(mysql["user"]),
        "password": resolve_env(mysql["password"]),
        "database": resolve_env(mysql["database"]),
    }


def main():
    try:
        db = get_db_config()
    except Exception as e:
        print(f"读取config.yaml失败: {e}")
        return

    conn = pymysql.connect(
        **db, charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=True
    )
    cur = conn.cursor()

    for symbol in SYMBOLS_TO_BAN:
        try:
            cur.execute("""
                INSERT INTO trading_symbol_rating
                    (symbol, rating_level, margin_multiplier, score_bonus,
                     hard_stop_loss_count, level_change_reason, level_changed_at)
                VALUES (%s, 3, 0.0, 999, 0, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    rating_level       = 3,
                    margin_multiplier  = 0.0,
                    score_bonus        = 999,
                    level_change_reason = %s,
                    level_changed_at   = NOW()
            """, (symbol, REASON, REASON))
            print(f"OK: {symbol} -> Level 3 永久禁止")
        except Exception as e:
            print(f"ERROR: {symbol} 设置失败: {e}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
