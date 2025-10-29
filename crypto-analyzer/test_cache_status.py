"""
测试缓存状态脚本
检查缓存表数据和API响应
"""

import sys
from pathlib import Path
import asyncio
import yaml
from datetime import datetime
from sqlalchemy import create_engine, text
from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def load_config():
    """加载配置文件"""
    config_path = project_root / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_db_connection(config):
    """获取数据库连接（与DatabaseService完全相同的方式）"""
    from urllib.parse import quote_plus

    # 按照 DatabaseService 的方式读取配置
    db_config = config.get('database', {})
    mysql_config = db_config.get('mysql', {})

    host = mysql_config.get('host', 'localhost')
    port = mysql_config.get('port', 3306)
    user = mysql_config.get('user', 'root')
    password = mysql_config.get('password', '')
    database = mysql_config.get('database', 'binance-data')

    logger.info(f"\n📊 数据库配置:")
    logger.info(f"   Host: {host}")
    logger.info(f"   Port: {port}")
    logger.info(f"   User: {user}")
    logger.info(f"   Database: {database}")
    logger.info(f"   Password: {'*' * len(password) if password else '(空)'}")

    # URL编码密码以处理特殊字符（与DatabaseService完全相同）
    password_encoded = quote_plus(password)

    # 创建连接字符串（与DatabaseService完全相同）
    db_uri = f"mysql+pymysql://{user}:{password_encoded}@{host}:{port}/{database}?charset=utf8mb4"

    return create_engine(
        db_uri,
        pool_pre_ping=True,  # 自动检测连接是否有效
        echo=False
    )


def check_cache_tables(engine):
    """检查缓存表是否存在"""
    logger.info("\n" + "="*80)
    logger.info("1️⃣  检查缓存表是否存在")
    logger.info("="*80)

    cache_tables = [
        'price_stats_24h',
        'technical_indicators_cache',
        'news_sentiment_aggregation',
        'funding_rate_stats',
        'hyperliquid_symbol_aggregation',
        'investment_recommendations_cache'
    ]

    with engine.connect() as conn:
        for table in cache_tables:
            try:
                result = conn.execute(text(f"SHOW TABLES LIKE '{table}'"))
                exists = result.fetchone() is not None

                if exists:
                    # 检查记录数
                    count_result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = count_result.fetchone()[0]

                    # 检查最后更新时间
                    if table != 'price_stats_24h':  # price_stats_24h没有updated_at字段
                        time_result = conn.execute(text(
                            f"SELECT MAX(updated_at) FROM {table}"
                        ))
                        last_update = time_result.fetchone()[0]
                        logger.info(f"  ✅ {table:40s} | 记录数: {count:5d} | 最后更新: {last_update}")
                    else:
                        logger.info(f"  ✅ {table:40s} | 记录数: {count:5d}")
                else:
                    logger.error(f"  ❌ {table:40s} | 表不存在")
            except Exception as e:
                logger.error(f"  ❌ {table:40s} | 错误: {e}")


