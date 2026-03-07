"""Unit tests for config.py."""

import json
import importlib
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

import pytest

import config


# ---------------------------------------------------------------------------
# Environment toggle
# ---------------------------------------------------------------------------

class TestEnvironmentToggle:
    def setup_method(self):
        """Reset module-level state before each test."""
        config._use_sandbox = True

    def test_default_is_sandbox(self):
        assert config.is_sandbox() is True

    def test_set_sandbox_false(self):
        config.set_sandbox(False)
        assert config.is_sandbox() is False

    def test_set_sandbox_true(self):
        config.set_sandbox(False)
        config.set_sandbox(True)
        assert config.is_sandbox() is True

    def test_get_api_base_sandbox(self):
        config.set_sandbox(True)
        url = config.get_api_base()
        assert "cert" in url or "sandbox" in url.lower() or url == config.TASTYTRADE_API_BASE_SANDBOX

    def test_get_api_base_production(self):
        config.set_sandbox(False)
        url = config.get_api_base()
        assert url == config.TASTYTRADE_API_BASE


# ---------------------------------------------------------------------------
# OAuth credentials
# ---------------------------------------------------------------------------

class TestOAuthCredentials:
    def setup_method(self):
        config._use_sandbox = True

    def test_get_oauth_credentials_sandbox(self):
        config.set_sandbox(True)
        cid, csec, rtok = config.get_oauth_credentials()
        assert cid  == config.TASTYTRADE_CLIENT_ID_SANDBOX
        assert csec == config.TASTYTRADE_CLIENT_SECRET_SANDBOX
        assert rtok == config.TASTYTRADE_REFRESH_TOKEN_SANDBOX

    def test_get_oauth_credentials_production(self):
        config.set_sandbox(False)
        cid, csec, rtok = config.get_oauth_credentials()
        assert cid  == config.TASTYTRADE_CLIENT_ID
        assert csec == config.TASTYTRADE_CLIENT_SECRET
        assert rtok == config.TASTYTRADE_REFRESH_TOKEN

    def test_returns_three_values(self):
        result = config.get_oauth_credentials()
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class TestPreferences:
    def test_get_pref_missing_key_returns_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_PREFS_PATH", tmp_path / "prefs.json")
        assert config.get_pref("nonexistent_key") is None

    def test_get_pref_missing_key_returns_custom_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_PREFS_PATH", tmp_path / "prefs.json")
        assert config.get_pref("nonexistent_key", "fallback") == "fallback"

    def test_set_and_get_pref(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_PREFS_PATH", tmp_path / "prefs.json")
        config.set_pref("theme", "dark")
        assert config.get_pref("theme") == "dark"

    def test_set_pref_persists_multiple_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_PREFS_PATH", tmp_path / "prefs.json")
        config.set_pref("key1", "value1")
        config.set_pref("key2", 42)
        assert config.get_pref("key1") == "value1"
        assert config.get_pref("key2") == 42

    def test_set_pref_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_PREFS_PATH", tmp_path / "prefs.json")
        config.set_pref("mode", "light")
        config.set_pref("mode", "dark")
        assert config.get_pref("mode") == "dark"

    def test_load_prefs_returns_empty_on_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_PREFS_PATH", tmp_path / "no_such_file.json")
        result = config._load_prefs()
        assert result == {}

    def test_save_prefs_creates_parent_dirs(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "prefs.json"
        monkeypatch.setattr(config, "_PREFS_PATH", nested)
        config._save_prefs({"x": 1})
        assert nested.exists()
        data = json.loads(nested.read_text())
        assert data == {"x": 1}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_index_symbols_list(self):
        assert isinstance(config.INDEX_SYMBOLS, list)
        assert len(config.INDEX_SYMBOLS) > 0

    def test_default_api_base_is_string(self):
        assert isinstance(config.TASTYTRADE_API_BASE, str)
        assert config.TASTYTRADE_API_BASE.startswith("http")

    def test_default_sandbox_api_base_is_string(self):
        assert isinstance(config.TASTYTRADE_API_BASE_SANDBOX, str)
        assert config.TASTYTRADE_API_BASE_SANDBOX.startswith("http")
