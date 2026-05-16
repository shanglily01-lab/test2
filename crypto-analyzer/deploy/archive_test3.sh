#!/bin/bash
# test3 归档脚本 (Phase 5)
#
# 在执行前**必须**:
# 1. test2 完整运行 ≥ 24 小时无异常
# 2. 数据库各 source 都有新数据写入 (含 strategy_live + strategy_bigmid 来源)
# 3. test3 进程已全部停止 24h+ 且无副作用
# 4. 已备份 test3 整个目录
#
# 这个脚本不会自动执行任何 destructive 操作 — 它只是步骤参考。
# 你必须手动 review 后逐行执行。

set -e

TEST3_DIR="${TEST3_DIR:-/path/to/test3}"
TEST2_DIR="${TEST2_DIR:-/path/to/test2/crypto-analyzer}"
ARCHIVE_DIR="${ARCHIVE_DIR:-/backup}"
DATE_STAMP="$(date +%Y%m%d_%H%M%S)"

echo "=========================================="
echo "test3 归档脚本 — DRY RUN"
echo "=========================================="
echo "TEST3_DIR=$TEST3_DIR"
echo "TEST2_DIR=$TEST2_DIR"
echo "ARCHIVE_DIR=$ARCHIVE_DIR"
echo "DATE_STAMP=$DATE_STAMP"
echo

# ─────────────────────────────────────────────
# Step 0: 确认前置条件
# ─────────────────────────────────────────────

echo "=== Step 0: 前置确认 ==="
echo "请确认以下条件 (按 Ctrl+C 退出, 任意键继续):"
echo
echo "[ ] test2 已运行 ≥ 24 小时无 crash"
echo "[ ] futures_positions 表近 24h 有新开仓 (含 strategy_live: source)"
echo "[ ] strategy_state 表近 24h 有更新"
echo "[ ] test3 所有进程已停止 ≥ 24 小时"
echo "[ ] 数据库已备份 (mysqldump)"
echo "[ ] 关键 .env 已备份"
echo
read -p "全部确认 OK? (输入 yes 继续): " confirm
[[ "$confirm" != "yes" ]] && { echo "取消"; exit 1; }

# ─────────────────────────────────────────────
# Step 1: 验证 test3 进程都死了
# ─────────────────────────────────────────────

echo
echo "=== Step 1: 验证 test3 进程已停止 ==="
test3_procs=$(ps aux | grep -E "$TEST3_DIR" | grep -v grep | wc -l)
if [ "$test3_procs" -gt 0 ]; then
    echo "ERROR: 还有 $test3_procs 个 test3 进程在跑:"
    ps aux | grep -E "$TEST3_DIR" | grep -v grep
    echo "请先 kill 它们"
    exit 1
fi
echo "OK: test3 无进程"

# ─────────────────────────────────────────────
# Step 2: 验证 test3 .env (敏感数据) 已迁到 test2
# ─────────────────────────────────────────────

echo
echo "=== Step 2: 验证 .env 关键配置迁移 ==="
if [ -f "$TEST3_DIR/crypto-analyzer/.env" ]; then
    # 提取 test3 独有 keys (test2 没有的)
    only_in_test3=$(comm -23 \
        <(grep -E "^[A-Z_]+=" "$TEST3_DIR/crypto-analyzer/.env" | cut -d= -f1 | sort) \
        <(grep -E "^[A-Z_]+=" "$TEST2_DIR/.env" | cut -d= -f1 | sort))
    if [ -n "$only_in_test3" ]; then
        echo "WARNING: test3 .env 还有 test2 没有的 keys:"
        echo "$only_in_test3"
        echo "请手动合并到 test2/.env 再继续"
        read -p "已合并? (yes 继续): " ok
        [[ "$ok" != "yes" ]] && exit 1
    else
        echo "OK: .env 关键 keys 已对齐"
    fi
fi

# ─────────────────────────────────────────────
# Step 3: 备份 (tar.gz)
# ─────────────────────────────────────────────

echo
echo "=== Step 3: 备份 test3 ==="
mkdir -p "$ARCHIVE_DIR"
archive_file="$ARCHIVE_DIR/test3_archive_${DATE_STAMP}.tar.gz"

if [ -f "$archive_file" ]; then
    echo "ERROR: 备份文件已存在: $archive_file"
    exit 1
fi

echo "创建备份: $archive_file"
tar czf "$archive_file" \
    --exclude='**/__pycache__' \
    --exclude='**/logs/*' \
    --exclude='**/.git' \
    --exclude='**/venv' \
    -C "$(dirname $TEST3_DIR)" \
    "$(basename $TEST3_DIR)"

if [ ! -f "$archive_file" ]; then
    echo "ERROR: 备份失败"
    exit 1
fi

archive_size=$(du -h "$archive_file" | cut -f1)
echo "OK: 备份完成 ($archive_size)"

# 验证备份可读
echo "验证备份完整性..."
tar tzf "$archive_file" > /dev/null && echo "OK: 备份完整" || { echo "ERROR: 备份损坏"; exit 1; }

# ─────────────────────────────────────────────
# Step 4: 等用户最终确认
# ─────────────────────────────────────────────

echo
echo "=========================================="
echo "准备删除 $TEST3_DIR"
echo "=========================================="
echo "备份已保存: $archive_file"
echo
echo "再次确认: 真要删除 test3 吗?"
echo "(删除后可从 $archive_file 恢复, 但建议至少保留 30 天)"
read -p "输入 DELETE 删除, 其他任意输入退出: " final_confirm

if [ "$final_confirm" = "DELETE" ]; then
    echo
    echo "=== Step 5: 删除 test3 ==="
    rm -rf "$TEST3_DIR"
    echo "OK: $TEST3_DIR 已删除"
    echo
    echo "完成!"
    echo "备份位置: $archive_file"
    echo "如需恢复: tar xzf $archive_file -C $(dirname $TEST3_DIR)"
else
    echo "取消删除. $TEST3_DIR 保留, 但备份已生成 ($archive_file)"
fi
