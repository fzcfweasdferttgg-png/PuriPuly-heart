"""Compat shim left after the Discord OAuth loopback listener was removed.

Only the callback completion page renderer used by the UI stays; the local
HTTP listener and the OAuth state machine are gone.
"""

from __future__ import annotations

from puripuly_heart.core.discord.oauth_callback_page import render_oauth_callback_completion_page


def render_discord_oauth_callback_completion_page(locale: str | None = None) -> bytes:
    return render_oauth_callback_completion_page(locale)
