#!/usr/bin/env python3
"""Write temporary sample Excel templates for planning."""
from pathlib import Path

from openpyxl import Workbook

root = Path(__file__).resolve().parents[1] / "docs"
root.mkdir(parents=True, exist_ok=True)

wb = Workbook()
ws = wb.active
ws.title = "Siparis"
ws.append(["Ürün Adı", "Miktar (adet)", "Termin Tarihi", "Müşteri", "Not"])
ws.append(["KAPAK-A", 5000, "2026-10-01", "Firma X", ""])
ws.append(["KOL-B", 1200, "15.10.2026", "Firma Y", "acil"])
wb.save(root / "ornek-siparis.xlsx")

wb2 = Workbook()
ws2 = wb2.active
ws2.title = "Stok"
ws2.append(["Ürün Adı", "Stok (adet)", "Sevk Edilen"])
ws2.append(["KAPAK-A", 800, 200])
ws2.append(["KOL-B", 50, ""])
wb2.save(root / "ornek-stok.xlsx")
print("wrote", root / "ornek-siparis.xlsx", root / "ornek-stok.xlsx")
