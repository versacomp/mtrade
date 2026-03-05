"""Shared navigation for authenticated views."""

import asyncio
import flet as ft


def nav_app_bar(
    page: ft.Page,
    title: str,
    current_route: str,
    username: str = "User",
) -> ft.AppBar:
    """AppBar with Dashboard, Chart links and logout."""
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
            ft.Container(width=8),
            ft.Text(username, size=14),
            ft.IconButton(
                ft.Icons.LOGOUT,
                tooltip="Sign out",
                on_click=lambda _: asyncio.create_task(page.push_route("/")) or page.update(),
            ),
        ],
    )
