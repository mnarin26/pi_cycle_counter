"""Shared in-memory DB fixtures for planning tests (no FastAPI, no camera)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Camera,
    Cycle,
    Machine,
    MachineDowntime,
    Mold,
    MoldMachineCapability,
    MoldRun,
    OrderLine,
    Product,
    StockMovement,
)

T0 = datetime(2026, 9, 10, 9, 6, tzinfo=timezone.utc)


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False)()


def _machine(db: Session, cam: Camera, name: str, slot: int, enabled: bool = True) -> Machine:
    m = Machine(camera_id=cam.id, name=name, slot_index=slot, enabled=enabled)
    db.add(m)
    db.flush()
    return m


def _run(
    db: Session,
    mold: Mold,
    machine: Machine,
    start: datetime,
    hours: float,
    *,
    cycle_s: float,
    cavity: int,
    cycles_every: int = 1,
) -> MoldRun:
    end = start + timedelta(hours=hours)
    presses = int(hours * 3600 / cycle_s)
    run = MoldRun(
        mold_id=mold.id,
        machine_id=machine.id,
        started_at=start,
        ended_at=end,
        end_reason="mold_change",
        camera_cycles=presses,
        active_cavity_snapshot=cavity,
        product_added=presses * cavity,
    )
    db.add(run)
    t = start
    n = 0
    while t + timedelta(seconds=cycle_s) <= end:
        db.add(
            Cycle(
                machine_id=machine.id,
                mold_id=mold.id,
                cycle_time_s=cycle_s,
                t_start=t,
                t_end=t + timedelta(seconds=cycle_s),
                is_counted=True,
            )
        )
        t += timedelta(seconds=cycle_s * cycles_every)
        n += 1
    return run


def seed_factory(db: Session, now: datetime) -> dict:
    """Small plant: 3 machines, 3 products, 4 molds (product A has two molds)."""
    cam = Camera(name="Cam", rtsp_url="")
    db.add(cam)
    db.flush()
    m1 = _machine(db, cam, "M1", 1)
    m2 = _machine(db, cam, "M2", 2)
    m3 = _machine(db, cam, "M3", 3)
    _machine(db, cam, "M4-kapalı", 4, enabled=False)

    pa = Product(name="5723410100")
    pb = Product(name="4306080600")
    pc = Product(name="6101811700 NEM SENSORLU MENTESE KAP. GR. KK ARCP1")
    db.add_all([pa, pb, pc])
    db.flush()

    ka1 = Mold(name="K-A1", product_id=pa.id, status="active", cavity_count=4, target_cycle_s=20.0,
               mount_minutes=30, removal_minutes=15, work_mode="auto")
    ka2 = Mold(name="K-A2", product_id=pa.id, status="active", cavity_count=2, target_cycle_s=25.0,
               mount_minutes=40, removal_minutes=20, work_mode="manual")
    kb = Mold(name="K-B", product_id=pb.id, status="active", cavity_count=1, target_cycle_s=40.0,
              mount_minutes=20, removal_minutes=10, work_mode="manual")
    kc = Mold(name="K-C", product_id=pc.id, status="active", cavity_count=2, target_cycle_s=30.0,
              mount_minutes=25, removal_minutes=25, work_mode="auto")
    db.add_all([ka1, ka2, kb, kc])
    db.flush()

    db.add_all(
        [
            MoldMachineCapability(mold_id=ka1.id, machine_id=m1.id, allowed=True, priority=1),
            MoldMachineCapability(mold_id=ka1.id, machine_id=m2.id, allowed=True, priority=2),
            MoldMachineCapability(mold_id=ka2.id, machine_id=m2.id, allowed=True, priority=1),
            MoldMachineCapability(mold_id=ka2.id, machine_id=m3.id, allowed=True, priority=2),
            MoldMachineCapability(mold_id=kb.id, machine_id=m3.id, allowed=True, priority=1),
            MoldMachineCapability(mold_id=kc.id, machine_id=m1.id, allowed=True, priority=2),
            MoldMachineCapability(mold_id=kc.id, machine_id=m2.id, allowed=True, priority=1),
        ]
    )

    # History before `now`: K-A1 good on M1, K-A2 slower on M2, K-B on M3, K-C on M2.
    _run(db, ka1, m1, now - timedelta(days=10), 2, cycle_s=21.0, cavity=4)
    _run(db, ka2, m2, now - timedelta(days=8), 2, cycle_s=28.0, cavity=2, cycles_every=2)
    _run(db, kb, m3, now - timedelta(days=6), 2, cycle_s=42.0, cavity=1)
    _run(db, kc, m2, now - timedelta(days=4), 1.5, cycle_s=31.0, cavity=2)

    # M3 was unreliable: three closed downtimes in the last 30 days.
    for d in (20, 12, 3):
        db.add(
            MachineDowntime(
                machine_id=m3.id,
                start_at=now - timedelta(days=d),
                end_at=now - timedelta(days=d) + timedelta(hours=5),
                note="arıza",
            )
        )

    for p, q in ((pa, 500), (pb, 70), (pc, 0)):
        db.add(
            StockMovement(
                product_id=p.id,
                qty=q,
                kind="excel_reset",
                created_at=now - timedelta(days=1),
            )
        )
    db.flush()
    return {
        "machines": [m1, m2, m3],
        "products": {"A": pa, "B": pb, "C": pc},
        "molds": {"A1": ka1, "A2": ka2, "B": kb, "C": kc},
        "camera": cam,
    }


def add_open_orders(db: Session, f: dict, now: datetime) -> list[OrderLine]:
    pa, pb, pc = f["products"]["A"], f["products"]["B"], f["products"]["C"]
    lines = [
        OrderLine(product_name_raw=pa.name, product_id=pa.id, qty=3000, due_date=now + timedelta(days=2)),
        OrderLine(product_name_raw=pb.name, product_id=pb.id, qty=50, due_date=now + timedelta(days=3)),
        OrderLine(product_name_raw=pc.name, product_id=pc.id, qty=1200, due_date=now + timedelta(days=5)),
        OrderLine(product_name_raw=pa.name, product_id=pa.id, qty=2000, due_date=now + timedelta(days=9)),
        OrderLine(product_name_raw="BILINMEYEN", product_id=None, qty=10, due_date=now + timedelta(days=4)),
    ]
    db.add_all(lines)
    db.flush()
    return lines
