from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import AppSetting

router = APIRouter()


class SettingKV(BaseModel):
    key: str
    value: dict


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
