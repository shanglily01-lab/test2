#!/usr/bin/env python3
"""
信号分析定时任务 - 每6小时运行一次
分析24H K线强度 + 信号捕捉情况
"""
import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import datetime
from loguru import logger
import yaml
from dotenv import load_dotenv

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.signal_analysis_service import SignalAnalysisService


def main():
    """主函数"""
    logger.info("=" * 100)
    logger.info(f"📊 信号分析任务开始 | 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 100)

    # 加载环境变量
    load_dotenv()

    # 数据库配置
    db_config = {
        'host': os.getenv('DB_HOST'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME'),
        'charset': 'utf8mb4',
        'cursorclass': None
    }

    # 加载交易对列表
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    symbols = config.get('symbols', [])

    logger.info(f"将分析 {len(symbols)} 个交易对")

    # 创建信号分析服务
    service = SignalAnalysisService(db_config)

    try:
        # 执行分析
        report = service.analyze_all_symbols(symbols, hours=24)

        # 打印报告
        print_report(report)

        # 保存到数据库
        save_to_database(report, db_config)

        logger.info("✅ 信号分析任务完成")

    except Exception as e:
        logger.error(f"❌ 信号分析失败: {e}", exc_info=True)
        raise
    finally:
        service.close()

    logger.info("=" * 100)


def print_report(report: dict):
    """打印分析报告"""
    stats = report['statistics']
    results = report['results']
    missed = report['missed_opportunities']

    print("\n" + "=" * 120)
    print("【信号分析报告 - 24H K线强度 + 捕捉情况】")
    print("=" * 120)

    # 统计信息
    print(f"\n【总体统计】")
    print(f"  分析交易对: {stats['total_analyzed']}")
    print(f"  有交易机会: {stats['should_trade']}")
    print(f"  已开仓: {stats['has_position']} (正确{stats['correct_captures']}个, 方向错误{stats['wrong_direction']}个)")
    print(f"  错过机会: {stats['missed']}")
    print(f"  有效捕获率: {stats['capture_rate']:.1f}%")

    # Top机会（前15个）
    print(f"\n【Top 15 强力信号】")
    print("=" * 120)

    for i, r in enumerate(results[:15], 1):
        s = r['symbol']
        s5m = r['strength_5m']
        s15m = r['strength_15m']
        s1h = r['strength_1h']
        sig = r['signal_status']

        # 判断多空倾向
        if s1h['net_power'] >= 3:
            trend = '强多'
        elif s1h['net_power'] <= -3:
            trend = '强空'
        elif s1h['bull_pct'] > 55:
            trend = '偏多'
        elif s1h['bull_pct'] < 45:
            trend = '偏空'
        else:
            trend = '震荡'

        # 判断捕捉状态
        has_pos = sig['has_position']
        if has_pos:
            pos = sig['position']
            status = f"✓已捕捉({pos['position_side']})"
        else:
            status = "✗错过"

        print(f"\n{i:2d}. {s:15s} | {trend:4s} | {status}")
        print(f"    1H: 阳线{s1h['bull_pct']:4.0f}% ({s1h['bull']:2d}/{s1h['total']:2d}) | "
              f"强阳{s1h['strong_bull']:2d} 强阴{s1h['strong_bear']:2d} | 净力量{s1h['net_power']:+3d}")
        print(f"   15M: 阳线{s15m['bull_pct']:4.0f}% ({s15m['bull']:3d}/{s15m['total']:3d}) | "
              f"强阳{s15m['strong_bull']:2d} 强阴{s15m['strong_bear']:2d} | 净力量{s15m['net_power']:+3d}")
        print(f"    5M: 阳线{s5m['bull_pct']:4.0f}% ({s5m['bull']:3d}/{s5m['total']:3d}) | "
              f"强阳{s5m['strong_bull']:2d} 强阴{s5m['strong_bear']:2d} | 净力量{s5m['net_power']:+3d}")

    # 错过的机会（前10个）
    if missed:
        print(f"\n【错过的高质量机会】(前10个)")
        print("=" * 120)
        for i, opp in enumerate(missed[:10], 1):
            print(f"{i:2d}. {opp['symbol']:15s} | 建议{opp['side']:5s} | {opp['reason']}")
            print(f"    1H净力量: {opp['net_power_1h']:+d} | 15M净力量: {opp['net_power_15m']:+d} | 5M净力量: {opp['net_power_5m']:+d}")

    print("\n" + "=" * 120)


def save_to_database(report: dict, db_config: dict):
    """保存分析结果到数据库"""
    import pymysql
    import json

    # 修复db_config，添加cursorclass
    config = db_config.copy()
    config['cursorclass'] = pymysql.cursors.DictCursor

    conn = pymysql.connect(**config)
    cursor = conn.cursor()

    stats = report['statistics']
    analysis_time = report['analysis_time']

    try:
        # 保存到signal_analysis_reports表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signal_analysis_reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                analysis_time DATETIME NOT NULL,
                total_analyzed INT NOT NULL,
                has_position INT NOT NULL,
                should_trade INT NOT NULL,
                missed_opportunities INT NOT NULL,
                wrong_direction INT NOT NULL,
                correct_captures INT NOT NULL,
                capture_rate DECIMAL(5,2) NOT NULL,
                report_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_analysis_time (analysis_time)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')

        # 序列化完整报告
        report_json = json.dumps({
            'top_opportunities': report['results'][:30],
            'missed_opportunities': report['missed_opportunities'][:20]
        }, ensure_ascii=False, default=str)

        cursor.execute('''
            INSERT INTO signal_analysis_reports
            (analysis_time, total_analyzed, has_position, should_trade,
             missed_opportunities, wrong_direction, correct_captures, capture_rate, report_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            analysis_time,
            stats['total_analyzed'],
            stats['has_position'],
            stats['should_trade'],
            stats['missed'],
            stats['wrong_direction'],
            stats['correct_captures'],
            stats['capture_rate'],
            report_json
        ))

        conn.commit()
        logger.info(f"✅ 分析报告已保存到数据库")

    except Exception as e:
        logger.error(f"保存报告到数据库失败: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
