"""Shared navigation for authenticated views."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import flet as ft

import api.connection_status as cs
import config
from config import set_pref

_ET = ZoneInfo("America/New_York")

# (tooltip label, route, icon)
_NAV_ITEMS: list[tuple[str, str, str]] = [
    ("Dashboard", "/dashboard", ft.Icons.DASHBOARD),
    ("Chart",     "/chart",     ft.Icons.CANDLESTICK_CHART),
    ("Liquidity", "/liquidity", ft.Icons.WATER_DROP),
    ("Analysis",  "/analysis",  ft.Icons.ANALYTICS),
    ("Settings",  "/settings",  ft.Icons.SETTINGS),
]

_APPBAR_BGCOLOR = "#0F172A"          # slate-900
_ICON_ACTIVE    = ft.Colors.WHITE
_ICON_DIM       = ft.Colors.with_opacity(0.50, ft.Colors.WHITE)


def nav_app_bar(
    page: ft.Page,
    title: str,
    current_route: str,
    username: str = "User",
) -> ft.AppBar:
    """Compact AppBar: icon-only nav · status dot · env dot · clock · theme · logout."""

    dot_ref       = ft.Ref[ft.Container]()
    theme_btn_ref = ft.Ref[ft.IconButton]()
    clock_ref     = ft.Ref[ft.Text]()

    # ── ET clock — self-terminates when this AppBar is replaced ───────────────
    async def _clock_loop() -> None:
        while True:
            await asyncio.sleep(1)
            if clock_ref.current is None:
                return
            clock_ref.current.value = datetime.now(_ET).strftime("%H:%M:%S ET")
            clock_ref.current.update()

    asyncio.create_task(_clock_loop())

    # ── Connection status dot ──────────────────────────────────────────────────
    def _refresh(state: cs.ConnState, detail: str) -> None:
        if dot_ref.current:
            dot_ref.current.bgcolor = cs.COLORS[state]
            dot_ref.current.tooltip = f"{state.value} — {detail}"
            dot_ref.current.update()

    cs.register_listener(_refresh)
    _init_state, _init_detail = cs.get()

    # ── Theme toggle ───────────────────────────────────────────────────────────
    def _theme_icon() -> str:
        return (
            ft.Icons.LIGHT_MODE
            if page.theme_mode == ft.ThemeMode.DARK
            else ft.Icons.DARK_MODE
        )

    def _toggle_theme(_: ft.ControlEvent) -> None:
        page.theme_mode = (
            ft.ThemeMode.LIGHT
            if page.theme_mode == ft.ThemeMode.DARK
            else ft.ThemeMode.DARK
        )
        set_pref("theme_mode", "light" if page.theme_mode == ft.ThemeMode.LIGHT else "dark")
        if theme_btn_ref.current:
            theme_btn_ref.current.icon = _theme_icon()
            theme_btn_ref.current.update()
        page.update()

    # ── Icon nav buttons ───────────────────────────────────────────────────────
    nav_buttons: list[ft.Control] = []
    for label, route, icon in _NAV_ITEMS:
        is_active = current_route == route

        async def _navigate(e: ft.ControlEvent, _route: str = route) -> None:
            await page.push_route(_route)

        nav_buttons.append(
            ft.IconButton(
                icon=icon,
                icon_color=_ICON_ACTIVE if is_active else _ICON_DIM,
                icon_size=20,
                tooltip=label,
                disabled=is_active,
                on_click=_navigate if not is_active else None,
                style=ft.ButtonStyle(
                    bgcolor={
                        ft.ControlState.DEFAULT:  (
                            ft.Colors.with_opacity(0.18, ft.Colors.WHITE)
                            if is_active else ft.Colors.TRANSPARENT
                        ),
                        ft.ControlState.HOVERED:  ft.Colors.with_opacity(0.10, ft.Colors.WHITE),
                        ft.ControlState.DISABLED: ft.Colors.with_opacity(0.18, ft.Colors.WHITE),
                    },
                    padding=ft.padding.all(5),
                ),
            )
        )

    # ── Environment dot ────────────────────────────────────────────────────────
    _sandbox = config.is_sandbox()
    env_dot = ft.Container(
        width=8,
        height=8,
        border_radius=4,
        bgcolor="#E65100" if _sandbox else "#1B5E20",
        tooltip=f"{'SANDBOX' if _sandbox else 'PRODUCTION'} — {config.get_api_base()}",
    )

    return ft.AppBar(
        title=ft.Text(title, color=ft.Colors.WHITE, size=14),
        bgcolor=_APPBAR_BGCOLOR,
        actions=[
            *nav_buttons,
            ft.Container(width=8),
            # ── Connection dot (color = LIVE/DEMO/OFFLINE) ─────────────────────
            ft.Container(
                ref=dot_ref,
                width=8,
                height=8,
                border_radius=4,
                bgcolor=cs.COLORS[_init_state],
                tooltip=f"{_init_state.value} — {_init_detail}",
            ),
            ft.Container(width=5),
            # ── Environment dot (amber = sandbox, green = production) ──────────
            env_dot,
            ft.Container(width=10),
            # ── ET clock ───────────────────────────────────────────────────────
            ft.Text(
                ref=clock_ref,
                value=datetime.now(_ET).strftime("%H:%M:%S ET"),
                size=11,
                color=ft.Colors.with_opacity(0.60, ft.Colors.WHITE),
                font_family="monospace",
                tooltip="Current time (US Eastern)",
            ),
            ft.Container(width=2),
            # ── Theme toggle ───────────────────────────────────────────────────
            ft.IconButton(
                ref=theme_btn_ref,
                icon=_theme_icon(),
                icon_color=_ICON_DIM,
                icon_size=18,
                tooltip="Toggle dark / light mode",
                on_click=_toggle_theme,
                style=ft.ButtonStyle(padding=ft.padding.all(4)),
            ),
            # ── Logout ─────────────────────────────────────────────────────────
            ft.IconButton(
                ft.Icons.LOGOUT,
                icon_color=_ICON_DIM,
                icon_size=18,
                tooltip=f"Sign out ({username})",
                on_click=lambda _: (
                    cs.clear_listener(),
                    asyncio.create_task(page.push_route("/")),
                    page.update(),
                ),
                style=ft.ButtonStyle(padding=ft.padding.all(4)),
            ),
            ft.Container(width=4),
        ],
    )
