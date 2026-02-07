import os
from pathlib import Path

# 读取被使用的文件列表
with open('used_files.txt', 'r', encoding='utf-8') as f:
    used_files = set(line.strip() for line in f if line.strip())

# 添加核心文件
used_files.add('check_big4_trend.py')
used_files.add('reset_weights.py')

# 添加templates
templates_dir = Path('templates')
if templates_dir.exists():
    for file in templates_dir.rglob('*'):
        if file.is_file():
            used_files.add(str(file).replace('\', '/'))

print(f'有用文件: {len(used_files)}')

# 扫描所有Python文件
all_py_files = []
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', 'env', '__pycache__', 'node_modules', 'logs']]
    
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file).replace('\', '/').lstrip('./')
            all_py_files.append(filepath)

# 找出未使用的文件
unused = [f for f in all_py_files if f not in used_files]

print(f'未使用文件: {len(unused)}')
print('\n未使用的文件列表:')
for file in sorted(unused):
    print(file)

with open('unused_files_final.txt', 'w', encoding='utf-8') as f:
    for file in sorted(unused):
        f.write(file + '\n')
