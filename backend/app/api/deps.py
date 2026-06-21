from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.services.auth_service import CurrentUser, get_session_user

__all__ = [
    "get_db",
    "get_current_user",
    "require_panel_8000",
    "require_panel_8080",
    "require_super_or_admin",
    "client_ip",
]


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = request.cookies.get(settings.session_cookie_name)
    user = get_session_user(db, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Oturum gerekli")
    return user


def require_panel_8000(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not (user.is_super or user.has("panel_8000")):
        raise HTTPException(status_code=403, detail="8000 paneli icin yetkiniz yok")
    return user


def require_panel_8080(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not (user.is_super or user.has("panel_8080")):
        raise HTTPException(status_code=403, detail="8080 paneli icin yetkiniz yok")
    return user


def require_super_or_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    # Admin operators have all panel permissions; super is unrestricted.
    if user.is_super or (user.has("panel_8080") and user.has("bot_mold_create")):
        return user
    raise HTTPException(status_code=403, detail="Bu islem icin yonetici yetkisi gerekli")
