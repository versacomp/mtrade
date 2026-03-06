"""
Strategy Analysis view — KPI dashboard, equity curve, and back-test engine.

Loads persisted SimTrade data from disk for the selected symbol and computes
full performance statistics.  The back-test tab replays the strategy over the
cached 48-hour candle history so results are independent of the current session.
"""

import asyncio
import math
from datetime import datetime
from typing import Optional

import flet as ft
import flet.canvas as cv

import api.connection_status as cs
from views.nav import nav_app_bar
from views.institutional_liquidity_view import (
    QUICK_SYMBOLS,
    RR_RATIO,
    COL_LABEL,
    COL_CHIP_ACT,
    COL_SIG_BULL,
    COL_SIG_BEAR,
    COL_TRADE_SL,
    COL_TRADE_SL_BE,
    COL_TRADE_SL_TRAIL,
    _load_sim_trades,
    compute_kpis,
    prepare_backtest,
    simulate_trades,
)

# ── Palette (matches the dark theme of the liquidity view) ─────────────────────
_BG       = "#111111"
_CARD_BG  = "#1a1a1a"
_BORDER   = "#2a2a2a"
_GREEN    = "#44DD88"
_RED      = "#FF5555"
_AMBER    = "#FFD700"
_DIM      = "#555555"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt(v: float, decimals: int = 2, prefix: str = "", plus: bool = False) -> str:
    """Format a float, replacing ±inf with '∞' / '—'."""
    if not math.isfinite(v):
        return "∞" if v > 0 else "—"
    sign = "+" if plus and v > 0 else ""
    return f"{prefix}{sign}{v:.{decimals}f}"


def _trend_color(v: float, good_positive: bool = True) -> str:
    if v == 0:
        return "white"
    positive = v > 0
    return _GREEN if (positive == good_positive) else _RED


def _kpi_card(label: str, value: str, color: str = "white",
              subtitle: str = "") -> ft.Control:
    children: list[ft.Control] = [
        ft.Text(label, size=11, color=COL_LABEL),
        ft.Text(value, size=22, weight=ft.FontWeight.BOLD, color=color),
    ]
    if subtitle:
        children.append(ft.Text(subtitle, size=10, color=COL_LABEL))
    return ft.Card(
        content=ft.Container(
            content=ft.Column(children, spacing=2, tight=True),
            padding=ft.padding.all(14),
            bgcolor=_CARD_BG,
            border_radius=8,
            border=ft.border.all(1, _BORDER),
        ),
        elevation=0,
    )


def _section(title: str) -> ft.Text:
    return ft.Text(title, size=14, weight=ft.FontWeight.W_600, color="white")


# ── Equity curve canvas ────────────────────────────────────────────────────────

