"""Mold lookup, creation, and machine assignment (Telegram / QR)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def _standard_changeover_seconds(prev: Mold | None, new: Mold | None) -> float:
    """(prev removal + new mount) minutes -> seconds; skips removal when mold unchanged."""
    seconds = 0.0
    if prev and new and prev.id != new.id and prev.removal_minutes:
        seconds += float(prev.removal_minutes) * 60.0
    if new and new.mount_minutes:
        seconds += float(new.mount_minutes) * 60.0
    return seconds


def assign_mold_to_machine(
    db: Session,
    *,
    machine_id: int,
    mold_id: int,
    source: str = "telegram",
    operator_name: str | None = None,
    operator_id: str | None = None,
    assigned_at: datetime | None = None,
    changeover_start: datetime | None = None,
    changeover_end: datetime | None = None,
) -> tuple[Machine, Mold]:
    machine = db.get(Machine, machine_id)
    mold = db.get(Mold, mold_id)
    if not machine:
        raise ValueError("Makine bulunamadi")
    if not mold:
        raise ValueError("Kalip bulunamadi")

    when = assigned_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if when > now + timedelta(minutes=5):
        raise ValueError("Atama saati gelecekte olamaz")
    if when < now - timedelta(days=14):
        raise ValueError("Atama saati en fazla 14 gun geriye alinabilir")

    prev_mold = machine.current_mold_id and db.get(Mold, machine.current_mold_id) or None

    # Changeover window: explicit if provided, else standard removal+mount ending at assignment.
    co_start: datetime | None = None
    co_end: datetime | None = None
    if changeover_start and changeover_end:
        cs = changeover_start if changeover_start.tzinfo else changeover_start.replace(tzinfo=timezone.utc)
        ce = changeover_end if changeover_end.tzinfo else changeover_end.replace(tzinfo=timezone.utc)
        if ce <= cs:
            raise ValueError("Kalip degisimi bitisi baslangictan sonra olmali")
        co_start, co_end = cs, ce
    else:
        std = _standard_changeover_seconds(prev_mold, mold)
        if std > 0:
            co_end = when
            co_start = when - timedelta(seconds=std)

    # Bir kalip ayni anda yalnizca bir makinede calisabilir.
    db.query(Machine).filter(Machine.current_mold_id == mold.id, Machine.id != machine.id).update(
        {Machine.current_mold_id: None},
        synchronize_session=False,
    )
    machine.current_mold_id = mold.id
    link_mold_machine(db, mold.id, machine.id)
    payload: dict = {
        "source": source,
        "mold_id": mold.id,
        "mold_name": mold.name,
        "mold_qr_code": mold.qr_code,
        "operator_name": operator_name,
        "operator_id": operator_id,
    }
    if co_start and co_end:
        payload["changeover_start"] = co_start.astimezone(timezone.utc).isoformat()
        payload["changeover_end"] = co_end.astimezone(timezone.utc).isoformat()
    db.add(
        Event(
            type="mold_assigned",
            machine_id=machine.id,
            payload=json_dumps(payload),
            created_at=when,
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
    target_cycle_s: float | None = None,
    daily_target_count: int | None = None,
    work_mode: str = "auto",
    mount_minutes: int | None = None,
    removal_minutes: int | None = None,
    tolerance_s: float = 0.35,
) -> Mold:
    code = qr_code.strip()
    if not code:
        raise ValueError("Kalip kodu bos")
    if find_mold_by_qr_code(db, code):
        raise ValueError("Bu QR kodu zaten kayitli")
    nm = name.strip()
    if not nm:
        raise ValueError("Kalip adi bos")
    target = float(target_cycle_s) if target_cycle_s and target_cycle_s > 0 else None
    daily_target = int(daily_target_count) if daily_target_count and daily_target_count > 0 else None
    tol = float(tolerance_s) if tolerance_s and tolerance_s > 0 else 0.35
    mode = "manual" if str(work_mode).strip().lower() == "manual" else "auto"
    mount_m = int(mount_minutes) if mount_minutes and mount_minutes > 0 else None
    removal_m = int(removal_minutes) if removal_minutes and removal_minutes > 0 else None
    mold = Mold(
        qr_code=code,
        name=nm,
        status="active",
        avg_cycle_s=target or 0.0,
        target_cycle_s=target,
        daily_target_count=daily_target,
        work_mode=mode,
        mount_minutes=mount_m,
        removal_minutes=removal_m,
        tolerance_s=tol,
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