def check_investment_recommendations_cache(engine):
    """检查投资建议缓存表详细数据"""
    logger.info("\n" + "="*80)
    logger.info("2️⃣  检查投资建议缓存表数据")
    logger.info("="*80)

    with engine.connect() as conn:
        try:
            # 检查表是否存在
            result = conn.execute(text("SHOW TABLES LIKE 'investment_recommendations_cache'"))
            if not result.fetchone():
                logger.error("  ❌ investment_recommendations_cache 表不存在！")
                logger.info("\n  💡 请先执行SQL脚本创建缓存表:")
                logger.info("     mysql < scripts/migrations/001_create_cache_tables.sql")
                return

            # 检查记录数
            count_result = conn.execute(text("SELECT COUNT(*) FROM investment_recommendations_cache"))
            count = count_result.fetchone()[0]

            if count == 0:
                logger.warning("  ⚠️  investment_recommendations_cache 表为空！")
                logger.info("\n  💡 请运行缓存更新服务:")
                logger.info("     python scripts/管理/update_cache_manual.py")
                return

            logger.info(f"  ✅ 缓存表记录数: {count}")

            # 查询前5条数据（signal是MySQL保留字，需要用反引号）
            query = text("""
                SELECT
                    symbol,
                    `signal`,
                    confidence,
                    total_score,
                    technical_score,
                    news_score,
                    funding_score,
                    hyperliquid_score,
                    current_price,
                    risk_level,
                    updated_at
                FROM investment_recommendations_cache
                ORDER BY confidence DESC
                LIMIT 5
            """)

            result = conn.execute(query)
            rows = result.fetchall()

            logger.info("\n  📊 投资建议缓存数据 (Top 5):")
            logger.info("  " + "-"*120)
            logger.info(f"  {'币种':<12} {'信号':<15} {'置信度':<8} {'总分':<8} {'技术':<8} {'新闻':<8} {'资金':<8} {'价格':<12} {'更新时间':<20}")
            logger.info("  " + "-"*120)

            for row in rows:
                symbol = row[0]
                signal = row[1] or 'N/A'
                confidence = f"{row[2]:.1f}%" if row[2] else 'N/A'
                total_score = f"{row[3]:.1f}" if row[3] else 'N/A'
                tech_score = f"{row[4]:.1f}" if row[4] else 'N/A'
                news_score = f"{row[5]:.1f}" if row[5] else 'N/A'
                fund_score = f"{row[6]:.1f}" if row[6] else 'N/A'
                price = f"${row[8]:,.2f}" if row[8] else 'N/A'
                updated = row[10].strftime('%Y-%m-%d %H:%M:%S') if row[10] else 'N/A'

                logger.info(f"  {symbol:<12} {signal:<15} {confidence:<8} {total_score:<8} {tech_score:<8} {news_score:<8} {fund_score:<8} {price:<12} {updated:<20}")

            logger.info("  " + "-"*120)

        except Exception as e:
            logger.error(f"  ❌ 检查投资建议缓存表失败: {e}")
            import traceback
            traceback.print_exc()


def check_original_table(engine):
    """检查原始投资建议表"""
    logger.info("\n" + "="*80)
    logger.info("3️⃣  检查原始投资建议表")
    logger.info("="*80)

    with engine.connect() as conn:
        try:
            # 检查表是否存在
            result = conn.execute(text("SHOW TABLES LIKE 'investment_recommendations'"))
            if not result.fetchone():
                logger.warning("  ⚠️  investment_recommendations 表不存在")
                return

            # 检查记录数
            count_result = conn.execute(text("SELECT COUNT(*) FROM investment_recommendations"))
            count = count_result.fetchone()[0]
            logger.info(f"  ℹ️  原始表记录数: {count}")

            if count > 0:
                # 查询最后更新时间
                time_result = conn.execute(text("SELECT MAX(updated_at) FROM investment_recommendations"))
                last_update = time_result.fetchone()[0]
                logger.info(f"  ℹ️  最后更新时间: {last_update}")

        except Exception as e:
            logger.error(f"  ❌ 检查原始表失败: {e}")


