"""Settings view — streaming interval, candle database, and related preferences."""
import logging

import flet as ft

import config
from api.candle_db import DEFAULT_DB_PATH, get_db, reset_db
from views.nav import nav_app_bar

log = logging.getLogger(__name__)

# ── Candle interval options (dxFeed aggregation-period syntax) ─────────────────
INTERVALS: list[tuple[str, str]] = [
    ("1s",  "1 Second"),
    ("5s",  "5 Seconds"),
    ("15s", "15 Seconds"),
    ("30s", "30 Seconds"),
    ("1m",  "1 Minute (default)"),
    ("3m",  "3 Minutes"),
    ("5m",  "5 Minutes"),
    ("15m", "15 Minutes"),
    ("30m", "30 Minutes"),
    ("1h",  "1 Hour"),
    ("4h",  "4 Hours"),
    ("1d",  "1 Day"),
]

_COL_BG    = "#111111"
_COL_CARD  = "#1A1A1A"
_COL_LABEL = "#888888"
_COL_HL    = "#FF9800"
_COL_OK    = "#44DD88"
_COL_ERR   = "#FF5555"


def build_settings_view(client, page: ft.Page) -> ft.View:
    """Return the /settings ft.View."""
    user_info = client.user or {}
    username  = user_info.get("username") or user_info.get("email") or "User"

    # ── Current preference values ───────────────────────────────────────────────
    cur_interval = config.get_pref("candle_interval", "1m")
    cur_db_on    = bool(config.get_pref("candle_db_enabled", False))
    cur_db_path  = config.get_pref("candle_db_path", "") or str(DEFAULT_DB_PATH)

    # ── UI refs ────────────────────────────────────────────────────────────────
    interval_dd_ref   = ft.Ref[ft.Dropdown]()
    db_switch_ref     = ft.Ref[ft.Switch]()
    db_path_ref       = ft.Ref[ft.TextField]()
    db_section_ref    = ft.Ref[ft.Column]()

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _snack(msg: str, color: str = _COL_OK) -> None:
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg, color=ft.Colors.WHITE),
            bgcolor=color,
            open=True,
        )
        page.update()

    # ── DB stats content (rebuilt on every refresh) ────────────────────────────
    def _build_db_stats() -> list[ft.Control]:
        """Return a list of controls showing live DB stats + per-series table."""
        if not config.get_pref("candle_db_enabled", False):
            return [ft.Text("Recording is disabled.", size=12, color=_COL_LABEL)]
        try:
            db  = get_db()
            s   = db.stats()
            rows = db.symbols()
        except Exception as exc:
            return [ft.Text(f"DB error: {exc}", size=12, color=_COL_ERR)]

        size_kb   = s["db_size_bytes"] / 1024
        total_txt = (
            f"{s['total_candles']:,} candles  ·  {size_kb:.1f} KB  ·  {s['db_path']}"
        )

        def _clear(sym: str | None = None, ivl: str | None = None) -> None:
            try:
                n = get_db().delete(sym, ivl)
                _snack(f"Deleted {n:,} candle(s).")
            except Exception as exc:
                _snack(f"Error: {exc}", _COL_ERR)
            _refresh_db()

        table_rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(sym, color=ft.Colors.WHITE, size=12)),
                ft.DataCell(ft.Text(ivl, color=ft.Colors.WHITE70, size=12)),
                ft.DataCell(ft.Text(f"{cnt:,}", color=ft.Colors.WHITE, size=12)),
                ft.DataCell(
                    ft.TextButton(
                        "Clear",
                        style=ft.ButtonStyle(color=_COL_ERR),
                        on_click=lambda _, s=sym, i=ivl: _clear(s, i),
                    )
                ),
            ])
            for sym, ivl, cnt in rows
        ]

        controls: list[ft.Control] = [
            ft.Text(total_txt, size=11, color=_COL_LABEL),
        ]
        if table_rows:
            controls.append(
                ft.DataTable(
                    columns=[
                        ft.DataColumn(ft.Text("Symbol",   color=_COL_LABEL, size=11)),
                        ft.DataColumn(ft.Text("Interval", color=_COL_LABEL, size=11)),
                        ft.DataColumn(ft.Text("Candles",  color=_COL_LABEL, size=11)),
                        ft.DataColumn(ft.Text("",         color=_COL_LABEL, size=11)),
                    ],
                    rows=table_rows,
                    heading_row_color="#1E1E1E",
                    border=ft.border.all(1, "#333333"),
                    border_radius=8,
                    column_spacing=20,
                )
            )
        controls.append(
            ft.Row(
                [
                    ft.OutlinedButton(
                        "Refresh",
                        on_click=lambda _: _refresh_db(),
                        style=ft.ButtonStyle(color=ft.Colors.WHITE70),
                    ),
                    ft.OutlinedButton(
                        "Clear All",
                        on_click=lambda _: _clear(),
                        style=ft.ButtonStyle(color=_COL_ERR),
                    ),
                ],
                spacing=10,
            )
        )
        return controls

    def _refresh_db(_=None) -> None:
        if db_section_ref.current:
            db_section_ref.current.controls = _build_db_stats()
            db_section_ref.current.update()

    # ── Card builder ───────────────────────────────────────────────────────────
    def _card(title: str, controls: list[ft.Control]) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=_COL_HL),
                    ft.Divider(color="#333333", height=1),
                    *controls,
                ],
                spacing=14,
            ),
            bgcolor=_COL_CARD,
            border_radius=10,
            padding=ft.padding.all(16),
        )

    # ── Streaming card ─────────────────────────────────────────────────────────
    def _save_interval(_: ft.ControlEvent) -> None:
        val = interval_dd_ref.current.value
        if val:
            config.set_pref("candle_interval", val)
            _snack(f"Interval set to {val}. Re-open Liquidity view to apply.")

    streaming_card = _card("Streaming", [
        ft.Text(
            "Select the candle aggregation period sent to the DXLink WebSocket. "
            "Changing this clears the in-memory symbol cache when the Liquidity "
            "view is next opened.\n\n"
            "Note: sub-minute intervals (1s / 5s) produce high data volumes and "
            "may increase CPU usage during back-fills.",
            size=12,
            color=_COL_LABEL,
        ),
        ft.Row(
            [
                ft.Dropdown(
                    ref=interval_dd_ref,
                    value=cur_interval,
                    options=[ft.dropdown.Option(v, t) for v, t in INTERVALS],
                    label="Candle Interval",
                    width=240,
                    text_style=ft.TextStyle(color=ft.Colors.WHITE),
                    label_style=ft.TextStyle(color=ft.Colors.WHITE70),
                    bgcolor="#222222",
                    border_color=ft.Colors.WHITE24,
                ),
                ft.ElevatedButton(
                    "Save",
                    on_click=_save_interval,
                    style=ft.ButtonStyle(bgcolor=_COL_HL, color=ft.Colors.BLACK),
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    ])

    # ── Database card ──────────────────────────────────────────────────────────
    def _toggle_db(e: ft.ControlEvent) -> None:
        config.set_pref("candle_db_enabled", e.control.value)
        _snack(f"DB recording {'enabled' if e.control.value else 'disabled'}.")
        _refresh_db()

    def _save_path(_: ft.ControlEvent) -> None:
        raw = db_path_ref.current.value.strip()
        config.set_pref("candle_db_path", raw)
        reset_db()  # force singleton re-open at new path on next use
        _snack("DB path saved. Re-open Liquidity view to apply.")

    database_card = _card("Candle Database (SQLite)", [
        ft.Text(
            "When enabled, every incoming candle is persisted to a local SQLite "
            "database. The data can be queried for replay or back-testing via "
            "api.candle_db.get_db().query(symbol, interval, from_ms, to_ms).",
            size=12,
            color=_COL_LABEL,
        ),
        ft.Switch(
            ref=db_switch_ref,
            value=cur_db_on,
            label="Record candles to database",
            active_color=_COL_HL,
            label_text_style=ft.TextStyle(color=ft.Colors.WHITE),
            on_change=_toggle_db,
        ),
        ft.Row(
            [
                ft.TextField(
                    ref=db_path_ref,
                    value=cur_db_path,
                    label="Database Path",
                    hint_text=str(DEFAULT_DB_PATH),
                    expand=True,
                    text_style=ft.TextStyle(color=ft.Colors.WHITE),
                    label_style=ft.TextStyle(color=ft.Colors.WHITE70),
                    bgcolor="#222222",
                    border_color=ft.Colors.WHITE24,
                ),
                ft.ElevatedButton(
                    "Set Path",
                    on_click=_save_path,
                    style=ft.ButtonStyle(bgcolor="#444444", color=ft.Colors.WHITE),
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        ft.Column(
            ref=db_section_ref,
            controls=_build_db_stats(),
            spacing=10,
        ),
    ])

    body = ft.Column(
        [streaming_card, database_card],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
    )

    return ft.View(
        route="/settings",
        bgcolor=_COL_BG,
        appbar=nav_app_bar(page, "Settings", "/settings", username),
        controls=[
            ft.Container(
                content=body,
                padding=ft.padding.all(20),
                expand=True,
            )
        ],
        scroll=ft.ScrollMode.AUTO,
    )
