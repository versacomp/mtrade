"""Shared navigation for authenticated views."""

import flet as ft

import api.connection_status as cs

_NAV_ITEMS: list[tuple[str, str]] = [
    ("Dashboard", "/dashboard"),
    ("Chart",     "/chart"),
    ("Liquidity", "/liquidity"),
    ("Analysis",  "/analysis"),
]


def nav_app_bar(
    page: ft.Page,
    title: str,
    current_route: str,
    username: str = "User",
) -> ft.AppBar:
    """AppBar with nav links, logout, and a live connection-status indicator."""

    dot_ref      = ft.Ref[ft.Container]()
    lbl_ref      = ft.Ref[ft.Text]()
    progress_ref = ft.Ref[ft.ProgressBar]()

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

    # ── Nav buttons ────────────────────────────────────────────────────────────
    nav_buttons: list[ft.Control] = []
    for label, route in _NAV_ITEMS:
        is_active = current_route == route

        async def _navigate(e: ft.ControlEvent, _route: str = route) -> None:
            # Immediate visual feedback: show progress bar while view rebuilds.
            if progress_ref.current:
                progress_ref.current.visible = True
                progress_ref.current.update()
            await page.push_route(_route)

        btn = ft.TextButton(
            label,
            disabled=is_active,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.DEFAULT:  ft.Colors.WHITE if is_active else ft.Colors.with_opacity(0.7, ft.Colors.WHITE),
                    ft.ControlState.DISABLED: ft.Colors.WHITE,
                },
                bgcolor={
                    ft.ControlState.DEFAULT:  ft.Colors.with_opacity(0.18, ft.Colors.WHITE) if is_active else ft.Colors.TRANSPARENT,
                    ft.ControlState.HOVERED:  ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
                    ft.ControlState.DISABLED: ft.Colors.with_opacity(0.18, ft.Colors.WHITE),
                },
                overlay_color=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
            ),
            on_click=_navigate if not is_active else None,
        )
        nav_buttons.append(btn)

    return ft.AppBar(
        title=ft.Text(title),
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
            ft.Container(width=12),
            # ── User / logout ─────────────────────────────────────────────────
            ft.Text(username, size=14),
            ft.IconButton(
                ft.Icons.LOGOUT,
                tooltip="Sign out",
                on_click=lambda _: (
                    cs.clear_listener(),
                    page.run_task(page.push_route, "/"),
                    page.update(),
                ),
            ),
        ],
        # Thin indeterminate progress bar — visible only during navigation.
        bottom=ft.ProgressBar(
            ref=progress_ref,
            visible=False,
            color=ft.Colors.BLUE_300,
            bgcolor=ft.Colors.TRANSPARENT,
            value=None,  # indeterminate
        ),
    )
