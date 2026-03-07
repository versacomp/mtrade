"""Unit tests for api/oauth.py."""

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from api.oauth import TokenResponse, TastytradeOAuth, login_with_password


# ---------------------------------------------------------------------------
# TokenResponse dataclass
# ---------------------------------------------------------------------------

class TestTokenResponse:
    def test_basic_construction(self):
        tr = TokenResponse(session_token="tok123", user={"username": "alice"})
        assert tr.session_token == "tok123"
        assert tr.user == {"username": "alice"}
        assert tr.expires_at is None

    def test_construction_with_expiry(self):
        ts = time.time() + 900
        tr = TokenResponse(session_token="tok", user={}, expires_at=ts)
        assert tr.expires_at == ts


# ---------------------------------------------------------------------------
# TastytradeOAuth._parse_token_response
# ---------------------------------------------------------------------------

class TestParseTokenResponse:
    def setup_method(self):
        self.oauth = TastytradeOAuth(
            base_url="https://api.example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
        )

    def test_parse_nested_data_session_token(self):
        data = {
            "data": {
                "session-token": "st-nested",
                "user": {"username": "bob"},
            }
        }
        result = self.oauth._parse_token_response(data)
        assert result.session_token == "st-nested"
        assert result.user == {"username": "bob"}

    def test_parse_access_token_in_data(self):
        data = {"data": {"access_token": "at-nested"}}
        result = self.oauth._parse_token_response(data)
        assert result.session_token == "at-nested"

    def test_parse_access_token_top_level(self):
        data = {"access_token": "at-top"}
        result = self.oauth._parse_token_response(data)
        assert result.session_token == "at-top"

    def test_parse_sets_expires_at(self):
        before = time.time()
        data = {"data": {"session-token": "tok"}}
        result = self.oauth._parse_token_response(data)
        assert result.expires_at >= before + TastytradeOAuth.SESSION_DURATION_SECONDS - 1

    def test_parse_raises_on_missing_token(self):
        with pytest.raises(ValueError, match="No session token"):
            self.oauth._parse_token_response({})

    def test_parse_empty_user_defaults_to_empty_dict(self):
        data = {"data": {"session-token": "tok"}}
        result = self.oauth._parse_token_response(data)
        assert result.user == {}


# ---------------------------------------------------------------------------
# TastytradeOAuth.exchange_refresh_token
# ---------------------------------------------------------------------------

class TestExchangeRefreshToken:
    def _make_oauth(self):
        return TastytradeOAuth(
            base_url="https://api.example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
        )

    def _mock_response(self, json_data, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.reason = "OK" if status == 200 else "Error"
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def test_successful_exchange_via_oauth_endpoint(self):
        oauth = self._make_oauth()
        good_resp = self._mock_response({"data": {"session-token": "tok-ok", "user": {"username": "u"}}})
        with patch("requests.post", return_value=good_resp):
            token = oauth.exchange_refresh_token()
        assert token.session_token == "tok-ok"
        assert token.user == {"username": "u"}

    def test_falls_back_to_sessions_endpoint(self):
        oauth = self._make_oauth()
        # First call (oauth/token) fails with HTTPError; second (sessions) succeeds
        fail_resp = MagicMock()
        fail_resp.status_code = 401
        fail_resp.reason = "Unauthorized"
        fail_resp.raise_for_status.side_effect = requests.HTTPError(response=fail_resp)

        good_resp = self._mock_response({"data": {"session-token": "tok-fallback", "user": {}}})

        with patch("requests.post", side_effect=[fail_resp, good_resp]):
            token = oauth.exchange_refresh_token()
        assert token.session_token == "tok-fallback"

    def test_raises_value_error_when_all_endpoints_fail(self):
        oauth = self._make_oauth()
        fail_resp = MagicMock()
        fail_resp.status_code = 401
        fail_resp.reason = "Unauthorized"
        fail_resp.raise_for_status.side_effect = requests.HTTPError(response=fail_resp)

        with patch("requests.post", return_value=fail_resp):
            with pytest.raises(ValueError, match="Failed to exchange refresh token"):
                oauth.exchange_refresh_token()

    def test_network_error_falls_back_then_raises(self):
        oauth = self._make_oauth()
        with patch("requests.post", side_effect=requests.ConnectionError("network down")):
            with pytest.raises(ValueError):
                oauth.exchange_refresh_token()

    def test_strips_trailing_slash_from_base_url(self):
        oauth = TastytradeOAuth(
            base_url="https://api.example.com///",
            client_id="c",
            client_secret="s",
            refresh_token="r",
        )
        assert not oauth.base_url.endswith("/")

    def test_sends_correct_payload_to_oauth_endpoint(self):
        oauth = self._make_oauth()
        good_resp = self._mock_response({"data": {"session-token": "t", "user": {}}})
        with patch("requests.post", return_value=good_resp) as mock_post:
            oauth.exchange_refresh_token()
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1].get("json") or call_kwargs[0][1]
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "rtoken"
        assert payload["client_id"] == "cid"


# ---------------------------------------------------------------------------
# login_with_password
# ---------------------------------------------------------------------------

class TestLoginWithPassword:
    def _mock_response(self, json_data, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.reason = "OK"
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def test_successful_login(self):
        good_resp = self._mock_response({
            "data": {
                "session-token": "pass-tok",
                "user": {"username": "alice"},
            }
        })
        with patch("requests.post", return_value=good_resp):
            token = login_with_password("https://api.example.com", "alice", "secret")
        assert token.session_token == "pass-tok"
        assert token.user == {"username": "alice"}

    def test_raises_on_http_error(self):
        fail_resp = MagicMock()
        fail_resp.status_code = 401
        fail_resp.reason = "Unauthorized"
        fail_resp.text = "bad credentials"
        fail_resp.raise_for_status.side_effect = requests.HTTPError(response=fail_resp)
        with patch("requests.post", return_value=fail_resp):
            with pytest.raises(requests.HTTPError):
                login_with_password("https://api.example.com", "user", "wrong")

    def test_raises_when_no_session_token_in_response(self):
        resp = self._mock_response({"data": {}})
        with patch("requests.post", return_value=resp):
            with pytest.raises(ValueError, match="No session token"):
                login_with_password("https://api.example.com", "user", "pw")

    def test_expires_at_is_set(self):
        before = time.time()
        resp = self._mock_response({"data": {"session-token": "t", "user": {}}})
        with patch("requests.post", return_value=resp):
            token = login_with_password("https://api.example.com", "u", "p")
        assert token.expires_at is not None
        assert token.expires_at > before

    def test_strips_trailing_slash_from_base_url(self):
        resp = self._mock_response({"data": {"session-token": "t", "user": {}}})
        with patch("requests.post", return_value=resp) as mock_post:
            login_with_password("https://api.example.com///", "u", "p")
        url_called = mock_post.call_args[0][0]
        assert not url_called.endswith("///")

    def test_sends_remember_me_true(self):
        resp = self._mock_response({"data": {"session-token": "t", "user": {}}})
        with patch("requests.post", return_value=resp) as mock_post:
            login_with_password("https://api.example.com", "u", "p")
        payload = mock_post.call_args[1]["json"]
        assert payload["remember-me"] is True
