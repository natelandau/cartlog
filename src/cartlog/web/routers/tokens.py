"""Routes for per-user API token management: list, mint (plaintext shown once), revoke."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session  # noqa: TC002 - runtime import for FastAPI Depends

from cartlog.auth.tokens import ApiTokenService
from cartlog.db.models import User
from cartlog.web.auth import require_editor
from cartlog.web.dependencies import get_session
from cartlog.web.templating import templates

router = APIRouter()

# Annotated alias so every route declares the editor guard without repeating Depends().
RequireEditor = Annotated[User, Depends(require_editor)]


@router.get("/account/tokens", response_class=HTMLResponse)
def get_tokens_panel(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    current_user: RequireEditor,
) -> HTMLResponse:
    """Render the API tokens panel listing the current user's tokens.

    Shows metadata only (name, created, last used, revoked state); the one-time
    plaintext is never re-displayed after the initial mint response.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        current_user: The authenticated Editor or Admin (enforced by RequireEditor).

    Returns:
        Rendered HTML fragment for the tokens panel.
    """
    tokens = ApiTokenService(session).list_for_user(current_user.id)
    return templates.TemplateResponse(
        request,
        "partials/_tokens_panel.html",
        {
            "tokens": tokens,
            "new_token_plaintext": None,
            "current_user": current_user,
        },
    )


@router.post("/account/tokens", response_class=HTMLResponse)
def mint_token(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    current_user: RequireEditor,
    name: Annotated[str, Form()],
) -> HTMLResponse:
    """Mint a new API token for the current user and return the plaintext once.

    The plaintext is only shown in this response; it cannot be retrieved again.
    The caller should copy it before leaving the page.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        current_user: The authenticated Editor or Admin (enforced by RequireEditor).
        name: A human-readable label for the token (e.g. "iphone").

    Returns:
        Rendered HTML fragment showing the new plaintext and the updated token list.
    """
    svc = ApiTokenService(session)
    _row, plaintext = svc.mint(current_user, name)
    session.commit()
    # Re-load so the new row appears in the list (commit flushes the id).
    tokens = svc.list_for_user(current_user.id)
    return templates.TemplateResponse(
        request,
        "partials/_tokens_panel.html",
        {
            "tokens": tokens,
            "new_token_plaintext": plaintext,
            "current_user": current_user,
        },
        status_code=201,
    )


@router.post("/account/tokens/{token_id}/revoke", response_class=HTMLResponse)
def revoke_token(
    token_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    current_user: RequireEditor,
) -> HTMLResponse:
    """Revoke one of the current user's tokens.

    Ownership is enforced inside ApiTokenService.revoke so one user can never
    soft-delete another user's token. Already-revoked tokens are ignored silently.

    Args:
        token_id: The primary key of the ApiToken row to revoke.
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        current_user: The authenticated Editor or Admin (enforced by RequireEditor).

    Returns:
        Rendered HTML fragment with the updated token list.
    """
    svc = ApiTokenService(session)
    svc.revoke(token_id=token_id, user_id=current_user.id)
    session.commit()
    tokens = svc.list_for_user(current_user.id)
    return templates.TemplateResponse(
        request,
        "partials/_tokens_panel.html",
        {
            "tokens": tokens,
            "new_token_plaintext": None,
            "current_user": current_user,
        },
    )
