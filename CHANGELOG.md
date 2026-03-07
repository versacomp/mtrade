# Changelog

All notable changes to MTrade are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
MTrade uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] — 2026-xx-xx

### Added

#### Configurable Candle Interval
- `api/dxlink_streamer.py` — `stream_candles()` accepts an `interval` parameter using dxFeed aggregation-period syntax (`1s`, `5s`, `15s`, `30s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`); defaults to `1m` for full backward compatibility
- History back-fill window scales with the selected interval so the buffer always covers approximately the same wall-clock span

#### Candle Database (`api/candle_db.py`)
- SQLite store at `~/.mtrade/candles.db` (path configurable) that persists every incoming candle with `INSERT OR REPLACE` deduplication keyed on `(symbol, interval, ts_ms)`
- `CandleDB.query(symbol, interval, from_ms, to_ms, limit)` returns `list[dict]` suitable for replay and back-testing without any API calls
- `CandleDB.symbols()` — inventory of all stored series with candle counts
- `CandleDB.delete(symbol, interval)` — targeted or full wipe
- `CandleDB.stats()` — total candles, file size, path
- Module-level singleton (`get_db()` / `reset_db()`) shared across the application

#### Settings View (`/settings`)
- New `views/settings_view.py` — accessible via the nav bar
- **Streaming section**: candle interval dropdown with save to `~/.mtrade/preferences.json`; change takes effect on next Liquidity view open
- **Database section**: enable/disable recording switch, database path override, live stats table showing per-series candle counts, per-series and global Clear buttons, Refresh button
- All preferences stored under existing keys: `candle_interval`, `candle_db_enabled`, `candle_db_path`

#### Market Hours Awareness (`api/market_hours.py`)
- CME Globex equity-index futures schedule modelled in ET: open Sunday 6:00 PM, daily maintenance 5:00–6:00 PM, weekend close Friday 5:00 PM
- `is_market_open()`, `market_status()` → `(bool, "Open · closes in Xh Ym")`, `seconds_until_open()`
- Liquidity view Phase 2 stream loop: when market is closed, DXLink connection is skipped entirely and demo mode activates immediately; status dot shows `"Closed · opens in Xh Ym"`; state is re-checked every 30 seconds
- When market reopens the full 10-retry DXLink reconnect sequence is attempted automatically

#### AppBar ET Clock
- Live `HH:MM:SS ET` clock displayed in every AppBar using a self-terminating async task per view; task stops automatically when the AppBar is replaced on navigation

### Changed

#### Compact Navigation Bar
- Nav buttons replaced with icon-only `ft.IconButton` controls (`DASHBOARD`, `CANDLESTICK_CHART`, `WATER_DROP`, `ANALYTICS`, `SETTINGS`) with page-name tooltips; active page shown with white icon and subtle highlight, inactive pages dimmed
- Status text label (`CONNECTED / DEMO / OFFLINE`) removed; full detail available in the status dot tooltip
- Environment pill badge (`SANDBOX / PRODUCTION`) replaced with an 8 px coloured dot (amber = sandbox, green = production) with URL tooltip
- Username text removed; surfaced in the logout button tooltip
- Overall AppBar content width reduced by approximately half

---

## [0.0.1] — 2026-03-06

Initial public release.

### Added

#### Core Application
- Flet (Python/Flutter) desktop application with route-based navigation
- OAuth2 and username/password authentication against tastytrade REST API
- Sandbox / Production environment toggle on the login screen, with a persistent nav bar badge showing the active environment
- Dark / light theme toggle with preference persisted to `~/.mtrade/preferences.json`
- Obsidian + Sapphire colour scheme (`#0F172A` app bar, `BLUE_500` seed)
- Active-route highlighting and async nav button handlers to eliminate screen flicker between views

#### DXLink Market Data Streaming
- WebSocket candle streamer (`api/dxlink_streamer.py`) implementing the full DXLink protocol: SETUP → AUTH → CHANNEL_REQUEST → FEED_SUBSCRIPTION
- Exponential back-off reconnection (5 s base, 60 s ceiling, ±10 % jitter, up to 10 attempts)
- Automatic demo-mode fallback after all retries are exhausted
- KEEPALIVE frames every 30 seconds
- Connection status indicator (`CONNECTED` / `RECONNECTING` / `OFFLINE` / `DEMO`) in the nav bar

