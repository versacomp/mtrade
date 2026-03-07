"""
MTrade - Trading tool connecting to tastytrade API.

Supports both the production API (api.tastyworks.com) and the sandbox/certification
environment (api.cert.tastyworks.com). The active environment is selected at login.

OAuth: https://developer.tastytrade.com/oauth/
API Overview: https://developer.tastytrade.com/api-overview/
"""

import asyncio
import logging
import logging.handlers
import flet as ft

import config
from version import __version__

from views.analysis_view import build_analysis_view
from views.settings_view import build_settings_view
from views.chart_view import build_chart_view
from views.dashboard_view import build_dashboard_view
from views.institutional_liquidity_view import build_institutional_liquidity_view
from views.login_view import build_login_view


def _configure_logging() -> None:
    """
    Route all api.* logs to console and a rolling file (mtrade_api.log).
    Level: DEBUG — captures every request, response status, and error body.
    """
    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    # 5 MB rolling log, keep last 3 files
    file_handler = logging.handlers.RotatingFileHandler(
        "mtrade_api.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    # Only configure the api.* namespace to avoid Flet framework noise
    api_log = logging.getLogger("api")
    api_log.setLevel(logging.DEBUG)
    if not api_log.handlers:
        api_log.addHandler(console)
        api_log.addHandler(file_handler)
    api_log.propagate = False


def main(page: ft.Page) -> None:
    """MTrade main app entry point."""
    _configure_logging()
    page.title = f"MTrade v{__version__}"
    _saved = config.get_pref("theme_mode", "system")
    page.theme_mode = {
        "dark":  ft.ThemeMode.DARK,
        "light": ft.ThemeMode.LIGHT,
    }.get(_saved, ft.ThemeMode.SYSTEM)
    page.padding = 0
    page.spacing = 0

    # Mobile-friendly: viewport meta, responsive layout
    page.theme = ft.Theme(
        color_scheme_seed=ft.Colors.BLUE_500,
        visual_density=ft.VisualDensity.COMPACT,
        page_transitions=ft.PageTransitionsTheme(
            android=ft.PageTransitionTheme.ZOOM,
            ios=ft.PageTransitionTheme.CUPERTINO,
        ),
    )

    # Responsive breakpoints for mobile
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.scroll = ft.ScrollMode.AUTO

    client_ref: list = []  # Mutable container for authenticated client

    def on_login_success(c) -> None:
        """Store the authenticated client and navigate to the dashboard."""
        client_ref.append(c)
        page.views.clear()
        page.views.append(build_dashboard_view(c, page))
        asyncio.create_task(page.push_route("/dashboard"))
        page.update()

    def on_login_error(msg: str) -> None:
        """Display a transient error snack-bar and surface the message to the user."""
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg),
            bgcolor=ft.Colors.ERROR_CONTAINER,
            open=True,
        )
        page.update()

    def route_change(e: ft.RouteChangeEvent) -> None:
        """
        Rebuild the view stack whenever the active route changes.

        Clears existing views and appends the view that matches the current
        route.  Unauthenticated access to protected routes redirects to ``/``.
        """
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
        elif troute.match("/liquidity"):
            if client_ref:
                page.views.append(build_institutional_liquidity_view(client_ref[0], page))
            else:
                asyncio.create_task(page.push_route("/"))
                return
        elif troute.match("/analysis"):
            if client_ref:
                page.views.append(build_analysis_view(client_ref[0], page))
            else:
                asyncio.create_task(page.push_route("/"))
                return
        elif troute.match("/settings"):
            if client_ref:
                page.views.append(build_settings_view(client_ref[0], page))
            else:
                asyncio.create_task(page.push_route("/"))
                return
        page.update()

    def view_pop(e: ft.ViewPopEvent) -> None:
        """Remove the top view and navigate back to the view below it."""
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
