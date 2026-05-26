#!/usr/bin/env python3
"""修复 top50.html 排名逻辑 + 排序方向，保障 UTF-8 编码不被破坏。"""
import re

PATH = "templates/top50.html"

with open(PATH, encoding="utf-8") as f:
    c = f.read()

# ---- 1. 替换排名逻辑 ----
# 旧代码特征：用 r.rank_score 的值计算排名位置
old_start = "var html = rows.map(function(r, idx) {"
old_end = "var wr = parseFloat(r.win_rate) || 0;"
idx_start = c.find(old_start)
idx_end = c.find(old_end, idx_start)

if idx_start == -1 or idx_end == -1:
    raise RuntimeError("Cannot find ranking code block in template")

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
    }

"""

c = c[:idx_start] + new_block + c[idx_end:]

# ---- 2. 排序方向改升序 ----
c = c.replace("var _sortAsc = false;", "var _sortAsc = true;")

# ---- 3. 写入 ----
with open(PATH, "w", encoding="utf-8") as f:
    f.write(c)

# ---- 4. 校验 ----
with open(PATH, "rb") as f:
    raw = f.read()
raw.decode("utf-8")  # raises if invalid

assert "超级大脑" in c or "超级大脑" in c, "Chinese text lost"
assert "TOP 50" in c, "TOP50 text lost"
assert "rankPos" in c, "rankPos not found"
assert "var _sortAsc = true;" in c, "sort direction unchanged"
assert "_data.length - rank" not in c, "old ranking code still present"

print("ALL CHECKS PASSED")
print("- UTF-8 encoding: valid")
print("- Chinese text:   intact")
print("- Ranking logic:  idx+1 (array-based)")
print("- Sort order:     ASC (rank_score 1=best first)")
