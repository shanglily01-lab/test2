"""Gemini LLM env/config (shared by legacy Gemini paths + Big4 Gemini)."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_TIMEOUT_S = int(os.getenv("GEMINI_SWAN_TIMEOUT_S", "180"))

__all__ = ["GEMINI_MODEL", "GEMINI_API_KEY", "GEMINI_TIMEOUT_S"]