def _build_equity_canvas(equity: list[float], width: int, height: int = 160) -> ft.Control:
    if not equity:
        return ft.Container(
            width=width, height=height, bgcolor=_BG,
            content=ft.Text("No closed trades yet", color=COL_LABEL, size=12),
            alignment=ft.alignment.center,
            border_radius=8,
            border=ft.border.all(1, _BORDER),
        )

    PAD_L, PAD_R, PAD_T, PAD_B = 52, 16, 14, 26
    plot_w = width  - PAD_L - PAD_R
    plot_h = height - PAD_T - PAD_B

    mn = min(equity)
    mx = max(equity)
    if mx == mn:
        mn -= 1.0; mx += 1.0
    pad = (mx - mn) * 0.12
    mn -= pad; mx += pad
    rng = mx - mn

    def px(i: int) -> float:
        return PAD_L + i * plot_w / max(len(equity) - 1, 1)

    def py(v: float) -> float:
        return PAD_T + plot_h * (mx - v) / rng

    shapes: list[cv.Shape] = [
        cv.Rect(x=0, y=0, width=float(width), height=float(height),
                paint=ft.Paint(color=_BG, style=ft.PaintingStyle.FILL)),
    ]

    # Y-axis grid lines and labels (4 levels)
    for step in range(5):
        ref_v = mn + rng * step / 4
        ref_y = py(ref_v)
        shapes.append(cv.Line(x1=float(PAD_L), y1=ref_y,
                               x2=float(width - PAD_R), y2=ref_y,
                               paint=ft.Paint(color="#222222", stroke_width=0.5)))
        shapes.append(cv.Text(
            x=2, y=ref_y - 7,
            spans=[ft.TextSpan(_fmt(ref_v, 1, plus=True),
                               style=ft.TextStyle(size=8, color=_DIM))],
        ))

    # Zero baseline
    if mn < 0 < mx:
        zy = py(0.0)
        kx = float(PAD_L)
        while kx < width - PAD_R:
            kx2 = min(kx + 6.0, float(width - PAD_R))
            shapes.append(cv.Line(x1=kx, y1=zy, x2=kx2, y2=zy,
                                   paint=ft.Paint(color="#444444", stroke_width=1.0)))
            kx += 10.0
        shapes.append(cv.Text(
            x=float(PAD_L) - 18, y=zy - 6,
            spans=[ft.TextSpan("0", style=ft.TextStyle(size=8, color="#666666"))],
        ))

    # Equity line — colored by sign of the value at each point
    prev_pt: Optional[tuple[float, float]] = None
    for i, v in enumerate(equity):
        pt = (px(i), py(v))
        if prev_pt is not None:
            col = _GREEN if v >= 0 else _RED
            shapes.append(cv.Line(
                x1=prev_pt[0], y1=prev_pt[1], x2=pt[0], y2=pt[1],
                paint=ft.Paint(color=col, stroke_width=1.8),
            ))
        prev_pt = pt

    # Peak marker
    peak_val = max(equity)
    peak_idx = equity.index(peak_val)
    if peak_val > 0:
        shapes.append(cv.Rect(
            x=px(peak_idx) - 3, y=py(peak_val) - 3, width=6, height=6,
            paint=ft.Paint(color=_GREEN, style=ft.PaintingStyle.FILL),
        ))
        shapes.append(cv.Text(
            x=px(peak_idx) + 5, y=py(peak_val) - 11,
            spans=[ft.TextSpan(_fmt(peak_val, 1, plus=True),
                               style=ft.TextStyle(size=8, color=_GREEN,
                                                  weight=ft.FontWeight.BOLD))],
        ))

    # Max-drawdown trough marker
    trough_val = min(equity)
    trough_idx = equity.index(trough_val)
    if trough_val < peak_val:
        shapes.append(cv.Rect(
            x=px(trough_idx) - 3, y=py(trough_val) - 3, width=6, height=6,
            paint=ft.Paint(color=_RED, style=ft.PaintingStyle.FILL),
        ))
        shapes.append(cv.Text(
            x=px(trough_idx) + 5, y=py(trough_val) + 2,
            spans=[ft.TextSpan(_fmt(trough_val, 1, plus=True),
                               style=ft.TextStyle(size=8, color=_RED,
                                                  weight=ft.FontWeight.BOLD))],
        ))

    # X-axis trade count label
    shapes.append(cv.Text(
        x=float(PAD_L), y=float(height - PAD_B + 6),
        spans=[ft.TextSpan("Trade 1",
                           style=ft.TextStyle(size=8, color=_DIM))],
    ))
    shapes.append(cv.Text(
        x=float(width - PAD_R - 36), y=float(height - PAD_B + 6),
        spans=[ft.TextSpan(f"Trade {len(equity)}",
                           style=ft.TextStyle(size=8, color=_DIM))],
    ))

    return cv.Canvas(shapes=shapes, width=float(width), height=float(height))


# ── KPI row ────────────────────────────────────────────────────────────────────

