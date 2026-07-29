"""Legacy import shim — use app.api.advisor_api."""
from __future__ import annotations

from app.api.advisor_api import router  # noqa: F401

__all__ = ["router"]
