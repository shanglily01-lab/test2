"""One-off: replace duplicated sidebar HTML with Jinja include."""
import re
from pathlib import Path

INCLUDE = '{% include "partials/desktop_sidebar.html" %}'
FILES = [
    "ai_shadow_compare.html", "api-keys.html", "binance_news.html", "blockchain_gas.html",
    "coin_futures_trading.html", "corporate_treasury.html", "dashboard.html", "data_management.html",
    "deepseek_explore.html", "etf_data.html", "futures_review.html", "futures_trading.html",
    "gemini_advisor_reviews.html", "gemini_explore.html", "live_trading.html",
    "market_regime.html", "paper_trading.html", "signal_blacklist.html", "spot_trading.html",
    "symbol_blacklist.html", "system_settings.html", "technical_signals.html", "top50.html",
    "trading_manual.html",
]

pat = re.compile(
    r"(?:<!--[^\n>]*(?:[Ss]idebar|SideNavBar)[^\n]*-->\s*)?"
    r"<aside\b.*?</aside>\s*"
    r"(?:<script>\s*\(function\(\)\{.*?sidebar-nav.*?</script>\s*)?"
    r'(?:<div id="sidebar-overlay".*?</div>\s*)?',
    re.DOTALL,
)

root = Path(__file__).resolve().parent.parent / "templates"
failed = []
for name in FILES:
    p = root / name
    text = p.read_text(encoding="utf-8")
    if INCLUDE in text:
        print(f"skip (already): {name}")
        continue
    new_text, n = pat.subn(INCLUDE + "\n", text, count=1)
    if n != 1:
        failed.append(name)
        print(f"FAIL {name}: {n} replacements")
        continue
    p.write_text(new_text, encoding="utf-8")
    print(f"ok: {name}")

if failed:
    raise SystemExit(1)
