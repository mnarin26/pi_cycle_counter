# Injection Monitor — Kullanım Kılavuzu

Repodaki **canlı `main` / rsp3b** koduna göredir (Eyl 2026).  
Önemli: **otomatik kalıp eşleştirme (`AUTO_MOLD_MATCHING`) kapalıdır.** Kalıp önerisi / candidate akışı çalışmaz. Analitik sayfası menüde yoktur.

---

## 1. Genel yapı

| Bileşen | Port | Giriş |
|--------|------|--------|
| İzleme paneli | **8000** | Pano, TV, makine detay, kalıplar (manuel), olaylar |
| Admin / kalibrasyon | **8080** | Kamera, çizgi, canlı önizleme + noktalar, makine parametreleri, teshis |

- Vision + sayım yalnız **8000** sürecinde.
- **Canlı kamera görüntüsü ve reflektör noktaları 8080’dedir** (8000’de `/live` rotası yok).
- Oturum gerekir (süper / günlük şifre). **`/tv`** public kalabilir.

### Kapalı / yok özellikler (dokümanda yanlış yazılmasın)

| Özellik | Durum |
|---------|--------|
| Otomatik kalıp matcher | **Kapalı** (`auto_mold_matching=false`) |
| Kalıp önerisi / candidate “Ad ver / Yok say” akışı | **Kapalı** (matcher ile birlikte) |
| Makine detayda “kalıp eşleştirmesini çalıştır / boş olanlar / aralığı yeniden işle” | **Yok** (UI’da yok; API kalıntısı olsa bile kullanılmaz) |
| Analitik sayfası (`/analytics`) | **Yok** (route yok) |
| Schmitt sayım modu | UI’da eski alan var; **canlı sayım her zaman zigzag** |

---

## 2. İzleme paneli (8000)

Rotalar: `/`, `/tv`, `/machines/:id`, `/molds`, `/events`, `/login`.  
**`/live` ve `/analytics` yok.**

### 2.1 Pano / TV / makine detay

- Karttaki “Döngü” çoğu yerde **son tur süresi** (`cycle_time_last`), adet değil.
- TV board: bugünkü sayılan çevrimler, kalıp adı (Telegram ile **manuel** atanmışsa), saatlik grafik.
- Makine detay: aralık özeti, zaman–süre grafiği, son çevrimler. **Otomatik kalıp eşleştirme butonu yok.**

### 2.2 Kalıplar (`/molds`)

Manuel kalıp listesi / düzenleme (ad, hedef süre vb.).  
Bot veya panelden **elle atama** dışında otomatik “aday kalıp önerisi” **yok**.

### 2.3 Olaylar

Olay listesi. Matcher kapalıyken `mold_suggestion` / auto-match olayları üretilmez.

---

## 3. Admin (8080)

### 3.1 Makineler canlı tablosu

| Sütun | Anlamı |
|-------|--------|
| Durum | OPEN / CLOSED / MOVING / UNKNOWN (zigzag uçları) |
| Pos | Reflektör konumu 0..1 |
| Son cevrim | Son tur süresi (sn) |
| **Tespit eşiği** | Reflektörü **bulma** eşiği (peak−bg). **Çevrim sayacı değil.** |
| **Tespit sinyali (peak/bg)** | `peak/bg` = çizgide en parlak / arka plan (0–255); `Δ` = fark; `len` = parlak segment. “Reflektör görünüyor mu?” teşhisi. Aydınlık zeminde peak≈bg olur → nokta kaybolabilir; bu sayım kuralı değil, **tespit** sorunudur. |

### 3.2 Makine parametreleri — hangisi aktif?

**Aktif (tespit / çizgi):** kamera, ad, çizgi kalınlığı, threshold modu + prominence (peak−bg), uzunluk min/max, occlusion grace, makine aktif, çizgi kaydet.

**Sayım (zigzag):** canlıda **global** `min_prominence=0.12`, `jump_abs=0.30`. Admin’deki “Strok algılama eşiği” kaydedilir ama kod şu an makine değerini **yok sayıp 0.12 sabit** kullanır.

**Kullanılmıyor (eski Schmitt, `<details>` altında):** Sayım modu, kapalı polarite, kapalı referans, histerezis, yumuşatma, otomatik öğrenme.

### 3.3 Canlı görüntü

8080 “Canlı görüntü” paneli: JPEG + reflektör noktaları. Bu **8000’de değil**.

### 3.4 Pi host (anlık)

CPU, sıcaklık, load, RAM, **disk boş/toplam**.  
**Otomatik akmaz** — “Durumu yenile” (veya log durumu yenileme) ile güncellenir.

---

## 4. Döngü sayımı (zigzag)

1. Çizgi profili → `position_01` (reflektör bulunduysa).
2. Peak/trough zigzag → OPEN/CLOSED.
3. `cycle_tracker`: **A→B→A = 1 çevrim**.
4. DB kaydı: matcher kapalı → kalıp otomatik bağlanmaz; `current_mold_id` varsa ad anlık görüntü olarak yazılabilir (Telegram ataması).

---

## 5. Tipik kurulum

1. **8080:** RTSP, çizgi kaydet, canlıda nokta görünüyor mu bak.
2. Tespit zayıfsa (aydınlık zemin): threshold / thMin / çizgi / uzunluk — **sayım eşiği değil**.
3. **8000:** Pano / TV / detay ile süre ve adet.
4. Kalıp: Telegram bot veya manuel; otomatik öneri yok.

---

## 6. Dosyalar

| Konu | Dosya |
|------|--------|
| Zigzag SM | `backend/app/vision/state_machine.py` |
| Orchestrator | `backend/app/vision/orchestrator.py` |
| Matcher (kapalı) | `backend/app/services/mold_matcher.py` + `config.auto_mold_matching` |
| Admin UI | `backend/admin_static/index.html` |
| Deploy | `deploy/README-DEPLOY.md` |

---

*Son güncelleme: matcher/analitik kapalı gerçekliğine göre düzeltilmiş kılavuz (Eyl 2026).*
