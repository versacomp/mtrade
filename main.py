"""
MTrade - Trading tool connecting to tastytrade API.

Uses tastytrade REST API at api.cert.tastyworks.com (sandbox).
OAuth: https://developer.tastytrade.com/oauth/
API Overview: https://developer.tastytrade.com/api-overview/
"""

import asyncio
import flet as ft

from views.chart_view import build_chart_view
from views.dashboard_view import build_dashboard_view
from views.login_view import build_login_view


def main(page: ft.Page) -> None:
    """MTrade main app entry point."""
    page.title = "MTrade"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.padding = 0
    page.spacing = 0

    # Mobile-friendly: viewport meta, responsive layout
    page.theme = ft.Theme(
        color_scheme_seed=ft.Colors.BLUE_700,
        visual_density=ft.VisualDensity.COMPACT,
        page_transitions=ft.PageTransitionsTheme(
            android=ft.PageTransitionTheme.ZOOM,
            ios=ft.PageTransitionTheme.CUPERTINO,
        ),
    )
    page.theme_mode = ft.ThemeMode.SYSTEM

    # Responsive breakpoints for mobile
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.scroll = ft.ScrollMode.AUTO

    client_ref: list = []  # Mutable container for authenticated client

    def on_login_success(c) -> None:
        client_ref.append(c)
        page.views.clear()
        page.views.append(build_dashboard_view(c, page))
        asyncio.create_task(page.push_route("/dashboard"))
        page.update()

    def on_login_error(msg: str) -> None:
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg),
            bgcolor=ft.Colors.ERROR_CONTAINER,
            open=True,
        )
        page.update()

    def route_change(e: ft.RouteChangeEvent) -> None:
        troute = ft.TemplateRoute(page.route or "/")
        page.views.clear()
        if troute.match("/"):
            page.views.append(
                build_login_view(on_success=on_login_success, on_error=on_login_error)
            )
        elif troute.match("/dashboard"):
            if client_ref:
                page.views.append(build_dashboard_view(client_ref[0], page))
            else:
                asyncio.create_task(page.push_route("/"))
                return
        elif troute.match("/chart"):
            if client_ref:
                page.views.append(build_chart_view(client_ref[0], page))
            else:
                asyncio.create_task(page.push_route("/"))
                return
        page.update()

    def view_pop(e: ft.ViewPopEvent) -> None:
        page.views.pop()
        top = page.views[-1]
        asyncio.create_task(page.push_route(top.route))

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    # Trigger initial view setup (route_change only fires on route *change*,
    # so we must call it manually on startup)
    route_change(None)


if __name__ == "__main__":
    # AppView: FLET_APP (native window), FLET_APP_WEB, WEB_BROWSER
    ft.run(
        main,
        view=ft.AppView.FLET_APP,
        assets_dir="assets",
    )
