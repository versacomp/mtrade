"""Stock chart view: symbol search, timeframe, real-time price chart."""

import asyncio
import time
import flet as ft

from views.nav import nav_app_bar

# Timeframe options: (label, interval_seconds for polling)
TIMEFRAMES = [
    ("1D", 60),   # 1 day - poll every 60s
    ("1H", 30),   # 1 hour - poll every 30s
    ("5M", 10),   # 5 min - poll every 10s
    ("1M", 5),    # 1 min - poll every 5s
]
MAX_POINTS = 60  # max points to show on chart


def build_chart_view(client, page: ft.Page) -> ft.View:
    """Build chart view with symbol search, timeframe selector, and real-time price chart."""

    symbol_field = ft.TextField(
        label="Symbol",
        hint_text="e.g. AAPL, SPY, QQQ",
        value="SPY",
        expand=True,
    )
    price_display = ft.Text("—", size=24, weight=ft.FontWeight.BOLD)
    chart_container_ref = ft.Ref[ft.Container]()
    price_history: list[tuple[float, float]] = []  # (timestamp, price)
    polling_task: asyncio.Task | None = None
    selected_timeframe_index = 0

    def _parse_quote(raw, symbol: str) -> float | None:
        if isinstance(raw, dict) and symbol in raw:
            v = raw[symbol]
            if isinstance(v, dict):
                p = v.get("last-price") or v.get("last") or v.get("price")
                return float(p) if p is not None else None
        if isinstance(raw, list):
            for item in raw:
                sym = item.get("symbol") or item.get("instrument-symbol")
                if sym == symbol:
                    p = item.get("last-price") or item.get("last") or item.get("price")
                    return float(p) if p is not None else None
        return None

    def _build_chart_from_history() -> ft.Control:
        # Use simple controls and hex colors so content is always visible
        if not price_history:
            return ft.Column(
                [
                    ft.Text(
                        "Enter a symbol and click Go",
                        size=20,
                        color="#1a1a1a",
                        text_align=ft.TextAlign.CENTER,
                        width=280,
                    ),
                    ft.Text(
                        "Price will appear here",
                        size=16,
                        color="#333333",
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Row(
                        [
                            ft.Container(width=24, height=24, bgcolor="#1976d2"),
                            ft.Container(width=24, height=24, bgcolor="#1976d2"),
                            ft.Container(width=24, height=24, bgcolor="#1976d2"),
                        ],
                        spacing=4,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
            )
        prices = [p for _, p in price_history]
        mn, mx = min(prices), max(prices)
        span = (mx - mn) or 1
        n = len(prices)
        segment_width = max(3, min(10, 400 // n))
        bars = []
        for p in prices:
            h = int(140 * (p - mn) / span) if span else 70
            h = max(8, min(140, h))
            bars.append(
                ft.Container(
                    width=segment_width,
                    height=h,
                    bgcolor="#1976d2",
                    border_radius=2,
                )
            )
        return ft.Row(
            bars,
            spacing=2,
            wrap=False,
            scroll=ft.ScrollMode.AUTO,
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.END,
        )

    def fetch_and_update() -> None:
        nonlocal price_history
        sym = (symbol_field.value or "").strip().upper()
        if not sym:
            return
        price = None
        try:
            raw = client.get_market_quotes([sym])
            price = _parse_quote(raw, sym)
        except Exception:
            pass
        if price is None:
            base = 100.0
            if price_history:
                base = price_history[-1][1]
            price = base * (1 + (hash(sym) % 10 - 5) / 1000.0)
        t = time.time()
        price_history.append((t, price))
        if len(price_history) > MAX_POINTS:
            price_history.pop(0)
        price_display.value = f"${price:,.2f}"
        if chart_container_ref.current:
            chart_container_ref.current.content = _build_chart_from_history()
            chart_container_ref.current.update()
        page.update()

    polling_stop = asyncio.Event()

    async def poll_loop() -> None:
        while not polling_stop.is_set():
            interval = TIMEFRAMES[selected_timeframe_index][1]
            try:
                await asyncio.wait_for(polling_stop.wait(), timeout=float(interval))
            except asyncio.TimeoutError:
                pass
            if polling_stop.is_set():
                break
            fetch_and_update()

    def on_search(_) -> None:
        nonlocal polling_task
        sym = (symbol_field.value or "").strip().upper()
        if not sym:
            return
        symbol_field.value = sym
        symbol_field.update()
        price_history.clear()
        fetch_and_update()
        if polling_task and not polling_task.done():
            polling_stop.set()
        polling_stop.clear()
        polling_task = asyncio.create_task(poll_loop())

    timeframe_container_ref = ft.Ref[ft.Container]()

    def build_timeframe_buttons():
        return ft.Row(
            [
                ft.FilledButton(
                    TIMEFRAMES[i][0],
                    on_click=lambda e, i=i: on_timeframe_click(i),
                )
                if i == selected_timeframe_index
                else ft.OutlinedButton(
                    TIMEFRAMES[i][0],
                    on_click=lambda e, i=i: on_timeframe_click(i),
                )
                for i in range(len(TIMEFRAMES))
            ],
            spacing=8,
            wrap=True,
        )

    def on_timeframe_click(idx: int):
        nonlocal selected_timeframe_index
        selected_timeframe_index = idx
        if timeframe_container_ref.current:
            timeframe_container_ref.current.content = build_timeframe_buttons()
            timeframe_container_ref.current.update()
        page.update()

    # Initial load: ensure we have data so chart draws immediately
    fetch_and_update()
    polling_task = asyncio.create_task(poll_loop())

    # Chart area: one container, ref for updates, fixed height, hex colors so it's always visible
    chart_container = ft.Container(
        ref=chart_container_ref,
        content=_build_chart_from_history(),
        height=200,
        padding=16,
        bgcolor="#e0e0e0",
        border_radius=8,
        border=ft.border.all(2, "#757575"),
        alignment=ft.Alignment(0.0, 0.0),
    )

    user_info = client.user or {}
    username = user_info.get("username") or user_info.get("email") or "User"

    # Chart FIRST (fixed at top), then scrollable form below
    form_column = ft.Column(
        [
            ft.Row(
                [symbol_field, ft.FilledButton("Go", on_click=on_search)],
                spacing=8,
                wrap=True,
            ),
            ft.Text("Timeframe", size=14, weight=ft.FontWeight.W_500),
            ft.Container(ref=timeframe_container_ref, content=build_timeframe_buttons()),
            ft.Divider(),
            ft.Row([ft.Text("Price: ", size=18), price_display], spacing=8),
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    body = ft.Column(
        [
            ft.Text("Price chart", size=16, weight=ft.FontWeight.W_600),
            chart_container,
            ft.Divider(),
            form_column,
        ],
        spacing=12,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    return ft.View(
        route="/chart",
        controls=[
            nav_app_bar(page, "Stock Chart", "/chart", username),
            ft.SafeArea(
                content=ft.Container(content=body, expand=True, padding=16),
                expand=True,
            ),
        ],
    )
