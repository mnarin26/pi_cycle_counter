"""Stock ledger helpers (production / shipment / excel_reset)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Product, StockMovement


def get_or_create_product(db: Session, name: str) -> Product:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Ürün adı boş olamaz")
    row = db.query(Product).filter(Product.name == cleaned).first()
    if row:
        return row
    # Case-insensitive match for Turkish Excel variance
    row = db.query(Product).filter(Product.name.ilike(cleaned)).first()
    if row:
        return row
    row = Product(name=cleaned)
    db.add(row)
    db.flush()
    return row


def add_movement(
    db: Session,
    *,
    product_id: int,
    qty: int,
    kind: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    note: str | None = None,
    created_at: datetime | None = None,
) -> StockMovement:
    if kind not in ("production", "shipment", "excel_reset"):
        raise ValueError(f"Geçersiz stok hareketi: {kind}")
    if kind == "shipment" and qty > 0:
        qty = -abs(qty)
    if kind == "production" and qty < 0:
        qty = abs(qty)
    mov = StockMovement(
        product_id=int(product_id),
        qty=int(qty),
        kind=kind,
        ref_type=ref_type,
        ref_id=ref_id,
        note=note,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(mov)
    db.flush()
    return mov


def current_stock(db: Session, product_id: int) -> int:
    """Balance = last excel_reset absolute qty + later production/shipment deltas."""
    pid = int(product_id)
    last_reset = (
        db.query(StockMovement)
        .filter(StockMovement.product_id == pid, StockMovement.kind == "excel_reset")
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .first()
    )
    if last_reset is None:
        rows = (
            db.query(StockMovement)
            .filter(StockMovement.product_id == pid, StockMovement.kind != "excel_reset")
            .all()
        )
        return int(sum(int(r.qty) for r in rows))
    base = int(last_reset.qty)
    later = (
        db.query(StockMovement)
        .filter(
            StockMovement.product_id == pid,
            StockMovement.kind != "excel_reset",
            StockMovement.created_at >= last_reset.created_at,
            StockMovement.id > last_reset.id,
        )
        .all()
    )
    # Also include same-timestamp later ids already covered; keep simple:
    later2 = (
        db.query(StockMovement)
        .filter(
            StockMovement.product_id == pid,
            StockMovement.kind != "excel_reset",
            StockMovement.id > last_reset.id,
        )
        .all()
    )
    return base + int(sum(int(r.qty) for r in later2))


def stock_as_of(db: Session, product_id: int, as_of: datetime) -> tuple[int, str]:
    """Ledger balance at `as_of` → (qty, confidence: high | low | unknown).

    high    = an excel_reset at/before as_of + later deltas up to as_of
    low     = no reset before as_of; sum of earlier deltas only
    unknown = nothing recorded before as_of
    """
    pid = int(product_id)
    at = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    rows = (
        db.query(StockMovement)
        .filter(StockMovement.product_id == pid, StockMovement.created_at <= at)
        .order_by(StockMovement.id.asc())
        .all()
    )
    if not rows:
        return 0, "unknown"
    resets = [r for r in rows if r.kind == "excel_reset"]
    if not resets:
        return int(sum(int(r.qty) for r in rows)), "low"
    base = max(resets, key=lambda r: (_aware(r.created_at), r.id))
    later = [r for r in rows if r.kind != "excel_reset" and r.id > base.id]
    return int(base.qty) + int(sum(int(r.qty) for r in later)), "high"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def list_stock_balances(db: Session) -> list[dict]:
    products = db.query(Product).order_by(Product.name.asc()).all()
    out = []
    for p in products:
        out.append(
            {
                "product_id": p.id,
                "product_name": p.name,
                "qty": current_stock(db, p.id),
                "min_stock": p.min_stock,
            }
        )
    return out