#### Institutional Liquidity View (`/liquidity`)
- Real-time 1-minute candlestick chart for 40+ futures instruments across Equity Index, Metals, Energy, Interest Rates, FX, and Agricultural sectors
- Liquidity grab reversal signal detection: 3-candle swing structure, wick-sweep + 30 % close-back threshold
- Key institutional levels: 4-hour high/low and previous-day high/low (`4HH`, `4HL`, `PDH`, `PDL`)
- Three-tier signal quality system: **Prime** (RSI divergence + trend aligned), **Filtered** (divergence only), **Weak** (no divergence)
- Wilder RSI-14 divergence detection with dots on the RSI sub-panel
- SMA 200 pro-trend filter with chart background tint (green above / red below)
- ADX-14 range rotation filter: ADX < 25 + level within 25 % of rolling 20-period band
- Chart overlays: SMA 50 (orange), SMA 200 (green/red), 20-period range bands, key level lines, volume profile with Point of Control
- RSI and ADX sub-panels with OB/OS reference lines and dashed ADX threshold
- Interactive pan (click-drag) and zoom (scroll wheel) with "Jump to live" edge button
- 48-hour rolling candle cache per symbol, seeded from disk before stream connects
- Multi-symbol support with quick-access chips (MES, MNQ, M2K, MYM, MGC)
- Persistent per-symbol candle and trade cache under `~/.mtrade/`
- Alert system: overlay badge flash, snack-bar notification, audible beep (Windows)
- Background stream persistence — stream continues running when navigating away from the view

#### Simulated Trade Management
- Paper trading engine triggered by Prime-tier signals passing all active filters
- Entry at signal candle close; stop loss at wick tip; take profit at 1:2 R:R
- Ratcheting stop: breakeven at 50 % of TP distance, trailing stop at 75 %
- Maximum one concurrent open trade per symbol
- Opposing-signal flip with 120-second re-entry cooldown and viability check
- Trade levels drawn on chart (entry / SL / TP) with colour-coded stop stage
- Paper trading badge (yellow) shown when sim is active

#### Live Trading
- Live trading toggle with confirmation dialog before enabling
- Real order placement via `POST /accounts/{acct}/orders` (Market entry + Stop SL + Limit TP)
- Order cancellation via `DELETE /accounts/{acct}/orders/{id}`
- Live trading locks and disables the sim toggle; red throbbing **LIVE TRADING** badge
- Badge persists and restores correctly after view navigation

#### Strategy Analysis View (`/analysis`)
- KPI dashboard: Win Rate, Profit Factor, Expectancy, Max Drawdown, Sharpe, Recovery Factor, W:L Ratio, Total P&L
- Equity curve canvas coloured by trade sign with peak/trough markers
- Signal source breakdown table and ratcheting-stop exit breakdown card
- Full back-test engine over 48-hour cached history with no look-ahead bias
- Independent Trend Filter and Range Filter toggles for back-test comparison

#### Infrastructure
- `config.py` — centralised environment configuration with `get_api_base()`, `get_oauth_credentials()`, sandbox/production toggle
- `api/oauth.py` — OAuth2 refresh-token exchange with multi-endpoint fallback
- `api/tastytrade_client.py` — REST client with GET, POST, DELETE; futures symbol resolution; quote tokens; candle history
- Application-wide rotating file logger (`mtrade_api.log`, 5 MB × 3)
- User preferences persistence (`~/.mtrade/preferences.json`)
- MIT License, SECURITY.md, CHANGELOG.md, `.env.example`, comprehensive `.gitignore`
- README with Quick Start, sandbox setup guide, strategy documentation, and risk disclaimer

---

[0.0.2]: https://github.com/your-org/m-trade/releases/tag/v0.0.2
[0.0.1]: https://github.com/your-org/m-trade/releases/tag/v0.0.1
