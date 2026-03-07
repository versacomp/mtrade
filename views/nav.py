"""Shared navigation for authenticated views."""

import asyncio

import flet as ft

import api.connection_status as cs
import config
from config import set_pref

_NAV_ITEMS: list[tuple[str, str]] = [
    ("Dashboard", "/dashboard"),
    ("Chart",     "/chart"),
    ("Liquidity", "/liquidity"),
    ("Analysis",  "/analysis"),
    ("Settings",  "/settings"),
]

# AppBar always uses this colour so white text is visible in both themes.
_APPBAR_BGCOLOR = "#0F172A"   # slate-900 — obsidian with blue depth


def nav_app_bar(
    page: ft.Page,
    title: str,
    current_route: str,
    username: str = "User",
) -> ft.AppBar:
    """AppBar with nav links, theme toggle, logout, and connection-status dot."""

    dot_ref       = ft.Ref[ft.Container]()
    lbl_ref       = ft.Ref[ft.Text]()
    theme_btn_ref = ft.Ref[ft.IconButton]()

    def _refresh(state: cs.ConnState, detail: str) -> None:
        """Update the status dot and label when connection state changes."""
        color = cs.COLORS[state]
        if dot_ref.current:
            dot_ref.current.bgcolor = color
            dot_ref.current.tooltip = detail
            dot_ref.current.update()
        if lbl_ref.current:
            lbl_ref.current.value   = state.value
            lbl_ref.current.color   = color
            lbl_ref.current.tooltip = detail
            lbl_ref.current.update()

    cs.register_listener(_refresh)

    _init_state, _init_detail = cs.get()
    _init_color = cs.COLORS[_init_state]

    # ── Theme toggle ───────────────────────────────────────────────────────────
    def _theme_icon() -> str:
        """Return the icon for the theme toggle button.

        Returns a sun icon when the current theme is dark (clicking will switch to light),
        and a moon icon when the current theme is light (clicking will switch to dark).
        """
        return (
            ft.Icons.LIGHT_MODE
            if page.theme_mode == ft.ThemeMode.DARK
            else ft.Icons.DARK_MODE
        )

    def _toggle_theme(_: ft.ControlEvent) -> None:
        """Switch between dark and light theme and persist the choice to preferences."""
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

    # ── Nav buttons ────────────────────────────────────────────────────────────
    nav_buttons: list[ft.Control] = []
    for label, route in _NAV_ITEMS:
        is_active = current_route == route

        async def _navigate(e: ft.ControlEvent, _route: str = route) -> None:
            """Push *_route* onto the page navigation stack."""
            await page.push_route(_route)

        btn = ft.TextButton(
            label,
            disabled=is_active,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.DEFAULT:  ft.Colors.with_opacity(0.75, ft.Colors.WHITE),
                    ft.ControlState.HOVERED:  ft.Colors.WHITE,
                    ft.ControlState.DISABLED: ft.Colors.WHITE,
                },
                bgcolor={
                    ft.ControlState.DEFAULT:  (
                        ft.Colors.with_opacity(0.20, ft.Colors.WHITE) if is_active
                        else ft.Colors.TRANSPARENT
                    ),
                    ft.ControlState.HOVERED:  ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
                    ft.ControlState.DISABLED: ft.Colors.with_opacity(0.20, ft.Colors.WHITE),
                },
                overlay_color=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
            ),
            on_click=_navigate if not is_active else None,
        )
        nav_buttons.append(btn)

    return ft.AppBar(
        title=ft.Text(title, color=ft.Colors.WHITE),
        bgcolor=_APPBAR_BGCOLOR,
        actions=[
            *nav_buttons,
            ft.Container(width=12),
            # ── Status dot ────────────────────────────────────────────────────
            ft.Container(
                ref=dot_ref,
                width=10,
                height=10,
                border_radius=5,
                bgcolor=_init_color,
                tooltip=_init_detail,
            ),
            ft.Container(width=4),
            ft.Text(
                ref=lbl_ref,
                value=_init_state.value,
                size=11,
                color=_init_color,
                weight=ft.FontWeight.W_600,
                tooltip=_init_detail,
            ),
            ft.Container(width=8),
            # ── Environment badge ─────────────────────────────────────────────
            ft.Container(
                content=ft.Text(
                    "SANDBOX" if config.is_sandbox() else "PRODUCTION",
                    size=10,
                    color=ft.Colors.WHITE,
                    weight=ft.FontWeight.BOLD,
                ),
                bgcolor="#E65100" if config.is_sandbox() else "#1B5E20",
                border_radius=10,
                padding=ft.padding.symmetric(horizontal=8, vertical=3),
                tooltip=(
                    f"Connected to: {config.get_api_base()}"
                ),
            ),
            ft.Container(width=4),
            # ── Theme toggle ──────────────────────────────────────────────────
            ft.IconButton(
                ref=theme_btn_ref,
                icon=_theme_icon(),
                icon_color=ft.Colors.WHITE,
                icon_size=18,
                tooltip="Toggle dark / light mode",
                on_click=_toggle_theme,
                style=ft.ButtonStyle(padding=ft.padding.all(4)),
            ),
            ft.Container(width=4),
            # ── User / logout ─────────────────────────────────────────────────
            ft.Text(username, size=14, color=ft.Colors.WHITE),
            ft.IconButton(
                ft.Icons.LOGOUT,
                icon_color=ft.Colors.WHITE,
                tooltip="Sign out",
                on_click=lambda _: (
                    cs.clear_listener(),
                    asyncio.create_task(page.push_route("/")),
                    page.update(),
                ),
            ),
        ],
    )
