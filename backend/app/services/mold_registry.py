"""Mold lookup, creation, and machine assignment (Telegram / QR)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Event, Machine, Mold, json_dumps
from app.services.mold_matcher import link_mold_machine
from app.services.qr_codec import QrKind, QrPayload, parse_qr_text


def resolve_machine(db: Session, payload: QrPayload) -> Machine:
    if payload.kind != QrKind.MACHINE:
        raise ValueError("Bu QR makine kodu degil")
    code = payload.code.strip()
    if code.isdigit():
        m = db.get(Machine, int(code))
        if m:
            return m
    row = db.query(Machine).filter(Machine.qr_code == code).first()
    if row:
        return row
    row = db.query(Machine).filter(Machine.name.ilike(code)).first()
    if row:
        return row
    raise ValueError(f"Makine bulunamadi: {code}")


def resolve_mold(db: Session, payload: QrPayload) -> Mold:
    if payload.kind != QrKind.MOLD:
        raise ValueError("Bu QR kalip kodu degil")
    code = payload.code.strip()
    row = db.query(Mold).filter(Mold.qr_code == code).first()
    if row:
        return row
    if code.isdigit():
        row = db.get(Mold, int(code))
        if row:
            return row
    raise ValueError(f"Kalip bulunamadi: {code}")


def find_mold_by_qr_code(db: Session, code: str) -> Mold | None:
    return db.query(Mold).filter(Mold.qr_code == code.strip()).first()


def assign_mold_to_machine(
    db: Session,
    *,
    machine_id: int,
    mold_id: int,
    source: str = "telegram",
    operator_name: str | None = None,
    operator_id: str | None = None,
) -> tuple[Machine, Mold]:
    machine = db.get(Machine, machine_id)
    mold = db.get(Mold, mold_id)
    if not machine:
        raise ValueError("Makine bulunamadi")
    if not mold:
        raise ValueError("Kalip bulunamadi")
    machine.current_mold_id = mold.id
    link_mold_machine(db, mold.id, machine.id)
    db.add(
        Event(
            type="mold_assigned",
            machine_id=machine.id,
            payload=json_dumps(
                {
                    "source": source,
                    "mold_id": mold.id,
                    "mold_name": mold.name,
                    "mold_qr_code": mold.qr_code,
                    "operator_name": operator_name,
                    "operator_id": operator_id,
                }
            ),
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.refresh(machine)
    db.refresh(mold)
    return machine, mold


def create_mold_from_qr(
    db: Session,
    *,
    qr_code: str,
    name: str,
    source: str = "telegram",
    operator_name: str | None = None,
) -> Mold:
    code = qr_code.strip()
    if not code:
        raise ValueError("Kalip kodu bos")
    if find_mold_by_qr_code(db, code):
        raise ValueError("Bu QR kodu zaten kayitli")
    nm = name.strip()
    if not nm:
        raise ValueError("Kalip adi bos")
    mold = Mold(
        qr_code=code,
        name=nm,
        status="active",
        avg_cycle_s=0.0,
        tolerance_s=0.35,
        sample_count=0,
        confidence=0.0,
    )
    db.add(mold)
    db.flush()
    db.add(
        Event(
            type="mold_created",
            machine_id=None,
            payload=json_dumps(
                {
                    "source": source,
                    "mold_id": mold.id,
                    "mold_name": nm,
                    "mold_qr_code": code,
                    "operator_name": operator_name,
                }
            ),
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.refresh(mold)
    return mold


def parse_and_resolve_machine(db: Session, raw_qr: str) -> Machine:
    return resolve_machine(db, parse_qr_text(raw_qr))


def parse_and_resolve_mold(db: Session, raw_qr: str) -> Mold:
    return resolve_mold(db, parse_qr_text(raw_qr))