def _build_kpi_row(kpi: dict) -> ft.Control:
    pf   = kpi["profit_factor"]
    pf_s = "∞" if not math.isfinite(pf) else f"{pf:.2f}×"
    rec  = kpi["recovery"]
    rec_s= "∞" if not math.isfinite(rec) else f"{rec:.1f}×"

    cards = [
        _kpi_card("Win Rate",
                  f"{kpi['win_rate']:.0f}%",
                  _trend_color(kpi["win_rate"] - 50),
                  f"{kpi['wins']}W  {kpi['losses']}L  {kpi['opens']} open"),
        _kpi_card("Profit Factor",
                  pf_s,
                  _GREEN if pf >= 1.5 else (_AMBER if pf >= 1.0 else _RED)),
        _kpi_card("Expectancy",
                  _fmt(kpi["expectancy"], 2, plus=True) + " pts",
                  _trend_color(kpi["expectancy"]),
                  "avg pts per trade"),
        _kpi_card("Max Drawdown",
                  _fmt(-kpi["max_drawdown"], 1) + " pts",
                  _RED if kpi["max_drawdown"] > 0 else "white"),
        _kpi_card("Sharpe",
                  _fmt(kpi["sharpe"], 2),
                  _GREEN if kpi["sharpe"] >= 1.0 else (_AMBER if kpi["sharpe"] >= 0 else _RED)),
        _kpi_card("Recovery",
                  rec_s,
                  _GREEN if (math.isfinite(rec) and rec >= 2.0) else "white"),
        _kpi_card("W:L Ratio",
                  _fmt(kpi["wl_ratio"], 2) + "×",
                  _GREEN if kpi["wl_ratio"] >= 1.5 else "white",
                  f"avg +{kpi['avg_win']:.2f} / {kpi['avg_loss']:.2f}"),
        _kpi_card("Total P&L",
                  _fmt(kpi["total_pnl"], 2, plus=True) + " pts",
                  _trend_color(kpi["total_pnl"]),
                  f"{kpi['closed']} closed trades"),
    ]
    return ft.Row(cards, wrap=True, spacing=8, run_spacing=8)


# ── Source breakdown table ─────────────────────────────────────────────────────

def _build_source_table(src_stats: dict) -> ft.Control:
    if not src_stats:
        return ft.Text("No signal source data", color=COL_LABEL, size=12)

    rows = []
    for src in sorted(src_stats):
        s     = src_stats[src]
        total = s["wins"] + s["losses"]
        wr    = s["wins"] / total * 100 if total else 0.0
        avg   = s["pnl"]  / total       if total else 0.0
        rows.append(ft.DataRow(cells=[
            ft.DataCell(ft.Text(src,            size=12, color="white")),
            ft.DataCell(ft.Text(str(total),     size=12, color=COL_LABEL)),
            ft.DataCell(ft.Text(f"{s['wins']}W / {s['losses']}L", size=12, color=COL_LABEL)),
            ft.DataCell(ft.Text(f"{wr:.0f}%",  size=12,
                                color=_GREEN if wr >= 50 else _RED)),
            ft.DataCell(ft.Text(_fmt(avg, 2, plus=True), size=12,
                                color=_GREEN if avg >= 0 else _RED,
                                weight=ft.FontWeight.BOLD)),
        ]))

    return ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Source",  size=11, color=COL_LABEL)),
            ft.DataColumn(ft.Text("Trades",  size=11, color=COL_LABEL), numeric=True),
            ft.DataColumn(ft.Text("W / L",   size=11, color=COL_LABEL)),
            ft.DataColumn(ft.Text("Win %",   size=11, color=COL_LABEL), numeric=True),
            ft.DataColumn(ft.Text("Avg P&L", size=11, color=COL_LABEL), numeric=True),
        ],
        rows=rows,
        border=ft.border.all(1, _BORDER),
        border_radius=8,
        heading_row_color=ft.Colors.with_opacity(0.06, "white"),
        data_row_min_height=32,
        column_spacing=18,
    )


# ── Protection breakdown ───────────────────────────────────────────────────────

