"""Unit tests for api/tastytrade_client.py."""

import time
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from api.oauth import TokenResponse
from api.tastytrade_client import TastytradeClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = "https://api.example.com"


def _make_client(**kwargs) -> TastytradeClient:
    return TastytradeClient(BASE_URL, **kwargs)


def _mock_response(json_data=None, status=200, raise_for_status=None):
    resp = MagicMock()
    resp.status_code = status
    resp.reason = "OK" if status < 400 else "Error"
    resp.json.return_value = json_data or {}
    resp.content = b"content"
    if raise_for_status:
        resp.raise_for_status.side_effect = raise_for_status
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_base_url_trailing_slash_stripped(self):
        client = TastytradeClient("https://api.example.com///")
        assert not client.base_url.endswith("/")

    def test_oauth_created_when_all_credentials_present(self):
        client = _make_client(client_id="c", client_secret="s", refresh_token="r")
        assert client._oauth is not None

    def test_oauth_not_created_when_missing_credentials(self):
        client = _make_client()
        assert client._oauth is None

    def test_session_token_can_be_preset(self):
        client = _make_client(session_token="preset-tok")
        assert client._session_token == "preset-tok"


# ---------------------------------------------------------------------------
# _ensure_token
# ---------------------------------------------------------------------------

class TestEnsureToken:
    def test_raises_when_no_token_and_no_oauth(self):
        client = _make_client()
        with pytest.raises(ValueError, match="Not authenticated"):
            client._ensure_token()

    def test_returns_existing_valid_token(self):
        client = _make_client(session_token="valid-tok")
        client._token_expires_at = time.time() + 900
        assert client._ensure_token() == "valid-tok"

    def test_refreshes_expired_token_via_oauth(self):
        client = _make_client(client_id="c", client_secret="s", refresh_token="r")
        client._session_token = "old-tok"
        client._token_expires_at = time.time() - 1  # expired

        new_token = TokenResponse(
            session_token="new-tok",
            user={"username": "u"},
            expires_at=time.time() + 900,
        )
        client._oauth.exchange_refresh_token = MagicMock(return_value=new_token)
        result = client._ensure_token()
        assert result == "new-tok"
        assert client._session_token == "new-tok"

    def test_refreshes_when_no_token(self):
        client = _make_client(client_id="c", client_secret="s", refresh_token="r")
        new_token = TokenResponse(
            session_token="fresh-tok",
            user={},
            expires_at=time.time() + 900,
        )
        client._oauth.exchange_refresh_token = MagicMock(return_value=new_token)
        assert client._ensure_token() == "fresh-tok"


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_sets_session_token(self):
        client = _make_client()
        token_resp = TokenResponse(session_token="login-tok", user={"username": "u"}, expires_at=time.time() + 900)
        with patch("api.tastytrade_client.login_with_password", return_value=token_resp):
            client.login("user", "pass")
        assert client._session_token == "login-tok"

    def test_login_sets_user(self):
        client = _make_client()
        token_resp = TokenResponse(session_token="t", user={"username": "alice"}, expires_at=time.time() + 900)
        with patch("api.tastytrade_client.login_with_password", return_value=token_resp):
            client.login("alice", "pw")
        assert client.user == {"username": "alice"}


# ---------------------------------------------------------------------------
# set_session_token
# ---------------------------------------------------------------------------

class TestSetSessionToken:
    def test_sets_token_directly(self):
        client = _make_client()
        client.set_session_token("direct-tok")
        assert client._session_token == "direct-tok"

    def test_sets_custom_expiry(self):
        client = _make_client()
        future = time.time() + 1800
        client.set_session_token("tok", expires_at=future)
        assert client._token_expires_at == future

    def test_default_expiry_is_in_future(self):
        client = _make_client()
        before = time.time()
        client.set_session_token("tok")
        assert client._token_expires_at > before


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_headers_include_authorization(self):
        client = _make_client(session_token="my-tok")
        client._token_expires_at = time.time() + 900
        headers = client._headers()
        assert "Authorization" in headers
        assert "my-tok" in headers["Authorization"]

    def test_headers_not_double_bearer(self):
        client = _make_client(session_token="Bearer already")
        client._token_expires_at = time.time() + 900
        headers = client._headers()
        assert headers["Authorization"].count("Bearer") == 1


