# Geçici Excel formatları (Planlama)

Gerçek formatlar gelince yalnız parser kolon eşlemesi değişecek:
- `backend/app/services/planning/excel_orders.py` → `ORDER_COLUMNS`
- `backend/app/services/planning/excel_stock.py` → `STOCK_COLUMNS`

## Sipariş Excel (geçici)

| Ürün Adı | Miktar (adet) | Termin Tarihi | Müşteri | Not |
|----------|---------------|---------------|---------|-----|
| KAPAK-A  | 5000          | 2026-10-01    | Firma X |     |
| KOL-B    | 1200          | 15.10.2026    | Firma Y | acil |

Tarih: `YYYY-MM-DD`, `DD.MM.YYYY` veya `DD/MM/YYYY`.

## Stok Excel (geçici)

| Ürün Adı | Stok (adet) | Sevk Edilen |
|----------|-------------|-------------|
| KAPAK-A  | 800         | 200         |
| KOL-B    | 50          |             |

`Sevk Edilen` opsiyoneldir. Onayda stok Excel **gerçek veri** sayılır (`excel_reset`).
