"""Temporary order Excel parser — column map easy to swap when real format arrives."""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from typing import Any

# Temporary column headers (Turkish). Change only this map when real format is provided.
ORDER_COLUMNS = {
    "product": ("ürün adı", "urun adi", "ürün", "urun", "product", "product name"),
    "qty": ("miktar (adet)", "miktar", "adet", "qty", "quantity"),
    "due": ("termin tarihi", "termin", "teslim", "due", "due date"),
    "customer": ("müşteri", "musteri", "customer"),
    "note": ("not", "note", "açıklama", "aciklama"),
}


def _norm(s: Any) -> str:
    return str(s or "").strip().lower().replace("ı", "i").replace("İ", "i")


def _match_col(header: str, aliases: tuple[str, ...]) -> bool:
    h = _norm(header)
    return any(_norm(a) == h or _norm(a) in h for a in aliases)


def _parse_date(val: Any) -> datetime | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        dt = val
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    text = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_qty(val: Any) -> int:
    if val is None or val == "":
        return 0
    try:
        return int(float(str(val).replace(",", ".").strip()))
    except ValueError:
        return 0


def parse_orders_xlsx(data: bytes) -> dict[str, Any]:
    """Return preview rows sorted by due date. Does not write DB."""
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise RuntimeError("openpyxl yüklü değil") from e

    file_hash = hashlib.sha256(data).hexdigest()[:32]
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return {"file_hash": file_hash, "rows": [], "errors": ["Boş dosya"]}

    col_map: dict[str, int] = {}
    for idx, cell in enumerate(header or []):
        for key, aliases in ORDER_COLUMNS.items():
            if key not in col_map and _match_col(str(cell or ""), aliases):
                col_map[key] = idx
    if "product" not in col_map or "qty" not in col_map:
        return {
            "file_hash": file_hash,
            "rows": [],
            "errors": [
                "Geçici format bekleniyor: Ürün Adı | Miktar (adet) | Termin Tarihi | Müşteri | Not"
            ],
            "columns_found": [str(c) for c in (header or [])],
        }

    parsed: list[dict] = []
    errors: list[str] = []
    for i, row in enumerate(rows_iter, start=2):
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        product = str(row[col_map["product"]] or "").strip()
        qty = _parse_qty(row[col_map["qty"]])
        due = _parse_date(row[col_map["due"]]) if "due" in col_map else None
        customer = (
            str(row[col_map["customer"]] or "").strip() if "customer" in col_map else ""
        )
        note = str(row[col_map["note"]] or "").strip() if "note" in col_map else ""
        if not product:
            errors.append(f"Satır {i}: ürün adı boş")
            continue
        if qty <= 0:
            errors.append(f"Satır {i}: miktar geçersiz ({product})")
            continue
        parsed.append(
            {
                "row": i,
                "product_name_raw": product,
                "qty": qty,
                "due_date": due.isoformat() if due else None,
                "customer": customer or None,
                "note": note or None,
            }
        )

    parsed.sort(key=lambda r: (r["due_date"] is None, r["due_date"] or "", r["product_name_raw"]))
    return {"file_hash": file_hash, "rows": parsed, "errors": errors}
