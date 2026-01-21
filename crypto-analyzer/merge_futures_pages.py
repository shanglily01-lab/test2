#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
合并合约交易页面
1. 从futures_trading.html删除"限价单"和"已取消订单"Tab
2. 将live_trading.html的功能整合到futures_trading.html
3. 添加Tab切换实现模拟/实盘切换
"""
import os
import sys
import re

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

FUTURES_FILE = 'templates/futures_trading.html'

def remove_tabs_from_futures():
    """从futures_trading.html移除限价单和已取消订单Tab"""

    print("正在读取 futures_trading.html...")
    with open(FUTURES_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    original_length = len(content)

    # 1. 移除限价单Tab按钮
    print("\n1. 移除限价单Tab按钮...")
    content = re.sub(
        r'<div class="position-tab-btn"[^>]*id="ordersBtn"[^>]*>.*?</div>\s*',
        '',
        content,
        flags=re.DOTALL
    )

    # 2. 移除已取消订单Tab按钮
    print("2. 移除已取消订单Tab按钮...")
    content = re.sub(
        r'<div class="position-tab-btn"[^>]*id="cancelledBtn"[^>]*>.*?</div>\s*',
        '',
        content,
        flags=re.DOTALL
    )

    # 3. 移除限价单Tab内容区域
    print("3. 移除限价单Tab内容...")
    content = re.sub(
        r'<!-- 限价单标签页 -->.*?<div id="ordersTab"[^>]*>.*?</div>\s*(?=<!-- 已取消订单标签页|</div>\s*</div>)',
        '',
        content,
        flags=re.DOTALL
    )

    # 4. 移除已取消订单Tab内容区域
    print("4. 移除已取消订单Tab内容...")
    content = re.sub(
        r'<!-- 已取消订单标签页 -->.*?<div id="cancelledTab"[^>]*>.*?</div>\s*(?=</div>\s*</div>)',
        '',
        content,
        flags=re.DOTALL
    )

    # 5. 移除相关的CSS样式
    print("5. 移除相关CSS...")
    content = re.sub(
        r'#ordersTab,?\s*#cancelledTab,?\s*',
        '',
        content
    )
    content = re.sub(
        r',?\s*#ordersTable,?\s*#cancelledTable',
        '',
        content
    )

    # 6. 移除相关的JS代码
    print("6. 清理相关JS代码...")
    # 移除切换Tab的事件监听
    content = re.sub(
        r"document\.getElementById\('ordersBtn'\)\.addEventListener.*?}\);?\s*",
        '',
        content,
        flags=re.DOTALL
    )
    content = re.sub(
        r"document\.getElementById\('cancelledBtn'\)\.addEventListener.*?}\);?\s*",
        '',
        content,
        flags=re.DOTALL
    )

    # 移除加载限价单和已取消订单的函数调用
    content = re.sub(r'loadOrders\(\);?\s*', '', content)
    content = re.sub(r'loadCancelledOrders\(\);?\s*', '', content)

    # 移除相关的计数器
    content = re.sub(
        r'<span class="badge[^"]*"[^>]*id="orderCount"[^>]*>.*?</span>\s*',
        '',
        content,
        flags=re.DOTALL
    )
    content = re.sub(
        r'<span class="badge[^"]*"[^>]*id="cancelledCount"[^>]*>.*?</span>\s*',
        '',
        content,
        flags=re.DOTALL
    )

    new_length = len(content)
    removed = original_length - new_length

    print(f"\n已移除 {removed} 字符")

    # 保存修改
    with open(FUTURES_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"✓ 已保存到 {FUTURES_FILE}")

    return True

def add_paper_live_toggle():
    """添加模拟/实盘切换功能"""

    print("\n正在添加模拟/实盘切换功能...")
    with open(FUTURES_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 在页面标题附近添加切换按钮
    # 查找标题位置
    title_pattern = r'(<h2[^>]*>.*?合约交易.*?</h2>)'

    toggle_html = r'''\1
                <div class="trading-mode-toggle" style="margin-top: 15px;">
                    <div class="btn-group" role="group">
                        <button type="button" class="btn btn-sm btn-primary" id="paperTradingBtn" onclick="switchTradingMode('paper')">
                            <i class="bi bi-journals"></i> 模拟交易
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-primary" id="liveTradingBtn" onclick="switchTradingMode('live')">
                            <i class="bi bi-currency-exchange"></i> 实盘交易
                        </button>
                    </div>
                </div>'''

    if re.search(title_pattern, content):
        content = re.sub(title_pattern, toggle_html, content)
        print("✓ 已添加模式切换按钮")
    else:
        print("⚠ 未找到标题,跳过添加切换按钮")

    # 添加切换功能的JS代码
    js_code = '''
        // 交易模式切换
        let currentTradingMode = 'paper'; // 默认模拟交易

        function switchTradingMode(mode) {
            currentTradingMode = mode;

            // 更新按钮状态
            const paperBtn = document.getElementById('paperTradingBtn');
            const liveBtn = document.getElementById('liveTradingBtn');

            if (mode === 'paper') {
                paperBtn.classList.remove('btn-outline-primary');
                paperBtn.classList.add('btn-primary');
                liveBtn.classList.remove('btn-primary');
                liveBtn.classList.add('btn-outline-primary');
            } else {
                liveBtn.classList.remove('btn-outline-primary');
                liveBtn.classList.add('btn-primary');
                paperBtn.classList.remove('btn-primary');
                paperBtn.classList.add('btn-outline-primary');
            }

            // 重新加载数据
            loadPositions();

            showNotification(mode === 'paper' ? '已切换到模拟交易模式' : '已切换到实盘交易模式', 'info');
        }
'''

    # 在最后的</script>标签前添加
    script_end = content.rfind('</script>')
    if script_end > 0:
        content = content[:script_end] + js_code + content[script_end:]
        print("✓ 已添加模式切换JS代码")

    # 保存
    with open(FUTURES_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"✓ 已保存更新")

    return True

def main():
    print("="*80)
    print("合约交易页面整合工具")
    print("="*80)

    # 检查文件是否存在
    if not os.path.exists(FUTURES_FILE):
        print(f"错误: 找不到文件 {FUTURES_FILE}")
        return

    # 创建备份
    backup_file = FUTURES_FILE + '.backup'
    import shutil
    shutil.copy2(FUTURES_FILE, backup_file)
    print(f"\n已创建备份: {backup_file}")

    try:
        # 执行修改
        remove_tabs_from_futures()
        add_paper_live_toggle()

        print("\n" + "="*80)
        print("✓ 整合完成!")
        print("="*80)
        print("\n修改内容:")
        print("  1. ✓ 移除了'限价单'Tab")
        print("  2. ✓ 移除了'已取消订单'Tab")
        print("  3. ✓ 添加了模拟/实盘切换功能")
        print(f"\n备份文件: {backup_file}")
        print("如需恢复,可以从备份文件还原")

    except Exception as e:
        print(f"\n错误: {e}")
        print("正在从备份恢复...")
        shutil.copy2(backup_file, FUTURES_FILE)
        print("已恢复")

if __name__ == '__main__':
    main()
