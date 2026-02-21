#!/bin/bash
# 系统清理脚本 - 基于核心文件依赖分析
# 删除未使用的services、strategies和测试文件

set -e

echo "========================================="
echo "  加密货币分析系统 - 文件清理（最终版）"
echo "========================================="
echo ""

# 创建备份目录
BACKUP_DIR="cleanup_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
echo "✓ 创建备份目录: $BACKUP_DIR"
echo ""

# 要删除的文件列表
declare -a FILES_TO_DELETE=(
    # Services (19个)
    "app/services/advanced_adaptive_optimizer.py"
    "app/services/daily_optimizer_service.py"
    "app/services/daily_review_analyzer.py"
    "app/services/kline_score_calculator.py"
    "app/services/market_observer.py"
    "app/services/market_regime_manager.py"
    "app/services/multi_timeframe_analyzer.py"
    "app/services/notification_service.py"
    "app/services/pending_order_executor.py"
    "app/services/position_validator.py"
    "app/services/realtime_position_monitor.py"
    "app/services/resonance_checker.py"
    "app/services/scoring_weight_optimizer.py"
    "app/services/signal_quality_manager.py"
    "app/services/signal_reversal_monitor.py"
    "app/services/smart_auto_trader.py"
    "app/services/smart_decision_brain_enhanced.py"
    "app/services/smart_exit_optimizer_kline_methods.py"
    "app/services/spot_trader_service.py"

    # Strategies (4个)
    "app/strategies/buy_sell_analyzer.py"
    "app/strategies/price_predictor.py"
    "app/strategies/strategy_optimizer.py"
    "app/strategies/trade_diagnostic.py"

    # 测试和临时文件 (2个)
    "test_v2_kline_logic.py"
    "check_big4_score.py"

    # 文档 (1个)
    "docs/超级大脑完整逻辑深度解析.md"
)

# 统计
TOTAL_FILES=${#FILES_TO_DELETE[@]}
BACKED_UP=0
DELETED=0
NOT_FOUND=0

echo "========================================="
echo "阶段1：备份文件 ($TOTAL_FILES 个)"
echo "========================================="
echo ""

for file in "${FILES_TO_DELETE[@]}"; do
    if [ -f "$file" ]; then
        cp "$file" "$BACKUP_DIR/"
        echo "✓ 已备份: $file"
        ((BACKED_UP++))
    else
        echo "⚠ 文件不存在: $file"
        ((NOT_FOUND++))
    fi
done

echo ""
echo "备份完成: $BACKED_UP 个文件已备份，$NOT_FOUND 个文件不存在"
echo ""

# 询问是否继续
read -p "是否继续删除文件？(y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消删除操作"
    exit 0
fi

echo ""
echo "========================================="
echo "阶段2：删除文件"
echo "========================================="
echo ""

for file in "${FILES_TO_DELETE[@]}"; do
    if [ -f "$file" ]; then
        rm "$file"
        echo "✓ 已删除: $file"
        ((DELETED++))
    fi
done

echo ""
echo "删除完成: $DELETED 个文件已删除"
echo ""

echo "========================================="
echo "阶段3：清理Python缓存"
echo "========================================="
echo ""

# 统计缓存大小
CACHE_COUNT=$(find . -type d -name "__pycache__" 2>/dev/null | wc -l)
PYC_COUNT=$(find . -name "*.pyc" 2>/dev/null | wc -l)

echo "发现 $CACHE_COUNT 个 __pycache__ 目录"
echo "发现 $PYC_COUNT 个 .pyc 文件"
echo ""

if [ $CACHE_COUNT -gt 0 ] || [ $PYC_COUNT -gt 0 ]; then
    read -p "是否清理Python缓存？(Y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find . -name "*.pyc" -delete 2>/dev/null || true
        find . -name "*.pyo" -delete 2>/dev/null || true
        echo "✓ Python缓存已清理"
    fi
fi

echo ""
echo "========================================="
echo "阶段4：生成清理报告"
echo "========================================="
echo ""

REPORT_FILE="cleanup_report_$(date +%Y%m%d_%H%M%S).txt"
cat > "$REPORT_FILE" << EOF
清理报告
========================================
生成时间: $(date '+%Y-%m-%d %H:%M:%S')

已删除文件列表：
========================================

Services (19个):
$(printf '%s\n' "${FILES_TO_DELETE[@]}" | grep "services/" | sed 's/^/  - /')

Strategies (4个):
$(printf '%s\n' "${FILES_TO_DELETE[@]}" | grep "strategies/" | sed 's/^/  - /')

测试文件 (2个):
$(printf '%s\n' "${FILES_TO_DELETE[@]}" | grep -E "test_|check_" | sed 's/^/  - /')

文档 (1个):
$(printf '%s\n' "${FILES_TO_DELETE[@]}" | grep "docs/" | sed 's/^/  - /')

========================================
统计：
========================================

总文件数: $TOTAL_FILES
已备份: $BACKED_UP
已删除: $DELETED
未找到: $NOT_FOUND
清理缓存: $CACHE_COUNT 个目录，$PYC_COUNT 个文件

========================================
备份位置：
========================================

$BACKUP_DIR/

========================================
下一步操作：
========================================

1. 运行语法检查：
   python -m py_compile smart_trader_service.py
   python -m py_compile coin_futures_trader_service.py
   python -m py_compile app/main.py

2. Git提交：
   git add -A
   git commit -m "chore: 清理未使用的services和strategies文件

   删除24个未被核心服务使用的文件：
   - Services: 19个
   - Strategies: 4个
   - 测试文件: 2个
   - 重复文档: 1个

   备份位置: $BACKUP_DIR/"
   git push

3. 监控系统运行24-48小时

4. 30天后无问题可删除备份：
   rm -rf $BACKUP_DIR

EOF

echo "✓ 清理报告已生成: $REPORT_FILE"
cat "$REPORT_FILE"

echo ""
echo "========================================="
echo "清理完成！"
echo "========================================="
echo ""
echo "备份目录: $BACKUP_DIR"
echo "清理报告: $REPORT_FILE"
echo ""
echo "建议下一步："
echo "1. 运行语法检查验证核心文件"
echo "2. Git提交更改"
echo "3. 监控系统运行"
echo "4. 30天后删除备份"
echo ""
