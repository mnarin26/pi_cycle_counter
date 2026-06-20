from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import AppSetting
from app.services.reset_production import wipe_production_history
from app.services.stored_settings import (
    add_operator,
    get_section,
    patch_section,
    remove_operator,
    ssh_connection_string,
    ssh_public_view,
    telegram_public_view,
)

router = APIRouter()


class TelegramSettingsPatch(BaseModel):
    enabled: bool | None = None
    bot_username: str | None = None
    bot_token: str | None = Field(default=None, description="Empty or omitted keeps existing token")


class TelegramOperatorAdd(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    telegram_user_id: str = Field(..., min_length=1, max_length=32)
    level: int = Field(default=2, ge=1, le=2)


class SshSettingsPatch(BaseModel):
    host: str | None = None
    user: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    auth_method: str | None = None
    key_path: str | None = None
    alias: str | None = None


class SettingKV(BaseModel):
    key: str
    value: dict


@router.get("/telegram")
def get_telegram_settings(db: Session = Depends(get_db)):
    raw = get_section(db, "telegram")
    return telegram_public_view(raw)


@router.patch("/telegram")
def patch_telegram_settings(body: TelegramSettingsPatch, db: Session = Depends(get_db)):
    patch = body.model_dump(exclude_unset=True)
    token = patch.pop("bot_token", None)
    if token is not None:
        token = token.strip()
        if token and token != "__UNCHANGED__":
            patch["bot_token"] = token
    patch_section(db, "telegram", patch)
    return telegram_public_view(get_section(db, "telegram"))


@router.post("/telegram/operators")
def add_telegram_operator(body: TelegramOperatorAdd, db: Session = Depends(get_db)):
    try:
        return add_operator(db, name=body.name, telegram_user_id=body.telegram_user_id, level=body.level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/telegram/operators/{user_id}")
def delete_telegram_operator(user_id: str, db: Session = Depends(get_db)):
    return remove_operator(db, user_id)


@router.get("/ssh")
def get_ssh_settings(db: Session = Depends(get_db)):
    raw = get_section(db, "ssh")
    view = ssh_public_view(raw)
    view["connection_string"] = ssh_connection_string(raw)
    return view


@router.patch("/ssh")
def patch_ssh_settings(body: SshSettingsPatch, db: Session = Depends(get_db)):
    patch = body.model_dump(exclude_unset=True)
    if "auth_method" in patch and patch["auth_method"] not in ("key", "password"):
        raise HTTPException(400, detail="auth_method must be key or password")
    raw = patch_section(db, "ssh", patch)
    view = ssh_public_view(raw)
    view["connection_string"] = ssh_connection_string(raw)
    return view


@router.post("/maintenance/reset-production-data")
def reset_production_data(db: Session = Depends(get_db)):
    """
    Clears runtime production history while keeping machine/camera configuration.
    """
    stats = wipe_production_history(db)
    return {"ok": True, **stats}


@router.get("/{key}")
def get_setting(key: str, db: Session = Depends(get_db)):
    row = db.get(AppSetting, key)
    if not row:
        return {"key": key, "value": {}}
    return {"key": key, "value": json.loads(row.value_json)}


@router.patch("/{key}")
def patch_setting(key: str, body: dict = Body(...), db: Session = Depends(get_db)):
    row = db.get(AppSetting, key)
    if not row:
        row = AppSetting(key=key, value_json="{}")
        db.add(row)
        db.flush()
    cur = json.loads(row.value_json)
    cur.update(body)
    row.value_json = json.dumps(cur)
    db.commit()
    return {"key": key, "value": cur}
