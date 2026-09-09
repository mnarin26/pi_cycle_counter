from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.config import settings
from app.db.session import get_db
from app.services import auth_service, audit_log
from app.services.auth_service import CurrentUser
from app.services.network_scope import direct_client_ip

router = APIRouter()


class LoginBody(BaseModel):
    username: str = ""
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=int(settings.session_max_hours * 3600),
        path="/",
    )


@router.get("/login-mode")
def login_mode(request: Request):
    return auth_service.login_hint(direct_client_ip(request))


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    scope_ip = direct_client_ip(request)
    if auth_service.is_locked_out(ip):
        raise HTTPException(status_code=429, detail="Cok fazla hatali deneme. 15 dakika sonra tekrar deneyin.")

    result = auth_service.login(db, body.password, client_ip=scope_ip, username=body.username)
    if result is None:
        auth_service.register_failure(ip)
        raise HTTPException(status_code=401, detail="Sifre hatali")

    token, user = result
    auth_service.clear_failures(ip)
    _set_session_cookie(response, token)
    audit_log.log_action(
        db,
        actor_type=user.actor_type,
        action="auth.login",
        actor_name=user.display_name,
        telegram_user_id=user.telegram_user_id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "actor_type": user.actor_type,
        "display_name": user.display_name,
        "permissions": user.permissions,
        "is_super": user.is_super,
    }


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(settings.session_cookie_name)
    user = auth_service.get_session_user(db, token)
    auth_service.logout(db, token)
    response.delete_cookie(settings.session_cookie_name, path="/")
    if user is not None:
        audit_log.log_action(
            db,
            actor_type=user.actor_type,
            action="auth.logout",
            actor_name=user.display_name,
            telegram_user_id=user.telegram_user_id,
            ip=client_ip(request),
        )
    return {"ok": True}


@router.post("/change-password")
def change_password(
    body: ChangePasswordBody,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        auth_service.change_own_password(db, user, body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "actor_type": user.actor_type,
        "display_name": user.display_name,
        "permissions": user.permissions,
        "is_super": user.is_super,
    }
