"""
tastytrade REST API client.

Connects to either the production API (api.tastyworks.com) or the sandbox
environment (api.cert.tastyworks.com). The base URL is supplied by the caller
and resolved from environment variables via config.get_api_base().

API reference: https://developer.tastytrade.com/api-overview/
"""

import logging
import time
from datetime import date
from typing import Any

import requests

from api.oauth import TastytradeOAuth, TokenResponse, login_with_password

log = logging.getLogger(__name__)


class TastytradeClient:
    """
    Client for tastytrade REST API.

    Supports OAuth2 (refresh token) or login/password authentication.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
        session_token: str | None = None,
    ) -> None:
        """
        Initialise the client for *base_url*.

        When *client_id*, *client_secret*, and *refresh_token* are all provided
        an :class:`~api.oauth.TastytradeOAuth` helper is created so that the
        session token is refreshed automatically before each request.  Supplying
        *session_token* directly skips OAuth and uses that token as-is.
        """
        self.base_url = base_url.rstrip("/")
        self._session_token: str | None = session_token
        self._token_expires_at: float = 0.0
        self._user: dict[str, Any] = {}

        self._oauth: TastytradeOAuth | None = None
        if client_id and client_secret and refresh_token:
            self._oauth = TastytradeOAuth(
                base_url, client_id, client_secret, refresh_token
            )

    def _ensure_token(self) -> str:
        """Ensure we have a valid session token, refreshing if needed."""
        if self._oauth and (
            not self._session_token or time.time() >= self._token_expires_at
        ):
            log.debug("Token expired or missing — refreshing via OAuth")
            token_resp = self._oauth.exchange_refresh_token()
            self._session_token = token_resp.session_token
            self._token_expires_at = token_resp.expires_at or 0.0
            self._user = token_resp.user
            log.info("Token refreshed — expires at %.0f", self._token_expires_at)

        if not self._session_token:
            log.error("No session token available — not authenticated")
            raise ValueError("Not authenticated. Use OAuth credentials or login().")

        return self._session_token

    def login(self, username: str, password: str) -> None:
        """
        Login with username/password (for sandbox or when OAuth not configured).
        """
        resp = login_with_password(self.base_url, username, password)
        self._session_token = resp.session_token
        self._token_expires_at = resp.expires_at or 0.0
        self._user = resp.user

    def set_session_token(self, token: str, expires_at: float | None = None) -> None:
        """Set session token directly (e.g., after external OAuth flow)."""
        self._session_token = token
        self._token_expires_at = expires_at or (time.time() + 900)

    def _headers(self) -> dict[str, str]:
        """Build the authorisation headers required by every tastytrade API request."""
        token = self._ensure_token()
        # tastytrade uses Bearer token for OAuth; raw token also accepted for sessions
        auth_value = f"Bearer {token}" if not token.startswith("Bearer ") else token
        return {
            "Authorization": auth_value,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """Perform an authenticated GET request and return the parsed JSON response dict; raise on HTTP errors."""
        url = f"{self.base_url}{path}"
        try:
            r = requests.get(url, headers=self._headers(), params=params, timeout=30)
            log.debug("GET ← %s %s | %s", r.status_code, r.reason, url)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            log.error("GET ✗ %s | %s | %s", exc.response.status_code, url, body)
            raise
        except requests.RequestException as exc:
            log.error("GET ✗ network error | %s | %s", url, exc)
            raise

    def _delete(self, path: str) -> dict[str, Any]:
        """Perform an authenticated DELETE request and return the parsed JSON response dict (empty dict if no content); raise on HTTP errors."""
        url = f"{self.base_url}{path}"
        try:
            r = requests.delete(url, headers=self._headers(), timeout=30)
            log.debug("DELETE ← %s %s | %s", r.status_code, r.reason, url)
            r.raise_for_status()
            return r.json() if r.content else {}
        except requests.HTTPError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            log.error("DELETE ✗ %s | %s | %s", exc.response.status_code, url, body)
            raise
        except requests.RequestException as exc:
            log.error("DELETE ✗ network error | %s | %s", url, exc)
            raise

    def _post(self, path: str, json: dict | None = None) -> dict[str, Any]:
        """Perform an authenticated POST request with optional JSON body and return the parsed JSON response dict; raise on HTTP errors."""
        url = f"{self.base_url}{path}"
        try:
            r = requests.post(url, headers=self._headers(), json=json, timeout=30)
            log.debug("POST ← %s %s | %s", r.status_code, r.reason, url)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            log.error("POST ✗ %s | %s | %s", exc.response.status_code, url, body)
            raise
        except requests.RequestException as exc:
            log.error("POST ✗ network error | %s | %s", url, exc)
            raise

    @property
    def user(self) -> dict[str, Any]:
        """Current user info after authentication."""
        return self._user

    def get_accounts(self) -> list[dict]:
        """GET /customers/me/accounts - List accounts for the authenticated customer."""
        data = self._get("/customers/me/accounts")
        return data.get("data", {}).get("items", [])

    def get_balances(self, account_number: str) -> dict:
        """GET /accounts/:account-number/balances"""
        data = self._get(f"/accounts/{account_number}/balances")
        return data.get("data", {})

    def get_positions(self, account_number: str) -> list[dict]:
        """GET /accounts/:account-number/positions"""
        data = self._get(f"/accounts/{account_number}/positions")
        return data.get("data", {}).get("items", [])

    def get_market_quotes(self, symbols: list[str]) -> dict:
        """
        Fetch market quotes for symbols.

        tastytrade market data endpoint per API docs.
        Uses /market-metrics/equity-futures-options or equivalent.
        """
        if not symbols:
            return {}
        symbols_param = ",".join(symbols)
        path = f"/market-metrics/equity-futures-options?symbols={symbols_param}"
        try:
            data = self._get(path)
            log.debug("get_market_quotes OK — symbols=%s", symbols)
            return data.get("data", data)
        except requests.HTTPError:
            log.warning("get_market_quotes primary path failed — trying fallback")
            try:
                path = f"/market-metrics/quotes?symbols={symbols_param}"
                data = self._get(path)
                log.debug("get_market_quotes fallback OK — symbols=%s", symbols)
                return data.get("data", data)
            except requests.HTTPError:
                log.error("get_market_quotes both paths failed — symbols=%s", symbols)
                return {}

    def get_quote(self, symbol: str) -> dict:
        """Get quote for a single symbol."""
        result = self.get_market_quotes([symbol])
        if isinstance(result, dict) and symbol in result:
            return result[symbol]
        if isinstance(result, list):
            for item in result:
                if item.get("symbol") == symbol:
                    return item
        return {}

    def get_futures_streamer_symbol(self, underlying: str) -> str:
        """
        Look up the front-month futures streamer-symbol for DXLink subscription.

        Calls GET /instruments/futures?product-code={code} and returns the
        nearest-expiry active contract's streamer-symbol (e.g. '/MESU26:XCME').
        Falls back to '/{underlying}' on any error so streaming can still attempt.
        """
        product_code = underlying.upper().lstrip("/")
        try:
            data  = self._get("/instruments/futures", params={"product-code": product_code})
            items = data.get("data", {}).get("items", [])
            if not items:
                log.warning("get_futures_streamer_symbol: no contracts for %s", product_code)
                return f"/{product_code}"

            today = date.today()
            candidates: list[tuple[date, str]] = []
            for item in items:
                sym = item.get("streamer-symbol", "")
                exp = item.get("expiration-date", "")
                if not sym or not exp:
                    continue
                try:
                    exp_date = date.fromisoformat(exp)
                    if exp_date >= today:
                        candidates.append((exp_date, sym))
                except ValueError:
                    continue

            if not candidates:
                log.warning("get_futures_streamer_symbol: no active contracts for %s", product_code)
                return f"/{product_code}"

            candidates.sort(key=lambda x: x[0])
            front_month_sym = candidates[0][1]
            log.info("Futures streamer-symbol: %s → %s", product_code, front_month_sym)
            return front_month_sym

        except Exception as exc:
            log.warning("get_futures_streamer_symbol failed for %s: %s", product_code, exc)
            return f"/{product_code}"

    def get_quote_token(self) -> dict:
        """
        GET /api-quote-tokens — retrieve a DXLink streaming token.

        Returns dict with keys: token, dxlink-url (and optionally websocket-url,
        level, streamer-token) per:
        https://developer.tastytrade.com/streaming-market-data/#get-api-quote-tokens
        """
        data = self._get("/api-quote-tokens")
        return data.get("data", data)

    def place_order(self, account_number: str, order_body: dict) -> dict[str, Any]:
        """POST /accounts/{account_number}/orders — place a new order."""
        log.info("Placing order: acct=%s body=%s", account_number, order_body)
        data = self._post(f"/accounts/{account_number}/orders", json=order_body)
        return data.get("data", data)

    def cancel_order(self, account_number: str, order_id: str) -> dict[str, Any]:
        """DELETE /accounts/{account_number}/orders/{order_id} — cancel an open order."""
        log.info("Cancelling order: acct=%s order_id=%s", account_number, order_id)
        data = self._delete(f"/accounts/{account_number}/orders/{order_id}")
        return data.get("data", data)

    def get_candle_history(self, symbol: str, n_minutes: int = 240) -> list[dict]:
        """
        Fetch historical 1-minute OHLCV candles for a symbol.

        Returns list of dicts with keys: timestamp, open, high, low, close.
        Returns empty list if the API does not support the endpoint.
        """
        end_ts = int(time.time())
        start_ts = end_ts - n_minutes * 60

        log.debug(
            "get_candle_history — symbol=%s n_minutes=%d start=%d end=%d",
            symbol, n_minutes, start_ts, end_ts,
        )

        # Pattern 1: tastytrade candle history endpoint
        try:
            data = self._get(
                f"/market-data/candles/{symbol}/history",
                params={"period": "1m", "start-time": start_ts, "end-time": end_ts},
            )
            candles = data.get("data", {}).get("candles", [])
            if candles:
                log.info("get_candle_history pattern-1 OK — %d candles for %s", len(candles), symbol)
                return candles
            log.warning("get_candle_history pattern-1 returned empty candles for %s", symbol)
        except Exception as exc:
            log.warning("get_candle_history pattern-1 failed for %s — %s", symbol, exc)

        # Pattern 2: market-data history (TD Ameritrade-style params)
        try:
            data = self._get(
                "/market-data/history",
                params={
                    "symbol": symbol,
                    "period-type": "day",
                    "period": 1,
                    "frequency-type": "minute",
                    "frequency": 1,
                    "start-date": start_ts * 1000,
                    "end-date": end_ts * 1000,
                },
            )
            candles = data.get("candles", []) or data.get("data", {}).get("candles", [])
            if candles:
                log.info("get_candle_history pattern-2 OK — %d candles for %s", len(candles), symbol)
                return candles
            log.warning("get_candle_history pattern-2 returned empty candles for %s", symbol)
        except Exception as exc:
            log.warning("get_candle_history pattern-2 failed for %s — %s", symbol, exc)

        log.error("get_candle_history — all patterns failed for %s, falling back to demo", symbol)
        return []
