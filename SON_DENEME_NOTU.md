# Son deneme / mimari notu (güncel)

Eski Mayıs 2026 “çizgi tabanlı takip” deneme notlarının yerini alır. Tarihçe için git geçmişine bakın.

## Güncel sayım (Eyl 2026 — `main` / canlı rsp3b)

**Detector:** 1D çizgi profili (`line_pipeline`) — ROI maskesi detector yolunda yok; ROI admin’de çizgi hizalamaya yardımcı.

**Durum makinesi:** peak / trough **zigzag** (`state_machine.py`)

- Peak ≈ OPEN, trough ≈ CLOSED (p0 kapalı uç, p1 açık uç).
- Salınım ≥ `min_prominence` (canlı ~0.12) değilse yön değişmez.
- `|Δpos| ≥ jump_abs` (canlı ~0.30) → teleport; önceki pos tutulur.
- Mutlak OPEN/CLOSED eşik öğrenmesi yok.

**Sayaç:** `cycle_tracker` — onaylı uçlar arasında **A→B→A = 1 çevrim**.

**Performans (canlı tipik):** kamera `target_width≈480`, paylaşımlı gray + process worker; işlem ms genelde düşük çift haneli.

## Eski yaklaşımlar (artık canlıda yok)

- Dwell / `stability_confirm` ile “uçta bekle” sayımı
- Schmitt sabit bant sayacı (deneme kodları tools’ta kalabilir)
- Shadow wagon yarım-strok bin sayacı (PC araç; canlı SM değil)

## Kalibrasyon hatırlatması

- Çizgiyi 8080’de kaydet (`axis_p0` / `axis_p1` + `line_thickness`).
- İsteğe bağlı uzunluk kalibrasyonu.
- Tespit (peak/bg) ile sayım (zigzag pos) ayrıdır; aydınlık zeminde önce tespit ayarlarına bak.

## Matcher

`AUTO_MOLD_MATCHING=false` (varsayılan canlı): çevrim yazılır, otomatik kalıp önerisi/eşleşme yok.
