"""Products CRUD for planning / mold linkage."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_panel_8000, require_planning
from app.db.models import Product
from app.services.planning.plan_state import mark_active_plans_stale
from app.services.planning.stock import get_or_create_product

router = APIRouter()


class ProductOut(BaseModel):
    id: int
    name: str
    min_stock: int | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)


class ProductPatch(BaseModel):
    min_stock: int | None = Field(None, ge=0)


@router.get("", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db)):
    return db.query(Product).order_by(Product.name.asc()).all()


@router.post("", response_model=ProductOut)
def create_product(
    body: ProductCreate,
    db: Session = Depends(get_db),
    user=Depends(require_panel_8000),
):
    try:
        p = get_or_create_product(db, body.name)
        db.commit()
        db.refresh(p)
        return p
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e


@router.patch("/{product_id}", response_model=ProductOut)
def patch_product(
    product_id: int,
    body: ProductPatch,
    db: Session = Depends(get_db),
    user=Depends(require_planning),
):
    p = db.get(Product, product_id)
    if p is None:
        raise HTTPException(404, detail="Ürün bulunamadı")
    new = body.min_stock or None
    if new != p.min_stock:
        p.min_stock = new
        mark_active_plans_stale(db, "Asgari stok değişti")
    db.commit()
    db.refresh(p)
    return p
