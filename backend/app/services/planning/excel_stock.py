"""Stock Excel parser — layout driven by 8080 excel_format settings."""

from __future__ import annotations

import hashlib
import io
from typing import Any

from app.services.planning.excel_format import _col_to_index, get_excel_format


def _parse_qty(val: Any) -> int:
    if val is None or val == "":
        return 0
    try:
        return int(float(str(val).replace(",", ".").strip()))
    except ValueError:
        return 0


DEFAULT_STOCK_FALLBACK = {
    "layout": "table",
    "product_col": "A",
    "qty_col": "B",
    "data_start_row": 2,
}


def parse_stock_xlsx(data: bytes, db=None) -> dict[str, Any]:
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise RuntimeError("openpyxl yüklü değil") from e

    file_hash = hashlib.sha256(data).hexdigest()[:32]
    cfg = DEFAULT_STOCK_FALLBACK
    if db is not None:
        cfg = get_excel_format(db)["stock"]

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    product_col = _col_to_index(cfg["product_col"])
    qty_col = _col_to_index(cfg["qty_col"])
    start = int(cfg["data_start_row"])

    parsed: list[dict] = []
    errors: list[str] = []
    max_row = ws.max_row or (start + 500)
    for r in range(start, min(max_row, start + 5000) + 1):
        product = str(ws.cell(row=r, column=product_col).value or "").strip()
        if not product:
            continue
        # skip header-like rows
        if product.lower() in ("ürün", "urun", "ürün adı", "product", "malzeme", "malzeme tanımı"):
            continue
        qty = _parse_qty(ws.cell(row=r, column=qty_col).value)
        parsed.append({"row": r, "product_name_raw": product, "qty": qty, "shipped": None})

    if not parsed:
        errors = ["Stok satırı bulunamadı — Excel format ayarını kontrol edin"]

    return {"file_hash": file_hash, "rows": parsed, "errors": errors, "format_used": cfg}
