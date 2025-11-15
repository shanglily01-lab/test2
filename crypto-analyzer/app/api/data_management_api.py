# -*- coding: utf-8 -*-
"""
数据管理API
提供数据统计、查询和维护功能
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from typing import Dict, List, Optional
from datetime import datetime, timedelta, date
from loguru import logger
import pymysql
from pymysql.cursors import DictCursor
import yaml
from pathlib import Path
import csv
import io
import asyncio
import pandas as pd
from app.services.data_collection_task_manager import task_manager, TaskStatus
import threading

router = APIRouter(prefix="/api/data-management", tags=["数据管理"])

# 数据库连接池（全局变量）
_db_pool = None
_db_pool_lock = threading.Lock()
_db_config = None


def get_db_config():
    """获取数据库配置（缓存）"""
    global _db_config
    if _db_config is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            _db_config = config.get('database', {}).get('mysql', {})
        else:
            _db_config = {}
    return _db_config


def get_db_connection():
    """获取数据库连接（使用连接池）"""
    global _db_pool
    
    if _db_pool is None:
        with _db_pool_lock:
            # 双重检查，避免重复创建
            if _db_pool is None:
                db_config = get_db_config()
                try:
                    # 使用 pymysql 的连接池（通过自定义实现）
                    # 由于 pymysql 没有内置连接池，我们使用简单的连接复用
                    _db_pool = {
                        'config': db_config,
                        'connections': [],
                        'max_size': 20,  # 增加连接池大小
                        'lock': threading.Lock()
                    }
                    logger.info("✅ 数据管理API数据库连接池初始化成功")
                except Exception as e:
                    logger.error(f"❌ 数据库连接池初始化失败: {e}")
                    raise
    
    # 尝试从池中获取连接
    pool = _db_pool
    with pool['lock']:
        # 清理已关闭的连接
        pool['connections'] = [conn for conn in pool['connections'] if conn.open]
        
        # 如果有可用连接，直接返回
        if pool['connections']:
            return pool['connections'].pop()
        
        # 否则创建新连接
        if len(pool['connections']) < pool['max_size']:
            try:
                conn = pymysql.connect(
                    host=pool['config'].get('host', 'localhost'),
                    port=pool['config'].get('port', 3306),
                    user=pool['config'].get('user', 'root'),
                    password=pool['config'].get('password', ''),
                    database=pool['config'].get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=DictCursor,
                    connect_timeout=5,
                    read_timeout=10,
                    write_timeout=10
                )
                return conn
            except Exception as e:
                logger.error(f"❌ 创建数据库连接失败: {e}")
                raise
    
    # 如果池已满，创建临时连接（不放入池中）
    db_config = get_db_config()
    return pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4',
        cursorclass=DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10
    )


def return_db_connection(conn):
    """归还数据库连接到池中"""
    global _db_pool
    if _db_pool and conn and conn.open:
        pool = _db_pool
        with pool['lock']:
            if len(pool['connections']) < pool['max_size']:
                pool['connections'].append(conn)
                return
    # 如果池已满或连接已关闭，直接关闭连接
    if conn:
        try:
            conn.close()
        except:
            pass


class DBConnection:
    """数据库连接上下文管理器"""
    def __init__(self):
        self.conn = None
    
    def __enter__(self):
        self.conn = get_db_connection()
        return self.conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            return_db_connection(self.conn)
        return False


def _update_config_file(config_path: Path, symbols: List[str], data_type: str, timeframe: str = None) -> bool:
    """
    更新config.yaml文件，添加新的交易对和时间周期
    
    Args:
        config_path: 配置文件路径
        symbols: 要添加的交易对列表
        data_type: 数据类型 ('price' 或 'kline')
        timeframe: 时间周期（仅kline类型需要）
    
    Returns:
        是否成功更新
    """
    try:
        # 读取现有配置
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        updated = False
        
        # 更新symbols列表
        if 'symbols' not in config:
            config['symbols'] = []
        
        existing_symbols = set(config['symbols'])
        new_symbols = []
        for symbol in symbols:
            symbol = symbol.strip()
            if symbol and symbol not in existing_symbols:
                config['symbols'].append(symbol)
                new_symbols.append(symbol)
                updated = True
        
        # 如果是K线数据，更新timeframes列表
        if data_type == 'kline' and timeframe:
            if 'collector' not in config:
                config['collector'] = {}
            if 'timeframes' not in config['collector']:
                config['collector']['timeframes'] = []
            
            existing_timeframes = set(config['collector']['timeframes'])
            if timeframe not in existing_timeframes:
                config['collector']['timeframes'].append(timeframe)
                updated = True
        
        # 如果有更新，保存配置文件
        if updated:
            with open(config_path, 'w', encoding='utf-8') as f:
                # 使用更好的格式选项保持YAML可读性
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False, 
                         sort_keys=False, indent=2, width=120)
            logger.info(f"配置文件已更新: 添加了 {len(new_symbols)} 个新交易对" + 
                      (f"，时间周期 {timeframe}" if data_type == 'kline' and timeframe else ""))
        
        return updated
        
    except Exception as e:
        logger.error(f"更新配置文件失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _check_status_active(latest_time, threshold_seconds):
    """
    检查数据采集状态是否活跃
    
    判断逻辑：
    - 如果最新数据时间在阈值内，返回 'active'
    - 如果最新数据时间超过阈值但小于阈值的3倍，返回 'warning'（可能延迟）
    - 如果最新数据时间超过阈值的3倍，返回 'inactive'
    """
    try:
        if not latest_time:
            return 'inactive'
        
        # 转换为datetime对象
        if isinstance(latest_time, str):
            # 尝试解析ISO格式
            try:
                latest_time = datetime.fromisoformat(latest_time.replace('Z', '+00:00'))
            except:
                # 如果失败，尝试其他常见格式
                try:
                    # 尝试 MySQL datetime 格式: YYYY-MM-DD HH:MM:SS
                    latest_time = datetime.strptime(latest_time, '%Y-%m-%d %H:%M:%S')
                except:
                    try:
                        # 尝试带毫秒的格式
                        latest_time = datetime.strptime(latest_time, '%Y-%m-%d %H:%M:%S.%f')
                    except:
                        logger.error(f"无法解析时间格式: {latest_time}")
                        return 'inactive'
        elif not isinstance(latest_time, datetime):
            # 如果是其他类型（如MySQL的datetime对象），尝试转换
            if hasattr(latest_time, 'isoformat'):
                try:
                    latest_time = datetime.fromisoformat(latest_time.isoformat())
                except:
                    latest_time = datetime.strptime(str(latest_time), '%Y-%m-%d %H:%M:%S')
            else:
                try:
                    latest_time = datetime.strptime(str(latest_time), '%Y-%m-%d %H:%M:%S')
                except:
                    logger.error(f"无法转换时间类型: {type(latest_time)}, value: {latest_time}")
                    return 'inactive'
        
        # 处理时区：统一转换为无时区的本地时间进行比较
        if latest_time.tzinfo is not None:
            # 如果有时区信息，转换为本地时间
            latest_time = latest_time.replace(tzinfo=None)
        
        # 计算时间差（秒）
        now = datetime.now()
        time_diff = (now - latest_time).total_seconds()
        
        # 判断状态
        if time_diff < 0:
            # 未来时间，可能是时区问题，视为活跃
            return 'active'
        elif time_diff <= threshold_seconds:
            return 'active'
        elif time_diff <= threshold_seconds * 3:
            return 'warning'  # 延迟但可能还在运行
        else:
            return 'inactive'
            
    except Exception as e:
        logger.error(f"检查状态失败: {e}, latest_time={latest_time}, type={type(latest_time)}")
        import traceback
        traceback.print_exc()
        return 'inactive'


@router.get("/statistics")
async def get_data_statistics():
    """
    获取所有数据表的统计信息（优化版本）
    包括记录数、最新数据时间、数据范围等
    对于大表使用近似计数和索引优化查询
    """
    try:
        with DBConnection() as conn:
            cursor = conn.cursor()
            
            # 定义所有数据表及其描述
            # 大表列表：使用近似计数和优化查询
            large_tables = {'price_data', 'kline_data'}
            
            # 时区说明：
            # - Binance数据（price_data, kline_data, funding_rate_data等）：UTC时间（UTC+0）
            # - 其他数据（ETF、企业金库、新闻、信号等）：本地时间（UTC+8）
            tables = [
            {'name': 'price_data', 'label': '实时价格数据', 'description': '存储各交易所的实时价格数据', 'time_field': 'timestamp', 'is_binance': True},
            {'name': 'kline_data', 'label': 'K线数据', 'description': '存储不同时间周期的K线数据', 'time_field': 'open_time', 'is_timestamp_ms': True, 'is_binance': True},
            {'name': 'news_data', 'label': '新闻数据', 'description': '存储加密货币相关新闻', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'funding_rate_data', 'label': '资金费率数据', 'description': '存储合约资金费率数据', 'time_field': 'timestamp', 'is_binance': True},
            {'name': 'futures_open_interest', 'label': '持仓量数据', 'description': '存储合约持仓量数据', 'time_field': 'timestamp', 'is_binance': True},
            {'name': 'futures_long_short_ratio', 'label': '多空比数据', 'description': '存储合约多空比数据', 'time_field': 'timestamp', 'is_binance': True},
            {'name': 'smart_money_transactions', 'label': '聪明钱交易', 'description': '存储聪明钱包的交易记录', 'time_field': 'timestamp', 'is_binance': False},
            {'name': 'smart_money_signals', 'label': '聪明钱信号', 'description': '存储聪明钱交易信号', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'ema_signals', 'label': 'EMA信号', 'description': '存储EMA技术指标信号', 'time_field': 'timestamp', 'is_binance': False},
            {'name': 'investment_recommendations', 'label': '投资建议', 'description': '存储AI生成的投资建议', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'futures_positions', 'label': '合约持仓', 'description': '存储合约交易持仓记录', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'futures_orders', 'label': '合约订单', 'description': '存储合约交易订单记录', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'futures_trades', 'label': '合约交易', 'description': '存储合约交易记录', 'time_field': 'timestamp', 'is_binance': False},
            {'name': 'paper_trading_accounts', 'label': '模拟账户', 'description': '存储模拟交易账户信息', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'paper_trading_orders', 'label': '模拟订单', 'description': '存储模拟交易订单记录', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'paper_trading_positions', 'label': '模拟持仓', 'description': '存储模拟交易持仓记录', 'time_field': 'created_at', 'is_binance': False},
            {'name': 'crypto_etf_flows', 'label': 'ETF数据', 'description': '存储加密货币ETF资金流向数据', 'time_field': 'date', 'is_binance': False},
            {'name': 'corporate_treasury_financing', 'label': '企业融资', 'description': '存储企业金库融资数据', 'time_field': 'date', 'is_binance': False},
        ]
        
        # 先一次性获取所有表的大小和行数估算（从 information_schema）
        table_names = [f"'{t['name']}'" for t in tables]
        if not table_names:
            return {
                'success': True,
                'data': {
                    'tables': [],
                    'summary': {
                        'total_tables': 0,
                        'total_records': 0,
                        'total_size_mb': 0
                    }
                }
            }
        
        # 获取当前数据库名（从配置中读取，更可靠）
        db_config = get_db_config()
        database_name = db_config.get('database', 'binance-data')
        
        cursor.execute("""
            SELECT 
                TABLE_NAME as table_name,
                ROUND(((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024), 2) AS size_mb,
                TABLE_ROWS as estimated_rows
            FROM information_schema.TABLES 
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME IN ({})
        """.format(','.join(table_names)), (database_name,))
        
        table_info_map = {}
        for row in cursor.fetchall():
            # 确保使用字典访问方式（DictCursor）
            if isinstance(row, dict):
                # 处理可能的字段名大小写问题（DictCursor 会使用别名）
                table_name = row.get('table_name') or row.get('TABLE_NAME')
                size_mb = row.get('size_mb') or row.get('SIZE_MB') or 0
                estimated_rows = row.get('estimated_rows') or row.get('ESTIMATED_ROWS') or 0
            else:
                # 如果是元组，按位置访问
                table_name = row[0] if len(row) > 0 else None
                size_mb = row[1] if len(row) > 1 else 0
                estimated_rows = row[2] if len(row) > 2 else 0
            
            if table_name:
                table_info_map[table_name] = {
                    'size_mb': float(size_mb) if size_mb else 0,
                    'estimated_rows': int(estimated_rows) if estimated_rows else 0
                }
        
        statistics = []
        
        for table in tables:
            try:
                table_name = table['name']
                
                # 检查表是否存在
                if table_name not in table_info_map:
                    statistics.append({
                        **table,
                        'exists': False,
                        'count': 0,
                        'latest_time': None,
                        'oldest_time': None,
                        'size_mb': 0
                    })
                    continue
                
                table_info = table_info_map[table_name]
                size_mb = table_info['size_mb']
                
                # 对于大表，使用估算行数；对于小表，使用精确计数
                if table_name in large_tables:
                    # 大表：使用估算值（更快）
                    count = table_info['estimated_rows']
                    count_approx = True
                else:
                    # 小表：使用精确计数
                    try:
                        cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
                        count_result = cursor.fetchone()
                        count = count_result['count'] if count_result else 0
                        count_approx = False
                    except:
                        count = table_info['estimated_rows']
                        count_approx = True
                
                # 获取最新和最早的时间戳（使用索引字段）
                latest_time = None
                oldest_time = None
                time_field = table.get('time_field', 'timestamp')
                is_timestamp_ms = table.get('is_timestamp_ms', False)
                is_binance = table.get('is_binance', False)  # 是否是Binance数据（UTC时间）
                
                try:
                    if is_timestamp_ms and time_field == 'open_time':
                        # kline_data 表的 open_time 是毫秒时间戳
                        cursor.execute("""
                            SELECT 
                                FROM_UNIXTIME(MAX(open_time)/1000) as max_time,
                                FROM_UNIXTIME(MIN(open_time)/1000) as min_time
                            FROM kline_data
                            WHERE open_time IS NOT NULL
                        """)
                    else:
                        # 其他表使用标准时间字段，使用索引优化
                        cursor.execute(f"""
                            SELECT 
                                MAX({time_field}) as max_time,
                                MIN({time_field}) as min_time
                            FROM {table_name}
                            WHERE {time_field} IS NOT NULL
                        """)
                    
                    time_result = cursor.fetchone()
                    if time_result and time_result.get('max_time'):
                        max_time = time_result['max_time']
                        min_time = time_result['min_time']
                        
                        # 格式化时间戳，统一使用数据库存储的时间（本地时间UTC+8）
                        # 所有数据统一标记为本地时间（+08:00），不再区分Binance和其他数据
                        if hasattr(max_time, 'isoformat'):
                            # 统一标记为本地时间（UTC+8）
                            latest_time = max_time.isoformat()
                            if not latest_time.endswith(('Z', '+', '-')):
                                latest_time = latest_time + '+08:00'
                            elif latest_time.endswith('Z'):
                                # 如果是UTC时间，转换为本地时间标记
                                latest_time = latest_time.replace('Z', '+08:00')
                            
                            if min_time:
                                oldest_time = min_time.isoformat()
                                if not oldest_time.endswith(('Z', '+', '-')):
                                    oldest_time = oldest_time + '+08:00'
                                elif oldest_time.endswith('Z'):
                                    oldest_time = oldest_time.replace('Z', '+08:00')
                            else:
                                oldest_time = None
                        else:
                            # 字符串格式（DATE类型）
                            latest_time = str(max_time)
                            if not latest_time.endswith(('Z', '+', '-', 'T')):
                                latest_time = latest_time + 'T00:00:00+08:00'
                            elif latest_time.endswith('Z'):
                                latest_time = latest_time.replace('Z', '+08:00')
                            
                            if min_time:
                                oldest_time = str(min_time)
                                if not oldest_time.endswith(('Z', '+', '-', 'T')):
                                    oldest_time = oldest_time + 'T00:00:00+08:00'
                                elif oldest_time.endswith('Z'):
                                    oldest_time = oldest_time.replace('Z', '+08:00')
                            else:
                                oldest_time = None
                except Exception as e:
                    logger.debug(f"获取 {table_name} 时间失败: {e}")
                    # 尝试其他时间字段
                    for field in ['timestamp', 'created_at', 'updated_at', 'open_time', 'trade_time', 'date']:
                        if field == time_field:
                            continue
                        try:
                            cursor.execute(f"SELECT MAX({field}) as max_time, MIN({field}) as min_time FROM {table_name} WHERE {field} IS NOT NULL")
                            time_result = cursor.fetchone()
                            if time_result and time_result.get('max_time'):
                                latest_time = time_result['max_time'].isoformat() if hasattr(time_result['max_time'], 'isoformat') else str(time_result['max_time'])
                                oldest_time = time_result['min_time'].isoformat() if hasattr(time_result['min_time'], 'isoformat') else str(time_result['min_time'])
                                break
                        except:
                            continue
                
                statistics.append({
                    **table,
                    'exists': True,
                    'count': count,
                    'count_approx': count_approx if table_name in large_tables else False,
                    'latest_time': latest_time,
                    'oldest_time': oldest_time,
                    'size_mb': size_mb
                })
                
            except Exception as e:
                logger.error(f"获取表 {table['name']} 统计信息失败: {e}")
                statistics.append({
                    **table,
                    'exists': False,
                    'count': 0,
                    'latest_time': None,
                    'oldest_time': None,
                    'size_mb': 0,
                    'error': str(e)
                })
        
        # 计算总计
        total_count = sum(s['count'] for s in statistics)
        total_size = sum(s['size_mb'] for s in statistics)
        
        return {
            'success': True,
            'data': {
                'tables': statistics,
                'summary': {
                    'total_tables': len(statistics),
                    'total_records': total_count,
                    'total_size_mb': round(total_size, 2)
                }
            }
        }
        
    except Exception as e:
        logger.error(f"获取数据统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取数据统计失败: {str(e)}")


@router.get("/table/{table_name}/sample")
async def get_table_sample(table_name: str, limit: int = 10):
    """
    获取指定表的样本数据
    
    Args:
        table_name: 表名
        limit: 返回记录数限制
    """
    try:
        with DBConnection() as conn:
            cursor = conn.cursor()
            
            # 安全检查：只允许查询白名单中的表
            allowed_tables = [
                'price_data', 'kline_data', 'news_data', 'funding_rate_data',
            'futures_open_interest', 'futures_long_short_ratio',
            'smart_money_transactions', 'smart_money_signals', 'ema_signals',
            'investment_recommendations', 'futures_positions', 'futures_orders',
            'futures_trades', 'paper_trading_accounts', 'paper_trading_orders',
            'paper_trading_positions', 'crypto_etf_flows', 'corporate_treasury_financing'
        ]
        
        if table_name not in allowed_tables:
            raise HTTPException(status_code=400, detail=f"不允许查询表: {table_name}")
        
        # 检查表是否存在
        cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail=f"表 {table_name} 不存在")
        
        # 获取样本数据（尝试不同的排序字段）
        order_fields = ['id', 'timestamp', 'created_at', 'updated_at', 'open_time', 'trade_time', 'date']
        rows = []
        
        for field in order_fields:
            try:
                cursor.execute(f"SELECT * FROM {table_name} ORDER BY {field} DESC LIMIT %s", (limit,))
                rows = cursor.fetchall()
                if rows:
                    break
            except:
                continue
        
        # 如果所有排序字段都失败，直接查询
        if not rows:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT %s", (limit,))
            rows = cursor.fetchall()
        
        # 转换数据格式
        sample_data = []
        for row in rows:
            row_dict = {}
            for key, value in row.items():
                if isinstance(value, datetime):
                    row_dict[key] = value.isoformat()
                elif isinstance(value, (int, float)):
                    row_dict[key] = value
                else:
                    row_dict[key] = str(value) if value is not None else None
            sample_data.append(row_dict)
            
            return {
                'success': True,
                'table_name': table_name,
                'count': len(sample_data),
                'data': sample_data
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取表 {table_name} 样本数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取样本数据失败: {str(e)}")


@router.delete("/table/{table_name}/cleanup")
async def cleanup_old_data(
    table_name: str,
    days: int = 30,
    confirm: bool = False
):
    """
    清理指定表的旧数据
    
    Args:
        table_name: 表名
        days: 保留最近N天的数据
        confirm: 确认删除（必须为True才能执行）
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="必须设置 confirm=true 才能执行删除操作")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 安全检查：只允许清理白名单中的表
        allowed_tables = [
            'price_data', 'kline_data', 'news_data', 'funding_rate_data',
            'futures_open_interest', 'futures_long_short_ratio',
            'smart_money_transactions', 'ema_signals'
        ]
        
        if table_name not in allowed_tables:
            raise HTTPException(status_code=400, detail=f"不允许清理表: {table_name}")
        
        # 检查表是否存在
        cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail=f"表 {table_name} 不存在")
        
        # 获取删除前的记录数
        cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
        before_count = cursor.fetchone()['count']
        
        # 尝试不同的时间字段
        time_fields = ['timestamp', 'created_at', 'updated_at', 'open_time', 'trade_time', 'date']
        deleted_count = 0
        
        for field in time_fields:
            try:
                cursor.execute(f"SELECT COUNT(*) as count FROM {table_name} WHERE {field} < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
                old_count = cursor.fetchone()['count']
                
                if old_count > 0:
                    cursor.execute(f"DELETE FROM {table_name} WHERE {field} < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
                    deleted_count = cursor.rowcount
                    conn.commit()
                    break
            except:
                continue
        
        # 获取删除后的记录数
        cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
        after_count = cursor.fetchone()['count']
        
        cursor.close()
        conn.close()
        
        return {
            'success': True,
            'table_name': table_name,
            'before_count': before_count,
            'deleted_count': deleted_count,
            'after_count': after_count,
            'message': f'成功清理 {deleted_count} 条超过 {days} 天的旧数据'
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"清理表 {table_name} 数据失败: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"清理数据失败: {str(e)}")


@router.get("/collection-status")
async def get_collection_status():
    """
    获取各类数据的采集情况
    包括实时数据、合约数据、新闻数据等的最新采集时间
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        collection_status = []
        
        # 1. 实时价格数据采集情况
        try:
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    MAX(timestamp) as latest_time,
                    MIN(timestamp) as oldest_time,
                    COUNT(DISTINCT symbol) as symbol_count,
                    COUNT(DISTINCT exchange) as exchange_count
                FROM price_data
            """)
            price_result = cursor.fetchone()
            collection_status.append({
                'type': '实时价格数据',
                'category': 'market_data',
                'icon': 'bi-graph-up',
                'description': '各交易所的实时价格数据',
                'count': price_result['count'] if price_result else 0,
                'latest_time': price_result['latest_time'].isoformat() if price_result and price_result['latest_time'] else None,
                'oldest_time': price_result['oldest_time'].isoformat() if price_result and price_result['oldest_time'] else None,
                'symbol_count': price_result['symbol_count'] if price_result else 0,
                'exchange_count': price_result['exchange_count'] if price_result else 0,
                'status': _check_status_active(price_result['latest_time'], 600) if price_result and price_result['latest_time'] else 'inactive'  # 价格数据每1分钟采集，阈值设为10分钟
            })
        except Exception as e:
            logger.error(f"获取实时价格数据采集情况失败: {e}")
            collection_status.append({
                'type': '实时价格数据',
                'category': 'market_data',
                'icon': 'bi-graph-up',
                'description': '各交易所的实时价格数据',
                'status': 'error',
                'error': str(e)
            })
        
        # 2. K线数据采集情况
        try:
            # 尝试使用timestamp字段，如果失败则使用created_at
            try:
                cursor.execute("""
                    SELECT 
                        COUNT(*) as count,
                        MAX(timestamp) as latest_time,
                        MIN(timestamp) as oldest_time,
                        COUNT(DISTINCT symbol) as symbol_count,
                        COUNT(DISTINCT timeframe) as timeframe_count
                    FROM kline_data
                """)
            except:
                # 如果timestamp字段不存在或出错，使用created_at
                cursor.execute("""
                    SELECT 
                        COUNT(*) as count,
                        MAX(created_at) as latest_time,
                        MIN(created_at) as oldest_time,
                        COUNT(DISTINCT symbol) as symbol_count,
                        COUNT(DISTINCT timeframe) as timeframe_count
                    FROM kline_data
                """)
            kline_result = cursor.fetchone()
            
            # 对于K线数据，使用created_at字段来判断状态（本地时间，更准确）
            # 因为timestamp字段可能是UTC时间，会导致时区判断错误
            # 不同时间周期的采集频率不同，使用最严格的时间周期（1m）来判断
            # 但也要考虑其他时间周期（5m, 15m, 1h, 1d）的采集频率
            status = 'inactive'
            
            # 检查1分钟K线的最新created_at时间（最严格，阈值10分钟）
            cursor.execute("""
                SELECT MAX(created_at) as latest_created
                FROM kline_data
                WHERE timeframe = '1m' AND created_at IS NOT NULL
            """)
            kline_1m_result = cursor.fetchone()
            if kline_1m_result and kline_1m_result['latest_created']:
                status_1m = _check_status_active(kline_1m_result['latest_created'], 600)  # 10分钟阈值
                if status_1m == 'active':
                    status = 'active'
                elif status_1m == 'warning' and status != 'active':
                    status = 'warning'
            
            # 如果1分钟数据不活跃，检查其他时间周期
            if status == 'inactive':
                # 检查5分钟K线（阈值30分钟）
                cursor.execute("""
                    SELECT MAX(created_at) as latest_created
                    FROM kline_data
                    WHERE timeframe = '5m' AND created_at IS NOT NULL
                """)
                kline_5m_result = cursor.fetchone()
                if kline_5m_result and kline_5m_result['latest_created']:
                    status_5m = _check_status_active(kline_5m_result['latest_created'], 1800)  # 30分钟阈值
                    if status_5m == 'active':
                        status = 'active'
                    elif status_5m == 'warning' and status != 'active':
                        status = 'warning'
                
                # 检查15分钟K线（阈值1小时）
                cursor.execute("""
                    SELECT MAX(created_at) as latest_created
                    FROM kline_data
                    WHERE timeframe = '15m' AND created_at IS NOT NULL
                """)
                kline_15m_result = cursor.fetchone()
                if kline_15m_result and kline_15m_result['latest_created']:
                    status_15m = _check_status_active(kline_15m_result['latest_created'], 3600)  # 1小时阈值
                    if status_15m == 'active':
                        status = 'active'
                    elif status_15m == 'warning' and status != 'active':
                        status = 'warning'
                
                # 检查1小时K线（阈值3小时）
                cursor.execute("""
                    SELECT MAX(created_at) as latest_created
                    FROM kline_data
                    WHERE timeframe = '1h' AND created_at IS NOT NULL
                """)
                kline_1h_result = cursor.fetchone()
                if kline_1h_result and kline_1h_result['latest_created']:
                    status_1h = _check_status_active(kline_1h_result['latest_created'], 10800)  # 3小时阈值
                    if status_1h == 'active':
                        status = 'active'
                    elif status_1h == 'warning' and status != 'active':
                        status = 'warning'
                
                # 检查1天K线（阈值2天）
                cursor.execute("""
                    SELECT MAX(created_at) as latest_created
                    FROM kline_data
                    WHERE timeframe = '1d' AND created_at IS NOT NULL
                """)
                kline_1d_result = cursor.fetchone()
                if kline_1d_result and kline_1d_result['latest_created']:
                    status_1d = _check_status_active(kline_1d_result['latest_created'], 172800)  # 2天阈值
                    if status_1d == 'active':
                        status = 'active'
                    elif status_1d == 'warning' and status != 'active':
                        status = 'warning'
            
            # 如果created_at字段为空，回退到使用timestamp字段（考虑时区问题）
            if status == 'inactive':
                latest_time = kline_result['latest_time'] if kline_result else None
                if latest_time:
                    # timestamp可能是UTC时间，需要加上8小时（UTC+8）来判断
                    # 或者直接使用timestamp，但阈值放宽
                    status = _check_status_active(latest_time, 1800)  # 放宽到30分钟阈值
            
            # 获取最新的created_at时间用于显示（更准确）
            cursor.execute("""
                SELECT MAX(created_at) as latest_created
                FROM kline_data
                WHERE created_at IS NOT NULL
            """)
            latest_created_result = cursor.fetchone()
            display_time = latest_created_result['latest_created'] if latest_created_result and latest_created_result['latest_created'] else latest_time
            
            collection_status.append({
                'type': 'K线数据',
                'category': 'market_data',
                'icon': 'bi-bar-chart',
                'description': '不同时间周期的K线数据',
                'count': kline_result['count'] if kline_result else 0,
                'latest_time': display_time.isoformat() if display_time else None,
                'oldest_time': kline_result['oldest_time'].isoformat() if kline_result and kline_result['oldest_time'] else None,
                'symbol_count': kline_result['symbol_count'] if kline_result else 0,
                'timeframe_count': kline_result['timeframe_count'] if kline_result else 0,
                'status': status
            })
        except Exception as e:
            logger.error(f"获取K线数据采集情况失败: {e}")
            import traceback
            traceback.print_exc()
            collection_status.append({
                'type': 'K线数据',
                'category': 'market_data',
                'icon': 'bi-bar-chart',
                'description': '不同时间周期的K线数据',
                'status': 'error',
                'error': str(e)
            })
        
        # 3. 合约数据采集情况
        try:
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    MAX(timestamp) as latest_time,
                    MIN(timestamp) as oldest_time
                FROM futures_open_interest
            """)
            futures_result = cursor.fetchone()
            collection_status.append({
                'type': '合约数据',
                'category': 'futures_data',
                'icon': 'bi-graph-up-arrow',
                'description': '合约持仓量、资金费率、多空比等数据',
                'count': futures_result['count'] if futures_result else 0,
                'latest_time': futures_result['latest_time'].isoformat() if futures_result and futures_result['latest_time'] else None,
                'oldest_time': futures_result['oldest_time'].isoformat() if futures_result and futures_result['oldest_time'] else None,
                'status': _check_status_active(futures_result['latest_time'], 1200) if futures_result and futures_result['latest_time'] else 'inactive'  # 合约数据每1分钟采集，阈值设为20分钟
            })
        except Exception as e:
            logger.error(f"获取合约数据采集情况失败: {e}")
            collection_status.append({
                'type': '合约数据',
                'category': 'futures_data',
                'icon': 'bi-graph-up-arrow',
                'description': '合约持仓量、资金费率、多空比等数据',
                'status': 'error',
                'error': str(e)
            })
        
        # 4. 新闻数据采集情况
        try:
            # 新闻数据表使用published_datetime字段，如果没有则使用created_at
            try:
                cursor.execute("""
                    SELECT 
                        COUNT(*) as count,
                        MAX(published_datetime) as latest_time,
                        MIN(published_datetime) as oldest_time,
                        COUNT(DISTINCT source) as source_count
                    FROM news_data
                """)
            except:
                # 如果published_datetime字段不存在或出错，使用created_at
                cursor.execute("""
                    SELECT 
                        COUNT(*) as count,
                        MAX(created_at) as latest_time,
                        MIN(created_at) as oldest_time,
                        COUNT(DISTINCT source) as source_count
                    FROM news_data
                """)
            news_result = cursor.fetchone()
            collection_status.append({
                'type': '新闻数据',
                'category': 'news_data',
                'icon': 'bi-newspaper',
                'description': '加密货币相关新闻',
                'count': news_result['count'] if news_result else 0,
                'latest_time': news_result['latest_time'].isoformat() if news_result and news_result['latest_time'] else None,
                'oldest_time': news_result['oldest_time'].isoformat() if news_result and news_result['oldest_time'] else None,
                'source_count': news_result['source_count'] if news_result else 0,
                'status': _check_status_active(news_result['latest_time'], 3600) if news_result and news_result['latest_time'] else 'inactive'
            })
        except Exception as e:
            logger.error(f"获取新闻数据采集情况失败: {e}")
            import traceback
            traceback.print_exc()
            collection_status.append({
                'type': '新闻数据',
                'category': 'news_data',
                'icon': 'bi-newspaper',
                'description': '加密货币相关新闻',
                'status': 'error',
                'error': str(e)
            })
        
        # 5. ETF数据情况
        try:
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    MAX(trade_date) as latest_time,
                    MIN(trade_date) as oldest_time,
                    COUNT(DISTINCT ticker) as etf_count
                FROM crypto_etf_flows
            """)
            etf_result = cursor.fetchone()
            
            # ETF数据是手动导入的，不是自动采集的
            # 状态判断：如果有数据且最近30天内有更新，显示为active；如果有数据但较旧，显示为warning；如果没有数据，显示为inactive
            status = 'inactive'
            if etf_result and etf_result['count'] > 0:
                if etf_result['latest_time']:
                    # 检查最新更新时间是否在30天内
                    latest_time = etf_result['latest_time']
                    # latest_time已经是date对象（从数据库查询返回）
                    if latest_time:
                        # 计算时间差（秒）
                        if isinstance(latest_time, date):
                            latest_datetime = datetime.combine(latest_time, datetime.min.time())
                        else:
                            latest_datetime = latest_time
                        time_diff = (datetime.now() - latest_datetime).total_seconds()
                        if time_diff < 2592000:  # 30天 = 2592000秒
                            status = 'active'
                        else:
                            status = 'warning'  # 数据较旧
                    else:
                        status = 'active'  # 有数据但没有更新时间字段
                else:
                    status = 'active'  # 有数据但没有更新时间字段
            
            collection_status.append({
                'type': 'ETF数据',
                'category': 'etf_data',
                'icon': 'bi-pie-chart',
                'description': '加密货币ETF资金流向数据（手动导入）',
                'count': etf_result['count'] if etf_result else 0,
                'latest_time': etf_result['latest_time'].isoformat() if etf_result and etf_result['latest_time'] else None,
                'oldest_time': etf_result['oldest_time'].isoformat() if etf_result and etf_result['oldest_time'] else None,
                'etf_count': etf_result['etf_count'] if etf_result else 0,
                'status': status
            })
        except Exception as e:
            logger.error(f"获取ETF数据情况失败: {e}")
            collection_status.append({
                'type': 'ETF数据',
                'category': 'etf_data',
                'icon': 'bi-pie-chart',
                'description': '加密货币ETF资金流向数据',
                'status': 'error',
                'error': str(e)
            })
        
        # 6. 企业金库数据情况
        try:
            # 企业金库数据包括两个表：持仓记录(purchases)和融资记录(financing)
            # 检查持仓记录表（主要数据）
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    MAX(updated_at) as latest_time,
                    MIN(created_at) as oldest_time,
                    COUNT(DISTINCT company_id) as company_count
                FROM corporate_treasury_purchases
            """)
            purchases_result = cursor.fetchone()
            
            # 检查融资记录表
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    MAX(updated_at) as latest_time,
                    MIN(created_at) as oldest_time,
                    COUNT(DISTINCT company_id) as company_count
                FROM corporate_treasury_financing
            """)
            financing_result = cursor.fetchone()
            
            # 合并两个表的数据
            total_count = (purchases_result['count'] if purchases_result else 0) + (financing_result['count'] if financing_result else 0)
            total_company_count = max(
                purchases_result['company_count'] if purchases_result else 0,
                financing_result['company_count'] if financing_result else 0
            )
            
            # 获取最新的更新时间（从两个表中取最新的）
            latest_time = None
            if purchases_result and purchases_result['latest_time']:
                latest_time = purchases_result['latest_time']
            if financing_result and financing_result['latest_time']:
                if not latest_time or financing_result['latest_time'] > latest_time:
                    latest_time = financing_result['latest_time']
            
            # 获取最早的创建时间
            oldest_time = None
            if purchases_result and purchases_result['oldest_time']:
                oldest_time = purchases_result['oldest_time']
            if financing_result and financing_result['oldest_time']:
                if not oldest_time or financing_result['oldest_time'] < oldest_time:
                    oldest_time = financing_result['oldest_time']
            
            # 企业金库数据是手动导入的，不是自动采集的
            # 状态判断：如果有数据且最近30天内有更新，显示为active；如果有数据但较旧，显示为warning；如果没有数据，显示为inactive
            status = 'inactive'
            if total_count > 0:
                if latest_time:
                    # 检查最新更新时间是否在30天内
                    # latest_time已经是datetime对象（从数据库查询返回）
                    # 计算时间差（秒）
                    time_diff = (datetime.now() - latest_time).total_seconds()
                    if time_diff < 2592000:  # 30天 = 2592000秒
                        status = 'active'
                    else:
                        status = 'warning'  # 数据较旧
                else:
                    status = 'active'  # 有数据但没有更新时间字段
            
            collection_status.append({
                'type': '企业金库数据',
                'category': 'treasury_data',
                'icon': 'bi-building',
                'description': '企业持仓和融资记录数据（手动导入）',
                'count': total_count,
                'latest_time': latest_time.isoformat() if latest_time else None,
                'oldest_time': oldest_time.isoformat() if oldest_time else None,
                'company_count': total_company_count,
                'status': status
            })
        except Exception as e:
            logger.error(f"获取企业金库数据情况失败: {e}")
            collection_status.append({
                'type': '企业金库数据',
                'category': 'treasury_data',
                'icon': 'bi-building',
                'description': '企业融资记录数据',
                'status': 'error',
                'error': str(e)
            })
        
        # 7. Hyperliquid聪明钱数据情况
        try:
            # 检查hyperliquid_wallet_trades表
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    MAX(trade_time) as latest_time,
                    MIN(trade_time) as oldest_time,
                    COUNT(DISTINCT address) as wallet_count,
                    COUNT(DISTINCT coin) as coin_count
                FROM hyperliquid_wallet_trades
            """)
            hyperliquid_trades_result = cursor.fetchone()
            
            # 检查hyperliquid_traders表
            cursor.execute("""
                SELECT COUNT(*) as trader_count
                FROM hyperliquid_traders
            """)
            hyperliquid_traders_result = cursor.fetchone()
            
            # 检查hyperliquid_monitored_wallets表
            cursor.execute("""
                SELECT COUNT(*) as monitored_count
                FROM hyperliquid_monitored_wallets
                WHERE is_monitoring = TRUE
            """)
            hyperliquid_monitored_result = cursor.fetchone()
            
            collection_status.append({
                'type': 'Hyperliquid聪明钱',
                'category': 'smart_money',
                'icon': 'bi-lightning-charge',
                'description': 'Hyperliquid平台聪明钱交易数据',
                'count': hyperliquid_trades_result['count'] if hyperliquid_trades_result else 0,
                'latest_time': hyperliquid_trades_result['latest_time'].isoformat() if hyperliquid_trades_result and hyperliquid_trades_result['latest_time'] else None,
                'oldest_time': hyperliquid_trades_result['oldest_time'].isoformat() if hyperliquid_trades_result and hyperliquid_trades_result['oldest_time'] else None,
                'wallet_count': hyperliquid_trades_result['wallet_count'] if hyperliquid_trades_result else 0,
                'trader_count': hyperliquid_traders_result['trader_count'] if hyperliquid_traders_result else 0,
                'monitored_count': hyperliquid_monitored_result['monitored_count'] if hyperliquid_monitored_result else 0,
                'coin_count': hyperliquid_trades_result['coin_count'] if hyperliquid_trades_result else 0,
                'status': _check_status_active(hyperliquid_trades_result['latest_time'], 86400) if hyperliquid_trades_result and hyperliquid_trades_result['latest_time'] else 'inactive'  # 24小时阈值
            })
        except Exception as e:
            logger.error(f"获取Hyperliquid聪明钱数据情况失败: {e}")
            import traceback
            traceback.print_exc()
            collection_status.append({
                'type': 'Hyperliquid聪明钱',
                'category': 'smart_money',
                'icon': 'bi-lightning-charge',
                'description': 'Hyperliquid平台聪明钱交易数据',
                'status': 'error',
                'error': str(e)
            })
        
        # 8. 链上聪明钱数据情况
        try:
            # 检查smart_money_transactions表
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    MAX(timestamp) as latest_time,
                    MIN(timestamp) as oldest_time,
                    COUNT(DISTINCT address) as wallet_count,
                    COUNT(DISTINCT token_symbol) as token_count,
                    COUNT(DISTINCT blockchain) as blockchain_count
                FROM smart_money_transactions
            """)
            smart_money_tx_result = cursor.fetchone()
            
            # 检查smart_money_signals表
            cursor.execute("""
                SELECT 
                    COUNT(*) as signal_count,
                    MAX(timestamp) as latest_signal_time,
                    COUNT(DISTINCT token_symbol) as signal_token_count
                FROM smart_money_signals
                WHERE is_active = TRUE
            """)
            smart_money_signal_result = cursor.fetchone()
            
            # 检查smart_money_addresses表
            cursor.execute("""
                SELECT COUNT(*) as address_count
                FROM smart_money_addresses
            """)
            smart_money_address_result = cursor.fetchone()
            
            collection_status.append({
                'type': '链上聪明钱',
                'category': 'smart_money',
                'icon': 'bi-wallet2',
                'description': '链上聪明钱交易和信号数据',
                'count': smart_money_tx_result['count'] if smart_money_tx_result else 0,
                'latest_time': smart_money_tx_result['latest_time'].isoformat() if smart_money_tx_result and smart_money_tx_result['latest_time'] else None,
                'oldest_time': smart_money_tx_result['oldest_time'].isoformat() if smart_money_tx_result and smart_money_tx_result['oldest_time'] else None,
                'wallet_count': smart_money_tx_result['wallet_count'] if smart_money_tx_result else 0,
                'address_count': smart_money_address_result['address_count'] if smart_money_address_result else 0,
                'token_count': smart_money_tx_result['token_count'] if smart_money_tx_result else 0,
                'blockchain_count': smart_money_tx_result['blockchain_count'] if smart_money_tx_result else 0,
                'signal_count': smart_money_signal_result['signal_count'] if smart_money_signal_result else 0,
                'latest_signal_time': smart_money_signal_result['latest_signal_time'].isoformat() if smart_money_signal_result and smart_money_signal_result['latest_signal_time'] else None,
                'status': _check_status_active(smart_money_tx_result['latest_time'], 86400) if smart_money_tx_result and smart_money_tx_result['latest_time'] else 'inactive'  # 24小时阈值
            })
        except Exception as e:
            logger.error(f"获取链上聪明钱数据情况失败: {e}")
            import traceback
            traceback.print_exc()
            collection_status.append({
                'type': '链上聪明钱',
                'category': 'smart_money',
                'icon': 'bi-wallet2',
                'description': '链上聪明钱交易和信号数据',
                'status': 'error',
                'error': str(e)
            })
        
        cursor.close()
        conn.close()
        
        return {
            'success': True,
            'data': collection_status
        }
        
    except Exception as e:
        logger.error(f"获取数据采集情况失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取数据采集情况失败: {str(e)}")


