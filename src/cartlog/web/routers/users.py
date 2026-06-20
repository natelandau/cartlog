"""Admin user management routes for creating, updating, and resetting user accounts."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session  # noqa: TC002 - runtime import for FastAPI Depends

from cartlog.auth.passwords import validate_password
from cartlog.auth.sessions import SessionService
from cartlog.auth.users import UserService
from cartlog.db.models import Role, User
from cartlog.web.auth import require_admin
from cartlog.web.dependencies import get_session
from cartlog.web.templating import templates

router = APIRouter()

# Annotated alias so every route can declare require_admin without repeating Depends().
RequireAdmin = Annotated[User, Depends(require_admin)]


def _active_admin_count(session: Session) -> int:
    """Return the number of currently active admins."""
    return (
        session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.ADMIN, User.is_active.is_(True))
        )
        or 0
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _admin: RequireAdmin,
) -> HTMLResponse:
    """Render the user management page listing all users with controls for each."""
    users = UserService(session).list_users()
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {"users": users, "roles": list(Role)},
    )


@router.post("/admin/users", response_class=HTMLResponse)
def admin_create_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _admin: RequireAdmin,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[str, Form()],
) -> HTMLResponse:
    """Create a new user account and re-render the users table fragment.

    Validates username uniqueness and the password policy before persisting so
    the admin gets inline feedback rather than a server error.
    """
    svc = UserService(session)

    if svc.get_by_username(username) is not None:
        users = svc.list_users()
        return templates.TemplateResponse(
            request,
            "partials/_users_table.html",
            {
                "users": users,
                "roles": list(Role),
                "error": f'Username "{username}" is already taken.',
            },
            status_code=422,
        )

    policy_error = validate_password(password)
    if policy_error:
        users = svc.list_users()
        return templates.TemplateResponse(
            request,
            "partials/_users_table.html",
            {"users": users, "roles": list(Role), "error": policy_error},
            status_code=422,
        )

    try:
        parsed_role = Role(role.lower())
    except ValueError:
        users = svc.list_users()
        return templates.TemplateResponse(
            request,
            "partials/_users_table.html",
            {"users": users, "roles": list(Role), "error": f'Unknown role "{role}".'},
            status_code=422,
        )

    svc.create_user(username, password, parsed_role)
    session.commit()

    users = svc.list_users()
    return templates.TemplateResponse(
        request,
        "partials/_users_table.html",
        {"users": users, "roles": list(Role), "error": None},
    )


@router.post("/admin/users/{user_id}/role", response_class=HTMLResponse)
def admin_set_role(
    user_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _admin: RequireAdmin,
    role: Annotated[str, Form()],
) -> HTMLResponse:
    """Change a user's role, blocking the change if it would remove the last active admin."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        new_role = Role(role.lower())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f'Unknown role "{role}"') from exc

    # Guard: do not allow demoting the last active admin.
    if user.role == Role.ADMIN and new_role != Role.ADMIN and _active_admin_count(session) <= 1:
        users = UserService(session).list_users()
        return templates.TemplateResponse(
            request,
            "partials/_users_table.html",
            {
                "users": users,
                "roles": list(Role),
                "error": "Cannot demote the last active admin. Promote another user to Admin first.",
            },
            status_code=422,
        )

    UserService(session).set_role(user, new_role)
    session.commit()

    users = UserService(session).list_users()
    return templates.TemplateResponse(
        request,
        "partials/_users_table.html",
        {"users": users, "roles": list(Role), "error": None},
    )


@router.post("/admin/users/{user_id}/active", response_class=HTMLResponse)
def admin_set_active(
    user_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _admin: RequireAdmin,
    active: Annotated[str, Form()],
) -> HTMLResponse:
    """Activate or deactivate a user, blocking deactivation of the last active admin."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    is_active = active.lower() not in ("false", "0", "no", "off")

    # Guard: do not allow deactivating the last active admin.
    if user.role == Role.ADMIN and not is_active and _active_admin_count(session) <= 1:
        users = UserService(session).list_users()
        return templates.TemplateResponse(
            request,
            "partials/_users_table.html",
            {
                "users": users,
                "roles": list(Role),
                "error": "Cannot deactivate the last active admin. Promote another user to Admin first.",
            },
            status_code=422,
        )

    svc = UserService(session)
    svc.set_active(user, active=is_active)
    # Revoke sessions when deactivating so the user is immediately signed out.
    if not is_active:
        SessionService(session).revoke_all_for_user(user_id)
    session.commit()

    users = svc.list_users()
    return templates.TemplateResponse(
        request,
        "partials/_users_table.html",
        {"users": users, "roles": list(Role), "error": None},
    )


@router.post("/admin/users/{user_id}/reset-password", response_class=HTMLResponse)
def admin_reset_password(
    user_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _admin: RequireAdmin,
) -> HTMLResponse:
    """Generate a temporary password, force a change on next login, and revoke all sessions.

    The temp password is shown once in the response so the admin can relay it;
    it is never stored in plaintext and cannot be retrieved again.
    """
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # token_urlsafe(9) produces a 12-character URL-safe string; long enough for policy,
    # short enough to copy-paste without frustration.
    temp_password = secrets.token_urlsafe(9)
    UserService(session).set_password(user, temp_password, must_change=True)
    SessionService(session).revoke_all_for_user(user_id)
    session.commit()

    return templates.TemplateResponse(
        request,
        "partials/_users_temp_password.html",
        {"user": user, "temp_password": temp_password},
    )
