"""
启动策略执行器的辅助脚本
"""
import sys
from pathlib import Path
import subprocess
import os

project_root = Path(__file__).parent.parent
os.chdir(project_root)

print("=" * 80)
print("启动策略执行器")
print("=" * 80)
print()

# 检查策略执行器文件是否存在
if not (project_root / 'app' / 'strategy_scheduler.py').exists():
    print("[ERROR] 找不到策略执行器文件: app/strategy_scheduler.py")
    sys.exit(1)

print("正在启动策略执行器...")
print("提示: 按 Ctrl+C 停止服务")
print()

# 启动策略执行器
try:
    subprocess.run([sys.executable, 'app/strategy_scheduler.py'], cwd=project_root)
except KeyboardInterrupt:
    print("\n策略执行器已停止")
except Exception as e:
    print(f"\n[ERROR] 启动失败: {e}")
    import traceback
    traceback.print_exc()

