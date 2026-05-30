# 已移除的历史脚本

以下文件已从仓库根目录删除（2026-05-31 清理），逻辑已并入 `smart_trader_service` / `multi_strategy_service`：

- `strategy_live.py` — S8 顶部反转已清理，从未有效开仓
- `strategy_bigmid.py` — Gemini 抄底已迁入 S9 (`scan_s9_gemini_ai`)

回滚请用 git 历史恢复，勿再启用 `deploy/systemd/crypto-strategy-*.service`。