def _build_protection_card(kpi: dict) -> ft.Control:
    total = kpi["closed"]

    def pct(n: int) -> str:
        return f" ({n/total*100:.0f}%)" if total else ""

    rows: list[ft.Control] = [
        ft.Row([
            ft.Container(width=10, height=10, bgcolor=_GREEN, border_radius=2),
            ft.Text("TP hit", size=12, color="white", expand=True),
            ft.Text(f"{kpi['tp_closed']}{pct(kpi['tp_closed'])}",
                    size=12, color=_GREEN, weight=ft.FontWeight.BOLD),
        ], spacing=8),
        ft.Row([
            ft.Container(width=10, height=10, bgcolor=COL_TRADE_SL_TRAIL, border_radius=2),
            ft.Text("Trailing stop", size=12, color="white", expand=True),
            ft.Text(f"{kpi['trail_closed']}{pct(kpi['trail_closed'])}",
                    size=12, color=COL_TRADE_SL_TRAIL, weight=ft.FontWeight.BOLD),
        ], spacing=8),
        ft.Row([
            ft.Container(width=10, height=10, bgcolor=COL_TRADE_SL_BE, border_radius=2),
            ft.Text("Breakeven", size=12, color="white", expand=True),
            ft.Text(f"{kpi['be_closed']}{pct(kpi['be_closed'])}",
                    size=12, color=COL_TRADE_SL_BE, weight=ft.FontWeight.BOLD),
        ], spacing=8),
        ft.Row([
            ft.Container(width=10, height=10, bgcolor=COL_TRADE_SL, border_radius=2),
            ft.Text("Original SL", size=12, color="white", expand=True),
            ft.Text(f"{kpi['orig_sl']}{pct(kpi['orig_sl'])}",
                    size=12, color=COL_TRADE_SL, weight=ft.FontWeight.BOLD),
        ], spacing=8),
        ft.Divider(height=1, color=_BORDER),
        ft.Row([
            ft.Text("Protected trades", size=12, color=COL_LABEL, expand=True),
            ft.Text(f"{kpi['protected']} saved",
                    size=12, color=_GREEN, weight=ft.FontWeight.W_600),
        ]),
    ]

    return ft.Card(
        content=ft.Container(
            content=ft.Column(rows, spacing=10, tight=True),
            padding=14,
            bgcolor=_CARD_BG,
            border_radius=8,
            border=ft.border.all(1, _BORDER),
            width=240,
        ),
        elevation=0,
    )


# ── Trade log table ────────────────────────────────────────────────────────────

def _build_trade_log(trades: list) -> ft.Control:
    closed = sorted(
        [t for t in trades if t.status in ("WIN", "LOSS")],
        key=lambda t: t.closed_at or 0,
        reverse=True,
    )[:60]   # cap at 60 rows for performance

    if not closed:
        return ft.Text("No closed trades to display", color=COL_LABEL, size=12)

    stage_labels = ["SL", "BE", "TR"]
    stage_colors = [COL_TRADE_SL, COL_TRADE_SL_BE, COL_TRADE_SL_TRAIL]

    rows = []
    for t in closed:
        opened_s = datetime.fromtimestamp(t.opened_at).strftime("%m/%d %H:%M") \
                   if t.opened_at else "—"
        stage    = min(t.sl_stage, 2)
        pnl_col  = _GREEN if t.pnl >= 0 else _RED
        dir_col  = COL_SIG_BULL if t.direction == "BULL" else COL_SIG_BEAR
        rows.append(ft.DataRow(cells=[
            ft.DataCell(ft.Text(opened_s,          size=11, color=COL_LABEL)),
            ft.DataCell(ft.Text(t.direction,       size=11, color=dir_col,
                                weight=ft.FontWeight.W_600)),
            ft.DataCell(ft.Text(t.source,          size=11, color=COL_LABEL)),
            ft.DataCell(ft.Text(f"{t.entry:.2f}",  size=11, color="white")),
            ft.DataCell(ft.Text(stage_labels[stage], size=11,
                                color=stage_colors[stage])),
            ft.DataCell(ft.Text(t.status,          size=11, color=pnl_col,
                                weight=ft.FontWeight.W_600)),
            ft.DataCell(ft.Text(_fmt(t.pnl, 2, plus=True), size=11, color=pnl_col,
                                weight=ft.FontWeight.BOLD)),
        ]))

    return ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Opened",  size=11, color=COL_LABEL)),
            ft.DataColumn(ft.Text("Dir",     size=11, color=COL_LABEL)),
            ft.DataColumn(ft.Text("Source",  size=11, color=COL_LABEL)),
            ft.DataColumn(ft.Text("Entry",   size=11, color=COL_LABEL), numeric=True),
            ft.DataColumn(ft.Text("Stage",   size=11, color=COL_LABEL)),
            ft.DataColumn(ft.Text("Result",  size=11, color=COL_LABEL)),
            ft.DataColumn(ft.Text("P&L",     size=11, color=COL_LABEL), numeric=True),
        ],
        rows=rows,
        border=ft.border.all(1, _BORDER),
        border_radius=8,
        heading_row_color=ft.Colors.with_opacity(0.06, "white"),
        data_row_min_height=30,
        column_spacing=14,
    )


