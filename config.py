"""MTrade application configuration."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

# API base URLs — loaded once from .env at startup
TASTYTRADE_API_BASE         = os.getenv("TASTYTRADE_API_BASE",         "https://api.tastyworks.com")
TASTYTRADE_API_BASE_SANDBOX = os.getenv("TASTYTRADE_API_BASE_SANDBOX", "https://api.cert.tastyworks.com")

# Production OAuth credentials
TASTYTRADE_CLIENT_ID     = os.getenv("TASTYTRADE_CLIENT_ID", "")
TASTYTRADE_CLIENT_SECRET = os.getenv("TASTYTRADE_CLIENT_SECRET", "")
TASTYTRADE_REFRESH_TOKEN = os.getenv("TASTYTRADE_REFRESH_TOKEN", "")

# Sandbox OAuth credentials
TASTYTRADE_CLIENT_ID_SANDBOX     = os.getenv("TASTYTRADE_CLIENT_ID_SANDBOX", "")
TASTYTRADE_CLIENT_SECRET_SANDBOX = os.getenv("TASTYTRADE_CLIENT_SECRET_SANDBOX", "")
TASTYTRADE_REFRESH_TOKEN_SANDBOX = os.getenv("TASTYTRADE_REFRESH_TOKEN_SANDBOX", "")

# ── Environment toggle ─────────────────────────────────────────────────────────
# Default to sandbox so users must explicitly opt in to production.
_use_sandbox: bool = True


def get_api_base() -> str:
    """Return the active API base URL based on the current environment selection."""
    return TASTYTRADE_API_BASE_SANDBOX if _use_sandbox else TASTYTRADE_API_BASE


def set_sandbox(sandbox: bool) -> None:
    """Switch between sandbox (True) and production (False) environments."""
    global _use_sandbox
    _use_sandbox = sandbox


def is_sandbox() -> bool:
    return _use_sandbox


def get_oauth_credentials() -> tuple[str, str, str]:
    """Return (client_id, client_secret, refresh_token) for the active environment."""
    if _use_sandbox:
        return TASTYTRADE_CLIENT_ID_SANDBOX, TASTYTRADE_CLIENT_SECRET_SANDBOX, TASTYTRADE_REFRESH_TOKEN_SANDBOX
    return TASTYTRADE_CLIENT_ID, TASTYTRADE_CLIENT_SECRET, TASTYTRADE_REFRESH_TOKEN

# ── User preferences (persisted to ~/.mtrade/preferences.json) ────────────────
import json as _json

_PREFS_PATH = Path.home() / ".mtrade" / "preferences.json"


def _load_prefs() -> dict:
    try:
        return _json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_prefs(prefs: dict) -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(_json.dumps(prefs, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_pref(key: str, default=None):
    return _load_prefs().get(key, default)


def set_pref(key: str, value) -> None:
    prefs = _load_prefs()
    prefs[key] = value
    _save_prefs(prefs)


# Major US Index symbols (tastytrade symbology)
# SPX = S&P 500 Index, NDX = Nasdaq 100, DJX = Dow Jones
INDEX_SYMBOLS = ["SPX", "NDX", "DJX"]
# ETF equivalents if index symbols unavailable: SPY, QQQ, DIA
