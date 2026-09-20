"""Products CRUD for planning / mold linkage."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_panel_8000
from app.db.models import Product
from app.services.planning.stock import get_or_create_product

router = APIRouter()


class ProductOut(BaseModel):
    id: int
    name: str
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)


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
