"""Login view with OAuth and username/password options."""

import flet as ft

import api.connection_status as cs
from config import (
    TASTYTRADE_API_BASE,
    TASTYTRADE_CLIENT_ID,
    TASTYTRADE_CLIENT_SECRET,
    TASTYTRADE_REFRESH_TOKEN,
)


def build_login_view(on_success, on_error) -> ft.View:
    """Build the login view with OAuth and optional username/password."""

    error_text = ft.Text("", color=ft.Colors.ERROR, visible=False)

    def show_error(msg: str) -> None:
        error_text.value = msg
        error_text.visible = True
        error_text.update()

    def report_error(msg: str) -> None:
        show_error(msg)
        on_error(msg)

    def use_oauth(e: ft.ControlEvent) -> None:
        if not all([TASTYTRADE_CLIENT_ID, TASTYTRADE_CLIENT_SECRET, TASTYTRADE_REFRESH_TOKEN]):
            report_error("Set TASTYTRADE_CLIENT_ID, TASTYTRADE_CLIENT_SECRET, and TASTYTRADE_REFRESH_TOKEN in .env")
            return
        try:
            from api.tastytrade_client import TastytradeClient

            client = TastytradeClient(
                TASTYTRADE_API_BASE,
                client_id=TASTYTRADE_CLIENT_ID,
                client_secret=TASTYTRADE_CLIENT_SECRET,
                refresh_token=TASTYTRADE_REFRESH_TOKEN,
            )
            client._ensure_token()
            cs.set_status(cs.ConnState.LIVE, "OAuth authenticated")
            on_success(client)
        except Exception as ex:
            cs.set_status(cs.ConnState.OFFLINE, f"OAuth failed: {ex}")
            report_error(str(ex))

    def do_password_login(e: ft.ControlEvent) -> None:
        login_val = login_field.value
        pwd_val = password_field.value
        if not login_val or not pwd_val:
            report_error("Enter username and password")
            return
        try:
            from api.tastytrade_client import TastytradeClient

            client = TastytradeClient(TASTYTRADE_API_BASE)
            client.login(login_val, pwd_val)
            cs.set_status(cs.ConnState.LIVE, "Authenticated")
            on_success(client)
        except Exception as ex:
            cs.set_status(cs.ConnState.OFFLINE, f"Login failed: {ex}")
            report_error(str(ex))

    login_field = ft.TextField(
        label="Username or email",
        keyboard_type=ft.KeyboardType.EMAIL,
        autofill_hints=[ft.AutofillHint.EMAIL],
        expand=True,
    )
    password_field = ft.TextField(
        label="Password",
        password=True,
        can_reveal_password=True,
        autofill_hints=[ft.AutofillHint.PASSWORD],
        on_submit=do_password_login,
        expand=True,
    )

    return ft.View(
        route="/",
        controls=[
            ft.SafeArea(
                content=ft.Column(
                    [
                        ft.Text("MTrade", size=32, weight=ft.FontWeight.BOLD),
                        ft.Text(
                            "Connect to tastytrade (sandbox)",
                            size=14,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Divider(),
                        ft.Text("Sign in", size=18, weight=ft.FontWeight.W_500),
                        login_field,
                        password_field,
                        ft.ElevatedButton(
                            "Sign in with username/password",
                            on_click=do_password_login,
                            style=ft.ButtonStyle(
                                padding=ft.padding.symmetric(16, 24),
                            ),
                        ),
                        ft.Container(height=8),
                        ft.ElevatedButton(
                            "Sign in with OAuth (uses .env credentials)",
                            on_click=use_oauth,
                            style=ft.ButtonStyle(
                                bgcolor=ft.Colors.SECONDARY_CONTAINER,
                                color=ft.Colors.ON_SECONDARY_CONTAINER,
                                padding=ft.padding.symmetric(16, 24),
                            ),
                        ),
                        error_text,
                    ],
                    spacing=12,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                expand=True,
            )
        ],
        padding=16,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
