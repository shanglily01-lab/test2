"""
反 import 检查: 禁止业务代码直接出现 Binance REST 端点字符串.

约定:
    所有业务代码必须通过 app.services.binance_data_hub 获取币安数据,
    不允许出现 fapi.binance.com / dapi.binance.com / api.binance.com 字符串.

白名单 (必须直连或定义端点常量的合法模块):
    - app/services/binance_data_hub.py  (唯一合法 REST 入口)
    - app/services/binance_ws_price.py  (WS 服务, 但本来就不该出现 REST 端点)
    - app/utils/binance_rate_guard.py   (熔断器, 注释里有端点示例)
    - app/collectors/smart_futures_collector.py  (K 线采集器, 自带熔断已接)
    - scripts/check_no_direct_binance.py  (本脚本)

用法:
    python scripts/check_no_direct_binance.py
退出码:
    0  无违规
    1  发现违规
启动时也可由 main.py lifespan 调用 run_check(), 仅打告警, 不阻塞启动.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Windows 终端 GBK 默认编码无法打印部分中文/特殊符号, 输出时统一切到 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

FORBIDDEN_PATTERNS = [
    # REST 端点 URL
    r"fapi\.binance\.com",
    r"dapi\.binance\.com",
    r"\bapi\.binance\.com",
    # python-binance SDK 直连 (Client 类底层会打币安 REST)
    r"\bfrom\s+binance\.client\s+import",
    r"\bfrom\s+binance\s+import\s+Client",
    r"\bimport\s+binance\.client",
]

# WebSocket 端点 (fstream/dstream/stream) 不算 REST 配额, 不在禁止范围
# 公告 CMS (www.binance.com/bapi) 也不在 fapi 配额内, 但建议接 rate_guard 守卫

WHITELIST = {
    # 唯一合法 REST 入口
    "app/services/binance_data_hub.py",
    # WS 服务 (wss:// 不算 REST 配额)
    "app/services/binance_ws_price.py",
    # 熔断器自身
    "app/utils/binance_rate_guard.py",
    # K 线采集器 (已接 rate_guard)
    "app/collectors/smart_futures_collector.py",
    # 本扫描脚本
    "scripts/check_no_direct_binance.py",
    # 实盘 engine (BASE_URL 常量定义, 实际请求 _request 已接 rate_guard)
    "app/trading/binance_futures_engine.py",
    # 公告监控 (CMS 端点 www.binance.com/bapi, 不属交易 REST 配额, 已加 rate_guard 守卫)
    "app/services/binance_news_monitor.py",
    # 纯注释引用 (无实际请求)
    "app/services/position_sl_tp_monitor.py",
    # 本地一次性 K 线回填工具 (在本地机器跑, 不消耗服务器 IP 配额)
    "scripts/backfill_klines.py",
}

SCAN_ROOTS = ["app", "scripts"]
SCAN_TOP_LEVEL_FILES = [
    "smart_trader_service.py",
    "u_coin_style_trader_service.py",
    "fast_collector_service.py",
    "ws_kline_collector_service.py",
]


def _to_posix(p: Path, project_root: Path) -> str:
    try:
        rel = p.relative_to(project_root)
    except ValueError:
        return str(p)
    return rel.as_posix()


def scan(project_root: Path) -> List[Tuple[str, int, str]]:
    """
    扫描项目, 返回违规列表 [(rel_path, lineno, line_content), ...].
    """
    violations: List[Tuple[str, int, str]] = []
    compiled = [re.compile(p) for p in FORBIDDEN_PATTERNS]

    paths: List[Path] = []
    for root in SCAN_ROOTS:
        root_path = project_root / root
        if root_path.exists():
            paths.extend(root_path.rglob("*.py"))
    for name in SCAN_TOP_LEVEL_FILES:
        p = project_root / name
        if p.exists():
            paths.append(p)

    for path in paths:
        rel = _to_posix(path, project_root)
        if rel in WHITELIST:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"[check_no_direct_binance] 读取失败: {rel}: {e}", file=sys.stderr)
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat in compiled:
                if pat.search(line):
                    violations.append((rel, lineno, line.strip()))
                    break
    return violations


def run_check(project_root: Path = None, fail_hard: bool = False) -> int:
    """
    执行扫描.

    Args:
        project_root: 项目根, 默认从本脚本位置向上推
        fail_hard:    True 时违规返回 1, False 仅打告警返回 0

    Returns:
        0 / 1
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    violations = scan(project_root)
    if not violations:
        print("[check_no_direct_binance] OK - 没有发现直连 Binance REST 的代码")
        return 0

    print("=" * 70)
    print(f"[check_no_direct_binance] 发现 {len(violations)} 处直连 Binance REST 的代码:")
    print("=" * 70)
    for rel, lineno, line in violations:
        print(f"  {rel}:{lineno}  {line[:140]}")
    print("=" * 70)
    print("修复方法: 改为通过 app.services.binance_data_hub 取数据.")
    print("如确属合法直连 (如新增 collector), 请将文件加入 WHITELIST.")
    print("=" * 70)
    return 1 if fail_hard else 0


if __name__ == "__main__":
    sys.exit(run_check(fail_hard=True))
