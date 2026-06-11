"""Jinja2 rendering for desktop HTML pages with shared sidebar partial."""

from pathlib import Path
import inspect

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

project_root = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(project_root / "templates"))

# Desktop pages that use partials/desktop_sidebar.html (exclude index + mobile_*.html)
DESKTOP_SIDEBAR_TEMPLATES = frozenset({
    "ai_shadow_compare.html",
    "api-keys.html",
    "binance_news.html",
    "blockchain_gas.html",
    "coin_futures_trading.html",
    "corporate_treasury.html",
    "dashboard.html",
    "data_management.html",
    "deepseek_explore.html",
    "etf_data.html",
    "futures_review.html",
    "futures_trading.html",
    "gemini_advisor_reviews.html",
    "gemini_explore.html",
    "live_trading.html",
    "market_regime.html",
    "paper_trading.html",
    "signal_blacklist.html",
    "spot_trading.html",
    "symbol_blacklist.html",
    "system_settings.html",
    "technical_signals.html",
    "top50.html",
    "trading_manual.html",
})


def render_desktop_page(request: Request, template_name: str) -> HTMLResponse:
    path = project_root / "templates" / template_name
    if not path.exists():
        raise FileNotFoundError(template_name)
    params = list(inspect.signature(templates.TemplateResponse).parameters)
    if params and params[0] == "request":
        return templates.TemplateResponse(request, template_name, {"request": request})
    return templates.TemplateResponse(template_name, {"request": request})


def render_desktop_html(request: Request, template_name: str) -> str:
    """Render desktop template to HTML string (for token injection etc.)."""
    path = project_root / "templates" / template_name
    if not path.exists():
        raise FileNotFoundError(template_name)
    return templates.get_template(template_name).render({"request": request})
