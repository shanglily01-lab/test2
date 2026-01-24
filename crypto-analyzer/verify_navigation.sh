#!/bin/bash
# 验证所有页面的导航菜单更新

echo "================================"
echo "导航菜单更新验证报告"
echo "================================"
echo ""

cd templates

echo "1. 检查包含'U本位合约'的页面数量:"
u_count=$(grep -l "U本位合约" *.html | wc -l)
echo "   找到 $u_count 个页面"
echo ""

echo "2. 检查包含'币本位合约'的页面数量:"
coin_count=$(grep -l "币本位合约" *.html | wc -l)
echo "   找到 $coin_count 个页面"
echo ""

echo "3. 检查仍包含旧文本'合约交易'的导航链接:"
old_nav=$(grep -l 'bi-graph-up-arrow.*合约交易' *.html | grep -v futures_trading.html | grep -v coin_futures_trading.html)
if [ -z "$old_nav" ]; then
    echo "   ✓ 所有页面已更新（无遗留的'合约交易'导航）"
else
    echo "   ✗ 以下页面仍包含旧导航:"
    echo "$old_nav"
fi
echo ""

echo "4. 验证关键页面的导航结构:"
for file in dashboard.html paper_trading.html technical_signals.html; do
    echo "   检查 $file..."
    has_u=$(grep -q "U本位合约" "$file" && echo "✓" || echo "✗")
    has_coin=$(grep -q "币本位合约" "$file" && echo "✓" || echo "✗")
    echo "      U本位合约: $has_u  |  币本位合约: $has_coin"
done
echo ""

echo "5. 验证两个合约交易页面:"
echo "   futures_trading.html:"
grep -q "U本位合约交易 - AlphaFlow" futures_trading.html && echo "      ✓ 页面标题正确" || echo "      ✗ 页面标题错误"
grep -q 'href="/coin_futures_trading"' futures_trading.html && echo "      ✓ 包含币本位链接" || echo "      ✗ 缺少币本位链接"
echo ""
echo "   coin_futures_trading.html:"
grep -q "币本位合约交易 - AlphaFlow" coin_futures_trading.html && echo "      ✓ 页面标题正确" || echo "      ✗ 页面标题错误"
grep -q 'href="/futures_trading".*U本位合约' coin_futures_trading.html && echo "      ✓ 包含U本位链接" || echo "      ✗ 缺少U本位链接"

echo ""
echo "================================"
echo "验证完成！"
echo "================================"
