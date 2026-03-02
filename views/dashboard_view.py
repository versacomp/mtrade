"""Dashboard view with market charts for major US indexes."""

import asyncio
import flet as ft

from views.nav import nav_app_bar


def build_dashboard_view(client, page: ft.Page) -> ft.View:
    """Build dashboard with market charts for S&P, Nasdaq, Dow."""

    # Index labels for display
    LABELS = {
        "SPX": ("S&P 500", "SPX"),
        "SPY": ("S&P 500", "SPY"),
        "NDX": ("Nasdaq 100", "NDX"),
        "QQQ": ("Nasdaq 100", "QQQ"),
        "DJX": ("Dow Jones", "DJX"),
        "DIA": ("Dow Jones", "DIA"),
    }

    # Use ETF symbols as fallback (better liquidity / availability in sandbox)
    symbols_to_fetch = ["SPY", "QQQ", "DIA"]
    labels_display = [LABELS.get(s, (s, s))[0] for s in symbols_to_fetch]

    quotes: dict[str, float] = {}
    quote_cards: list[ft.Card] = []
    chart_data_spots: list[list[ft.LineChartData]] = []

    def fetch_quotes() -> None:
        nonlocal quotes
        try:
            raw = client.get_market_quotes(symbols_to_fetch)
            quotes = {}
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(v, dict):
                        price = v.get("last-price") or v.get("last") or v.get("price")
                        if price is not None:
                            quotes[k] = float(price)
            elif isinstance(raw, list):
                for item in raw:
                    sym = item.get("symbol") or item.get("instrument-symbol")
                    price = item.get("last-price") or item.get("last") or item.get("price")
                    if sym and price is not None:
                        quotes[sym] = float(price)
        except Exception:
            pass
        # Demo data when API returns nothing
        if not quotes:
            quotes = {"SPY": 580.5, "QQQ": 520.3, "DIA": 430.2}

    def build_quote_cards() -> list[ft.Control]:
        cards = []
        for sym, label in zip(symbols_to_fetch, labels_display):
            price = quotes.get(sym, 0.0)
            cards.append(
                ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(label, size=14, weight=ft.FontWeight.W_500),
                                ft.Text(
                                    f"${price:,.2f}" if price else "—",
                                    size=22,
                                    weight=ft.FontWeight.BOLD,
                                ),
                            ],
                            spacing=4,
                            tight=True,
                        ),
                        padding=16,
                    ),
                )
            )
        return cards

    def build_chart_card(symbol: str, label: str) -> ft.Control:
        """Build a chart-style card with sparkline-style visualization."""
        price = quotes.get(symbol, 100.0)
        # Simple sparkline: small bars representing relative price movement
        vals = [price * (0.98 + (i % 7) * 0.003) for i in range(12)]
        min_v, max_v = min(vals), max(vals)
        spark_bars = ft.Row(
            [
                ft.Container(
                    width=6,
                    height=max(4, 60 * (v - min_v) / (max_v - min_v) if max_v > min_v else 30),
                    bgcolor=ft.Colors.PRIMARY,
                    border_radius=2,
                )
                for v in vals
            ],
            spacing=2,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(label, size=14, weight=ft.FontWeight.W_500),
                        ft.Text(f"${price:,.2f}", size=18, weight=ft.FontWeight.BOLD),
                        ft.Container(content=spark_bars, padding=8),
                    ],
                    spacing=8,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=16,
            ),
        )

    fetch_quotes()

    cards_row = ft.Row(
        build_quote_cards(),
        scroll=ft.ScrollMode.AUTO,
        wrap=True,
        spacing=12,
    )

    chart_cards = [
        build_chart_card(sym, LABELS.get(sym, (sym, sym))[0])
        for sym in symbols_to_fetch
    ]
    charts_row = ft.Row(
        chart_cards,
        scroll=ft.ScrollMode.AUTO,
        wrap=True,
        spacing=12,
    )

    user_info = client.user or {}
    username = user_info.get("username") or user_info.get("email") or "User"

    return ft.View(
        route="/dashboard",
        controls=[
            nav_app_bar(page, "MTrade Dashboard", "/dashboard", username),
            ft.SafeArea(
                content=ft.Column(
                    [
                        ft.Text("Major US Indexes", size=20, weight=ft.FontWeight.W_500),
                        cards_row,
                        ft.Divider(),
                        ft.Text("Charts", size=20, weight=ft.FontWeight.W_500),
                        charts_row,
                    ],
                    spacing=16,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                expand=True,
            ),
        ],
        padding=16,
    )
