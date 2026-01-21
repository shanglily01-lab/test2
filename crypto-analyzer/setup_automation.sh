#!/bin/bash
# 一键设置超级大脑自动化脚本
# 运行: bash setup_automation.sh

echo "=================================="
echo "  超级大脑自动化配置工具"
echo "=================================="

# 获取当前脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "项目路径: $SCRIPT_DIR"

# 检查Python3
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到python3"
    exit 1
fi
echo "✅ Python3已安装"

# 检查crontab
if ! command -v crontab &> /dev/null; then
    echo "❌ 错误: 未找到crontab"
    exit 1
fi
echo "✅ crontab已安装"

# 备份现有crontab
echo ""
echo "正在备份现有crontab..."
crontab -l > "$SCRIPT_DIR/crontab_backup_$(date +%Y%m%d_%H%M%S).txt" 2>/dev/null
echo "✅ 备份完成: crontab_backup_*.txt"

# 创建新的crontab配置
echo ""
echo "正在创建新的定时任务配置..."

cat > /tmp/super_brain_cron.txt <<EOF
# ============================================================
# 超级大脑自适应优化定时任务
# 自动生成时间: $(date)
# ============================================================

# 1. 市场观察 - 每5分钟
*/5 * * * * cd $SCRIPT_DIR && python3 run_market_observer.py >> logs/market_observer.log 2>&1

# 2. 6小时市场状态分析 - 每6小时整点 (0, 6, 12, 18点)
0 */6 * * * cd $SCRIPT_DIR && python3 run_market_regime_analysis.py >> logs/regime_analysis.log 2>&1

# 3. 信号权重优化 - 每天凌晨2点
0 2 * * * cd $SCRIPT_DIR && python3 safe_weight_optimizer.py >> logs/weight_optimizer_cron.log 2>&1

# 4. 重启服务（加载新权重） - 每天凌晨2:05
5 2 * * * pkill -f smart_trader_service.py && sleep 2 && cd $SCRIPT_DIR && nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 5. 高级优化（止盈止损+仓位） - 每3天凌晨3点
0 3 */3 * * cd $SCRIPT_DIR && echo "y" | python3 run_advanced_optimization.py >> logs/advanced_optimizer_cron.log 2>&1

# 6. 重启服务（加载止盈止损和仓位） - 每3天凌晨3:10
10 3 */3 * * pkill -f smart_trader_service.py && sleep 2 && cd $SCRIPT_DIR && nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &

# 7. 每日报告 - 每天早上8点
0 8 * * * cd $SCRIPT_DIR && python3 analyze_smart_brain_2days.py > logs/daily_report_\$(date +\\%Y\\%m\\%d).txt 2>&1

# 8. 每周深度清理 - 每周日凌晨4点
0 4 * * 0 cd $SCRIPT_DIR && python3 cleanup_old_positions.py >> logs/weekly_cleanup.log 2>&1

EOF

# 询问用户确认
echo ""
echo "即将安装以下定时任务:"
echo "-----------------------------------"
cat /tmp/super_brain_cron.txt
echo "-----------------------------------"
echo ""
read -p "确认安装？(y/n): " -n 1 -r
echo

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # 合并现有crontab和新任务
    (crontab -l 2>/dev/null | grep -v "超级大脑" | grep -v "run_market_observer" | grep -v "run_market_regime" | grep -v "safe_weight_optimizer" | grep -v "run_advanced_optimization" | grep -v "analyze_smart_brain"; cat /tmp/super_brain_cron.txt) | crontab -

    echo "✅ 定时任务安装成功！"
    echo ""
    echo "验证安装:"
    crontab -l | grep -A 20 "超级大脑"

    echo ""
    echo "=================================="
    echo "  🎉 自动化配置完成！"
    echo "=================================="
    echo ""
    echo "系统将自动执行:"
    echo "  ✅ 每5分钟观察市场"
    echo "  ✅ 每6小时分析市场状态"
    echo "  ✅ 每天凌晨2点优化权重"
    echo "  ✅ 每天凌晨2:05重启服务"
    echo "  ✅ 每3天凌晨3点优化止盈止损"
    echo "  ✅ 每天早上8点生成报告"
    echo ""
    echo "查看日志:"
    echo "  tail -f logs/market_observer.log"
    echo "  tail -f logs/weight_optimizer_cron.log"
    echo "  tail -f logs/smart_trader.log"
    echo ""
    echo "手动运行验证:"
    echo "  python3 verify_deployment.py"
    echo ""
else
    echo "❌ 安装已取消"
    exit 1
fi

# 清理临时文件
rm -f /tmp/super_brain_cron.txt

echo "提示: 如需移除定时任务，运行 'crontab -e' 并删除相关行"