def _parse_date(date_str: str):
    """解析日期字符串，支持多种格式"""
    if not date_str:
        return None
    try:
        # 尝试多种日期格式
        for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d']:
            try:
                return datetime.strptime(str(date_str).strip(), fmt).date()
            except:
                continue
        return None
    except:
        return None

def _parse_number(num_str: str):
    """解析数字字符串，支持逗号、货币符号等"""
    if not num_str:
        return None
    try:
        # 移除逗号、货币符号、空格
        cleaned = str(num_str).replace(',', '').replace('$', '').replace(' ', '').strip()
        # 处理括号表示负数的情况
        is_negative = False
        if cleaned.startswith('(') and cleaned.endswith(')'):
            is_negative = True
            cleaned = cleaned[1:-1]
        if cleaned:
            value = float(cleaned)
            return -value if is_negative else value
        return None
    except:
        return None


@router.post("/import/etf")
async def import_etf_data(
    file: UploadFile = File(...),
    asset_type: str = Form("BTC")
):
    """
    导入ETF数据文件（CSV格式）
    
    支持多种字段名格式：
    - Date/date, Ticker/ticker
    - NetInflow/net_inflow, GrossInflow/gross_inflow, GrossOutflow/gross_outflow
    - AUM/aum, BTC_Holdings/BTCHoldings/btc_holdings, ETH_Holdings/ETHHoldings/eth_holdings
    - NAV/nav, Close/close_price, Volume/volume
    """
    try:
        if not file.filename.endswith('.csv'):
            raise HTTPException(status_code=400, detail="只支持CSV文件")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 读取文件内容
        content = await file.read()
        text_content = content.decode('utf-8-sig')  # 处理BOM
        reader = csv.DictReader(io.StringIO(text_content))
        
        imported = 0
        errors = []
        
        for row_num, row in enumerate(reader, start=2):  # 从第2行开始（第1行是表头）
            try:
                # 支持多种字段名格式
                trade_date_str = row.get('Date') or row.get('date') or row.get('trade_date') or row.get('TradeDate')
                ticker = (row.get('Ticker') or row.get('ticker') or row.get('TICKER')).strip().upper() if (row.get('Ticker') or row.get('ticker') or row.get('TICKER')) else None
                
                if not ticker or not trade_date_str:
                    errors.append(f"第{row_num}行: 缺少必要字段 (Ticker/Date)")
                    continue
                
                # 解析日期（支持多种格式）
                trade_date = _parse_date(trade_date_str)
                if not trade_date:
                    errors.append(f"第{row_num}行: 日期格式错误 (支持 YYYY-MM-DD, MM/DD/YYYY 等)")
                    continue
                
                # 查找ETF产品（获取ID和资产类型）
                cursor.execute("SELECT id, asset_type FROM crypto_etf_products WHERE ticker = %s", (ticker,))
                etf_result = cursor.fetchone()
                
                if not etf_result:
                    # 尝试查找所有ETH ETF，提供更详细的错误信息
                    if asset_type.upper() == 'ETH':
                        cursor.execute("SELECT ticker FROM crypto_etf_products WHERE asset_type = 'ETH'")
                        eth_tickers = [r['ticker'] for r in cursor.fetchall()]
                        errors.append(f"第{row_num}行: 未找到ETF产品 '{ticker}'。可用的ETH ETF: {', '.join(eth_tickers)}")
                    else:
                        errors.append(f"第{row_num}行: 未找到ETF产品 '{ticker}'，请先在系统中添加该ETF")
                    logger.warning(f"ETF导入: 第{row_num}行，未找到ticker '{ticker}' (资产类型: {asset_type})")
                    continue
                
                etf_id = etf_result['id']
                db_asset_type = etf_result['asset_type']
                
                # 验证资产类型是否匹配
                if asset_type.upper() != db_asset_type.upper():
                    errors.append(f"第{row_num}行: ETF '{ticker}' 的资产类型是 {db_asset_type}，但导入时选择的是 {asset_type}")
                    logger.warning(f"ETF导入: 第{row_num}行，资产类型不匹配 - ticker: {ticker}, 数据库: {db_asset_type}, 表单: {asset_type}")
                    continue
                
                # 解析数值字段（支持多种字段名）
                net_inflow = _parse_number(row.get('NetInflow') or row.get('net_inflow') or row.get('Net_Inflow'))
                gross_inflow = _parse_number(row.get('GrossInflow') or row.get('gross_inflow') or row.get('Gross_Inflow'))
                gross_outflow = _parse_number(row.get('GrossOutflow') or row.get('gross_outflow') or row.get('Gross_Outflow'))
                aum = _parse_number(row.get('AUM') or row.get('aum'))
                
                # 解析持仓量（根据ETF资产类型）
                btc_holdings = None
                eth_holdings = None
                if db_asset_type == 'BTC':
                    btc_holdings = _parse_number(
                        row.get('BTC_Holdings') or row.get('BTCHoldings') or 
                        row.get('btc_holdings') or row.get('Holdings') or row.get('holdings')
                    )
                elif db_asset_type == 'ETH':
                    eth_holdings = _parse_number(
                        row.get('ETH_Holdings') or row.get('ETHHoldings') or 
                        row.get('eth_holdings') or row.get('Holdings') or row.get('holdings')
                    )
                
                nav = _parse_number(row.get('NAV') or row.get('nav'))
                close_price = _parse_number(row.get('Close') or row.get('close_price') or row.get('ClosePrice'))
                volume = _parse_number(row.get('Volume') or row.get('volume'))
                data_source = (row.get('data_source') or row.get('DataSource') or 'manual').strip()
                
                # 插入或更新数据
                cursor.execute("""
                    INSERT INTO crypto_etf_flows
                    (etf_id, ticker, trade_date, net_inflow, gross_inflow, gross_outflow,
                     aum, btc_holdings, eth_holdings, nav, close_price, volume, data_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        net_inflow = VALUES(net_inflow),
                        gross_inflow = VALUES(gross_inflow),
                        gross_outflow = VALUES(gross_outflow),
                        aum = VALUES(aum),
                        btc_holdings = VALUES(btc_holdings),
                        eth_holdings = VALUES(eth_holdings),
                        nav = VALUES(nav),
                        close_price = VALUES(close_price),
                        volume = VALUES(volume),
                        data_source = VALUES(data_source),
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    etf_id, ticker, trade_date, net_inflow, gross_inflow, gross_outflow,
                    aum, btc_holdings, eth_holdings, nav, close_price, volume, data_source
                ))
                
                imported += 1
                
            except Exception as e:
                error_msg = f"第{row_num}行: {str(e)}"
                errors.append(error_msg)
                logger.error(f"导入ETF数据第{row_num}行失败: {e}")
                logger.error(f"  行数据: {row}")
                import traceback
                logger.error(traceback.format_exc())
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # 如果没有任何导入成功，且没有错误信息，可能是文件格式问题
        if imported == 0 and len(errors) == 0:
            errors.append("文件格式可能不正确，请检查CSV文件是否包含正确的列（Date, Ticker等）")
        
        return {
            'success': imported > 0,  # 只有成功导入至少一条记录才算成功
            'imported': imported,
            'errors': errors,
            'error_count': len(errors),
            'message': f'成功导入 {imported} 条记录，失败 {len(errors)} 条' if imported > 0 or len(errors) > 0 else '未导入任何记录，请检查文件格式'
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入ETF数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入ETF数据失败: {str(e)}")


def parse_bitcoin_treasuries_format(text: str):
    """
    解析 Bitcoin Treasuries 网站的复制格式
    
    参考 scripts/corporate_treasury/batch_import.py 的 parse_bitcoin_treasuries_format 函数
    
    示例格式：
    1
    Strategy
    🇺🇸	MSTR	640,808
    
    返回：[(公司名, 股票代码, BTC数量), ...]
    """
    companies = []
    lines = text.strip().split('\n')
    
    logger.debug(f"开始解析，共 {len(lines)} 行")
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # 跳过注释行和空行
        if line.startswith('#') or not line:
            i += 1
            continue
        
        # 跳过排名数字
        if line.isdigit():
            i += 1
            continue
        
        # 如果是公司名（不包含制表符，且不是国旗行）
        # 注意：需要排除以各种国旗开头的行
        if '\t' not in line and line:
            # 检查是否是国旗行（可能包含各种国旗emoji）
            is_flag_line = False
            for flag in ['🇺🇸', '🇯🇵', '🇨🇦', '🇬🇧', '🇩🇪', '🇫🇷', '🇦🇺', '🇨🇭', '🇸🇬', '🇰🇷']:
                if line.startswith(flag):
                    is_flag_line = True
                    break
            
            if not is_flag_line:
                company_name = line
                
                # 下一行应该是国旗、股票代码和数量
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    
                    # 解析格式：🇺🇸	MSTR	640,808
                    parts = next_line.split('\t')
                    
                    if len(parts) >= 3:
                        ticker = parts[1].strip()
                        btc_amount_str = parts[2].strip().replace(',', '')
                        
                        try:
                            btc_amount = float(btc_amount_str)
                            companies.append((company_name, ticker, btc_amount))
                            logger.debug(f"解析成功: {company_name} ({ticker}) - {btc_amount:,.0f}")
                        except ValueError as e:
                            logger.warning(f"跳过无效数量: {company_name} - {parts[2]} (错误: {e})")
                    else:
                        logger.warning(f"跳过格式不正确的行: {company_name} 的下一行格式错误: {next_line}")
                    
                    i += 2  # 跳过下一行
                    continue
        
        i += 1
    
    logger.info(f"解析完成，共解析到 {len(companies)} 家公司")
    return companies


@router.post("/import/corporate-treasury")
async def import_corporate_treasury_data(
    file: UploadFile = File(...),
    asset_type: str = Form("BTC"),
    data_date: str = Form(None)
):
    """
    导入企业金库数据文件
    
    支持两种格式：
    1. 文本格式（.txt）：Bitcoin Treasuries 网站格式，导入持仓数据到 corporate_treasury_purchases
    2. CSV格式（.csv）：融资数据，导入到 corporate_treasury_financing
    """
    try:
        conn = get_db_connection()
        # 使用字典游标，与 get_db_connection() 返回的 DictCursor 一致
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        
        # 读取文件内容
        content = await file.read()
        text_content = content.decode('utf-8')
        
        # 判断文件类型
        is_text_format = file.filename.endswith('.txt')
        
        imported = 0
        updated = 0
        skipped = 0
        errors = []
        
        if is_text_format:
            # 文本格式：Bitcoin Treasuries 格式，导入持仓数据
            # 解析数据日期
            purchase_date = None
            if data_date:
                try:
                    purchase_date = datetime.strptime(data_date, '%Y-%m-%d').date()
                except:
                    raise HTTPException(status_code=400, detail="数据日期格式错误 (应为 YYYY-MM-DD)")
            else:
                purchase_date = datetime.now().date()
            
            # 解析文本格式
            companies = parse_bitcoin_treasuries_format(text_content)
            
            logger.info(f"解析到 {len(companies)} 家公司")
            if companies:
                logger.info(f"前3家公司: {companies[:3]}")
            
            if not companies:
                raise HTTPException(status_code=400, detail="无法解析文本格式，请检查文件格式是否正确。确保文件格式为：排名数字、公司名、国旗+股票代码+持仓量（用制表符分隔）")
            
            # 导入持仓数据（参考 batch_import.py 的 import_companies 逻辑）
            for company_name, ticker, holdings in companies:
                try:
                    # 1. 查找或创建公司
                    cursor.execute("""
                        SELECT id FROM corporate_treasury_companies
                        WHERE company_name = %s OR ticker_symbol = %s
                        LIMIT 1
                    """, (company_name, ticker))
                    company_result = cursor.fetchone()
                    
                    if not company_result:
                        # 创建新公司
                        cursor.execute("""
                            INSERT INTO corporate_treasury_companies
                            (company_name, ticker_symbol, category, is_active)
                            VALUES (%s, %s, %s, 1)
                        """, (company_name, ticker, 'holding'))
                        company_id = cursor.lastrowid
                        logger.info(f"新增公司: {company_name} ({ticker})")
                    else:
                        # 使用字典游标，通过键访问
                        company_id = company_result['id'] if isinstance(company_result, dict) else company_result[0]
                    
                    # 2. 检查是否已有该日期的记录
                    cursor.execute("""
                        SELECT id, cumulative_holdings FROM corporate_treasury_purchases
                        WHERE company_id = %s AND purchase_date = %s AND asset_type = %s
                    """, (company_id, purchase_date, asset_type))
                    existing = cursor.fetchone()
                    
                    if existing:
                        # 使用字典游标，通过键访问
                        existing_id = existing['id'] if isinstance(existing, dict) else existing[0]
                        existing_holdings = existing['cumulative_holdings'] if isinstance(existing, dict) else existing[1]
                        # 如果持仓量相同，跳过
                        if existing_holdings and float(existing_holdings) == holdings:
                            logger.debug(f"跳过（已存在）: {company_name} - {holdings:,.0f} {asset_type}")
                            skipped += 1
                            continue
                        
                        # 更新记录
                        cursor.execute("""
                            UPDATE corporate_treasury_purchases
                            SET cumulative_holdings = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (holdings, existing_id))
                        logger.info(f"更新: {company_name} ({ticker}) - {holdings:,.0f} {asset_type}")
                        updated += 1
                    else:
                        # 3. 获取上一次的持仓量（计算购买数量）
                        cursor.execute("""
                            SELECT cumulative_holdings FROM corporate_treasury_purchases
                            WHERE company_id = %s AND asset_type = %s
                            ORDER BY purchase_date DESC
                            LIMIT 1
                        """, (company_id, asset_type))
                        last_record = cursor.fetchone()
                        # 使用字典游标，通过键访问
                        if last_record:
                            if isinstance(last_record, dict):
                                last_holdings = float(last_record.get('cumulative_holdings', 0) or 0)
                            else:
                                last_holdings = float(last_record[0] or 0)
                        else:
                            last_holdings = 0
                        
                        # 计算购买数量
                        quantity = holdings - last_holdings
                        
                        # 插入新记录
                        cursor.execute("""
                            INSERT INTO corporate_treasury_purchases
                            (company_id, purchase_date, asset_type, quantity, cumulative_holdings, data_source)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (company_id, purchase_date, asset_type, quantity, holdings, 'manual'))
                        logger.info(f"新增: {company_name} ({ticker}) - {quantity:+,.0f} {asset_type} → {holdings:,.0f}")
                    imported += 1
                    
                except Exception as e:
                    error_msg = f"{company_name} ({ticker}): {str(e)}"
                    errors.append(error_msg)
                    logger.error(f"导入企业金库持仓数据失败: {error_msg}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            # 构建详细的消息
            total_processed = imported + updated + skipped
            message_parts = []
            if imported > 0:
                message_parts.append(f"新增 {imported} 条")
            if updated > 0:
                message_parts.append(f"更新 {updated} 条")
            if skipped > 0:
                message_parts.append(f"跳过 {skipped} 条（已存在且持仓量相同）")
            if errors:
                message_parts.append(f"失败 {len(errors)} 条")
            
            message = f"共处理 {total_processed} 条记录：" + "，".join(message_parts) if message_parts else f"共处理 {total_processed} 条记录"
            
        else:
            # CSV格式：融资数据
            if not file.filename.endswith('.csv'):
                raise HTTPException(status_code=400, detail="只支持 .txt 或 .csv 文件")
            
            # 解析数据日期
            financing_date = None
            if data_date:
                try:
                    financing_date = datetime.strptime(data_date, '%Y-%m-%d').date()
                except:
                    raise HTTPException(status_code=400, detail="数据日期格式错误 (应为 YYYY-MM-DD)")
            else:
                financing_date = datetime.now().date()
            
            # 读取CSV内容
            text_content_bom = content.decode('utf-8-sig')  # 处理BOM
            reader = csv.DictReader(io.StringIO(text_content_bom))
            
            for row_num, row in enumerate(reader, start=2):
                try:
                    company_name = row.get('company_name', '').strip()
                    ticker = (row.get('ticker') or row.get('ticker_symbol') or '').strip().upper()
                    
                    if not company_name:
                        errors.append(f"第{row_num}行: 缺少公司名称")
                        continue
                    
                    # 查找或创建公司
                    cursor.execute("""
                        SELECT id FROM corporate_treasury_companies
                        WHERE company_name = %s OR ticker_symbol = %s
                        LIMIT 1
                    """, (company_name, ticker))
                    company_result = cursor.fetchone()
                    
                    if not company_result:
                        cursor.execute("""
                            INSERT INTO corporate_treasury_companies
                            (company_name, ticker_symbol, category, is_active)
                            VALUES (%s, %s, %s, 1)
                        """, (company_name, ticker, 'holding'))
                        company_id = cursor.lastrowid
                    else:
                        company_id = company_result['id']
                    
                    # 解析融资日期
                    row_financing_date = financing_date
                    financing_date_str = row.get('financing_date', '').strip()
                    if financing_date_str:
                        parsed_date = _parse_date(financing_date_str)
                        if parsed_date:
                            row_financing_date = parsed_date
                    
                    # 解析数值字段
                    financing_type = (row.get('financing_type') or 'equity').strip() or 'equity'
                    amount = _parse_number(row.get('amount'))
                    purpose = row.get('purpose', '').strip() or None
                    announcement_url = row.get('announcement_url', '').strip() or None
                    notes = row.get('notes', '').strip() or None
                    data_source = (row.get('data_source') or 'manual').strip()
                    
                    # 插入融资记录
                    cursor.execute("""
                        INSERT INTO corporate_treasury_financing
                        (company_id, financing_date, financing_type, amount, purpose, announcement_url, notes, data_source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (company_id, row_financing_date, financing_type, amount, purpose, announcement_url, notes, data_source))
                    
                    imported += 1
                    
                except Exception as e:
                    errors.append(f"第{row_num}行: {str(e)}")
                    logger.error(f"导入企业金库融资数据第{row_num}行失败: {e}")
            
            message = f'成功导入 {imported} 条融资记录，失败 {len(errors)} 条'
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return {
            'success': True,
            'imported': imported,
            'updated': updated if is_text_format else 0,
            'skipped': skipped if is_text_format else 0,
            'errors': errors,
            'error_count': len(errors),
            'message': message
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入企业金库数据失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导入企业金库数据失败: {str(e)}")


@router.get("/template/etf")
async def download_etf_template(asset_type: str = "BTC"):
    """
    下载ETF数据导入模板CSV文件
    
    支持BTC和ETH两种资产类型
    
    Args:
        asset_type: 资产类型，BTC 或 ETH，默认为 BTC
    
    参考格式：
    - BTC: Date, Ticker, NetInflow, BTC_Holdings
    - ETH: Date, Ticker, NetInflow, ETH_Holdings
    """
    try:
        asset_type = asset_type.upper()
        if asset_type not in ['BTC', 'ETH']:
            asset_type = 'BTC'
        
        # 创建模板内容
        # NetInflow 是美元金额（不是百万美元），Holdings 是持仓量
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        if asset_type == 'BTC':
            # BTC ETF 模板
            holdings_column = "BTC_Holdings"
            template_content = f"Date,Ticker,NetInflow,{holdings_column}\n"
            # 常见的BTC ETF tickers
            btc_tickers = ['IBIT', 'FBTC', 'BITB', 'ARKB', 'BTCO', 'EZBC', 'BRRR', 'HODL', 'BTCW', 'GBTC', 'DEFI']
            for ticker in btc_tickers:
                template_content += f"{yesterday},{ticker},0,0\n"
            filename = "etf_btc_import_template.csv"
        else:
            # ETH ETF 模板
            holdings_column = "ETH_Holdings"
            template_content = f"Date,Ticker,NetInflow,{holdings_column}\n"
            # 常见的ETH ETF tickers（来自数据库schema）
            eth_tickers = ['ETHA', 'FETH', 'ETHW', 'ETHV', 'QETH', 'EZET', 'CETH', 'ETHE', 'ETH']
            for ticker in eth_tickers:
                template_content += f"{yesterday},{ticker},0,0\n"
            filename = "etf_eth_import_template.csv"
        
        # 创建临时文件
        template_path = Path(__file__).parent.parent.parent / "templates" / filename
        template_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(template_path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(template_content)
        
        return FileResponse(
            path=str(template_path),
            filename=filename,
            media_type="text/csv"
        )
        
    except Exception as e:
        logger.error(f"生成ETF模板失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成ETF模板失败: {str(e)}")


@router.get("/template/corporate-treasury")
async def download_corporate_treasury_template():
    """
    下载企业金库持仓数据导入模板（文本格式）
    
    参考 scripts/corporate_treasury/import_template.txt
    格式：从 Bitcoin Treasuries 网站复制的格式
    """
    try:
        # 创建模板内容（参考 scripts/corporate_treasury/import_template.txt）
        template_content = """# 企业金库批量导入模板
# 从 Bitcoin Treasuries 网站复制的格式示例
# 使用方法：复制以下内容，或从 https://bitcointreasuries.net/ 复制最新数据

1
Strategy
🇺🇸	MSTR	640,808
2
MARA Holdings, Inc.
🇺🇸	MARA	53,250
3
XXI
🇺🇸	CEP	43,514
4
Metaplanet Inc.
🇯🇵	MTPLF	30,823
5
Bitcoin Standard Treasury Company
🇺🇸	CEPO	30,021
6
Riot Platforms, Inc.
🇺🇸	RIOT	19,287
7
Tesla, Inc.
🇺🇸	TSLA	11,509
8
Coinbase Global, Inc.
🇺🇸	COIN	11,776
9
Block, Inc.
🇺🇸	SQ	8,692
10
Galaxy Digital Holdings Ltd
🇺🇸	GLXY	6,894
"""
        
        # 创建临时文件
        template_path = Path(__file__).parent.parent.parent / "templates" / "corporate_treasury_import_template.txt"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(template_path, 'w', encoding='utf-8', newline='') as f:
            f.write(template_content)
        
        return FileResponse(
            path=str(template_path),
            filename="corporate_treasury_import_template.txt",
            media_type="text/plain"
        )
        
    except Exception as e:
        logger.error(f"生成企业金库模板失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成企业金库模板失败: {str(e)}")


@router.get("/template/corporate-treasury-financing")
async def download_corporate_treasury_financing_template():
    """
    下载企业金库融资数据导入模板CSV文件
    
    用于导入 corporate_treasury_financing 表
    """
    try:
        # 创建模板内容（融资数据CSV格式）
        template_content = "company_name,ticker,financing_date,financing_type,amount,purpose,announcement_url,notes,data_source\n"
        template_content += "MicroStrategy,MSTR,2025-01-27,equity,1000000,购买BTC,https://example.com/announcement1,融资用于购买BTC,manual\n"
        template_content += "Tesla,TSLA,2025-01-27,convertible_note,500000,购买BTC,https://example.com/announcement2,可转换债券,manual\n"
        template_content += "Block,SQ,2025-01-27,loan,300000,购买BTC,https://example.com/announcement3,贷款融资,manual\n"
        template_content += "Coinbase,COIN,2025-01-27,atm,200000,购买BTC,https://example.com/announcement4,ATM融资,manual\n"
        
        # 创建临时文件
        template_path = Path(__file__).parent.parent.parent / "templates" / "corporate_treasury_financing_import_template.csv"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(template_path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(template_content)
        
        return FileResponse(
            path=str(template_path),
            filename="corporate_treasury_financing_import_template.csv",
            media_type="text/csv"
        )
        
    except Exception as e:
        logger.error(f"生成企业金库融资模板失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成企业金库融资模板失败: {str(e)}")


async def _execute_collection_task(task_id: str, request_data: Dict):
    """
    后台执行数据采集任务
    
    Args:
        task_id: 任务ID
        request_data: 采集请求数据
    """
    try:
        task_manager.set_task_status(task_id, TaskStatus.RUNNING)
        
        symbols = request_data.get('symbols', [])
        data_type = request_data.get('data_type', 'price')
        start_time_str = request_data.get('start_time')
        end_time_str = request_data.get('end_time')
        timeframes = request_data.get('timeframes', None)
        if not timeframes:
            timeframe = request_data.get('timeframe', '1h')
            timeframes = [timeframe] if data_type == 'kline' else []
        collect_futures = request_data.get('collect_futures', False)
        save_to_config = request_data.get('save_to_config', False)
        
        # 解析时间（与 /collect 端点相同的逻辑）
        # 前端发送的时间可能是本地时间（UTC+8）或UTC时间
        # 对于Binance数据采集，需要将本地时间转换为UTC时间
        try:
            if '+08:00' in start_time_str:
                # 本地时间（UTC+8），需要转换为UTC时间
                start_time = datetime.fromisoformat(start_time_str)
                start_time = start_time.replace(tzinfo=None) - timedelta(hours=8)
            elif 'Z' in start_time_str or '+00:00' in start_time_str:
                # UTC时间
                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                if start_time.tzinfo:
                    start_time = start_time.replace(tzinfo=None)
            else:
                # 没有时区信息，假设是本地时间（UTC+8）
                start_time = datetime.fromisoformat(start_time_str)
                start_time = start_time - timedelta(hours=8)
            
            if '+08:00' in end_time_str:
                # 本地时间（UTC+8），需要转换为UTC时间
                end_time = datetime.fromisoformat(end_time_str)
                end_time = end_time.replace(tzinfo=None) - timedelta(hours=8)
            elif 'Z' in end_time_str or '+00:00' in end_time_str:
                # UTC时间
                end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                if end_time.tzinfo:
                    end_time = end_time.replace(tzinfo=None)
            else:
                # 没有时区信息，假设是本地时间（UTC+8）
                end_time = datetime.fromisoformat(end_time_str)
                end_time = end_time - timedelta(hours=8)
        except Exception as e:
            logger.error(f"解析时间失败: {e}")
            raise
        
        # 计算总步骤数和预估数据量
        collect_price = data_type in ['price', 'both']
        collect_kline = data_type in ['kline', 'both']
        total_steps = len(symbols) * (
            (1 if collect_price else 0) + 
            (len(timeframes) if collect_kline else 0) + 
            (len(timeframes) if collect_futures else 0)
        )
        
        # 估算总数据量（用于更准确的进度计算）
        time_delta = end_time - start_time
        days = time_delta.total_seconds() / 86400
        estimated_total_records = 0
        
        # 注意：历史数据采集时，我们是从API获取K线数据，频率是固定的
        # price_interval 配置只影响实时采集，不影响历史数据采集
        # 历史价格数据使用1m K线，所以是每分钟1条
        
        if collect_price:
            # 价格数据：历史采集使用1m K线数据，每分钟1条
            estimated_total_records += len(symbols) * int(days * 24 * 60)
        
        if collect_kline:
            # K线数据：根据时间周期估算（K线数据频率是固定的）
            timeframe_minutes = {
                '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                '1h': 60, '4h': 240, '1d': 1440, '1w': 10080
            }
            for tf in timeframes:
                minutes = timeframe_minutes.get(tf, 60)
                estimated_total_records += len(symbols) * int(days * 24 * 60 / minutes)
        
        if collect_futures:
            # 合约K线数据：同样根据时间周期估算
            for tf in timeframes:
                minutes = timeframe_minutes.get(tf, 60)
                estimated_total_records += len(symbols) * int(days * 24 * 60 / minutes)
        
        task = task_manager.get_task(task_id)
        if task:
            task.total_steps = total_steps
            # 存储预估数据量，用于进度计算
            task.estimated_total_records = estimated_total_records
        
        # 更新初始状态
        task_manager.update_task_progress(
            task_id,
            current_step=f"准备采集 {len(symbols)} 个交易对，预估 {estimated_total_records:,} 条数据...",
            progress=0
        )
        
        # 导入采集器
        from app.collectors.price_collector import MultiExchangeCollector
        from app.collectors.binance_futures_collector import BinanceFuturesCollector
        from app.collectors.gate_collector import GateCollector
        
        # 加载配置
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 初始化采集器
        collector = MultiExchangeCollector(config)
        
        # 初始化合约采集器
        binance_futures_collector = None
        gate_collector = None
        if collect_futures:
            try:
                binance_config = config.get('exchanges', {}).get('binance', {})
                binance_futures_collector = BinanceFuturesCollector(binance_config)
            except Exception as e:
                logger.warning(f"Binance合约数据采集器初始化失败: {e}")
        
        # 初始化Gate.io采集器（用于HYPE/USDT）
        try:
            gate_config = config.get('exchanges', {}).get('gate', {})
            if gate_config.get('enabled', False):
                gate_collector = GateCollector(gate_config)
        except Exception as e:
            logger.warning(f"Gate.io采集器初始化失败: {e}")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        total_saved = 0
        errors = []
        completed_steps = 0
        
        # 遍历每个交易对
        for symbol_idx, symbol in enumerate(symbols):
            try:
                symbol = symbol.strip().upper()  # 统一转换为大写
                if not symbol:
                    continue
                
                # 确保格式正确（移除多余空格，统一格式）
                symbol = symbol.replace(' ', '').replace('_', '/')  # 支持 BTC_USDT 格式
                if '/' not in symbol and symbol.endswith('USDT'):
                    # 如果已经是 BTCUSDT 格式，转换为 BTC/USDT
                    base = symbol[:-4]  # 移除 USDT
                    symbol = f"{base}/USDT"
                
                # 判断是否使用Gate.io采集（仅HYPE/USDT）
                use_gate = (symbol.upper() == 'HYPE/USDT')
                
                task_manager.update_task_progress(
                    task_id,
                    current_step=f"正在采集 {symbol}...",
                    progress=(symbol_idx / len(symbols)) * 100
                )
                
                if collect_price:
                    task_manager.update_task_progress(
                        task_id,
                        current_step=f"正在从API获取 {symbol} 价格数据..."
                    )
                    
                    if use_gate and gate_collector:
                        # HYPE/USDT 从Gate.io采集
                        days = int((end_time - start_time).total_seconds() / 86400) + 1
                        since = int(start_time.timestamp())
                        df = await gate_collector.fetch_ohlcv(
                            symbol=symbol,
                            timeframe='1m',
                            limit=1000,
                            since=since
                        )
                        # 如果数据不够，需要分批获取
                        if df is not None and len(df) > 0:
                            all_data = [df]
                            last_timestamp = df['timestamp'].iloc[-1]
                            current_since = int(last_timestamp.timestamp()) + 1
                            while current_since < int(end_time.timestamp()):
                                next_df = await gate_collector.fetch_ohlcv(
                                    symbol=symbol,
                                    timeframe='1m',
                                    limit=1000,
                                    since=current_since
                                )
                                if next_df is None or len(next_df) == 0:
                                    break
                                all_data.append(next_df)
                                last_timestamp = next_df['timestamp'].iloc[-1]
                                current_since = int(last_timestamp.timestamp()) + 1
                                if len(next_df) < 1000:
                                    break
                                await asyncio.sleep(0.5)
                            if len(all_data) > 1:
                                df = pd.concat(all_data, ignore_index=True)
                                df = df.drop_duplicates(subset=['timestamp'])
                                df = df.sort_values('timestamp').reset_index(drop=True)
                    else:
                        # 其他交易对从Binance采集
                        df = await collector.fetch_historical_data(
                            symbol=symbol,
                            timeframe='1m',
                            days=int((end_time - start_time).total_seconds() / 86400) + 1,
                            exchange='binance' if not use_gate else None
                        )
                    
                    if df is not None and len(df) > 0:
                        task_manager.update_task_progress(
                            task_id,
                            current_step=f"✓ 已获取 {symbol} 价格数据 {len(df)} 条（原始），正在过滤..."
                        )
                    
                    if df is not None and len(df) > 0:
                        df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                        task_manager.update_task_progress(
                            task_id,
                            current_step=f"✓ 过滤后剩余 {len(df)} 条数据，正在保存..."
                        )
                        
                        if len(df) == 0:
                            task_manager.update_task_progress(
                                task_id,
                                current_step=f"⚠️ {symbol}: 过滤后无数据，可能时间范围不匹配"
                            )
                            errors.append(f"{symbol}: 价格数据时间范围不匹配")
                            continue
                        
                        saved_count = 0
                        total_rows = len(df)
                        update_interval = max(1, total_rows // 20)  # 每5%更新一次进度
                        
                        for idx, row_tuple in enumerate(df.iterrows()):
                            try:
                                _, row = row_tuple
                                created_at = datetime.now()
                                cursor.execute("""
                                    INSERT INTO price_data
                                    (symbol, exchange, timestamp, price, open_price, high_price, low_price, close_price, volume, quote_volume, bid_price, ask_price, change_24h, created_at)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON DUPLICATE KEY UPDATE
                                        price = VALUES(price),
                                        open_price = VALUES(open_price),
                                        high_price = VALUES(high_price),
                                        low_price = VALUES(low_price),
                                        close_price = VALUES(close_price),
                                        volume = VALUES(volume),
                                        quote_volume = VALUES(quote_volume),
                                        bid_price = VALUES(bid_price),
                                        ask_price = VALUES(ask_price),
                                        change_24h = VALUES(change_24h),
                                        created_at = VALUES(created_at)
                                """, (
                                    symbol, 'gate' if use_gate else 'binance', row['timestamp'],
                                    float(row['close']), float(row['open']),
                                    float(row['high']), float(row['low']),
                                    float(row['close']), float(row['volume']),
                                    float(row.get('quote_volume', 0)), 0, 0, 0, created_at
                                ))
                                if cursor.rowcount > 0:
                                    saved_count += 1
                                
                                # 实时更新进度（每保存一定数量后更新）
                                if saved_count % update_interval == 0 or saved_count == total_rows:
                                    total_saved_temp = total_saved + saved_count
                                    task = task_manager.get_task(task_id)
                                    if task and task.estimated_total_records > 0:
                                        # 基于实际保存的数据量计算进度
                                        progress = min(95, (total_saved_temp / task.estimated_total_records) * 100)
                                    else:
                                        # 回退到基于步骤的进度计算
                                        progress = min(95, (completed_steps / total_steps) * 100) if total_steps > 0 else 0
                                    
                                    task_manager.update_task_progress(
                                        task_id,
                                        current_step=f"正在保存 {symbol} 价格数据 ({saved_count}/{total_rows})...",
                                        total_saved=total_saved_temp,
                                        progress=progress
                                    )
                            except Exception as e:
                                logger.error(f"保存价格数据失败: {e}")
                                continue
                        
                        total_saved += saved_count
                        completed_steps += 1
                        task_manager.update_task_progress(
                            task_id,
                            completed_steps=completed_steps,
                            total_saved=total_saved,
                            current_step=f"✓ {symbol} 价格数据采集完成，保存 {saved_count} 条"
                        )
                    else:
                        errors.append(f"{symbol}: 未获取到价格数据")
                
                if collect_kline:
                    if not timeframes:
                        timeframes = ['1m', '5m', '15m', '1h', '1d']
                    
                    symbol_saved = 0
                    for timeframe in timeframes:
                        try:
                            task_manager.update_task_progress(
                                task_id,
                                current_step=f"正在从API获取 {symbol} {timeframe} K线数据..."
                            )
                            
                            if use_gate and gate_collector:
                                # HYPE/USDT 从Gate.io采集
                                days = int((end_time - start_time).total_seconds() / 86400) + 1
                                since = int(start_time.timestamp())
                                df = await gate_collector.fetch_ohlcv(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    limit=1000,
                                    since=since
                                )
                                # 如果数据不够，需要分批获取
                                if df is not None and len(df) > 0:
                                    all_data = [df]
                                    last_timestamp = df['timestamp'].iloc[-1]
                                    current_since = int(last_timestamp.timestamp()) + 1
                                    while current_since < int(end_time.timestamp()):
                                        next_df = await gate_collector.fetch_ohlcv(
                                            symbol=symbol,
                                            timeframe=timeframe,
                                            limit=1000,
                                            since=current_since
                                        )
                                        if next_df is None or len(next_df) == 0:
                                            break
                                        all_data.append(next_df)
                                        last_timestamp = next_df['timestamp'].iloc[-1]
                                        current_since = int(last_timestamp.timestamp()) + 1
                                        if len(next_df) < 1000:
                                            break
                                        await asyncio.sleep(0.5)
                                    if len(all_data) > 1:
                                        df = pd.concat(all_data, ignore_index=True)
                                        df = df.drop_duplicates(subset=['timestamp'])
                                        df = df.sort_values('timestamp').reset_index(drop=True)
                            else:
                                # 其他交易对从Binance采集
                                df = await collector.fetch_historical_data(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    days=int((end_time - start_time).total_seconds() / 86400) + 1,
                                    exchange='binance' if not use_gate else None
                                )
                            
                            if df is not None and len(df) > 0:
                                task_manager.update_task_progress(
                                    task_id,
                                    current_step=f"✓ 已获取 {symbol} {timeframe} K线数据 {len(df)} 条（原始），正在过滤..."
                                )
                            
                            if df is not None and len(df) > 0:
                                df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                                task_manager.update_task_progress(
                                    task_id,
                                    current_step=f"✓ 过滤后剩余 {len(df)} 条数据，正在保存..."
                                )
                                
                                if len(df) == 0:
                                    task_manager.update_task_progress(
                                        task_id,
                                        current_step=f"⚠️ {symbol} {timeframe}: 过滤后无数据，可能时间范围不匹配"
                                    )
                                    errors.append(f"{symbol} {timeframe}: K线数据时间范围不匹配")
                                    continue
                                
                                timeframe_saved = 0
                                total_rows = len(df)
                                update_interval = max(1, total_rows // 20)  # 每5%更新一次进度
                                
                                for idx, row_tuple in enumerate(df.iterrows()):
                                    try:
                                        _, row = row_tuple
                                        timestamp = row['timestamp']
                                        if isinstance(timestamp, pd.Timestamp):
                                            timestamp_dt = timestamp.to_pydatetime()
                                            open_time_ms = int(timestamp.timestamp() * 1000)
                                        elif isinstance(timestamp, datetime):
                                            timestamp_dt = timestamp
                                            open_time_ms = int(timestamp.timestamp() * 1000)
                                        else:
                                            timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                            open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                        
                                        timeframe_minutes = {
                                            '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                            '1h': 60, '4h': 240, '1d': 1440
                                        }.get(timeframe, 60)
                                        close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                        created_at = datetime.now()
                                        
                                        cursor.execute("""
                                            INSERT INTO kline_data
                                            (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                            ON DUPLICATE KEY UPDATE
                                                open_price = VALUES(open_price),
                                                high_price = VALUES(high_price),
                                                low_price = VALUES(low_price),
                                                close_price = VALUES(close_price),
                                                volume = VALUES(volume),
                                                quote_volume = VALUES(quote_volume),
                                                created_at = VALUES(created_at)
                                        """, (
                                            symbol, 'gate' if use_gate else 'binance', timeframe, open_time_ms, close_time_ms,
                                            timestamp_dt, float(row['open']), float(row['high']),
                                            float(row['low']), float(row['close']), float(row['volume']),
                                            float(row.get('quote_volume', 0)), created_at
                                        ))
                                        if cursor.rowcount > 0:
                                            timeframe_saved += 1
                                        
                                        # 实时更新进度（每保存一定数量后更新）
                                        if timeframe_saved % update_interval == 0 or timeframe_saved == total_rows:
                                            total_saved_temp = total_saved + symbol_saved + timeframe_saved
                                            task = task_manager.get_task(task_id)
                                            if task and task.estimated_total_records > 0:
                                                # 基于实际保存的数据量计算进度
                                                progress = min(95, (total_saved_temp / task.estimated_total_records) * 100)
                                            else:
                                                # 回退到基于步骤的进度计算
                                                progress = min(95, (completed_steps / total_steps) * 100) if total_steps > 0 else 0
                                            
                                            task_manager.update_task_progress(
                                                task_id,
                                                current_step=f"正在保存 {symbol} {timeframe} K线数据 ({timeframe_saved}/{total_rows})...",
                                                total_saved=total_saved_temp,
                                                progress=progress
                                            )
                                    except Exception as e:
                                        logger.error(f"保存K线数据失败: {e}")
                                        continue
                                
                                symbol_saved += timeframe_saved
                                completed_steps += 1
                                task_manager.update_task_progress(
                                    task_id,
                                    completed_steps=completed_steps,
                                    total_saved=total_saved + symbol_saved,
                                    current_step=f"✓ {symbol} {timeframe} K线数据采集完成，保存 {timeframe_saved} 条"
                                )
                        except Exception as e:
                            error_msg = str(e)
                            # 如果是无效交易对，提供更详细的错误信息
                            if 'Invalid symbol' in error_msg or '-1121' in error_msg:
                                error_msg = f"{symbol} {timeframe}: 交易对不存在或格式错误（币安可能不支持此交易对）"
                            else:
                                error_msg = f"{symbol} {timeframe}: {error_msg}"
                            errors.append(error_msg)
                            logger.error(f"采集 {symbol} {timeframe} K线数据失败: {e}")
                    
                    total_saved += symbol_saved
                    if symbol_saved == 0:
                        errors.append(f"{symbol}: 所有周期均未获取到K线数据")
                
                # 采集合约数据
                if collect_futures:
                    if use_gate and gate_collector:
                        # HYPE/USDT 从Gate.io采集合约数据
                        try:
                            task_manager.update_task_progress(
                                task_id,
                                current_step=f"正在采集 {symbol} 合约数据（Gate.io）..."
                            )
                            futures_saved = 0
                            
                            if not timeframes:
                                timeframes = ['1m', '5m', '15m', '1h', '1d']
                            
                            for timeframe in timeframes:
                                try:
                                    task_manager.update_task_progress(
                                        task_id,
                                        current_step=f"正在从API获取 {symbol} 合约 {timeframe} K线数据（Gate.io）..."
                                    )
                                    
                                    df = await gate_collector.fetch_historical_futures_data(
                                        symbol=symbol,
                                        timeframe=timeframe,
                                        days=int((end_time - start_time).total_seconds() / 86400) + 1
                                    )
                                    
                                    if df is not None and len(df) > 0:
                                        task_manager.update_task_progress(
                                            task_id,
                                            current_step=f"✓ 已获取 {symbol} 合约 {timeframe} K线数据 {len(df)} 条（原始），正在过滤..."
                                        )
                                    
                                    if df is not None and len(df) > 0:
                                        df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                                        task_manager.update_task_progress(
                                            task_id,
                                            current_step=f"✓ 过滤后剩余 {len(df)} 条数据，正在保存..."
                                        )
                                        
                                        if len(df) == 0:
                                            task_manager.update_task_progress(
                                                task_id,
                                                current_step=f"⚠️ {symbol} 合约 {timeframe}: 过滤后无数据，可能时间范围不匹配"
                                            )
                                            errors.append(f"{symbol} 合约 {timeframe}: K线数据时间范围不匹配")
                                            continue
                                        
                                        timeframe_saved = 0
                                        total_rows = len(df)
                                        update_interval = max(1, total_rows // 20)
                                        
                                        for idx, row_tuple in enumerate(df.iterrows()):
                                            try:
                                                _, row = row_tuple
                                                timestamp = row['timestamp']
                                                if isinstance(timestamp, pd.Timestamp):
                                                    timestamp_dt = timestamp.to_pydatetime()
                                                    open_time_ms = int(timestamp.timestamp() * 1000)
                                                elif isinstance(timestamp, datetime):
                                                    timestamp_dt = timestamp
                                                    open_time_ms = int(timestamp.timestamp() * 1000)
                                                else:
                                                    timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                                    open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                                
                                                timeframe_minutes = {
                                                    '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                                    '1h': 60, '4h': 240, '1d': 1440
                                                }.get(timeframe, 60)
                                                close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                                created_at = datetime.now()
                                                
                                                cursor.execute("""
                                                    INSERT INTO kline_data
                                                    (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                                    ON DUPLICATE KEY UPDATE
                                                        open_price = VALUES(open_price),
                                                        high_price = VALUES(high_price),
                                                        low_price = VALUES(low_price),
                                                        close_price = VALUES(close_price),
                                                        volume = VALUES(volume),
                                                        quote_volume = VALUES(quote_volume),
                                                        created_at = VALUES(created_at)
                                                """, (
                                                    symbol, 'gate_futures', timeframe, open_time_ms, close_time_ms,
                                                    timestamp_dt, float(row['open']), float(row['high']),
                                                    float(row['low']), float(row['close']), float(row['volume']),
                                                    float(row.get('quote_volume', 0)), created_at
                                                ))
                                                if cursor.rowcount > 0:
                                                    timeframe_saved += 1
                                                
                                                if timeframe_saved % update_interval == 0 or timeframe_saved == total_rows:
                                                    total_saved_temp = total_saved + futures_saved + timeframe_saved
                                                    task = task_manager.get_task(task_id)
                                                    if task and task.estimated_total_records > 0:
                                                        progress = min(95, (total_saved_temp / task.estimated_total_records) * 100)
                                                    else:
                                                        progress = min(95, (completed_steps / total_steps) * 100) if total_steps > 0 else 0
                                                    
                                                    task_manager.update_task_progress(
                                                        task_id,
                                                        current_step=f"正在保存 {symbol} 合约 {timeframe} K线数据 ({timeframe_saved}/{total_rows})...",
                                                        total_saved=total_saved_temp,
                                                        progress=progress
                                                    )
                                            except Exception as e:
                                                logger.error(f"保存合约K线数据失败: {e}")
                                                continue
                                        
                                        futures_saved += timeframe_saved
                                        completed_steps += 1
                                        task_manager.update_task_progress(
                                            task_id,
                                            completed_steps=completed_steps,
                                            total_saved=total_saved + futures_saved,
                                            current_step=f"✓ {symbol} 合约 {timeframe} K线数据采集完成，保存 {timeframe_saved} 条"
                                        )
                                except Exception as e:
                                    error_msg = f"{symbol} 合约 {timeframe}: {str(e)}"
                                    errors.append(error_msg)
                                    logger.error(f"采集 {symbol} 合约 {timeframe} K线数据失败: {e}")
                            
                            total_saved += futures_saved
                        except Exception as e:
                            error_msg = f"{symbol} 合约数据: {str(e)}"
                            errors.append(error_msg)
                            logger.error(f"采集 {symbol} 合约数据失败: {e}")
                    elif binance_futures_collector:
                        # 其他交易对从Binance采集合约数据
                        try:
                            task_manager.update_task_progress(
                                task_id,
                                current_step=f"正在采集 {symbol} 合约数据..."
                            )
                            futures_saved = 0
                            
                            if not timeframes:
                                timeframes = ['1m', '5m', '15m', '1h', '1d']
                            
                            for timeframe in timeframes:
                                try:
                                    task_manager.update_task_progress(
                                        task_id,
                                        current_step=f"正在从API获取 {symbol} 合约 {timeframe} K线数据..."
                                    )
                                    
                                    days = int((end_time - start_time).total_seconds() / 86400) + 1
                                    timeframe_minutes = {
                                        '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                        '1h': 60, '4h': 240, '1d': 1440
                                    }.get(timeframe, 60)
                                    klines_needed = int(days * 1440 / timeframe_minutes)
                                    limit = min(klines_needed, 1500)
                                    
                                    df = await binance_futures_collector.fetch_futures_klines(
                                        symbol=symbol,
                                        timeframe=timeframe,
                                        limit=limit
                                    )
                                    
                                    if df is not None and len(df) > 0:
                                        task_manager.update_task_progress(
                                            task_id,
                                            current_step=f"✓ 已获取 {symbol} 合约 {timeframe} K线数据 {len(df)} 条（原始），正在过滤..."
                                        )
                                    
                                    if df is not None and len(df) > 0:
                                        df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                                        task_manager.update_task_progress(
                                            task_id,
                                            current_step=f"✓ 过滤后剩余 {len(df)} 条数据，正在保存..."
                                        )
                                        
                                        if len(df) == 0:
                                            task_manager.update_task_progress(
                                                task_id,
                                                current_step=f"⚠️ {symbol} 合约 {timeframe}: 过滤后无数据，可能时间范围不匹配"
                                            )
                                            errors.append(f"{symbol} 合约 {timeframe}: K线数据时间范围不匹配")
                                            continue
                                        
                                        timeframe_saved = 0
                                        total_rows = len(df)
                                        update_interval = max(1, total_rows // 20)  # 每5%更新一次进度
                                        
                                        for idx, row_tuple in enumerate(df.iterrows()):
                                            try:
                                                _, row = row_tuple
                                                timestamp = row['timestamp']
                                                if isinstance(timestamp, pd.Timestamp):
                                                    timestamp_dt = timestamp.to_pydatetime()
                                                    open_time_ms = int(timestamp.timestamp() * 1000)
                                                elif isinstance(timestamp, datetime):
                                                    timestamp_dt = timestamp
                                                    open_time_ms = int(timestamp.timestamp() * 1000)
                                                else:
                                                    timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                                    open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                                
                                                timeframe_minutes = {
                                                    '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                                    '1h': 60, '4h': 240, '1d': 1440
                                                }.get(timeframe, 60)
                                                close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                                created_at = datetime.now()
                                                
                                                cursor.execute("""
                                                    INSERT INTO kline_data
                                                    (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                                    ON DUPLICATE KEY UPDATE
                                                        open_price = VALUES(open_price),
                                                        high_price = VALUES(high_price),
                                                        low_price = VALUES(low_price),
                                                        close_price = VALUES(close_price),
                                                        volume = VALUES(volume),
                                                        quote_volume = VALUES(quote_volume),
                                                        created_at = VALUES(created_at)
                                                """, (
                                                    symbol, 'binance_futures', timeframe, open_time_ms, close_time_ms,
                                                    timestamp_dt, float(row['open']), float(row['high']),
                                                    float(row['low']), float(row['close']), float(row['volume']),
                                                    float(row.get('quote_volume', 0)), created_at
                                                ))
                                                if cursor.rowcount > 0:
                                                    timeframe_saved += 1
                                                
                                                # 实时更新进度（每保存一定数量后更新）
                                                if timeframe_saved % update_interval == 0 or timeframe_saved == total_rows:
                                                    total_saved_temp = total_saved + futures_saved + timeframe_saved
                                                    task = task_manager.get_task(task_id)
                                                    if task and task.estimated_total_records > 0:
                                                        # 基于实际保存的数据量计算进度
                                                        progress = min(95, (total_saved_temp / task.estimated_total_records) * 100)
                                                    else:
                                                        # 回退到基于步骤的进度计算
                                                        progress = min(95, (completed_steps / total_steps) * 100) if total_steps > 0 else 0
                                                    
                                                    task_manager.update_task_progress(
                                                        task_id,
                                                        current_step=f"正在保存 {symbol} 合约 {timeframe} K线数据 ({timeframe_saved}/{total_rows})...",
                                                        total_saved=total_saved_temp,
                                                        progress=progress
                                                    )
                                            except Exception as e:
                                                logger.error(f"保存合约K线数据失败: {e}")
                                                continue
                                    
                                    futures_saved += timeframe_saved
                                    completed_steps += 1
                                    task_manager.update_task_progress(
                                        task_id,
                                        completed_steps=completed_steps,
                                        total_saved=total_saved + futures_saved,
                                        current_step=f"✓ {symbol} 合约 {timeframe} K线数据采集完成，保存 {timeframe_saved} 条"
                                    )
                                    
                                    # 延迟避免API限流
                                    await asyncio.sleep(0.3)
                                    
                                except Exception as e:
                                    error_msg = str(e)
                                    # 如果是无效交易对，提供更详细的错误信息
                                    if 'Invalid symbol' in error_msg or '-1121' in error_msg or 'HTTP 400' in error_msg:
                                        error_msg = f"{symbol} 合约 {timeframe}: 交易对不存在或格式错误（币安合约可能不支持此交易对）"
                                    else:
                                        error_msg = f"{symbol} 合约 {timeframe}: {error_msg}"
                                    errors.append(error_msg)
                                    logger.error(f"采集 {symbol} 合约 {timeframe} K线数据失败: {e}")
                            
                            total_saved += futures_saved
                            if futures_saved == 0:
                                errors.append(f"{symbol}: 所有周期均未获取到合约数据")
                                
                        except Exception as e:
                            error_msg = f"{symbol} 合约数据: {str(e)}"
                            errors.append(error_msg)
                            logger.error(f"采集 {symbol} 合约数据失败: {e}")
                
            except Exception as e:
                error_msg = f"{symbol}: {str(e)}"
                errors.append(error_msg)
                logger.error(f"采集 {symbol} 数据失败: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # 更新配置文件
        config_updated = False
        if save_to_config:
            try:
                price_updated = _update_config_file(config_path, symbols, 'price', None)
                if price_updated:
                    config_updated = True
                if collect_kline and timeframes:
                    for tf in timeframes:
                        updated = _update_config_file(config_path, symbols, 'kline', tf)
                        if updated:
                            config_updated = True
            except Exception as e:
                logger.error(f"更新配置文件失败: {e}")
        
        result = {
            'success': True,
            'total_saved': total_saved,
            'errors': errors,
            'config_updated': config_updated,
            'collect_futures': collect_futures,
            'message': f'成功采集 {total_saved} 条数据' + (f'，{len(errors)} 个错误' if errors else '')
        }
        
        task_manager.set_task_status(task_id, TaskStatus.COMPLETED, result)
        task_manager.update_task_progress(task_id, progress=100.0, current_step="采集完成")
        
    except Exception as e:
        logger.error(f"数据采集任务执行失败: {e}")
        import traceback
        traceback.print_exc()
        task_manager.set_task_status(
            task_id,
            TaskStatus.FAILED,
            {'success': False, 'error': str(e)}
        )


@router.post("/collect")
async def collect_historical_data(request: Dict, background_tasks: BackgroundTasks):
    """
    采集历史数据
    
    Args:
        request: 包含以下字段的字典
            - symbols: 交易对列表，如 ["BTC/USDT", "ETH/USDT"]
            - data_type: 数据类型，'price'、'kline' 或 'both'（同时采集价格和K线数据）
            - start_time: 开始时间 (ISO格式字符串)
            - end_time: 结束时间 (ISO格式字符串)
            - timeframes: 时间周期列表（仅K线数据需要），如 ['1m', '5m', '1h']，默认 ['1m', '5m', '15m', '1h', '1d']
            - timeframe: 单个时间周期（向后兼容，如果timeframes不存在则使用此字段）
            - collect_futures: 是否采集合约数据 (bool)
            - save_to_config: 是否保存到配置文件 (bool)
    """
    try:
        symbols = request.get('symbols', [])
        data_type = request.get('data_type', 'price')
        start_time_str = request.get('start_time')
        end_time_str = request.get('end_time')
        # 支持多个时间周期，默认所有周期
        timeframes = request.get('timeframes', None)
        if not timeframes:
            # 向后兼容：如果只有单个timeframe，转换为列表
            timeframe = request.get('timeframe', '1h')
            timeframes = [timeframe] if data_type == 'kline' else []
        collect_futures = request.get('collect_futures', False)
        save_to_config = request.get('save_to_config', False)
        
        if not symbols:
            raise HTTPException(status_code=400, detail="交易对列表不能为空")
        
        if not start_time_str or not end_time_str:
            raise HTTPException(status_code=400, detail="开始时间和结束时间不能为空")
        
        # 解析时间
        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"时间格式错误: {str(e)}")
        
        if start_time >= end_time:
            raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")
        
        # 创建后台任务
        task_id = task_manager.create_task(request)
        
        # 在后台执行采集任务
        background_tasks.add_task(_execute_collection_task, task_id, request)
        
        return {
            'success': True,
            'task_id': task_id,
            'message': '数据采集任务已提交，正在后台执行'
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建数据采集任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建数据采集任务失败: {str(e)}")


@router.get("/collect/task/{task_id}")
async def get_collection_task_status(task_id: str):
    """获取数据采集任务状态"""
    try:
        task = task_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        return task.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取任务状态失败: {str(e)}")


@router.post("/collect-sync")
async def collect_historical_data_sync(request: Dict):
    """
    同步采集历史数据（保留向后兼容）
    
    Args:
        request: 包含以下字段的字典
            - symbols: 交易对列表，如 ["BTC/USDT", "ETH/USDT"]
            - data_type: 数据类型，'price'、'kline' 或 'both'（同时采集价格和K线数据）
            - start_time: 开始时间 (ISO格式字符串)
            - end_time: 结束时间 (ISO格式字符串)
            - timeframes: 时间周期列表（仅K线数据需要），如 ['1m', '5m', '1h']，默认 ['1m', '5m', '15m', '1h', '1d']
            - timeframe: 单个时间周期（向后兼容，如果timeframes不存在则使用此字段）
            - collect_futures: 是否采集合约数据 (bool)
            - save_to_config: 是否保存到配置文件 (bool)
    """
    try:
        symbols = request.get('symbols', [])
        data_type = request.get('data_type', 'price')
        start_time_str = request.get('start_time')
        end_time_str = request.get('end_time')
        # 支持多个时间周期，默认所有周期
        timeframes = request.get('timeframes', None)
        if not timeframes:
            # 向后兼容：如果只有单个timeframe，转换为列表
            timeframe = request.get('timeframe', '1h')
            timeframes = [timeframe] if data_type == 'kline' else []
        collect_futures = request.get('collect_futures', False)
        save_to_config = request.get('save_to_config', False)
        
        if not symbols:
            raise HTTPException(status_code=400, detail="交易对列表不能为空")
        
        if not start_time_str or not end_time_str:
            raise HTTPException(status_code=400, detail="开始时间和结束时间不能为空")
        
        # 解析时间
        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"时间格式错误: {str(e)}")
        
        if start_time >= end_time:
            raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")
        
        # 导入采集器
        from app.collectors.price_collector import MultiExchangeCollector
        import yaml
        from pathlib import Path
        
        # 加载配置
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        if not config_path.exists():
            raise HTTPException(status_code=500, detail="配置文件不存在")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 初始化采集器
        collector = MultiExchangeCollector(config)
        
        # 初始化合约采集器（如果需要）
        futures_collector = None
        if collect_futures:
            try:
                from app.collectors.binance_futures_collector import BinanceFuturesCollector
                binance_config = config.get('exchanges', {}).get('binance', {})
                futures_collector = BinanceFuturesCollector(binance_config)
                logger.info("合约数据采集器初始化成功")
            except Exception as e:
                logger.warning(f"合约数据采集器初始化失败: {e}，将跳过合约数据采集")
                collect_futures = False
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        total_saved = 0
        errors = []
        
        # 遍历每个交易对
        for symbol in symbols:
            try:
                symbol = symbol.strip()
                if not symbol:
                    continue
                
                logger.info(f"开始采集 {symbol} 的{data_type}数据，时间范围: {start_time} - {end_time}")
                
                # 判断是否需要采集价格数据
                collect_price = data_type in ['price', 'both']
                # 判断是否需要采集K线数据
                collect_kline = data_type in ['kline', 'both']
                
                if collect_price:
                    # 采集价格数据 - 使用1分钟K线数据来获取历史价格
                    df = await collector.fetch_historical_data(
                        symbol=symbol,
                        timeframe='1m',
                        days=int((end_time - start_time).total_seconds() / 86400) + 1
                    )
                    
                    if df is not None and len(df) > 0:
                        # 过滤时间范围
                        df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                        
                        saved_count = 0
                        for _, row in df.iterrows():
                            try:
                                # 获取当前时间作为created_at
                                created_at = datetime.now()
                                
                                # 从K线数据转换为价格数据格式
                                cursor.execute("""
                                    INSERT INTO price_data
                                    (symbol, exchange, timestamp, price, open_price, high_price, low_price, close_price, volume, quote_volume, bid_price, ask_price, change_24h, created_at)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON DUPLICATE KEY UPDATE
                                        price = VALUES(price),
                                        open_price = VALUES(open_price),
                                        high_price = VALUES(high_price),
                                        low_price = VALUES(low_price),
                                        close_price = VALUES(close_price),
                                        volume = VALUES(volume),
                                        quote_volume = VALUES(quote_volume),
                                        bid_price = VALUES(bid_price),
                                        ask_price = VALUES(ask_price),
                                        change_24h = VALUES(change_24h),
                                        created_at = VALUES(created_at)
                                """, (
                                    symbol,
                                    'binance',  # 默认交易所
                                    row['timestamp'],
                                    float(row['close']),  # 使用收盘价作为价格
                                    float(row['open']),
                                    float(row['high']),
                                    float(row['low']),
                                    float(row['close']),
                                    float(row['volume']),
                                    float(row.get('quote_volume', 0)),
                                    0,  # bid
                                    0,  # ask
                                    0,  # change_24h (历史数据无法计算)
                                    created_at
                                ))
                                if cursor.rowcount > 0:
                                    saved_count += 1
                            except Exception as e:
                                logger.error(f"保存价格数据失败: {e}")
                                continue
                        
                        total_saved += saved_count
                        logger.info(f"{symbol} 价格数据采集完成，保存 {saved_count} 条")
                    else:
                        errors.append(f"{symbol}: 未获取到价格数据")
                    
                if collect_kline:
                    # 采集K线数据 - 对所有时间周期进行采集
                    if not timeframes:
                        timeframes = ['1m', '5m', '15m', '1h', '1d']  # 默认所有周期
                    
                    symbol_saved = 0
                    for timeframe in timeframes:
                        try:
                            logger.info(f"  采集 {symbol} {timeframe} K线数据...")
                            
                            if use_gate and gate_collector:
                                # HYPE/USDT 从Gate.io采集
                                days = int((end_time - start_time).total_seconds() / 86400) + 1
                                since = int(start_time.timestamp())
                                df = await gate_collector.fetch_ohlcv(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    limit=1000,
                                    since=since
                                )
                                # 如果数据不够，需要分批获取
                                if df is not None and len(df) > 0:
                                    all_data = [df]
                                    last_timestamp = df['timestamp'].iloc[-1]
                                    current_since = int(last_timestamp.timestamp()) + 1
                                    while current_since < int(end_time.timestamp()):
                                        next_df = await gate_collector.fetch_ohlcv(
                                            symbol=symbol,
                                            timeframe=timeframe,
                                            limit=1000,
                                            since=current_since
                                        )
                                        if next_df is None or len(next_df) == 0:
                                            break
                                        all_data.append(next_df)
                                        last_timestamp = next_df['timestamp'].iloc[-1]
                                        current_since = int(last_timestamp.timestamp()) + 1
                                        if len(next_df) < 1000:
                                            break
                                        await asyncio.sleep(0.5)
                                    if len(all_data) > 1:
                                        df = pd.concat(all_data, ignore_index=True)
                                        df = df.drop_duplicates(subset=['timestamp'])
                                        df = df.sort_values('timestamp').reset_index(drop=True)
                            else:
                                # 其他交易对从Binance采集
                                df = await collector.fetch_historical_data(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    days=int((end_time - start_time).total_seconds() / 86400) + 1,
                                    exchange='binance' if not use_gate else None
                                )
                            
                            if df is not None and len(df) > 0:
                                # 过滤时间范围
                                df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                                
                                if len(df) == 0:
                                    errors.append(f"{symbol} {timeframe}: K线数据时间范围不匹配")
                                    continue
                                
                                timeframe_saved = 0
                                for idx, row_tuple in enumerate(df.iterrows()):
                                    try:
                                        _, row = row_tuple
                                        # 计算时间戳（毫秒）
                                        timestamp = row['timestamp']
                                        # 确保timestamp是datetime类型
                                        if isinstance(timestamp, pd.Timestamp):
                                            timestamp_dt = timestamp.to_pydatetime()
                                            open_time_ms = int(timestamp.timestamp() * 1000)
                                        elif isinstance(timestamp, datetime):
                                            timestamp_dt = timestamp
                                            open_time_ms = int(timestamp.timestamp() * 1000)
                                        else:
                                            # 尝试转换
                                            timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                            open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                        
                                        # 计算收盘时间（根据时间周期）
                                        timeframe_minutes = {
                                            '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                            '1h': 60, '4h': 240, '1d': 1440
                                        }.get(timeframe, 60)
                                        close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                        
                                        # 获取当前时间作为created_at
                                        created_at = datetime.now()
                                        
                                        cursor.execute("""
                                            INSERT INTO kline_data
                                            (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                            ON DUPLICATE KEY UPDATE
                                                open_price = VALUES(open_price),
                                                high_price = VALUES(high_price),
                                                low_price = VALUES(low_price),
                                                close_price = VALUES(close_price),
                                                volume = VALUES(volume),
                                                quote_volume = VALUES(quote_volume),
                                                created_at = VALUES(created_at)
                                        """, (
                                            symbol,
                                            'gate' if use_gate else 'binance',
                                            timeframe,
                                            open_time_ms,
                                            close_time_ms,
                                            timestamp_dt,
                                            float(row['open']),
                                            float(row['high']),
                                            float(row['low']),
                                            float(row['close']),
                                            float(row['volume']),
                                            float(row.get('quote_volume', 0)),
                                            created_at
                                        ))
                                        if cursor.rowcount > 0:
                                            timeframe_saved += 1
                                    except Exception as e:
                                        logger.error(f"保存K线数据失败 ({timeframe}): {e}")
                                        continue
                                
                                symbol_saved += timeframe_saved
                                logger.info(f"  ✓ {symbol} {timeframe} K线数据采集完成，保存 {timeframe_saved} 条")
                            else:
                                logger.warning(f"  ⊗ {symbol} {timeframe}: 未获取到K线数据")
                        except Exception as e:
                            error_msg = f"{symbol} {timeframe}: {str(e)}"
                            errors.append(error_msg)
                            logger.error(f"采集 {symbol} {timeframe} K线数据失败: {e}")
                    
                    total_saved += symbol_saved
                    if symbol_saved > 0:
                        logger.info(f"{symbol} K线数据采集完成，共保存 {symbol_saved} 条（所有周期）")
                    else:
                        errors.append(f"{symbol}: 所有周期均未获取到K线数据")
                
                # 采集合约数据
                if collect_futures and futures_collector:
                    try:
                        logger.info(f"开始采集 {symbol} 的合约数据...")
                        futures_saved = 0
                        
                        # 对每个时间周期采集合约K线数据
                        for timeframe in timeframes:
                            try:
                                logger.info(f"  采集 {symbol} 合约 {timeframe} K线数据...")
                                
                                # 计算需要获取的数据量
                                days = int((end_time - start_time).total_seconds() / 86400) + 1
                                # 根据时间周期计算limit
                                timeframe_minutes = {
                                    '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                    '1h': 60, '4h': 240, '1d': 1440
                                }.get(timeframe, 60)
                                # 每个周期需要的K线数量
                                klines_needed = int(days * 1440 / timeframe_minutes)
                                limit = min(klines_needed, 1500)  # 币安限制最大1500
                                
                                # 获取合约K线数据
                                df = await futures_collector.fetch_futures_klines(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    limit=limit
                                )
                                
                                if df is not None and len(df) > 0:
                                    # 过滤时间范围
                                    df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                                    
                                    timeframe_saved = 0
                                    for _, row in df.iterrows():
                                        try:
                                            # 计算时间戳（毫秒）
                                            timestamp = row['timestamp']
                                            if isinstance(timestamp, pd.Timestamp):
                                                timestamp_dt = timestamp.to_pydatetime()
                                                open_time_ms = int(timestamp.timestamp() * 1000)
                                            elif isinstance(timestamp, datetime):
                                                timestamp_dt = timestamp
                                                open_time_ms = int(timestamp.timestamp() * 1000)
                                            else:
                                                timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                                open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                            
                                            # 计算收盘时间
                                            timeframe_minutes = {
                                                '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                                '1h': 60, '4h': 240, '1d': 1440
                                            }.get(timeframe, 60)
                                            close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                            
                                            # 获取当前时间作为created_at
                                            created_at = datetime.now()
                                            
                                            # 保存合约K线数据
                                            cursor.execute("""
                                                INSERT INTO kline_data
                                                (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                                ON DUPLICATE KEY UPDATE
                                                    open_price = VALUES(open_price),
                                                    high_price = VALUES(high_price),
                                                    low_price = VALUES(low_price),
                                                    close_price = VALUES(close_price),
                                                    volume = VALUES(volume),
                                                    quote_volume = VALUES(quote_volume),
                                                    created_at = VALUES(created_at)
                                            """, (
                                                symbol,
                                                'binance_futures',
                                                timeframe,
                                                open_time_ms,
                                                close_time_ms,
                                                timestamp_dt,
                                                float(row['open']),
                                                float(row['high']),
                                                float(row['low']),
                                                float(row['close']),
                                                float(row['volume']),
                                                float(row.get('quote_volume', 0)),
                                                created_at
                                            ))
                                            timeframe_saved += 1
                                        except Exception as e:
                                            logger.error(f"保存合约K线数据失败 ({timeframe}): {e}")
                                            continue
                                    
                                    futures_saved += timeframe_saved
                                    logger.info(f"  ✓ {symbol} 合约 {timeframe} K线数据采集完成，保存 {timeframe_saved} 条")
                                else:
                                    logger.warning(f"  ⊗ {symbol} 合约 {timeframe}: 未获取到K线数据")
                                
                                # 延迟避免API限流
                                await asyncio.sleep(0.3)
                                
                            except Exception as e:
                                error_msg = f"{symbol} 合约 {timeframe}: {str(e)}"
                                errors.append(error_msg)
                                logger.error(f"采集 {symbol} 合约 {timeframe} K线数据失败: {e}")
                        
                        total_saved += futures_saved
                        if futures_saved > 0:
                            logger.info(f"{symbol} 合约数据采集完成，共保存 {futures_saved} 条（所有周期）")
                        else:
                            errors.append(f"{symbol}: 所有周期均未获取到合约数据")
                            
                    except Exception as e:
                        error_msg = f"{symbol} 合约数据: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"采集 {symbol} 合约数据失败: {e}")
                
            except Exception as e:
                error_msg = f"{symbol}: {str(e)}"
                errors.append(error_msg)
                logger.error(f"采集 {symbol} 数据失败: {e}")
                import traceback
                traceback.print_exc()
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # 如果勾选了保存到配置文件，则更新config.yaml
        config_updated = False
        if save_to_config:
            try:
                # 更新交易对列表
                price_updated = _update_config_file(config_path, symbols, 'price', None)
                if price_updated:
                    config_updated = True
                
                # 对于K线数据，更新所有时间周期
                if collect_kline and timeframes:
                    for tf in timeframes:
                        updated = _update_config_file(config_path, symbols, 'kline', tf)
                        if updated:
                            config_updated = True
                
                if config_updated:
                    timeframe_str = ', '.join(timeframes) if collect_kline and timeframes else ''
                    logger.info(f"配置文件已更新: 添加了 {len(symbols)} 个交易对" + 
                              (f"，时间周期 {timeframe_str}" if timeframe_str else ""))
            except Exception as e:
                logger.error(f"更新配置文件失败: {e}")
        
        return {
            'success': True,
            'total_saved': total_saved,
            'errors': errors,
            'config_updated': config_updated,
            'collect_futures': collect_futures,
            'message': f'成功采集 {total_saved} 条数据' + (f'，{len(errors)} 个错误' if errors else '') + 
                      (f'，配置文件已更新' if config_updated else '')
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"数据采集失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"数据采集失败: {str(e)}")

