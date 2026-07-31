"""DeepSeek V4 API helpers — thinking 默认开会导致 content 空、JSON 开仓全挂。"""
from __future__ import annotations

# V4-Flash/Pro：thinking 默认 enabled；推理占满 max_tokens 时 message.content 为空。
# 探索/预测/顾问只要 JSON 正文 → 必须显式关闭。
DEEPSEEK_DISABLE_THINKING_BODY = {"thinking": {"type": "disabled"}}
