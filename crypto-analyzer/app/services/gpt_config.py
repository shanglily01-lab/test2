"""GPT / OpenAI 配置 — 从 .env 读取 (dotenv_values)，不依赖进程 os.environ."""
from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

_ROOT = Path(__file__).resolve().parents[2]
_ENV = dotenv_values(_ROOT / ".env")


def _get(key: str, default: str = "") -> str:
    raw = _ENV.get(key)
    if raw is None:
        return default
    s = str(raw).strip()
    return s if s else default


def _sanitize_openai_api_key(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    idx = s.find("sk-")
    if idx >= 0:
        return s[idx:].split()[0].strip()
    return s


def _normalize_openai_base_url(raw: str) -> str:
    base = (raw or "").strip().rstrip("/")
    if not base:
        return "https://api.openai.com/v1"
    return base if base.endswith("/v1") else f"{base}/v1"


GPT_API_KEY = _sanitize_openai_api_key(_get("OPENAI_API_KEY"))
GPT_MODEL = _get("GPT_MODEL") or _get("OPENAI_MODEL", "gpt-4o-mini")
GPT_TIMEOUT_S = int(_get("GPT_TIMEOUT_S") or _get("OPENAI_TIMEOUT_S", "180"))
GPT_BASE_URL = _normalize_openai_base_url(
    _get("OPENAI_BASE_URL", "https://api.openai.com/v1")
)