async def check_api_response():
    """检查API响应"""
    logger.info("\n" + "="*80)
    logger.info("4️⃣  检查API响应")
    logger.info("="*80)

    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            # 测试 /api/dashboard
            url = "http://localhost:9020/api/dashboard"
            logger.info(f"  🔗 请求: {url}")

            start_time = datetime.now()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                elapsed = (datetime.now() - start_time).total_seconds()

                if response.status == 200:
                    data = await response.json()

                    logger.info(f"  ✅ API响应成功 ({elapsed:.2f}秒)")

                    # 检查返回数据
                    if data.get('success'):
                        dashboard_data = data.get('data', {})
                        recommendations = dashboard_data.get('recommendations', [])

                        logger.info(f"\n  📊 Dashboard数据:")
                        logger.info(f"     价格数据: {len(dashboard_data.get('prices', []))} 个")
                        logger.info(f"     投资建议: {len(recommendations)} 个")
                        logger.info(f"     新闻数据: {len(dashboard_data.get('news', []))} 条")
                        logger.info(f"     最后更新: {dashboard_data.get('last_updated', 'N/A')}")

                        if recommendations:
                            logger.info(f"\n  💡 投资建议示例 (前3个):")
                            for i, rec in enumerate(recommendations[:3], 1):
                                symbol = rec.get('symbol', 'N/A')
                                signal = rec.get('signal', 'N/A')
                                confidence = rec.get('confidence', 0)
                                logger.info(f"     {i}. {symbol}: {signal} (置信度: {confidence:.1f}%)")
                        else:
                            logger.warning("\n  ⚠️  API返回的投资建议为空！")
                            logger.info("\n  可能的原因:")
                            logger.info("     1. 缓存表没有数据 - 运行: python scripts/管理/update_cache_manual.py")
                            logger.info("     2. scheduler.py 未运行 - 启动: python app/scheduler.py")
                            logger.info("     3. EnhancedDashboard 初始化失败 - 检查 main.py 日志")
                    else:
                        logger.error(f"  ❌ API返回失败: {data.get('error', '未知错误')}")
                else:
                    logger.error(f"  ❌ API响应失败: HTTP {response.status}")

    except aiohttp.ClientConnectorError:
        logger.error("  ❌ 无法连接到API服务器")
        logger.info("  💡 请确保 FastAPI 服务正在运行:")
        logger.info("     python app/main.py")
    except asyncio.TimeoutError:
        logger.error("  ❌ API请求超时")
    except Exception as e:
        logger.error(f"  ❌ API测试失败: {e}")
        import traceback
        traceback.print_exc()


def check_scheduler_status():
    """检查调度器状态"""
    logger.info("\n" + "="*80)
    logger.info("5️⃣  检查调度器进程")
    logger.info("="*80)

    import subprocess

    try:
        # 在Windows上使用不同的命令
        if sys.platform == 'win32':
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq python.exe'],
                capture_output=True,
                text=True
            )

            if 'scheduler.py' in result.stdout:
                logger.info("  ✅ scheduler.py 正在运行")
            else:
                logger.warning("  ⚠️  scheduler.py 未运行")
                logger.info("  💡 启动调度器: python app/scheduler.py")
        else:
            # Linux/Mac
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                text=True
            )

            if 'scheduler.py' in result.stdout:
                logger.info("  ✅ scheduler.py 正在运行")
            else:
                logger.warning("  ⚠️  scheduler.py 未运行")
                logger.info("  💡 启动调度器: python app/scheduler.py")

    except Exception as e:
        logger.warning(f"  ⚠️  无法检查进程状态: {e}")


def print_recommendations():
    """打印修复建议"""
    logger.info("\n" + "="*80)
    logger.info("💡 修复建议")
    logger.info("="*80)

    logger.info("\n如果投资建议数据为空，请按以下步骤操作：")
    logger.info("\n1️⃣  创建缓存表（如果不存在）:")
    logger.info("   mysql -h <host> -u <user> -p<password> <database> < scripts/migrations/001_create_cache_tables.sql")

    logger.info("\n2️⃣  手动更新缓存（立即生效）:")
    logger.info("   python scripts/管理/update_cache_manual.py")

    logger.info("\n3️⃣  启动调度器（自动更新）:")
    logger.info("   python app/scheduler.py")

    logger.info("\n4️⃣  启动API服务:")
    logger.info("   python app/main.py")

    logger.info("\n5️⃣  访问Dashboard:")
    logger.info("   http://localhost:9020/dashboard")

    logger.info("\n" + "="*80 + "\n")


def main():
    """主函数"""
    logger.info("\n")
    logger.info("🔍 " + "="*76)
    logger.info("🔍  缓存状态检查工具")
    logger.info("🔍 " + "="*76)
    logger.info(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("🔍 " + "="*76)

    try:
        # 加载配置
        config = load_config()

        # 获取数据库连接
        engine = get_db_connection(config)

        # 1. 检查缓存表
        check_cache_tables(engine)

        # 2. 检查投资建议缓存表
        check_investment_recommendations_cache(engine)

        # 3. 检查原始表
        check_original_table(engine)

        # 4. 检查API响应
        asyncio.run(check_api_response())

        # 5. 检查调度器状态
        check_scheduler_status()

        # 6. 打印修复建议
        print_recommendations()

    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
