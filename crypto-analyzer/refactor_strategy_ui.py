#!/usr/bin/env python3
"""
策略配置页面重构脚本
将配置项重新组织成三个大板块：开仓设置、平仓设置、订单管理
"""

import re
from pathlib import Path

# 文件路径
TEMPLATE_FILE = Path(__file__).parent / "templates" / "trading_strategies.html"
BACKUP_FILE = TEMPLATE_FILE.with_suffix(".html.backup")

# 板块标记
BLOCK1_START = '''                        <!-- ============================================ -->
                        <!-- 板块1：开仓设置 -->
                        <!-- ============================================ -->
                        <div class="major-block">
                            <div class="major-block-header">
                                <i class="bi bi-door-open"></i>
                                开仓设置
                                <span class="major-block-description">配置入场信号、条件和控制策略</span>
                            </div>

'''

BLOCK1_END = '''                        </div> <!-- 结束板块1: 开仓设置 -->

'''

BLOCK2_START = '''                        <!-- ============================================ -->
                        <!-- 板块2：平仓设置 -->
                        <!-- ============================================ -->
                        <div class="major-block">
                            <div class="major-block-header">
                                <i class="bi bi-door-closed"></i>
                                平仓设置
                                <span class="major-block-description">配置止损、止盈和出场策略</span>
                            </div>

'''

BLOCK2_END = '''                        </div> <!-- 结束板块2: 平仓设置 -->

'''

BLOCK3_START = '''                        <!-- ============================================ -->
                        <!-- 板块3：订单管理 -->
                        <!-- ============================================ -->
                        <div class="major-block">
                            <div class="major-block-header">
                                <i class="bi bi-list-check"></i>
                                订单管理
                                <span class="major-block-description">配置订单执行、实盘同步和通知</span>
                            </div>

'''

BLOCK3_END = '''                        </div> <!-- 结束板块3: 订单管理 -->

'''


def find_insertion_points(lines):
    """
    找到各个板块的插入点

    Returns:
        (block1_start, block1_end, block2_start, block2_end, block3_start, block3_end)
    """
    block1_start = None  # 第一个 form-section 之前
    block1_end = None    # 止损止盈配置之前
    block2_start = None  # 止损止盈配置开始
    block2_end = None    # 限价单配置之前
    block3_start = None  # 限价单配置开始
    block3_end = None    # 保存按钮之前

    for i, line in enumerate(lines):
        # 找到第一个 form-section（基础配置开始）
        if block1_start is None and '<div class="form-section">' in line and i > 500:
            block1_start = i

        # 找到"止损"配置（平仓设置开始）
        if block1_end is None and '止损（%）' in line:
            # 回溯找到这个 form-section 的开始
            for j in range(i, max(0, i-50), -1):
                if '<div class="form-section">' in lines[j]:
                    block1_end = j
                    block2_start = j
                    break

        # 找到"限价单"配置（订单管理开始）
        if block2_end is None and '限价单超时转市价' in line:
            # 回溯找到这个区域的开始（通常是一个注释或div）
            for j in range(i, max(0, i-50), -1):
                if '<!-- 限价单' in lines[j] or ('<div' in lines[j] and 'form-group' in lines[j]):
                    block2_end = j
                    block3_start = j
                    break

        # 找到保存按钮（表单结束）
        if block3_end is None and '保存策略' in line and 'button' in line:
            # 回溯找到按钮容器之前的位置
            for j in range(i, max(0, i-30), -1):
                if '</div>' in lines[j] and 'form-group' in lines[j-10:j]:
                    # 找到最后一个form-section的结束
                    block3_end = j + 1
                    break
            # 如果还没找到，使用按钮行之前
            if block3_end is None:
                block3_end = i

    return block1_start, block1_end, block2_start, block2_end, block3_start, block3_end


def refactor_template():
    """重构模板文件"""
    print(f"📂 读取文件: {TEMPLATE_FILE}")

    # 读取原文件
    with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 备份原文件
    print(f"💾 备份文件: {BACKUP_FILE}")
    with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    # 查找插入点
    print("🔍 分析文件结构...")
    block1_start, block1_end, block2_start, block2_end, block3_start, block3_end = find_insertion_points(lines)

    print(f"   板块1开始: line {block1_start + 1}")
    print(f"   板块1结束/板块2开始: line {block1_end + 1}")
    print(f"   板块2结束/板块3开始: line {block2_end + 1}")
    print(f"   板块3结束: line {block3_end + 1}")

    if None in [block1_start, block1_end, block2_start, block2_end, block3_start, block3_end]:
        print("❌ 错误：无法找到所有插入点")
        print(f"   block1_start={block1_start}, block1_end={block1_end}")
        print(f"   block2_start={block2_start}, block2_end={block2_end}")
        print(f"   block3_start={block3_start}, block3_end={block3_end}")
        return False

    # 插入板块标记（从后往前插入，避免行号变化）
    print("✏️  插入板块标记...")

    # 插入点列表（行号, 要插入的内容）
    insertions = [
        (block3_end, BLOCK3_END),
        (block3_start, BLOCK3_START),
        (block2_end, BLOCK2_END),
        (block2_start, BLOCK2_START),
        (block1_end, BLOCK1_END),
        (block1_start, BLOCK1_START),
    ]

    # 从后往前插入
    for line_num, content in insertions:
        lines.insert(line_num, content)
        print(f"   ✓ 在 line {line_num + 1} 插入标记")

    # 写入新文件
    print(f"💾 保存文件: {TEMPLATE_FILE}")
    with open(TEMPLATE_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    print("✅ 重构完成！")
    print(f"\n📌 注意事项:")
    print(f"   1. 原文件已备份到: {BACKUP_FILE}")
    print(f"   2. 请在浏览器中测试所有功能")
    print(f"   3. 如有问题，可使用备份文件恢复")
    print(f"\n🚀 下一步:")
    print(f"   1. 刷新浏览器查看效果")
    print(f"   2. 测试策略创建和编辑功能")
    print(f"   3. 确认所有配置项都正常显示和保存")

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("  策略配置页面重构工具")
    print("  将配置项重新组织成三个大板块")
    print("=" * 60)
    print()

    success = refactor_template()

    if success:
        print("\n✅ 重构成功！")
        exit(0)
    else:
        print("\n❌ 重构失败！")
        exit(1)
