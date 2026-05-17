from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

import cv2

from app.api.deps import get_db
from app.db.models import Camera

router = APIRouter()


class CameraOut(BaseModel):
    id: int
    name: str
    rtsp_url: str
    target_width: int
    target_fps: int
    enabled: bool
    status: str

    class Config:
        from_attributes = True


class CameraUpdate(BaseModel):
    name: str | None = None
    rtsp_url: str | None = None
    target_width: int | None = None
    target_fps: int | None = None
    enabled: bool | None = None


@router.get("", response_model=list[CameraOut])
def list_cameras(db: Session = Depends(get_db)):
    return db.query(Camera).order_by(Camera.id).all()


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, body: CameraUpdate, db: Session = Depends(get_db)):
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


@router.post("/{camera_id}/test")
def test_camera(camera_id: int, db: Session = Depends(get_db)):
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404)
    return {"camera_id": camera_id, "rtsp_configured": bool(c.rtsp_url.strip())}


@router.get("/{camera_id}/snapshot.jpg")
def snapshot_jpg(camera_id: int, request: Request):
    orch = request.app.state.vision
    w = orch.workers.get(camera_id)
    if not w:
        raise HTTPException(404, "camera worker not running")
    frame = w.read_latest()
    if frame is None:
        raise HTTPException(503, "no frame")
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    if not ok:
        raise HTTPException(500, "encode failed")
    age_ms = w.latest_age_ms() if hasattr(w, "latest_age_ms") else -1.0
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Frame-Age-Ms": f"{age_ms:.0f}",
    }
    return Response(content=buf.tobytes(), media_type="image/jpeg", headers=headers)
