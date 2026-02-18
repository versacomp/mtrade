"""MTrade application configuration."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

# API Configuration
TASTYTRADE_API_BASE = os.getenv(
    "TASTYTRADE_API_BASE", "https://api.cert.tastyworks.com"
)
TASTYTRADE_CLIENT_ID = os.getenv("TASTYTRADE_CLIENT_ID", "")
TASTYTRADE_CLIENT_SECRET = os.getenv("TASTYTRADE_CLIENT_SECRET", "")
TASTYTRADE_REFRESH_TOKEN = os.getenv("TASTYTRADE_REFRESH_TOKEN", "")

# Major US Index symbols (tastytrade symbology)
# SPX = S&P 500 Index, NDX = Nasdaq 100, DJX = Dow Jones
INDEX_SYMBOLS = ["SPX", "NDX", "DJX"]
# ETF equivalents if index symbols unavailable: SPY, QQQ, DIA