# ---------------------------------------------------------------------------
# get_accounts
# ---------------------------------------------------------------------------

class TestGetAccounts:
    def _authed_client(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        return client

    def test_returns_items_list(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"items": [{"account-number": "A123"}]}})
        with patch("requests.get", return_value=resp):
            accounts = client.get_accounts()
        assert accounts == [{"account-number": "A123"}]

    def test_returns_empty_list_on_empty_response(self):
        client = self._authed_client()
        resp = _mock_response({})
        with patch("requests.get", return_value=resp):
            accounts = client.get_accounts()
        assert accounts == []


# ---------------------------------------------------------------------------
# get_balances
# ---------------------------------------------------------------------------

class TestGetBalances:
    def test_returns_data_dict(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        resp = _mock_response({"data": {"cash-balance": "10000.00"}})
        with patch("requests.get", return_value=resp):
            bal = client.get_balances("ACC001")
        assert bal == {"cash-balance": "10000.00"}


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

class TestGetPositions:
    def test_returns_positions_list(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        resp = _mock_response({"data": {"items": [{"symbol": "MES"}]}})
        with patch("requests.get", return_value=resp):
            positions = client.get_positions("ACC001")
        assert positions == [{"symbol": "MES"}]


# ---------------------------------------------------------------------------
# get_market_quotes
# ---------------------------------------------------------------------------

class TestGetMarketQuotes:
    def _authed_client(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        return client

    def test_returns_empty_dict_for_no_symbols(self):
        client = self._authed_client()
        assert client.get_market_quotes([]) == {}

    def test_returns_data_on_success(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"SPY": {"last": 400}}})
        with patch("requests.get", return_value=resp):
            quotes = client.get_market_quotes(["SPY"])
        assert "SPY" in quotes

    def test_falls_back_on_http_error(self):
        client = self._authed_client()
        # Primary path fails, fallback succeeds
        fail_resp = _mock_response(
            status=404,
            raise_for_status=requests.HTTPError(response=MagicMock(status_code=404, text="not found")),
        )
        ok_resp = _mock_response({"data": {"SPY": {"last": 410}}})
        with patch("requests.get", side_effect=[fail_resp, ok_resp]):
            quotes = client.get_market_quotes(["SPY"])
        assert "SPY" in quotes

    def test_returns_empty_dict_when_both_paths_fail(self):
        client = self._authed_client()
        fail_resp = _mock_response(
            status=404,
            raise_for_status=requests.HTTPError(response=MagicMock(status_code=404, text="nf")),
        )
        with patch("requests.get", return_value=fail_resp):
            quotes = client.get_market_quotes(["SPY"])
        assert quotes == {}


# ---------------------------------------------------------------------------
# get_quote
# ---------------------------------------------------------------------------

class TestGetQuote:
    def _authed_client(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        return client

    def test_extracts_symbol_from_dict_response(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"SPY": {"last": 420}}})
        with patch("requests.get", return_value=resp):
            quote = client.get_quote("SPY")
        assert quote == {"last": 420}

    def test_extracts_symbol_from_list_response(self):
        client = self._authed_client()
        resp = _mock_response({"data": [{"symbol": "QQQ", "last": 330}]})
        with patch("requests.get", return_value=resp):
            quote = client.get_quote("QQQ")
        assert quote == {"symbol": "QQQ", "last": 330}

    def test_returns_empty_dict_when_symbol_not_found(self):
        client = self._authed_client()
        resp = _mock_response({})
        with patch("requests.get", return_value=resp):
            quote = client.get_quote("MISSING")
        assert quote == {}


# ---------------------------------------------------------------------------
# get_futures_streamer_symbol
# ---------------------------------------------------------------------------

