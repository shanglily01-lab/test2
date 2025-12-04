"""
行情识别与策略自适应 API
提供行情类型检测、参数配置等接口
"""

import json
import pymysql
import yaml
from fastapi import APIRouter, HTTPException, Query, Path
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime
import logging

from app.services.market_regime_detector import (
    MarketRegimeDetector,
    get_regime_display_name,
    get_regime_trading_suggestion
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/market-regime', tags=['Market Regime'])

# 加载数据库配置
try:
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    db_config = config.get('database', {}).get('mysql', {})
except Exception as e:
    logger.error(f"加载配置文件失败: {e}")
    db_config = {}


class RegimeParamsRequest(BaseModel):
    """行情参数配置请求"""
    strategy_id: int
    regime_type: str
    enabled: bool = True
    params: Dict
    description: Optional[str] = None


@router.get('/detect/{symbol:path}')
async def detect_market_regime(
    symbol: str = Path(..., description='交易对符号 (如 BTC/USDT)'),
    timeframe: str = Query('15m', description='时间周期')
):
    """
    检测单个交易对的行情类型

    Args:
        symbol: 交易对符号 (如 BTC/USDT)
        timeframe: 时间周期 (5m, 15m, 1h, 4h, 1d)

    Returns:
        行情检测结果
    """
    try:
        detector = MarketRegimeDetector(db_config)
        result = detector.detect_regime(symbol, timeframe)

        # 添加显示名称和建议
        result['regime_display'] = get_regime_display_name(result.get('regime_type', 'ranging'))
        result['trading_suggestion'] = get_regime_trading_suggestion(result.get('regime_type', 'ranging'))

        return {
            'success': True,
            'data': result
        }
    except Exception as e:
        logger.error(f"检测行情类型失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/detect-batch')
async def detect_batch_market_regime(
    symbols: str = Query(..., description='交易对列表，逗号分隔'),
    timeframe: str = Query('15m', description='时间周期')
):
    """
    批量检测多个交易对的行情类型

    Args:
        symbols: 交易对列表，逗号分隔 (如 BTC/USDT,ETH/USDT)
        timeframe: 时间周期

    Returns:
        各交易对的行情检测结果
    """
    try:
        symbol_list = [s.strip() for s in symbols.split(',') if s.strip()]
        if not symbol_list:
            raise HTTPException(status_code=400, detail='请提供交易对列表')

        detector = MarketRegimeDetector(db_config)
        results = {}

        for symbol in symbol_list:
            result = detector.detect_regime(symbol, timeframe)
            result['regime_display'] = get_regime_display_name(result.get('regime_type', 'ranging'))
            result['trading_suggestion'] = get_regime_trading_suggestion(result.get('regime_type', 'ranging'))
            results[symbol] = result

        return {
            'success': True,
            'timeframe': timeframe,
            'count': len(results),
            'data': results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量检测行情类型失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/latest/{symbol:path}')
async def get_latest_regime(
    symbol: str = Path(..., description='交易对符号 (如 BTC/USDT)'),
    timeframe: str = Query('15m', description='时间周期')
):
    """
    获取交易对最新的行情类型（从数据库缓存）

    Args:
        symbol: 交易对符号
        timeframe: 时间周期

    Returns:
        最新的行情类型记录
    """
    try:
        detector = MarketRegimeDetector(db_config)
        result = detector.get_latest_regime(symbol, timeframe)

        if result:
            result['regime_display'] = get_regime_display_name(result.get('regime_type', 'ranging'))
            return {
                'success': True,
                'data': result
            }
        else:
            return {
                'success': False,
                'message': f'未找到 {symbol} [{timeframe}] 的行情记录'
            }
    except Exception as e:
        logger.error(f"获取最新行情类型失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/history/{symbol:path}')
async def get_regime_history(
    symbol: str = Path(..., description='交易对符号 (如 BTC/USDT)'),
    timeframe: str = Query('15m', description='时间周期'),
    limit: int = Query(50, description='返回记录数')
):
    """
    获取行情类型历史记录

    Args:
        symbol: 交易对符号
        timeframe: 时间周期
        limit: 返回记录数

    Returns:
        行情历史记录列表
    """
    try:
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM market_regime
                WHERE symbol = %s AND timeframe = %s
                ORDER BY detected_at DESC
                LIMIT %s
            """, (symbol, timeframe, limit))
            rows = cursor.fetchall()

        connection.close()

        # 添加显示名称
        for row in rows:
            row['regime_display'] = get_regime_display_name(row.get('regime_type', 'ranging'))
            if row.get('details') and isinstance(row['details'], str):
                row['details'] = json.loads(row['details'])

        return {
            'success': True,
            'count': len(rows),
            'data': rows
        }
    except Exception as e:
        logger.error(f"获取行情历史失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/changes/{symbol:path}')
async def get_regime_changes(
    symbol: str = Path(..., description='交易对符号 (如 BTC/USDT)'),
    timeframe: str = Query('15m', description='时间周期'),
    limit: int = Query(20, description='返回记录数')
):
    """
    获取行情切换记录

    Args:
        symbol: 交易对符号
        timeframe: 时间周期
        limit: 返回记录数

    Returns:
        行情切换记录列表
    """
    try:
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM market_regime_changes
                WHERE symbol = %s AND timeframe = %s
                ORDER BY changed_at DESC
                LIMIT %s
            """, (symbol, timeframe, limit))
            rows = cursor.fetchall()

        connection.close()

        # 添加显示名称
        for row in rows:
            row['old_regime_display'] = get_regime_display_name(row.get('old_regime', ''))
            row['new_regime_display'] = get_regime_display_name(row.get('new_regime', ''))

        return {
            'success': True,
            'count': len(rows),
            'data': rows
        }
    except Exception as e:
        logger.error(f"获取行情切换记录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/params/{strategy_id}')
async def get_strategy_regime_params(strategy_id: int):
    """
    获取策略的行情参数配置

    Args:
        strategy_id: 策略ID

    Returns:
        各行情类型的参数配置
    """
    try:
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM strategy_regime_params
                WHERE strategy_id = %s
                ORDER BY regime_type
            """, (strategy_id,))
            rows = cursor.fetchall()

        connection.close()

        # 解析JSON参数
        for row in rows:
            if row.get('params') and isinstance(row['params'], str):
                row['params'] = json.loads(row['params'])
            row['regime_display'] = get_regime_display_name(row.get('regime_type', ''))

        return {
            'success': True,
            'strategy_id': strategy_id,
            'count': len(rows),
            'data': rows
        }
    except Exception as e:
        logger.error(f"获取策略行情参数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/params')
async def save_strategy_regime_params(request: RegimeParamsRequest):
    """
    保存策略的行情参数配置

    Args:
        request: 参数配置请求

    Returns:
        保存结果
    """
    try:
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        with connection.cursor() as cursor:
            # 检查是否已存在
            cursor.execute("""
                SELECT id FROM strategy_regime_params
                WHERE strategy_id = %s AND regime_type = %s
            """, (request.strategy_id, request.regime_type))
            existing = cursor.fetchone()

            params_json = json.dumps(request.params, ensure_ascii=False)

            if existing:
                # 更新
                cursor.execute("""
                    UPDATE strategy_regime_params
                    SET enabled = %s, params = %s, description = %s, updated_at = NOW()
                    WHERE strategy_id = %s AND regime_type = %s
                """, (request.enabled, params_json, request.description,
                      request.strategy_id, request.regime_type))
                action = 'updated'
            else:
                # 插入
                cursor.execute("""
                    INSERT INTO strategy_regime_params
                    (strategy_id, regime_type, enabled, params, description)
                    VALUES (%s, %s, %s, %s, %s)
                """, (request.strategy_id, request.regime_type, request.enabled,
                      params_json, request.description))
                action = 'created'

            connection.commit()

        connection.close()

        return {
            'success': True,
            'action': action,
            'message': f'行情参数配置已{action}'
        }
    except Exception as e:
        logger.error(f"保存策略行情参数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/params/{strategy_id}/{regime_type}')
async def delete_strategy_regime_params(strategy_id: int, regime_type: str):
    """
    删除策略的行情参数配置

    Args:
        strategy_id: 策略ID
        regime_type: 行情类型

    Returns:
        删除结果
    """
    try:
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4'
        )

        with connection.cursor() as cursor:
            cursor.execute("""
                DELETE FROM strategy_regime_params
                WHERE strategy_id = %s AND regime_type = %s
            """, (strategy_id, regime_type))
            affected = cursor.rowcount
            connection.commit()

        connection.close()

        if affected > 0:
            return {
                'success': True,
                'message': f'已删除 {regime_type} 行情参数配置'
            }
        else:
            return {
                'success': False,
                'message': f'未找到 {regime_type} 行情参数配置'
            }
    except Exception as e:
        logger.error(f"删除策略行情参数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/summary')
async def get_market_regime_summary(
    timeframe: str = Query('15m', description='时间周期')
):
    """
    获取所有交易对的行情类型汇总

    Returns:
        各行情类型的交易对数量统计
    """
    try:
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        with connection.cursor() as cursor:
            # 获取每个交易对最新的行情类型
            cursor.execute("""
                SELECT mr.symbol, mr.regime_type, mr.regime_score, mr.detected_at
                FROM market_regime mr
                INNER JOIN (
                    SELECT symbol, MAX(detected_at) as max_time
                    FROM market_regime
                    WHERE timeframe = %s
                    GROUP BY symbol
                ) latest ON mr.symbol = latest.symbol AND mr.detected_at = latest.max_time
                WHERE mr.timeframe = %s
                ORDER BY mr.regime_score DESC
            """, (timeframe, timeframe))
            rows = cursor.fetchall()

        connection.close()

        # 统计各行情类型数量
        summary = {
            'strong_uptrend': [],
            'weak_uptrend': [],
            'ranging': [],
            'weak_downtrend': [],
            'strong_downtrend': []
        }

        for row in rows:
            regime_type = row.get('regime_type', 'ranging')
            if regime_type in summary:
                summary[regime_type].append({
                    'symbol': row['symbol'],
                    'score': float(row['regime_score']) if row['regime_score'] else 0,
                    'detected_at': row['detected_at']
                })

        # 添加显示名称和数量
        result = {}
        for regime_type, symbols in summary.items():
            result[regime_type] = {
                'display': get_regime_display_name(regime_type),
                'suggestion': get_regime_trading_suggestion(regime_type),
                'count': len(symbols),
                'symbols': symbols
            }

        return {
            'success': True,
            'timeframe': timeframe,
            'total_symbols': len(rows),
            'data': result
        }
    except Exception as e:
        logger.error(f"获取行情汇总失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/regime-types')
async def get_regime_types():
    """
    获取所有行情类型定义

    Returns:
        行情类型列表及其说明
    """
    return {
        'success': True,
        'data': [
            {
                'type': 'strong_uptrend',
                'display': '强趋势上涨 📈',
                'description': 'EMA9>EMA26差距>1%, ADX>40',
                'suggestion': '趋势明确，可积极做多，使用持续趋势信号'
            },
            {
                'type': 'weak_uptrend',
                'display': '弱趋势上涨 ↗️',
                'description': 'EMA9>EMA26差距0.3-1%, ADX 25-40',
                'suggestion': '趋势较弱，谨慎做多，只在金叉信号时开仓'
            },
            {
                'type': 'ranging',
                'display': '震荡行情 ↔️',
                'description': 'EMA差值<0.3%, ADX<25',
                'suggestion': '震荡行情，建议观望或降低仓位，等待趋势明确'
            },
            {
                'type': 'weak_downtrend',
                'display': '弱趋势下跌 ↘️',
                'description': 'EMA9<EMA26差距0.3-1%, ADX 25-40',
                'suggestion': '趋势较弱，谨慎做空，只在死叉信号时开仓'
            },
            {
                'type': 'strong_downtrend',
                'display': '强趋势下跌 📉',
                'description': 'EMA9<EMA26差距>1%, ADX>40',
                'suggestion': '趋势明确，可积极做空，使用持续趋势信号'
            }
        ]
    }
