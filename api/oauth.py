"""
OAuth implementation for tastytrade API.

Implements OAuth2 refresh token flow as described at:
https://developer.tastytrade.com/oauth/

Credentials are obtained from OAuth application registration at:
https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications

For sandbox: Use credentials from https://developer.tastytrade.com/sandbox/
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)


@dataclass
class TokenResponse:
    """OAuth token response."""

    session_token: str
    user: dict[str, Any]
    expires_at: float | None = None  # Unix timestamp when token expires


class TastytradeOAuth:
    """
    OAuth2 client for tastytrade API.

    Uses refresh token flow:
    1. Exchange refresh_token + client credentials for session token
    2. Session tokens expire in ~15 minutes
    3. Refresh tokens never expire
    """

    SESSION_DURATION_SECONDS = 900  # 15 minutes

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> None:
        """
        Initialise the OAuth2 client.

        Args:
            base_url:      API base URL (production or sandbox), trailing slash stripped.
            client_id:     OAuth application client ID.
            client_secret: OAuth application client secret.
            refresh_token: Long-lived refresh token obtained from the OAuth app registration.
        """
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    def exchange_refresh_token(self) -> TokenResponse:
        """
        Exchange refresh token for session token.

        OAuth2 flow per https://developer.tastytrade.com/oauth/
        Tries standard OAuth2 token endpoint; falls back to tastytrade /sessions
        with OAuth params if needed.
        """
        # Try OAuth2 standard endpoint first
        token_url = f"{self.base_url}/oauth/token"
        session_url = f"{self.base_url}/sessions"
        endpoints_to_try = [
            (token_url, {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }),
            # tastytrade may use /sessions with refresh_token
            (session_url, {
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }),
        ]

        for url, payload in endpoints_to_try:
            log.debug("OAuth token exchange → POST request")
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                )
                log.debug("OAuth token exchange ← %s %s", response.status_code, response.reason)
                response.raise_for_status()
                data = response.json()
                token = self._parse_token_response(data)
                log.info("OAuth token exchange OK — user=%s", token.user.get("username", "?"))
                return token
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                reason = exc.response.reason if exc.response is not None else ""
                log.warning("OAuth token exchange ✗ %s %s", status, reason)
                continue
            except requests.RequestException as exc:
                log.warning("OAuth token exchange ✗ network error | %s",exc)
                continue

        log.error("OAuth token exchange — all endpoints failed. Check credentials and base URL.")
        raise ValueError(
            "Failed to exchange refresh token. Verify OAuth credentials and API base URL."
        )

    def _parse_token_response(self, data: dict) -> TokenResponse:
        """Parse token response from tastytrade API (nested under 'data' key)."""
        inner = data.get("data", data)
        session_token = (
            inner.get("session-token")
            or inner.get("access_token")
            or data.get("access_token")
        )
        user = inner.get("user", data.get("user", {}))
        if not session_token:
            raise ValueError("No session token in OAuth response")
        expires_at = time.time() + self.SESSION_DURATION_SECONDS
        return TokenResponse(session_token=session_token, user=user, expires_at=expires_at)


def login_with_password(base_url: str, login: str, password: str) -> TokenResponse:
    """
    Login with username/password (alternative to OAuth for sandbox).

    Uses POST /sessions per https://developer.tastytrade.com/basic-api-usage
    """
    url = f"{base_url.rstrip('/')}/sessions"
    log.debug("login_with_password → POST %s | user=%s", url, login)
    try:
        response = requests.post(
            url,
            json={"login": login, "password": password, "remember-me": True},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        log.debug("login_with_password ← %s %s", response.status_code, response.reason)
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = exc.response.text[:300] if exc.response is not None else ""
        log.error("login_with_password ✗ %s | %s", exc.response.status_code, body)
        raise
    data = response.json()
    inner = data.get("data", data)
    session_token = inner.get("session-token")
    user = inner.get("user", {})
    if not session_token:
        log.error("login_with_password — no session-token in response: %s", str(data)[:300])
        raise ValueError("No session token in response")
    log.info("login_with_password OK — user=%s", user.get("username", "?"))
    expires_at = time.time() + TastytradeOAuth.SESSION_DURATION_SECONDS
    return TokenResponse(session_token=session_token, user=user, expires_at=expires_at)