class TestGetFuturesStreamerSymbol:
    def _authed_client(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        return client

    def test_returns_front_month_symbol(self):
        client = self._authed_client()
        resp = _mock_response({
            "data": {
                "items": [
                    {"streamer-symbol": "/MESU26:XCME", "expiration-date": "2099-09-19"},
                    {"streamer-symbol": "/MESZ26:XCME", "expiration-date": "2099-12-19"},
                ]
            }
        })
        with patch("requests.get", return_value=resp):
            sym = client.get_futures_streamer_symbol("MES")
        assert sym == "/MESU26:XCME"

    def test_returns_fallback_on_empty_items(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"items": []}})
        with patch("requests.get", return_value=resp):
            sym = client.get_futures_streamer_symbol("MES")
        assert sym == "/MES"

    def test_returns_fallback_on_exception(self):
        client = self._authed_client()
        with patch("requests.get", side_effect=Exception("network error")):
            sym = client.get_futures_streamer_symbol("MES")
        assert sym == "/MES"

    def test_strips_leading_slash_from_underlying(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"items": []}})
        with patch("requests.get", return_value=resp) as mock_get:
            client.get_futures_streamer_symbol("/MES")
        params = mock_get.call_args[1]["params"]
        assert params["product-code"] == "MES"


# ---------------------------------------------------------------------------
# get_quote_token
# ---------------------------------------------------------------------------

class TestGetQuoteToken:
    def test_returns_token_data(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        resp = _mock_response({"data": {"token": "dxlink-token", "dxlink-url": "wss://example.com"}})
        with patch("requests.get", return_value=resp):
            result = client.get_quote_token()
        assert result == {"token": "dxlink-token", "dxlink-url": "wss://example.com"}


# ---------------------------------------------------------------------------
# place_order / cancel_order
# ---------------------------------------------------------------------------

class TestOrders:
    def _authed_client(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        return client

    def test_place_order_posts_to_correct_url(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"id": "ORDER1"}})
        with patch("requests.post", return_value=resp) as mock_post:
            result = client.place_order("ACC001", {"type": "Limit"})
        url = mock_post.call_args[0][0]
        assert "/accounts/ACC001/orders" in url

    def test_place_order_returns_data(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"id": "ORDER2"}})
        with patch("requests.post", return_value=resp):
            result = client.place_order("ACC001", {})
        assert result == {"id": "ORDER2"}

    def test_cancel_order_sends_delete(self):
        client = self._authed_client()
        resp = _mock_response({})
        resp.content = b""
        with patch("requests.delete", return_value=resp) as mock_del:
            client.cancel_order("ACC001", "ORD123")
        url = mock_del.call_args[0][0]
        assert "/accounts/ACC001/orders/ORD123" in url


# ---------------------------------------------------------------------------
# get_candle_history
# ---------------------------------------------------------------------------

class TestGetCandleHistory:
    def _authed_client(self):
        client = _make_client(session_token="tok")
        client._token_expires_at = time.time() + 900
        return client

    def test_returns_candles_from_pattern_1(self):
        client = self._authed_client()
        candles = [{"timestamp": 1000, "open": 100}]
        resp = _mock_response({"data": {"candles": candles}})
        with patch("requests.get", return_value=resp):
            result = client.get_candle_history("MES")
        assert result == candles

    def test_falls_back_to_pattern_2(self):
        client = self._authed_client()
        candles2 = [{"timestamp": 2000, "open": 200}]
        fail_resp = _mock_response({"data": {"candles": []}})  # empty → triggers fallback
        ok_resp = _mock_response({"candles": candles2})
        with patch("requests.get", side_effect=[fail_resp, ok_resp]):
            result = client.get_candle_history("MES")
        assert result == candles2

    def test_returns_empty_list_when_all_fail(self):
        client = self._authed_client()
        resp = _mock_response({"data": {"candles": []}})
        with patch("requests.get", return_value=resp):
            result = client.get_candle_history("MES")
        assert result == []
