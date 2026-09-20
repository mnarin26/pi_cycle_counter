"""Planning API: faults, stock, orders, Excel import, plans, compare, mold runs."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_panel_8000
from app.db.models import (
    Machine,
    MachineFault,
    Mold,
    MoldRun,
    OrderImport,
    OrderLine,
    PlanRun,
    PlanSlot,
    Product,
)
from app.services.planning.compare import compare_plan
from app.services.planning.engine import mold_machine_stats
from app.services.planning.excel_orders import parse_orders_xlsx
from app.services.planning.excel_stock import parse_stock_xlsx
from app.services.planning.mold_runs import count_diff_info
from app.services.planning.plan_engine import build_plan
from app.services.planning.stock import (
    add_movement,
    current_stock,
    get_or_create_product,
    list_stock_balances,
)

router = APIRouter()


# ----- Faults -----


class FaultOut(BaseModel):
    id: int
    machine_id: int
    start_at: datetime
    end_at: datetime | None = None
    note: str
    created_by: str | None = None

    class Config:
        from_attributes = True


class FaultCreate(BaseModel):
    machine_id: int = Field(..., gt=0)
    start_at: datetime | None = None
    note: str = ""


class FaultClose(BaseModel):
    end_at: datetime | None = None


@router.get("/faults", response_model=list[FaultOut])
def list_faults(
    open_only: bool = Query(False),
    machine_id: int | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(MachineFault)
    if open_only:
        q = q.filter(MachineFault.end_at.is_(None))
    if machine_id is not None:
        q = q.filter(MachineFault.machine_id == machine_id)
    return q.order_by(MachineFault.start_at.desc()).limit(200).all()


@router.post("/faults", response_model=FaultOut)
def create_fault(
    body: FaultCreate,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    if not db.get(Machine, body.machine_id):
        raise HTTPException(404, detail="Makine bulunamadı")
    start = body.start_at or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    row = MachineFault(
        machine_id=body.machine_id,
        start_at=start,
        note=(body.note or "").strip(),
        created_by=user.display_name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/faults/{fault_id}/close", response_model=FaultOut)
def close_fault(
    fault_id: int,
    body: FaultClose,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    row = db.get(MachineFault, fault_id)
    if not row:
        raise HTTPException(404)
    end = body.end_at or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end < row.start_at:
        raise HTTPException(400, detail="Bitiş başlangıçtan önce olamaz")
    row.end_at = end
    db.commit()
    db.refresh(row)
    return row


# ----- Stock -----


@router.get("/stock")
def get_stock(db: Session = Depends(get_db)):
    return {"rows": list_stock_balances(db)}


@router.post("/stock/excel/preview")
async def stock_excel_preview(
    file: UploadFile = File(...),
    user=Depends(require_panel_8000),
):
    data = await file.read()
    try:
        return parse_stock_xlsx(data)
    except RuntimeError as e:
        raise HTTPException(500, detail=str(e)) from e


class StockConfirmRow(BaseModel):
    product_name_raw: str
    qty: int


class StockConfirmBody(BaseModel):
    rows: list[StockConfirmRow]
    file_hash: str | None = None


@router.post("/stock/excel/confirm")
def stock_excel_confirm(
    body: StockConfirmBody,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    applied = 0
    for r in body.rows:
        p = get_or_create_product(db, r.product_name_raw)
        add_movement(
            db,
            product_id=p.id,
            qty=int(r.qty),
            kind="excel_reset",
            ref_type="import",
            note=f"Stok Excel ({user.display_name})",
        )
        applied += 1
    db.commit()
    return {"ok": True, "applied": applied, "stock": list_stock_balances(db)}


# ----- Orders -----


@router.get("/orders")
def list_orders(
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(OrderLine)
    if status:
        q = q.filter(OrderLine.status == status)
    rows = q.order_by(OrderLine.due_date.is_(None), OrderLine.due_date.asc(), OrderLine.id.desc()).limit(500).all()
    out = []
    for r in rows:
        stock = current_stock(db, r.product_id) if r.product_id else None
        out.append(
            {
                "id": r.id,
                "import_id": r.import_id,
                "product_name_raw": r.product_name_raw,
                "product_id": r.product_id,
                "product_name": db.get(Product, r.product_id).name if r.product_id else None,
                "qty": r.qty,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "customer": r.customer,
                "note": r.note,
                "status": r.status,
                "stock": stock,
                "net_need": (max(0, r.qty - stock) if stock is not None and r.status == "open" else None),
            }
        )
    return {"rows": out}


@router.post("/orders/excel/preview")
async def orders_excel_preview(
    file: UploadFile = File(...),
    user=Depends(require_panel_8000),
):
    data = await file.read()
    try:
        preview = parse_orders_xlsx(data)
    except RuntimeError as e:
        raise HTTPException(500, detail=str(e)) from e
    # Attach product match hints
    for row in preview.get("rows") or []:
        p = None
        # soft match without creating
        from sqlalchemy import func

        # will resolve on confirm
        row["matched"] = False
    return preview


class OrderConfirmRow(BaseModel):
    product_name_raw: str
    qty: int
    due_date: datetime | None = None
    customer: str | None = None
    note: str | None = None
    product_id: int | None = None


class OrderConfirmBody(BaseModel):
    rows: list[OrderConfirmRow]
    filename: str = ""
    file_hash: str | None = None


@router.post("/orders/excel/confirm")
def orders_excel_confirm(
    body: OrderConfirmBody,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    imp = OrderImport(
        filename=body.filename or "upload.xlsx",
        file_hash=body.file_hash,
        row_count=len(body.rows),
        created_by=user.display_name,
    )
    db.add(imp)
    db.flush()
    created = 0
    for r in body.rows:
        pid = r.product_id
        if pid is None:
            p = get_or_create_product(db, r.product_name_raw)
            pid = p.id
        due = r.due_date
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        db.add(
            OrderLine(
                import_id=imp.id,
                product_name_raw=r.product_name_raw,
                product_id=pid,
                qty=int(r.qty),
                due_date=due,
                customer=r.customer,
                note=r.note,
                status="open",
            )
        )
        created += 1
    db.commit()
    return {"ok": True, "import_id": imp.id, "created": created}


class OrderPatch(BaseModel):
    qty: int | None = Field(default=None, gt=0)
    due_date: datetime | None = None
    customer: str | None = None
    note: str | None = None
    status: str | None = None
    product_id: int | None = None


@router.patch("/orders/{line_id}")
def patch_order(
    line_id: int,
    body: OrderPatch,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    row = db.get(OrderLine, line_id)
    if not row:
        raise HTTPException(404)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ("open", "shipped", "cancelled"):
        raise HTTPException(400, detail="Geçersiz durum")
    prev_status = row.status
    for k, v in data.items():
        if k == "due_date" and v is not None and getattr(v, "tzinfo", None) is None:
            v = v.replace(tzinfo=timezone.utc)
        setattr(row, k, v)
    # Shipped → deduct stock once
    if prev_status != "shipped" and row.status == "shipped" and row.product_id:
        add_movement(
            db,
            product_id=int(row.product_id),
            qty=-abs(int(row.qty)),
            kind="shipment",
            ref_type="order_line",
            ref_id=row.id,
            note=f"Sipariş gönderildi #{row.id}",
        )
    db.commit()
    db.refresh(row)
    return {"ok": True, "id": row.id, "status": row.status}


# ----- Mold runs / count diff -----


@router.get("/mold-runs")
def list_mold_runs(
    machine_id: int | None = None,
    mold_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(MoldRun)
    if machine_id is not None:
        q = q.filter(MoldRun.machine_id == machine_id)
    if mold_id is not None:
        q = q.filter(MoldRun.mold_id == mold_id)
    rows = q.order_by(MoldRun.started_at.desc()).limit(limit).all()
    out = []
    for r in rows:
        mold = db.get(Mold, r.mold_id)
        diff = count_diff_info(r)
        out.append(
            {
                "id": r.id,
                "mold_id": r.mold_id,
                "mold_name": mold.name if mold else None,
                "machine_id": r.machine_id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "end_reason": r.end_reason,
                "camera_cycles": r.camera_cycles,
                "entered_press_count": r.entered_press_count,
                "active_cavity_snapshot": r.active_cavity_snapshot,
                "product_added": r.product_added,
                "count_diff": diff,
            }
        )
    return {"rows": out}


# ----- Stats / plan -----


@router.get("/mold-machine-stats")
def get_mold_machine_stats(days: int = Query(30, ge=1, le=180), db: Session = Depends(get_db)):
    return {"days": days, "rows": mold_machine_stats(db, days=days)}


@router.post("/plans/generate")
def generate_plan(
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    plan = build_plan(db, created_by=user.display_name)
    return _plan_detail(db, plan)


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    rows = db.query(PlanRun).order_by(PlanRun.id.desc()).limit(20).all()
    return {
        "rows": [
            {
                "id": p.id,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "created_by": p.created_by,
                "note": p.note,
            }
            for p in rows
        ]
    }


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(PlanRun, plan_id)
    if not plan:
        raise HTTPException(404)
    return _plan_detail(db, plan)


@router.get("/plans/{plan_id}/compare")
def plan_compare(plan_id: int, db: Session = Depends(get_db)):
    try:
        return compare_plan(db, plan_id)
    except ValueError as e:
        raise HTTPException(404, detail=str(e)) from e


def _plan_detail(db: Session, plan: PlanRun) -> dict:
    slots = db.query(PlanSlot).filter(PlanSlot.plan_run_id == plan.id).order_by(PlanSlot.id).all()
    return {
        "id": plan.id,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "created_by": plan.created_by,
        "note": plan.note,
        "slots": [
            {
                "id": s.id,
                "order_line_id": s.order_line_id,
                "product_id": s.product_id,
                "mold_id": s.mold_id,
                "machine_id": s.machine_id,
                "suggested_start": s.suggested_start.isoformat() if s.suggested_start else None,
                "suggested_end": s.suggested_end.isoformat() if s.suggested_end else None,
                "qty_planned": s.qty_planned,
                "reason": s.reason,
                "score": s.score,
            }
            for s in slots
        ],
    }