# ── Back-test results block ────────────────────────────────────────────────────

def _build_bt_results(bt_trades: list, sym: str, trend_on: bool) -> ft.Control:
    kpi = compute_kpis(bt_trades)
    filter_label = "trend filter ON" if trend_on else "trend filter OFF"
    pf  = kpi["profit_factor"]
    pf_s = "∞" if not math.isfinite(pf) else f"{pf:.2f}×"

    candle_count = len([t for t in bt_trades])  # total sim trades
    header = ft.Text(
        f"Back-test — {sym}  ({kpi['closed']} trades simulated, {filter_label})",
        size=13, color=COL_LABEL, weight=ft.FontWeight.W_500,
    )

    kpi_row = ft.Row([
        _kpi_card("Win Rate",       f"{kpi['win_rate']:.0f}%",
                  _trend_color(kpi["win_rate"] - 50)),
        _kpi_card("Profit Factor",  pf_s,
                  _GREEN if pf >= 1.5 else (_AMBER if pf >= 1.0 else _RED)),
        _kpi_card("Expectancy",     _fmt(kpi["expectancy"], 2, plus=True) + " pts",
                  _trend_color(kpi["expectancy"])),
        _kpi_card("Total P&L",      _fmt(kpi["total_pnl"], 2, plus=True) + " pts",
                  _trend_color(kpi["total_pnl"])),
        _kpi_card("Max Drawdown",   _fmt(-kpi["max_drawdown"], 1) + " pts",
                  _RED if kpi["max_drawdown"] > 0 else "white"),
    ], wrap=True, spacing=8, run_spacing=8)

    curve_w = 700
    curve   = _build_equity_canvas(kpi["equity_curve"], curve_w, 130)

    src_tbl = _build_source_table(kpi["source_stats"])

    return ft.Column([
        header,
        kpi_row,
        ft.Text("Back-test equity curve", size=12, color=COL_LABEL),
        ft.Container(content=curve, border_radius=8,
                     border=ft.border.all(1, _BORDER)),
        ft.Row([
            ft.Column([
                ft.Text("Signal source breakdown", size=12, color=COL_LABEL),
                src_tbl,
            ], spacing=8),
            ft.Container(width=24),
            ft.Column([
                ft.Text("Protection breakdown", size=12, color=COL_LABEL),
                _build_protection_card(kpi),
            ], spacing=8),
        ], wrap=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.START),
    ], spacing=12)


# ── Main view builder ──────────────────────────────────────────────────────────

