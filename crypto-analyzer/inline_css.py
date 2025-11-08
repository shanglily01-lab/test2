#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量将外部 CSS 内联到 HTML 文件中
"""

import re
from pathlib import Path

# CSS 内容（压缩版）
INLINE_CSS = """<style>
        /* Crypto Analyzer - Professional Trading Platform UI */
        :root {
            --primary-blue: #2B6FED; --primary-blue-hover: #1E5DD6; --primary-blue-light: #EBF3FF; --primary-blue-dark: #1A4BA8;
            --secondary-purple: #7C3AED; --secondary-orange: #F97316;
            --success-green: #10B981; --success-green-bg: #D1FAE5; --danger-red: #EF4444; --danger-red-bg: #FEE2E2;
            --warning-yellow: #F59E0B; --warning-yellow-bg: #FEF3C7; --info-blue: #3B82F6;
            --bg-primary: #0D1117; --bg-secondary: #161B22; --bg-tertiary: #21262D; --bg-card: #1C2128; --bg-hover: #2A3038;
            --text-primary: #E6EDF3; --text-secondary: #8B949E; --text-tertiary: #6E7681; --text-disabled: #484F58;
            --border-default: #30363D; --border-muted: #21262D; --border-subtle: #1C2128;
            --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.15); --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.2);
            --shadow-lg: 0 10px 20px rgba(0, 0, 0, 0.25); --shadow-xl: 0 20px 40px rgba(0, 0, 0, 0.3);
            --radius-sm: 4px; --radius-md: 8px; --radius-lg: 12px; --radius-xl: 16px; --radius-full: 9999px;
            --spacing-xs: 4px; --spacing-sm: 8px; --spacing-md: 12px; --spacing-lg: 16px; --spacing-xl: 24px; --spacing-2xl: 32px;
            --font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif;
            --font-mono: 'SF Mono', Monaco, 'Cascadia Code', 'Courier New', monospace;
            --transition-fast: 150ms cubic-bezier(0.4, 0, 0.2, 1); --transition-base: 200ms cubic-bezier(0.4, 0, 0.2, 1);
            --transition-slow: 300ms cubic-bezier(0.4, 0, 0.2, 1);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: var(--font-family); background-color: var(--bg-primary); color: var(--text-primary); line-height: 1.6; font-size: 14px; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
        .container { max-width: 1600px; margin: 0 auto; padding: var(--spacing-xl); }
        .header { background: var(--bg-secondary); border-bottom: 1px solid var(--border-default); padding: var(--spacing-lg) var(--spacing-xl); margin-bottom: var(--spacing-xl); border-radius: var(--radius-lg); }
        .header-title { font-size: 24px; font-weight: 700; color: var(--text-primary); margin-bottom: var(--spacing-sm); display: flex; align-items: center; gap: var(--spacing-md); }
        .header-subtitle { color: var(--text-secondary); font-size: 13px; }
        .nav-container { background: var(--bg-secondary); border-radius: var(--radius-lg); padding: var(--spacing-md); margin-bottom: var(--spacing-xl); border: 1px solid var(--border-default); }
        .nav-links { display: flex; gap: var(--spacing-sm); flex-wrap: wrap; }
        .nav-link { padding: var(--spacing-sm) var(--spacing-lg); background: transparent; color: var(--text-secondary); text-decoration: none; border-radius: var(--radius-md); transition: all var(--transition-base); font-weight: 500; font-size: 13px; border: 1px solid transparent; }
        .nav-link:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--border-default); }
        .nav-link.active { background: var(--primary-blue); color: white; border-color: var(--primary-blue); }
        .card { background: var(--bg-card); border: 1px solid var(--border-default); border-radius: var(--radius-lg); padding: var(--spacing-xl); margin-bottom: var(--spacing-xl); transition: all var(--transition-base); }
        .card:hover { border-color: var(--border-muted); box-shadow: var(--shadow-md); }
        .card-header { padding-bottom: var(--spacing-lg); margin-bottom: var(--spacing-lg); border-bottom: 1px solid var(--border-default); display: flex; justify-content: space-between; align-items: center; }
        .card-title { font-size: 18px; font-weight: 600; color: var(--text-primary); display: flex; align-items: center; gap: var(--spacing-sm); }
        .card-body { padding: 0; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: var(--spacing-lg); margin-bottom: var(--spacing-xl); }
        .stat-card { background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-tertiary) 100%); border: 1px solid var(--border-default); border-radius: var(--radius-lg); padding: var(--spacing-xl); text-align: center; transition: all var(--transition-base); position: relative; overflow: hidden; }
        .stat-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, var(--primary-blue), var(--secondary-purple)); opacity: 0; transition: opacity var(--transition-base); }
        .stat-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-lg); border-color: var(--primary-blue); }
        .stat-card:hover::before { opacity: 1; }
        .stat-label { font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: var(--spacing-sm); font-weight: 600; }
        .stat-value { font-size: 28px; font-weight: 700; color: var(--text-primary); margin-bottom: var(--spacing-xs); font-family: var(--font-mono); }
        .stat-change { font-size: 13px; color: var(--text-secondary); }
        .stat-change.positive { color: var(--success-green); }
        .stat-change.negative { color: var(--danger-red); }
        .table-container { overflow-x: auto; border-radius: var(--radius-md); border: 1px solid var(--border-default); }
        table { width: 100%; border-collapse: collapse; background: var(--bg-card); }
        thead { background: var(--bg-tertiary); border-bottom: 2px solid var(--border-default); }
        th { padding: var(--spacing-md) var(--spacing-lg); text-align: left; font-weight: 600; color: var(--text-secondary); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
        td { padding: var(--spacing-lg); border-bottom: 1px solid var(--border-default); color: var(--text-primary); font-size: 13px; }
        tr:last-child td { border-bottom: none; }
        tbody tr { transition: background-color var(--transition-fast); }
        tbody tr:hover { background: var(--bg-hover); }
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: var(--spacing-sm); padding: var(--spacing-md) var(--spacing-xl); font-size: 14px; font-weight: 500; line-height: 1; text-decoration: none; border: 1px solid transparent; border-radius: var(--radius-md); cursor: pointer; transition: all var(--transition-base); white-space: nowrap; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-primary { background: var(--primary-blue); color: white; border-color: var(--primary-blue); }
        .btn-primary:hover:not(:disabled) { background: var(--primary-blue-hover); border-color: var(--primary-blue-hover); transform: translateY(-1px); box-shadow: var(--shadow-md); }
        .btn-success { background: var(--success-green); color: white; }
        .btn-success:hover:not(:disabled) { background: #059669; transform: translateY(-1px); box-shadow: var(--shadow-md); }
        .btn-danger { background: var(--danger-red); color: white; }
        .btn-danger:hover:not(:disabled) { background: #DC2626; transform: translateY(-1px); box-shadow: var(--shadow-md); }
        .btn-secondary { background: var(--bg-tertiary); color: var(--text-primary); border-color: var(--border-default); }
        .btn-secondary:hover:not(:disabled) { background: var(--bg-hover); border-color: var(--border-muted); }
        .btn-ghost { background: transparent; color: var(--text-secondary); border-color: transparent; }
        .btn-ghost:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
        .btn-sm { padding: var(--spacing-sm) var(--spacing-md); font-size: 12px; }
        .badge { display: inline-flex; align-items: center; padding: var(--spacing-xs) var(--spacing-md); border-radius: var(--radius-full); font-size: 11px; font-weight: 600; letter-spacing: 0.3px; text-transform: uppercase; }
        .badge-success { background: var(--success-green-bg); color: var(--success-green); }
        .badge-danger { background: var(--danger-red-bg); color: var(--danger-red); }
        .badge-info { background: var(--primary-blue-light); color: var(--primary-blue); }
        .badge-neutral { background: var(--bg-tertiary); color: var(--text-secondary); }
        .price { font-family: var(--font-mono); font-weight: 600; font-size: 16px; }
        .position-card { background: var(--bg-card); border: 1px solid var(--border-default); border-left-width: 4px; border-radius: var(--radius-md); padding: var(--spacing-lg); margin-bottom: var(--spacing-md); transition: all var(--transition-base); }
        .position-card:hover { border-color: var(--border-muted); box-shadow: var(--shadow-md); }
        .position-card.long { border-left-color: var(--success-green); }
        .pnl-positive { color: var(--success-green); }
        .pnl-negative { color: var(--danger-red); }
        .status-indicator { display: inline-flex; align-items: center; gap: var(--spacing-sm); padding: var(--spacing-sm) var(--spacing-md); background: var(--bg-tertiary); border-radius: var(--radius-full); font-size: 12px; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
        .status-dot.online { background: var(--success-green); }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .empty-state { text-align: center; padding: var(--spacing-2xl); color: var(--text-secondary); }
        .empty-state-icon { font-size: 48px; margin-bottom: var(--spacing-lg); opacity: 0.5; }
        .empty-state-text { font-size: 15px; margin-bottom: var(--spacing-sm); }
        .text-primary { color: var(--text-primary); } .text-secondary { color: var(--text-secondary); }
        .text-success { color: var(--success-green); } .text-danger { color: var(--danger-red); }
        .d-flex { display: flex; } .align-center { align-items: center; } .justify-between { justify-content: space-between; }
        .gap-sm { gap: var(--spacing-sm); } .gap-md { gap: var(--spacing-md); }
        .mb-sm { margin-bottom: var(--spacing-sm); } .font-mono { font-family: var(--font-mono); }
        .text-xs { font-size: 11px; } .text-sm { font-size: 13px; } .text-2xl { font-size: 24px; }
        .fade-in { animation: fadeIn var(--transition-base) ease-in; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        .slide-up { animation: slideUp var(--transition-slow) ease-out; }
        @keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        @media (max-width: 768px) {
            .container { padding: var(--spacing-md); }
            .stats-grid { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--spacing-md); }
            .stat-value { font-size: 20px; }
            .nav-links { flex-direction: column; }
            th, td { padding: var(--spacing-sm); }
        }
    </style>"""

# 需要处理的文件列表（排除已经处理的 dashboard 和 etf_data）
FILES_TO_PROCESS = [
    'index_new.html',
    'contract_trading_new.html',
    'futures_trading_new.html',
    'paper_trading_new.html',
    'strategies_new.html',
    'corporate_treasury_new.html'
]

TEMPLATES_DIR = Path('templates')

def inline_css_in_file(file_path):
    """将外部 CSS 链接替换为内联 CSS"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查是否已经有内联 CSS
        if '<style>' in content and 'Crypto Analyzer - Professional Trading Platform UI' in content:
            print(f"[SKIP] {file_path.name}: 已包含内联 CSS")
            return False

        # 替换外部 CSS 链接
        pattern = r'<link rel="stylesheet" href="/static/css/trading-platform\.css">\s*<link href="https://cdn\.jsdelivr\.net/npm/bootstrap-icons'
        replacement = f'{INLINE_CSS}\n    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons'

        new_content = re.sub(pattern, replacement, content)

        if new_content == content:
            print(f"[WARN] {file_path.name}: 未找到需要替换的 CSS 链接")
            return False

        # 写回文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print(f"[OK] {file_path.name}: CSS 已内联")
        return True

    except Exception as e:
        print(f"[ERROR] {file_path.name}: {e}")
        return False

def main():
    print("="*60)
    print(" 批量内联 CSS 到 HTML 文件")
    print("="*60)
    print()

    success_count = 0
    skip_count = 0
    fail_count = 0

    for filename in FILES_TO_PROCESS:
        file_path = TEMPLATES_DIR / filename

        if not file_path.exists():
            print(f"[SKIP] {filename}: 文件不存在")
            skip_count += 1
            continue

        result = inline_css_in_file(file_path)
        if result:
            success_count += 1
        elif result is False:
            skip_count += 1
        else:
            fail_count += 1

    print()
    print("="*60)
    print(f"完成: {success_count} 个文件处理成功")
    print(f"跳过: {skip_count} 个文件")
    if fail_count > 0:
        print(f"失败: {fail_count} 个文件")
    print("="*60)

if __name__ == '__main__':
    main()
