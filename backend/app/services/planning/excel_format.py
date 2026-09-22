"""Configurable Excel layout for orders/stock — edited on 8080 admin panel."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.stored_settings import get_section, patch_section

SECTION = "excel_format"

DEFAULT_ORDERS: dict[str, Any] = {
    # matrix: product × date grid; table: classic header columns
    "layout": "matrix",
    # dates_along=row → dates in a horizontal row; column → dates stacked vertically
    "dates_along": "row",
    "product_col": "B",
    "data_start_row": 3,
    "date_row": 1,
    "date_start_col": "I",
    # used when dates_along=column
    "date_col": "I",
    "date_start_row": 2,
    "product_row": 2,
    "customer_col": "C",
    # table-mode columns (letters)
    "qty_col": "C",
    "due_col": "D",
    "note_col": "E",
}

DEFAULT_STOCK: dict[str, Any] = {
    "layout": "table",
    "product_col": "A",
    "qty_col": "B",
    "data_start_row": 2,
}


def _col_to_index(val: Any) -> int:
    """A→1, B→2, … AA→27. Also accepts 1-based ints."""
    if val is None or val == "":
        raise ValueError("Kolon boş")
    if isinstance(val, int):
        if val < 1:
            raise ValueError(f"Kolon indeksi geçersiz: {val}")
        return val
    s = str(val).strip().upper()
    if s.isdigit():
        n = int(s)
        if n < 1:
            raise ValueError(f"Kolon indeksi geçersiz: {val}")
        return n
    n = 0
    for ch in s:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Kolon harfi geçersiz: {val}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def col_letter(n: int) -> str:
    if n < 1:
        return "?"
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _norm_layout(raw: Any, default: str) -> str:
    v = str(raw or default).strip().lower()
    return v if v in ("matrix", "table") else default


def _norm_along(raw: Any) -> str:
    v = str(raw or "row").strip().lower()
    return "column" if v in ("column", "col", "sütun", "sutun") else "row"


def _norm_orders(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_ORDERS)
    out["layout"] = _norm_layout(src.get("layout"), DEFAULT_ORDERS["layout"])
    out["dates_along"] = _norm_along(src.get("dates_along"))
    for key in (
        "product_col",
        "date_start_col",
        "date_col",
        "customer_col",
        "qty_col",
        "due_col",
        "note_col",
    ):
        if src.get(key) is not None and str(src.get(key)).strip() != "":
            out[key] = str(src[key]).strip().upper()
    for key in ("data_start_row", "date_row", "date_start_row", "product_row"):
        if src.get(key) is not None and str(src.get(key)).strip() != "":
            try:
                out[key] = max(1, int(src[key]))
            except (TypeError, ValueError) as e:
                raise ValueError(f"{key} sayı olmalı") from e
    # validate letters parse
    _col_to_index(out["product_col"])
    _col_to_index(out["date_start_col"])
    _col_to_index(out["date_col"])
    return out


def _norm_stock(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_STOCK)
    out["layout"] = _norm_layout(src.get("layout"), DEFAULT_STOCK["layout"])
    for key in ("product_col", "qty_col"):
        if src.get(key) is not None and str(src.get(key)).strip() != "":
            out[key] = str(src[key]).strip().upper()
    if src.get("data_start_row") is not None and str(src.get("data_start_row")).strip() != "":
        try:
            out["data_start_row"] = max(1, int(src["data_start_row"]))
        except (TypeError, ValueError) as e:
            raise ValueError("data_start_row sayı olmalı") from e
    _col_to_index(out["product_col"])
    _col_to_index(out["qty_col"])
    return out


def get_excel_format(db: Session) -> dict[str, Any]:
    raw = get_section(db, SECTION)
    return {
        "orders": _norm_orders(raw.get("orders") if isinstance(raw.get("orders"), dict) else None),
        "stock": _norm_stock(raw.get("stock") if isinstance(raw.get("stock"), dict) else None),
    }


def patch_excel_format(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_excel_format(db)
    if "orders" in patch and patch["orders"] is not None:
        if not isinstance(patch["orders"], dict):
            raise ValueError("orders nesne olmalı")
        merged = {**current["orders"], **patch["orders"]}
        current["orders"] = _norm_orders(merged)
    if "stock" in patch and patch["stock"] is not None:
        if not isinstance(patch["stock"], dict):
            raise ValueError("stock nesne olmalı")
        merged = {**current["stock"], **patch["stock"]}
        current["stock"] = _norm_stock(merged)
    return patch_section(db, SECTION, current)


def preview_grid(cfg: dict[str, Any], *, kind: str = "orders") -> dict[str, Any]:
    """Small visual map for admin UI (cell roles)."""
    if kind == "stock":
        pc = _col_to_index(cfg["product_col"])
        qc = _col_to_index(cfg["qty_col"])
        start = int(cfg["data_start_row"])
        header_row = max(1, start - 1)
        cols = max(pc, qc, 4)
        rows = start + 3
        cells: list[dict[str, Any]] = []
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                role = "empty"
                label = ""
                if r == header_row:
                    if c == pc:
                        role, label = "header", "Ürün"
                    elif c == qc:
                        role, label = "header", "Stok"
                elif r >= start:
                    if c == pc:
                        role, label = "product", "ürün…"
                    elif c == qc:
                        role, label = "qty", "120"
                cells.append({"r": r, "c": c, "col": col_letter(c), "role": role, "label": label})
        return {
            "kind": "stock",
            "rows": rows,
            "cols": cols,
            "cells": cells,
            "hint": "",
        }

    layout = _norm_layout(cfg.get("layout"), "matrix")
    if layout == "table":
        pc = _col_to_index(cfg["product_col"])
        qc = _col_to_index(cfg.get("qty_col") or "C")
        due_c = _col_to_index(cfg["due_col"]) if cfg.get("due_col") else None
        cust_c = _col_to_index(cfg["customer_col"]) if cfg.get("customer_col") else None
        note_c = _col_to_index(cfg["note_col"]) if cfg.get("note_col") else None
        start = int(cfg["data_start_row"])
        header_row = max(1, start - 1)
        cols = max(pc, qc, due_c or 0, cust_c or 0, note_c or 0, 5)
        rows = start + 3
        cells = []
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                role, label = "empty", ""
                if r == header_row:
                    if c == pc:
                        role, label = "header", "Ürün"
                    elif c == qc:
                        role, label = "header", "Miktar"
                    elif due_c and c == due_c:
                        role, label = "header", "Termin"
                    elif cust_c and c == cust_c:
                        role, label = "header", "Müşteri"
                    elif note_c and c == note_c:
                        role, label = "header", "Not"
                elif r >= start:
                    if c == pc:
                        role, label = "product", "ürün…"
                    elif c == qc:
                        role, label = "qty", "100"
                    elif due_c and c == due_c:
                        role, label = "date", "09.09"
                    elif cust_c and c == cust_c:
                        role, label = "product", "firma"
                    elif note_c and c == note_c:
                        role, label = "header", "…"
                cells.append({"r": r, "c": c, "col": col_letter(c), "role": role, "label": label})
        return {
            "kind": "orders",
            "layout": "table",
            "rows": rows,
            "cols": cols,
            "cells": cells,
            "hint": "",
        }

    along = cfg.get("dates_along") or "row"
    if along == "column":
        dc = _col_to_index(cfg["date_col"])
        dsr = int(cfg["date_start_row"])
        pr = int(cfg["product_row"])
        cols = max(dc + 4, 8)
        rows = max(dsr + 4, pr + 1, 8)
        cells = []
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                role, label = "empty", ""
                if c == dc and r >= dsr:
                    role, label = "date", "termin"
                elif r == pr and c > dc:
                    role, label = "product", "ürün"
                elif r > pr and c > dc and r >= dsr:
                    role, label = "qty", "adet"
                cells.append({"r": r, "c": c, "col": col_letter(c), "role": role, "label": label})
        return {
            "kind": "orders",
            "layout": "matrix",
            "rows": rows,
            "cols": cols,
            "cells": cells,
            "hint": "",
        }

    pc = _col_to_index(cfg["product_col"])
    dsc = _col_to_index(cfg["date_start_col"])
    dr = int(cfg["date_row"])
    start = int(cfg["data_start_row"])
    cols = max(dsc + 5, pc + 1, 10)
    rows = max(start + 3, dr + 1, 6)
    cells = []
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            role, label = "empty", ""
            if r == dr and c >= dsc:
                role, label = "date", "termin"
            elif r >= start and c == pc:
                role, label = "product", "ürün"
            elif r >= start and c >= dsc:
                role, label = "qty", "adet"
            cells.append({"r": r, "c": c, "col": col_letter(c), "role": role, "label": label})
    return {
        "kind": "orders",
        "layout": "matrix",
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "hint": "",
    }
