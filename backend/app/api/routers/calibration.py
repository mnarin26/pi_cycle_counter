from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Machine

router = APIRouter()


class LearnBody(BaseModel):
    open_position_1d: float
    closed_position_1d: float
    threshold_min: int | None = None
    threshold_max: int | None = None
    confidence: float | None = None


class ReflectorLengthCalibBody(BaseModel):
    duration_s: float = 12.0
    min_samples: int = 30


def _percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    if len(arr) == 1:
        return float(arr[0])
    k = (len(arr) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(len(arr) - 1, lo + 1)
    frac = k - lo
    return float(arr[lo]) * (1.0 - frac) + float(arr[hi]) * frac


@router.post("/machines/{machine_id}/learn")
def learn_positions(machine_id: int, body: LearnBody, db: Session = Depends(get_db)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404)
    m.open_position_1d = body.open_position_1d
    m.closed_position_1d = body.closed_position_1d
    if body.threshold_min is not None:
        m.threshold_min = body.threshold_min
    if body.threshold_max is not None:
        m.threshold_max = body.threshold_max
    m.threshold_mode = "adaptive"
    m.learning_session = json.dumps(
        {
            "open_position_1d": body.open_position_1d,
            "closed_position_1d": body.closed_position_1d,
            "confidence": body.confidence or 0.0,
        }
    )
    db.commit()
    return {"ok": True, "machine_id": machine_id}


@router.post("/machines/{machine_id}/learn_reflector_length")
async def learn_reflector_length(
    machine_id: int,
    body: ReflectorLengthCalibBody,
    request: Request,
    db: Session = Depends(get_db),
):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404)

    duration_s = max(3.0, min(60.0, float(body.duration_s)))
    min_samples = max(10, min(1000, int(body.min_samples)))

    deadline = time.monotonic() + duration_s
    seg_lens: list[int] = []
    prominences: list[int] = []

    while time.monotonic() < deadline:
        snap = getattr(request.app.state.vision, "snapshot", None) or {}
        machine_rows = snap.get("machines", [])
        row = next((r for r in machine_rows if int(r.get("id", -1)) == machine_id), None)
        if row and row.get("position_01") is not None:
            seg = int(row.get("segment_len") or 0)
            prom = int(row.get("prominence") or 0)
            if seg > 0 and prom > 0:
                seg_lens.append(seg)
                prominences.append(prom)
        await asyncio.sleep(0.05)

    if len(seg_lens) < min_samples:
        raise HTTPException(
            400,
            f"Yetersiz ornek: {len(seg_lens)} < {min_samples}. Kalibrasyonda reflektoru cizgi boyunca gezdir.",
        )

    p10 = _percentile(seg_lens, 10.0)
    p90 = _percentile(seg_lens, 90.0)
    learned_min = max(1, int(round(p10 - 1.0)))
    learned_max = max(learned_min + 1, int(round(p90 + 1.0)))

    m.reflector_len_min = learned_min
    m.reflector_len_max = learned_max

    prior: dict = {}
    if m.learning_session:
        try:
            prior = json.loads(m.learning_session)
            if not isinstance(prior, dict):
                prior = {}
        except Exception:
            prior = {}
    prior.update(
        {
            "reflector_len_min": learned_min,
            "reflector_len_max": learned_max,
            "reflector_len_samples": len(seg_lens),
            "reflector_len_p10": p10,
            "reflector_len_p90": p90,
            "reflector_prom_median": _percentile(prominences, 50.0),
        }
    )
    m.learning_session = json.dumps(prior)
    db.commit()

    return {
        "ok": True,
        "machine_id": machine_id,
        "duration_s": duration_s,
        "sample_count": len(seg_lens),
        "reflector_len_min": learned_min,
        "reflector_len_max": learned_max,
        "p10": p10,
        "p90": p90,
    }


@router.get("/machines/{machine_id}/playback")
def playback_samples(machine_id: int, request: Request):
    buf = request.app.state.vision.playback
    return {"machine_id": machine_id, "samples": buf.get_machine(machine_id)}
