#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复错误价格持仓并更新配置文件

问题:
1. 配置文件中包含26个已下架的交易对 (SETTLING状态)
2. 这些交易对的价格是错误的
3. 需要删除这些无效持仓并更新配置文件
"""

import pymysql
import yaml
import requests
from loguru import logger
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

def get_binance_trading_symbols():
    """获取币安当前可交易的合约列表"""
    logger.info("获取币安合约信息...")
    response = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=10)
    exchange_info = response.json()

    trading_symbols = []
    settling_symbols = []

    for symbol_info in exchange_info['symbols']:
        symbol = symbol_info['symbol']
        status = symbol_info['status']

        # 转换为 BTC/USDT 格式
        if symbol.endswith('USDT'):
            formatted_symbol = symbol[:-4] + '/USDT'
            if status == 'TRADING':
                trading_symbols.append(formatted_symbol)
            elif status == 'SETTLING':
                settling_symbols.append(formatted_symbol)

    logger.info(f"✅ 可交易合约: {len(trading_symbols)} 个")
    logger.info(f"⚠️  结算中合约: {len(settling_symbols)} 个")

    return set(trading_symbols), set(settling_symbols)

def update_config_yaml(trading_symbols):
    """更新config.yaml，移除下架的交易对"""
    logger.info("\n=== 更新 config.yaml ===")

    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    old_symbols = config.get('symbols', [])
    logger.info(f"原有交易对: {len(old_symbols)} 个")

    # 过滤出仍在交易的交易对
    new_symbols = [s for s in old_symbols if s in trading_symbols]
    removed_symbols = [s for s in old_symbols if s not in trading_symbols]

    config['symbols'] = new_symbols

    # 备份原文件
    backup_file = f'config.yaml.backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    with open(backup_file, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    logger.info(f"✅ 原配置已备份到: {backup_file}")

    # 写入新配置
    with open('config.yaml', 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    logger.info(f"✅ 更新后交易对: {len(new_symbols)} 个")
    logger.info(f"❌ 已移除: {len(removed_symbols)} 个")

    if removed_symbols:
        logger.warning("移除的交易对:")
        for symbol in sorted(removed_symbols):
            logger.warning(f"  - {symbol}")

    return removed_symbols

def delete_bad_positions(settling_symbols):
    """删除building状态且交易对已下架的持仓"""
    logger.info("\n=== 清理无效持仓 ===")

    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    try:
        # 查询building状态的持仓
        cursor.execute("""
            SELECT id, symbol, position_side, entry_price, margin, created_at
            FROM futures_positions
            WHERE status = 'building'
            AND account_id = 2
            ORDER BY id DESC
        """)

        building_positions = cursor.fetchall()
        logger.info(f"找到 {len(building_positions)} 个building状态持仓")

        bad_positions = []
        for pos in building_positions:
            if pos['symbol'] in settling_symbols:
                bad_positions.append(pos)

        if not bad_positions:
            logger.success("✅ 没有需要清理的无效持仓")
            return

        logger.warning(f"发现 {len(bad_positions)} 个无效持仓(交易对已下架):")
        for pos in bad_positions:
            logger.warning(
                f"  ID:{pos['id']} {pos['symbol']} {pos['position_side']} "
                f"价格:{pos['entry_price']} 保证金:{pos['margin']} "
                f"时间:{pos['created_at']}"
            )

        # 确认删除
        logger.info("\n准备删除这些无效持仓...")
        position_ids = [pos['id'] for pos in bad_positions]

        # 删除持仓
        cursor.execute(f"""
            DELETE FROM futures_positions
            WHERE id IN ({','.join(map(str, position_ids))})
        """)

        conn.commit()
        logger.success(f"✅ 已删除 {len(bad_positions)} 个无效持仓")

        # 记录删除的详情
        logger.info("\n删除详情:")
        for pos in bad_positions:
            logger.info(f"  [{pos['id']}] {pos['symbol']} {pos['position_side']}")

    except Exception as e:
        logger.error(f"删除失败: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def add_to_blacklist(removed_symbols):
    """将移除的交易对加入trading_blacklist表"""
    logger.info("\n=== 更新交易黑名单 ===")

    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    try:
        for symbol in removed_symbols:
            # 检查是否已存在
            cursor.execute("""
                SELECT id FROM trading_blacklist
                WHERE symbol = %s
            """, (symbol,))

            if cursor.fetchone():
                logger.debug(f"{symbol} 已在黑名单中")
                continue

            # 插入黑名单
            cursor.execute("""
                INSERT INTO trading_blacklist (symbol, reason, is_active, created_at)
                VALUES (%s, %s, 1, NOW())
            """, (symbol, '币安合约已下架(SETTLING)'))

            logger.info(f"✅ {symbol} 已加入黑名单")

        conn.commit()
        logger.success(f"✅ 黑名单更新完成")

    except Exception as e:
        logger.error(f"更新黑名单失败: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

def main():
    logger.info("=" * 60)
    logger.info("开始修复错误价格持仓和配置文件")
    logger.info("=" * 60)

    # 1. 获取币安可交易的合约列表
    trading_symbols, settling_symbols = get_binance_trading_symbols()

    # 2. 更新config.yaml
    removed_symbols = update_config_yaml(trading_symbols)

    # 3. 删除无效持仓
    delete_bad_positions(settling_symbols)

    # 4. 将移除的交易对加入黑名单
    if removed_symbols:
        add_to_blacklist(removed_symbols)

    logger.info("\n" + "=" * 60)
    logger.info("✅ 修复完成！")
    logger.info("=" * 60)
    logger.info("\n建议:")
    logger.info("1. 重启 smart_trader_service.py 以重新加载配置")
    logger.info("2. 检查日志确认系统正常运行")
    logger.info("3. 建议每周检查一次币安合约状态")

if __name__ == '__main__':
    main()
