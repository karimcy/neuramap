"""Accounts: Google sign-in (OAuth via Authlib) + per-visitor workspace isolation.

Two layers, so project scoping works in EVERY deployment posture:

- **Google OAuth** activates when ``GRIDQ_GOOGLE_CLIENT_ID`` / ``GRIDQ_GOOGLE_CLIENT_SECRET``
  are set (create a Web application OAuth client in Google Cloud Console; authorised redirect
  URI = ``<public base>/auth/callback``). Authlib handles discovery/state/nonce - the
  security-critical parts are never hand-rolled (OSS evaluation: docs/redteam/OSS_EVALUATIONS.md
  §6). The signed-in identity is Google's stable ``sub`` claim.
- **Anonymous workspace fallback** (always on): without Google credentials - or before a visitor
  signs in - each browser gets a random workspace id in its signed session cookie, and runs are
  scoped to that workspace. This is the closest honest per-user isolation available with no
  external identity provider: private per browser profile, but NOT portable across devices and
  lost with cleared cookies (the run store itself keeps the data; sign-in later does not
  auto-claim old anonymous runs). The UI states this constraint; it is not hidden.

Sessions are itsdangerous-signed cookies (Starlette ``SessionMiddleware``). The signing secret
comes from ``GRIDQ_SESSION_SECRET``; when unset a random per-boot secret is used and every
session (sign-ins and anonymous workspaces alike) resets on restart - fine for a demo box,
stated in deploy/.env.example for production.

Ownership on disk is one small ``owner.json`` per run (see ``store.py``): runs saved before this
feature (no owner file) are "legacy/shared" and stay visible to everyone, deliberately - hiding
previously-visible data behind a new feature would look like data loss on a shared demo box.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

#: Session keys. ``user`` = the signed-in Google identity; ``ws`` = the anonymous workspace id.
_USER_KEY = "user"
_WS_KEY = "ws"


def google_configured() -> bool:
    """Whether Google OAuth credentials are present in the environment."""
    return bool(
        os.environ.get("GRIDQ_GOOGLE_CLIENT_ID") and os.environ.get("GRIDQ_GOOGLE_CLIENT_SECRET")
    )


def session_secret() -> str:
    """The cookie-signing secret: env-pinned in production, random per boot otherwise."""
    return os.environ.get("GRIDQ_SESSION_SECRET") or secrets.token_hex(32)


def current_user(request: Request) -> dict[str, Any]:
    """The visitor's identity for scoping and display. Never raises.

    Returns ``{"id", "email", "name", "picture", "signed_in", "google_available"}``.
    ``id`` is the ownership key: Google's ``sub`` (prefixed ``google:``) when signed in, else
    the anonymous workspace id (prefixed ``anon:``, created lazily on first need).
    """
    sess = request.session
    user = sess.get(_USER_KEY)
    if isinstance(user, dict) and user.get("sub"):
        return {
            "id": f"google:{user['sub']}",
            "email": user.get("email"),
            "name": user.get("name") or user.get("email"),
            "picture": user.get("picture"),
            "signed_in": True,
            "google_available": google_configured(),
        }
    ws = sess.get(_WS_KEY)
    if not isinstance(ws, str) or not ws:
        ws = secrets.token_hex(16)
        sess[_WS_KEY] = ws
    return {
        "id": f"anon:{ws}",
        "email": None,
        "name": None,
        "picture": None,
        "signed_in": False,
        "google_available": google_configured(),
    }


def visible_to(user_id: str, owner_id: str | None) -> bool:
    """The scoping rule: your own runs, plus legacy/shared runs with no recorded owner."""
    return owner_id is None or owner_id == user_id


def install(app: FastAPI, render: Any) -> None:
    """Wire the session middleware and the /auth routes onto the app.

    ``render`` is the app factory's template renderer (a closure over the Jinja environment) —
    passed in rather than imported, because it does not exist at module level.
    """
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(),
        session_cookie="gridq_session",
        same_site="lax",
        https_only=False,  # the public URL terminates TLS at the tunnel; cookie stays lax-signed
    )

    oauth = None
    if google_configured():
        from authlib.integrations.starlette_client import OAuth

        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=os.environ["GRIDQ_GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GRIDQ_GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    @app.get("/auth/login")
    async def auth_login(request: Request) -> Response:
        """Start the Google sign-in, or explain plainly why it is unavailable."""
        if oauth is None:
            # The honest constraint page (mission rule: state it, never fake it).
            return render(request, "auth_unavailable.html", {"nav_active": None}, status=503)
        # The redirect URI must be the PUBLIC base (the app sits behind a tunnel/proxy).
        base = os.environ.get("GRIDQ_OAUTH_REDIRECT_BASE") or str(request.base_url).rstrip("/")
        redirect_uri = f"{base}/auth/callback"
        return await oauth.google.authorize_redirect(request, redirect_uri)  # type: ignore[no-any-return]

    @app.get("/auth/callback")
    async def auth_callback(request: Request) -> Response:
        if oauth is None:
            return RedirectResponse("/auth/login", status_code=303)
        try:
            token = await oauth.google.authorize_access_token(request)
        except Exception:  # noqa: BLE001 — a failed/denied OAuth dance goes back to sign-in, never a 500
            return RedirectResponse("/auth/login", status_code=303)
        info = token.get("userinfo") or {}
        if not info.get("sub"):
            return RedirectResponse("/auth/login", status_code=303)
        request.session[_USER_KEY] = {
            "sub": str(info["sub"]),
            "email": info.get("email"),
            "name": info.get("name"),
            "picture": info.get("picture"),
        }
        return RedirectResponse("/projects", status_code=303)

    @app.get("/auth/logout")
    async def auth_logout(request: Request) -> Response:
        request.session.pop(_USER_KEY, None)
        return RedirectResponse("/", status_code=303)
