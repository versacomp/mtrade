"""
tastytrade REST API client.

Consumes REST services at api.cert.tastyworks.com (sandbox) per:
https://developer.tastytrade.com/api-overview/
"""

import time
from typing import Any

import requests

from api.oauth import TastytradeOAuth, TokenResponse, login_with_password


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
            token_resp = self._oauth.exchange_refresh_token()
            self._session_token = token_resp.session_token
            self._token_expires_at = token_resp.expires_at or 0.0
            self._user = token_resp.user

        if not self._session_token:
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
        token = self._ensure_token()
        # tastytrade uses Bearer token for OAuth; raw token also accepted for sessions
        auth_value = f"Bearer {token}" if not token.startswith("Bearer ") else token
        return {
            "Authorization": auth_value,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = requests.get(url, headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = requests.post(url, headers=self._headers(), json=json, timeout=30)
        r.raise_for_status()
        return r.json()

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
        # tastytrade market data: symbols as query param
        symbols_param = ",".join(symbols)
        path = f"/market-metrics/equity-futures-options?symbols={symbols_param}"
        try:
            data = self._get(path)
            return data.get("data", data)
        except requests.HTTPError:
            # Fallback: some APIs use different paths
            try:
                path = f"/market-metrics/quotes?symbols={symbols_param}"
                data = self._get(path)
                return data.get("data", data)
            except requests.HTTPError:
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
