#!/usr/bin/env python3
"""修复 top50.html: 恢复原始中文 + 排名逻辑改为数组下标 + 排序改升序。"""
import subprocess, re

# 1. 从 git 取干净原始版
result = subprocess.run(
    ["git", "show", "4989a18:crypto-analyzer/templates/top50.html"],
    capture_output=True,
)
content = result.stdout.decode("utf-8")

# 2. TOP100 -> TOP50
content = content.replace("TOP 100", "TOP 50")

# 3. 修复排名逻辑 - 用正则匹配旧代码块替换
old_pattern = (
    r'  var html = rows\.map\(function\(r, idx\) \{\s*'
    r'    var rank = r\.rank_score;\s*'
    r'    var rankBadge, rowCls = "";\s*'
    r'    if \(rank === _data\.length\) \{\s*'
    r"      rankBadge = '<span class=\\"rank-gold inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold\\">1</span>';"
    r'\s*      rowCls = "top1";\s*'
    r"    \} else if \(rank === _data\.length - 1\) \{\s*"
    r"      rankBadge = '<span class=\\"rank-silver inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold\\">2</span>';"
    r'\s*      rowCls = "top2";\s*'
    r"    \} else if \(rank === _data\.length - 2\) \{\s*"
    r"      rankBadge = '<span class=\\"rank-bronze inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold\\">3</span>';"
    r'\s*      rowCls = "top3";\s*'
    r"    \} else \{\s*"
    r"      var displayRank = _data\.length - rank \+ 1;\s*"
    r"      rankBadge = '<span class=\\"rank-normal inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold\\">" + "' \\+ displayRank \\+ '</span>';\\s*"
    r"    \\}"
)

new_block = """  var html = rows.map(function(r, idx) {
    var rankPos = idx + 1;
    var rankBadge, rowCls = '';
    if (rankPos === 1) {
      rankBadge = '<span class="rank-gold inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">1</span>';
      rowCls = 'top1';
    } else if (rankPos === 2) {
      rankBadge = '<span class="rank-silver inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">2</span>';
      rowCls = 'top2';
    } else if (rankPos === 3) {
      rankBadge = '<span class="rank-bronze inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">3</span>';
      rowCls = 'top3';
    } else {
      rankBadge = '<span class="rank-normal inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">' + rankPos + '</span>';
    }"""

# Simpler approach: find the exact old code by string matching
old_marker = "var html = rows.map(function(r, idx) {"
idx_start = content.find(old_marker)
if idx_start == -1:
    print("ERROR: could not find ranking code")
    print(repr(content[content.find("rows.map"):content.find("rows.map")+500]))
    raise SystemExit(1)

# Find the end of this block - look for 'var wr = parseFloat'
var_wr_marker = "var wr = parseFloat(r.win_rate) || 0;"
idx_end = content.find(var_wr_marker, idx_start)
if idx_end == -1:
    print("ERROR: could not find end marker")
    raise SystemExit(1)

old_block = content[idx_start:idx_end]
new_block_clean = """  var html = rows.map(function(r, idx) {
    var rankPos = idx + 1;
    var rankBadge, rowCls = '';
    if (rankPos === 1) {
      rankBadge = '<span class="rank-gold inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">1</span>';
      rowCls = 'top1';
    } else if (rankPos === 2) {
      rankBadge = '<span class="rank-silver inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">2</span>';
      rowCls = 'top2';
    } else if (rankPos === 3) {
      rankBadge = '<span class="rank-bronze inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">3</span>';
      rowCls = 'top3';
    } else {
      rankBadge = '<span class="rank-normal inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold">' + rankPos + '</span>';
    }

"""

content = content[:idx_start] + new_block_clean + content[idx_end:]

# 4. 排序方向
content = content.replace("var _sortAsc = false;", "var _sortAsc = true;")

# 5. 写入
with open("templates/top50.html", "w", encoding="utf-8") as f:
    f.write(content)

# 6. 验证
with open("templates/top50.html", "rb") as f:
    raw = f.read()
raw.decode("utf-8")  # must not raise
assert "超级大脑" in content, "Chinese missing"
assert "TOP 50" in content, "TOP50 missing"
assert "rankPos" in content, "rankPos missing"
assert "var _sortAsc = true;" in content, "sort direction wrong"
assert "_data.length - rank" not in content, "old ranking still present"
print("ALL CHECKS PASSED")
