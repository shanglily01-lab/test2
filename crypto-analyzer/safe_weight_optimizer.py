#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Safe Weight Optimizer - 带错误处理和通知的权重优化器
用于凌晨自动运行,确保错误能被记录和通知
"""

import sys
import os
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# 避免编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, 'app/services')
from scoring_weight_optimizer import ScoringWeightOptimizer

# 数据库配置 - 从.env读取
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

# 日志文件路径
LOG_DIR = Path('logs/weight_optimization')
LOG_DIR.mkdir(parents=True, exist_ok=True)


def write_log(message, level='INFO'):
    """写入日志文件"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_file = LOG_DIR / f"weight_optimization_{datetime.now().strftime('%Y%m%d')}.log"

    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] [{level}] {message}\n")

    print(f"[{timestamp}] [{level}] {message}")


def send_error_notification(error_msg):
    """发送错误通知 - 可以扩展为邮件/微信/Telegram等"""
    error_file = LOG_DIR / f"ERROR_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    with open(error_file, 'w', encoding='utf-8') as f:
        f.write(f"Weight Optimization Error Report\n")
        f.write(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'=' * 80}\n\n")
        f.write(error_msg)

    write_log(f"ERROR file created: {error_file}", 'ERROR')

    # TODO: 这里可以添加邮件/微信/Telegram通知
    # 例如:
    # send_email(subject="Weight Optimization Failed", body=error_msg)
    # send_wechat_message(error_msg)


def run_optimization():
    """运行权重优化 - 带完整错误处理"""

    write_log("=" * 80)
    write_log("Starting Weight Optimization")
    write_log("=" * 80)

    try:
        # 1. 初始化优化器
        write_log("Initializing optimizer...")
        optimizer = ScoringWeightOptimizer(db_config)

        # 2. 分析组件性能
        write_log("Analyzing component performance (last 7 days)...")
        try:
            performance = optimizer.analyze_component_performance(days=7)

            if not performance:
                write_log("No performance data available - skipping optimization", 'WARNING')
                return {
                    'success': True,
                    'skipped': True,
                    'reason': 'No performance data'
                }

            write_log(f"Analyzed {len(performance)} components")

        except Exception as e:
            error_msg = f"Failed to analyze component performance: {str(e)}\n{traceback.format_exc()}"
            write_log(error_msg, 'ERROR')
            send_error_notification(error_msg)
            return {
                'success': False,
                'error': str(e),
                'stage': 'analyze_performance'
            }

        # 3. 执行权重调整
        write_log("Executing weight adjustment...")
        try:
            result = optimizer.adjust_weights(dry_run=False)

            if result.get('adjusted'):
                write_log(f"Successfully adjusted {len(result['adjusted'])} weights")

                # 记录调整详情
                for adj in result['adjusted']:
                    write_log(
                        f"  - {adj['component']} ({adj['side']}): "
                        f"{adj['old_weight']:.1f} -> {adj['new_weight']:.1f} "
                        f"(change: {adj['adjustment']:+d}, "
                        f"perf: {adj['performance_score']:.2f})"
                    )

                # 写入调整摘要文件
                summary_file = LOG_DIR / f"adjustment_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                with open(summary_file, 'w', encoding='utf-8') as f:
                    f.write(f"Weight Adjustment Summary\n")
                    f.write(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Total adjustments: {len(result['adjusted'])}\n\n")

                    for adj in result['adjusted']:
                        f.write(f"{adj['component']} ({adj['side']}):\n")
                        f.write(f"  Old Weight: {adj['old_weight']:.1f}\n")
                        f.write(f"  New Weight: {adj['new_weight']:.1f}\n")
                        f.write(f"  Change: {adj['adjustment']:+d}\n")
                        f.write(f"  Performance Score: {adj['performance_score']:.2f}\n")
                        f.write(f"  Win Rate: {adj['win_rate']*100:.1f}%\n")
                        f.write(f"  Avg PnL: ${adj['avg_pnl']:.2f}\n")
                        f.write(f"  Orders: {adj['orders']}\n\n")

                write_log(f"Summary saved to: {summary_file}")

            else:
                write_log("No weight adjustments needed")

            return {
                'success': True,
                'adjusted': result.get('adjusted', []),
                'skipped': result.get('skipped', [])
            }

        except Exception as e:
            error_msg = f"Failed to adjust weights: {str(e)}\n{traceback.format_exc()}"
            write_log(error_msg, 'ERROR')
            send_error_notification(error_msg)
            return {
                'success': False,
                'error': str(e),
                'stage': 'adjust_weights'
            }

    except Exception as e:
        error_msg = f"Unexpected error in optimization: {str(e)}\n{traceback.format_exc()}"
        write_log(error_msg, 'ERROR')
        send_error_notification(error_msg)
        return {
            'success': False,
            'error': str(e),
            'stage': 'initialization'
        }

    finally:
        write_log("=" * 80)
        write_log("Weight Optimization Completed")
        write_log("=" * 80)


def main():
    """主函数"""
    result = run_optimization()

    # 返回退出码
    if result['success']:
        sys.exit(0)
    else:
        write_log(f"Optimization failed at stage: {result.get('stage', 'unknown')}", 'ERROR')
        sys.exit(1)


if __name__ == '__main__':
    main()
