# Cursor Claude 按钮恢复配置指南

## 问题描述

当 Cursor 检测到 Claude CLI 或远程环境时，它会：
- 隐藏 UI 插件
- 将 Claude 按钮替换为 "Open in Terminal"

## 解决方案

要恢复 Claude 按钮，需要：

### 1. 启用本地 Claude Provider

1. 打开 Cursor 设置（`Ctrl + ,` 或 `Cmd + ,`）
2. 搜索 "Claude Provider" 或 "AI Provider"
3. 确保选择了本地 Claude Provider（而不是 CLI 或远程）

### 2. 禁用 CLI Fallback

1. 在 Cursor 设置中搜索 "CLI fallback" 或 "Fallback"
2. 禁用 CLI fallback 选项
3. 确保使用本地 Provider 而不是 CLI

### 3. 检查环境变量

确保没有设置以下环境变量（这些可能导致 Cursor 使用 CLI）：
- `CLAUDE_API_KEY`（如果指向 CLI）
- `ANTHROPIC_API_KEY`（如果指向 CLI）
- 任何远程环境相关的环境变量

### 4. 项目配置修改 (由 Cursor 自动完成)

为了尝试自动恢复 Claude 按钮，我已在项目根目录的 `.vscode` 文件夹下创建了一个 `settings.json` 文件，并添加了以下配置：

```json
{
    "cursor.ai.defaultProvider": "claude-local",
    "cursor.ai.claudeCliFallback": false
}
```

### 5. 重启 Cursor

完成配置后，请务必重启 Cursor IDE 以使设置生效。

## 验证

配置成功后，你应该能看到：
- ✅ Claude 按钮正常显示
- ✅ UI 插件可用
- ✅ 不再显示 "Open in Terminal" 按钮

## 注意事项

- 这些设置通常在 Cursor 的全局设置中，而不是项目特定的配置
- 如果使用远程开发环境（SSH、Docker 等），可能需要额外的配置
- 确保本地 Claude API 密钥已正确配置

