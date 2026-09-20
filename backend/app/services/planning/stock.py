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


def list_stock_balances(db: Session) -> list[dict]:
    products = db.query(Product).order_by(Product.name.asc()).all()
    out = []
    for p in products:
        out.append(
            {
                "product_id": p.id,
                "product_name": p.name,
                "qty": current_stock(db, p.id),
            }
        )
    return out
