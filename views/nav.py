"""Shared navigation for authenticated views."""

import asyncio

import flet as ft

import api.connection_status as cs


def nav_app_bar(
    page: ft.Page,
    title: str,
    current_route: str,
    username: str = "User",
) -> ft.AppBar:
    """AppBar with nav links, logout, and a live connection-status indicator."""

    dot_ref = ft.Ref[ft.Container]()
    lbl_ref = ft.Ref[ft.Text]()

    def _refresh(state: cs.ConnState, detail: str) -> None:
        """Called by connection_status whenever the state changes."""
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

    # Register this AppBar as the active listener; replaces any previous one.
    cs.register_listener(_refresh)

    # Seed widget appearance from the module's last-known state so the colour
    # is correct immediately — even before the first set_status() fires.
    _init_state, _init_detail = cs.get()
    _init_color = cs.COLORS[_init_state]

    return ft.AppBar(
        title=ft.Text(title),
        actions=[
            ft.TextButton(
                "Dashboard",
                on_click=lambda _: asyncio.create_task(page.push_route("/dashboard")),
            ),
            ft.TextButton(
                "Chart",
                on_click=lambda _: asyncio.create_task(page.push_route("/chart")),
            ),
            ft.TextButton(
                "Liquidity",
                on_click=lambda _: asyncio.create_task(page.push_route("/liquidity")),
            ),
            ft.TextButton(
                "Analysis",
                on_click=lambda _: asyncio.create_task(page.push_route("/analysis")),
            ),
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
            ft.Container(width=12),
            # ── User / logout ─────────────────────────────────────────────────
            ft.Text(username, size=14),
            ft.IconButton(
                ft.Icons.LOGOUT,
                tooltip="Sign out",
                on_click=lambda _: (
                    cs.clear_listener(),
                    asyncio.create_task(page.push_route("/")),
                    page.update(),
                ),
            ),
        ],
    )
