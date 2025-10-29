# Windows Git 检查步骤

## 步骤1: 检查你是否在正确的目录

在Windows PowerShell或CMD中执行：
```bash
pwd  # 或者 cd (在CMD中)
```

应该显示类似：
```
C:\Users\你的用户名\code\test2\crypto-analyzer
```

如果不是，先切换到正确的目录：
```bash
cd C:\path\to\crypto-analyzer
```

## 步骤2: 检查当前分支和远程状态

```bash
git status
git remote -v
git log --oneline -3
```

应该看到：
- `On branch master`
- `origin  git@github.com:shanglily01-lab/test2.git` (或 https URL)
- 最新的commit应该是 `99b2b2f Fix Windows FileResponse OSError by converting Path to string`

## 步骤3: 强制拉取最新代码

如果git log显示的最新commit不是 `99b2b2f`，执行：

```bash
git fetch origin
git log origin/master --oneline -3
```

查看远程是否有 `99b2b2f` 这个commit。

如果远程有，但本地没有：
```bash
git pull origin master
```

## 步骤4: 如果还是不行，检查是否有本地修改

```bash
git status
```

如果显示有修改的文件，可以：
1. 保存你的修改：`git stash`
2. 拉取最新代码：`git pull`
3. 恢复你的修改：`git stash pop`

## 步骤5: 验证是否已经有最新代码

检查 app/main.py 中的FileResponse调用：

```bash
grep -n "FileResponse(str(" app/main.py
```

如果能看到类似这样的输出，说明已经是最新的：
```
293:        return FileResponse(str(favicon_path))
304:        return FileResponse(str(guide_path), media_type="text/markdown")
...
```

## 如果确认已经是最新的

那就直接重启服务器：

1. 停止当前运行的服务器（Ctrl+C）
2. 重新启动：
   ```bash
   python app/main.py
   ```
3. 测试页面：
   - http://localhost:8000/dashboard
   - http://localhost:8000/strategies
   - http://localhost:8000/auto-trading

## 预期结果

之前的错误：
```
OSError: [Errno 22] Invalid argument
```

应该完全消失，所有页面都能正常加载！