def build_analysis_view(client, page: ft.Page) -> ft.View:
    """Full-page strategy analysis and back-test view."""

    active_sym:   list[str]  = [QUICK_SYMBOLS[0]]
    bt_running:   list[bool] = [False]
    trend_on:     list[bool] = [True]

    # ── Refs ──────────────────────────────────────────────────────────────────
    chip_row_ref    = ft.Ref[ft.Container]()
    kpi_row_ref     = ft.Ref[ft.Container]()
    curve_ref       = ft.Ref[ft.Container]()
    src_table_ref   = ft.Ref[ft.Container]()
    prot_ref        = ft.Ref[ft.Container]()
    trade_log_ref   = ft.Ref[ft.Container]()
    bt_result_ref   = ft.Ref[ft.Container]()
    bt_btn_ref      = ft.Ref[ft.ElevatedButton]()
    no_data_ref     = ft.Ref[ft.Container]()

    # ── Render helpers ────────────────────────────────────────────────────────
    def _chart_width() -> int:
        return max(480, int(page.width or 900)) - 32

    def _render_live(sym: str) -> None:
        trades = _load_sim_trades(sym)
        kpi    = compute_kpis(trades)
        cw     = _chart_width()
        has_closed = kpi["closed"] > 0

        if no_data_ref.current:
            no_data_ref.current.visible = not has_closed
            no_data_ref.current.update()

        if kpi_row_ref.current:
            kpi_row_ref.current.content = _build_kpi_row(kpi)
            kpi_row_ref.current.visible = has_closed
            kpi_row_ref.current.update()

        if curve_ref.current:
            curve_ref.current.content = _build_equity_canvas(kpi["equity_curve"], cw)
            curve_ref.current.visible = has_closed
            curve_ref.current.update()

        if src_table_ref.current:
            src_table_ref.current.content = _build_source_table(kpi["source_stats"])
            src_table_ref.current.visible = has_closed
            src_table_ref.current.update()

        if prot_ref.current:
            prot_ref.current.content = _build_protection_card(kpi)
            prot_ref.current.visible = has_closed
            prot_ref.current.update()

        if trade_log_ref.current:
            trade_log_ref.current.content = _build_trade_log(trades)
            trade_log_ref.current.visible = has_closed
            trade_log_ref.current.update()

        if bt_result_ref.current:
            bt_result_ref.current.content = ft.Container()
            bt_result_ref.current.update()

        page.update()

    def _switch_symbol(sym: str) -> None:
        sym = sym.strip().upper()
        if not sym:
            return
        active_sym[0] = sym
        if chip_row_ref.current:
            chip_row_ref.current.content = _build_chip_row()
            chip_row_ref.current.update()
        _render_live(sym)

    def _build_chip_row() -> ft.Control:
        chips: list[ft.Control] = []
        for s in QUICK_SYMBOLS:
            if s == active_sym[0]:
                chips.append(ft.FilledButton(
                    s,
                    on_click=lambda e, sym=s: _switch_symbol(sym),
                    style=ft.ButtonStyle(
                        bgcolor=COL_CHIP_ACT, color="black",
                        padding=ft.padding.symmetric(horizontal=10, vertical=4),
                    ),
                ))
            else:
                chips.append(ft.OutlinedButton(
                    s,
                    on_click=lambda e, sym=s: _switch_symbol(sym),
                    style=ft.ButtonStyle(
                        color=COL_LABEL,
                        side=ft.BorderSide(1, "#3a3a3a"),
                        padding=ft.padding.symmetric(horizontal=10, vertical=4),
                    ),
                ))
        chips.append(ft.IconButton(
            ft.Icons.REFRESH,
            icon_color=COL_CHIP_ACT,
            icon_size=18,
            tooltip="Refresh",
            on_click=lambda e: _render_live(active_sym[0]),
        ))
        return ft.Row(chips, spacing=6, wrap=True)

    # ── Back-test ─────────────────────────────────────────────────────────────
    async def _on_run_backtest(e) -> None:
        if bt_running[0]:
            return
        bt_running[0] = True
        if bt_btn_ref.current:
            bt_btn_ref.current.disabled = True
            bt_btn_ref.current.text     = "Running…"
            bt_btn_ref.current.update()
        if bt_result_ref.current:
            bt_result_ref.current.content = ft.Row([
                ft.ProgressRing(width=18, height=18, stroke_width=2),
                ft.Text("Scanning candle history…", color=COL_LABEL, size=12),
            ], spacing=10)
            bt_result_ref.current.update()

        sym = active_sym[0]
        try:
            loop = asyncio.get_running_loop()
            candles, signals = await loop.run_in_executor(
                None, prepare_backtest, sym,
            )
            bt_trades = await loop.run_in_executor(
                None, simulate_trades, signals, candles, trend_on[0],
            )
            if bt_result_ref.current:
                if not candles:
                    bt_result_ref.current.content = ft.Text(
                        f"No candle cache found for {sym}. "
                        "Open the Liquidity view first to populate the buffer.",
                        color=_AMBER, size=12,
                    )
                elif not bt_trades:
                    bt_result_ref.current.content = ft.Text(
                        "Back-test ran but produced no trades with the current filters.",
                        color=COL_LABEL, size=12,
                    )
                else:
                    bt_result_ref.current.content = _build_bt_results(
                        bt_trades, sym, trend_on[0]
                    )
                bt_result_ref.current.update()
        finally:
            bt_running[0] = False
            if bt_btn_ref.current:
                bt_btn_ref.current.disabled = False
                bt_btn_ref.current.text     = "Run Back-test"
                bt_btn_ref.current.update()
        page.update()

    # ── Initial data load ─────────────────────────────────────────────────────
    sym0   = active_sym[0]
    trades0 = _load_sim_trades(sym0)
    kpi0    = compute_kpis(trades0)
    cw0     = _chart_width()
    has0    = kpi0["closed"] > 0

    user_info = client.user or {}
    username  = user_info.get("username") or user_info.get("email") or "User"

    # ── Layout ────────────────────────────────────────────────────────────────
    body = ft.Column(
        controls=[
            # Symbol picker
            ft.Container(ref=chip_row_ref, content=_build_chip_row()),
            ft.Divider(height=1, color=_BORDER),

            # ── No-data placeholder ───────────────────────────────────────────
            ft.Container(
                ref=no_data_ref,
                visible=not has0,
                content=ft.Column([
                    ft.Icon(ft.Icons.QUERY_STATS, size=48, color=COL_LABEL),
                    ft.Text("No closed trades for this symbol yet.",
                            size=14, color=COL_LABEL),
                    ft.Text("Open the Liquidity view and let some signals play out.",
                            size=12, color=_DIM),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
                padding=ft.padding.symmetric(vertical=32),
                alignment=ft.alignment.center,
            ),

            # ── Live trade KPIs ───────────────────────────────────────────────
            _section("Live Trade Performance"),
            ft.Container(
                ref=kpi_row_ref,
                visible=has0,
                content=_build_kpi_row(kpi0),
            ),

            # ── Equity curve ──────────────────────────────────────────────────
            ft.Text("Equity curve", size=12, color=COL_LABEL,
                    visible=has0),
            ft.Container(
                ref=curve_ref,
                visible=has0,
                content=_build_equity_canvas(kpi0["equity_curve"], cw0),
                border_radius=8,
                border=ft.border.all(1, _BORDER),
            ),

            # ── Breakdown tables ──────────────────────────────────────────────
            ft.Row(
                [
                    ft.Column([
                        ft.Text("Signal source breakdown",
                                size=12, color=COL_LABEL, visible=has0),
                        ft.Container(
                            ref=src_table_ref,
                            visible=has0,
                            content=_build_source_table(kpi0["source_stats"]),
                        ),
                    ], spacing=8),
                    ft.Container(width=24),
                    ft.Column([
                        ft.Text("Protection breakdown",
                                size=12, color=COL_LABEL, visible=has0),
                        ft.Container(
                            ref=prot_ref,
                            visible=has0,
                            content=_build_protection_card(kpi0),
                        ),
                    ], spacing=8),
                ],
                wrap=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),

            # ── Trade log ─────────────────────────────────────────────────────
            ft.Text("Trade log (most recent 60)", size=12, color=COL_LABEL,
                    visible=has0),
            ft.Container(
                ref=trade_log_ref,
                visible=has0,
                content=_build_trade_log(trades0),
            ),

            ft.Divider(color=_BORDER),

            # ── Back-test section ─────────────────────────────────────────────
            _section("Back-test"),
            ft.Text(
                "Replays the full strategy over the cached 48-hour candle history. "
                "Results are independent of any live trades.",
                size=12, color=COL_LABEL,
            ),
            ft.Row([
                ft.Checkbox(
                    label="Trend filter",
                    value=True,
                    active_color=COL_CHIP_ACT,
                    check_color="black",
                    label_style=ft.TextStyle(size=12, color=COL_LABEL),
                    on_change=lambda e: trend_on.__setitem__(0, e.control.value),
                ),
                ft.ElevatedButton(
                    ref=bt_btn_ref,
                    text="Run Back-test",
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    on_click=_on_run_backtest,
                    style=ft.ButtonStyle(
                        bgcolor=COL_CHIP_ACT,
                        color="black",
                        padding=ft.padding.symmetric(horizontal=20, vertical=10),
                    ),
                ),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),

            ft.Container(ref=bt_result_ref),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    return ft.View(
        route="/analysis",
        controls=[
            nav_app_bar(page, "Strategy Analysis", "/analysis", username),
            ft.SafeArea(
                content=body,
                expand=True,
            ),
        ],
        padding=16,
        bgcolor=_BG,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
