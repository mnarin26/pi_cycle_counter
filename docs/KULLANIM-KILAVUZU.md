# Injection Monitor — Kullanım Kılavuzu

Bu belge, repodaki **gerçek kod** (`frontend`, `backend`, `admin_static`) esas alınarak yazılmıştır. Amaç: her sayfa, buton ve temel API’nin ne işe yaradığını netleştirmek.

---

## 1. Genel yapı

| Bileşen | Port | Dosya / giriş |
|--------|------|----------------|
| **İzleme paneli** | `8000` | `backend/app/main.py` + `frontend/dist` |
| **Admin / kalibrasyon** | `8080` | `backend/admin_app.py` + `backend/admin_static/index.html` |

- Vision (kamera okuma, reflektör tespiti, döngü sayımı) yalnızca **8000** sürecinde çalışır.
- Admin paneli çoğu ayarı **8000 API** üzerinden okur/yazar; Pi saati, Wi‑Fi AP ve üretim sıfırlama gibi bazı işlemler **8080** üzerinden yapılır (CORS ve ayrı servis için).

Canlı veri: WebSocket `ws://<host>:8000/ws` — mesaj tipi `snapshot`, gövde `data.machines` / `data.cameras`.

---

## 2. İzleme paneli (port 8000)

Rotalar: `frontend/src/App.tsx`

| URL | Sayfa | Dosya |
|-----|--------|--------|
| `/` | Pano | `pages/Dashboard.tsx` |
| `/tv` | TV ekranı (tam ekran, menüsüz) | `pages/TvWall.tsx` |
| `/live` | Canlı görünüm | `pages/LiveView.tsx` |
| `/machines/:id` | Makine detay | `pages/MachineDetail.tsx` |
| `/molds` | Kalıplar | `pages/Molds.tsx` |
| `/analytics` | Analitik | `pages/Analytics.tsx` |
| `/events` | Olaylar | `pages/Events.tsx` |

**Not:** `pages/Settings.tsx` dosyası repoda var ama `App.tsx` içinde **route tanımlı değil**; menüde görünmez. Kamera/ayar için pratikte **8080 admin** kullanılır.

Üst çubuk (`Layout.tsx`): **WS** bağlantı durumu, toplam kamera **FPS**, vision **işlem ms** (`cpu_proxy`).

---

### 2.1 Pano (`/`)

**Ne yapar:** `state !== "DISABLED"` olan makineleri kart grid’inde gösterir. Veri kaynağı: canlı WebSocket snapshot.

**Kart (`MachineCard.tsx`)** — tıklanınca `/machines/{id}`:

| Alan | Anlamı |
|------|--------|
| Büyük yazı | Canlı durum: `OPEN`, `CLOSED`, `MOVING`, `UNKNOWN`, `DISABLED` |
| **Döngü:** | Etiket “Döngü” olsa da değer **`cycle_time_last`** (son tamamlanan tur süresi, saniye) — **sayım değil** |
| Kalıp | `mold_name` (atanmış kalıp adı) |
| Poz | Reflektör pozisyonu 0..1 (`position_01`) |
| Güven | Tespit güveni 0..100% |

---

### 2.2 TV ekranı (`/tv`)

Tam ekran üretim özeti; `localStorage` ile makine seçimi (`tv_selected_machine_ids`). Board verisi **45 saniyede bir** `GET /api/analytics/tv_board` ile yenilenir.

**Üst başlık**

| Öğe | Açıklama |
|-----|----------|
| Saat | İstanbul saat dilimi |
| WS | Canlı snapshot bağlantısı |
| **Makineleri seç** | Modal: hangi makineler TV’de listelensin |
| ← Pano | Ana menüye dönüş |

**Makine satırı (her seçili makine)**

| Alan | Kaynak | Açıklama |
|------|--------|----------|
| Durum rozeti | WS `state` | AÇIK / KAPALI / HAREKET / … |
| **döngü bugün** | `total_cycle_count` | Bugün **sayılan** (`is_counted=true`) tüm döngüler |
| Aktif kalıp | `active_mold_name` | Bugünkü son sayılan döngünün kalıbı |
| Kalıp döngüsü | `summary.cycle_count` | Sadece **aktif kalıba** ait bugünkü sayılan döngüler; kalıp yoksa **—** |
| Ort. süre / Min-Max | `summary` | Aktif kalıp istatistiği |
| **Son döngü** | WS `cycle_time_last` | Canlı son tur süresi (DB değil) |
| Saatlik grafik | `hourly` | Bugün saat bazlı döngü adedi (İstanbul saati) |

**Modal butonları:** Kaydet (seçimi sakla), Tümünü seç, İptal.

---

### 2.3 Canlı (`/live`)

| Kontrol | İşlev |
|---------|--------|
| **Kamera** seçici | Hangi kameranın JPEG önizlemesi gösterilecek |
| Görüntü | `GET /api/cameras/{id}/snapshot.jpg` — ~500 ms’de bir yenilenir |
| Yeşil nokta | Aynı kameraya bağlı, `centroid` olan aktif makinelerin reflektör merkezi |
| Alt şerit | Kamera id, `status`, `fps` |
| Alt liste | O kameradaki aktif makineler: ad, durum, kalıp |

---

### 2.4 Makine detay (`/machines/:id`)

**Veri:** `GET /api/analytics/machine_dashboard` + canlı WS + `GET /api/machines/{id}`.

**Üst kontroller**

| Kontrol | İşlev |
|---------|--------|
| Aralık | `daily` / `weekly` / `monthly` / `yearly` |
| Başlangıç / Bitiş | `datetime-local` — İstanbul (+03:00) olarak API’ye UTC çevrilir |
| Geçmiş işleme | `missing_only` veya `reprocess` → `POST /api/machines/{id}/replay-mold-matching` |
| **Kalıp eşleştirmesini çalıştır** | Seçilen aralıkta kalıp matcher’ı yeniden çalıştırır (en fazla 7 gün) |
| **Özet CSV indir** | `GET /api/machines/{id}/export?kind=summary` |
| **Döngü CSV indir** | `GET /api/machines/{id}/export?kind=cycles` |
| Panoya dön | `/` |

**Özet kutuları**

| Kutu | Anlamı |
|------|--------|
| Canlı durum | WS `state` |
| Toplam döngü | Seçilen aralıkta `is_counted=true` döngü sayısı |
| Aktif kalıp | Son sayılan döngüdeki kalıp + o kalıbın döngü adedi |
| Ort. / Son döngü | Özet istatistik (aktif kalıp varsa öncelik onda) |

**Çalışma analizi grafiği**

- Mod: `chart_mode` = `cycles` (zigzag çizgi; döngü süreleri, renk = kalıp).
- **Ekran çözünürlüğü:** grafikte kaydırma/zoom için zaman penceresi çözünürlüğü.
- Uzun aralıklarda lazy yükleme: `GET /api/analytics/cycles_viewport` ile ek döngüler çekilir.
- Grafik üzerinde gezinme: imleç yakınındaki döngü tooltip’te gösterilir; 2 dk’dan uzaksa “Bu saatte döngü yok”.

**Diğer bölümler**

- Kalıp dağılımı tablosu, canlı telemetri (peak/bg, prominence, segment len, FPS).
- Makine ayarları (salt okunur özet: debounce, stability, threshold).
- **Son 20 döngü** — yalnız `is_counted=true` kayıtlar.
- **Son olaylar** — `events` listesi (renkli tip).

**Replay modları**

- **Sadece boş olanlar:** `mold_id` dolu kayıtlara dokunmaz.
- **Aralığı yeniden işle:** Seçili penceredeki kalıp atamaları silinip matcher baştan çalışır.

---

### 2.5 Kalıplar (`/molds`)

**Kalıp listesi** — `GET /api/molds`, kart başına:

| Buton | API | Açıklama |
|-------|-----|----------|
| Düzenle | `PATCH /api/molds/{id}` | Ad, durum, ort. süre, tolerans, stabilite eşiği |
| Sil | `DELETE /api/molds/{id}` | Kalıbı siler; geçmiş döngülerde kalıp bağlantısı kalkar |
| Ad ver | `POST /api/molds/{id}/name` | `candidate` kalıba isim verir |
| Yok say | `POST /api/molds/{id}/ignore` | Kalıbı yok sayar |

Kalıp alanları: `candidate` / `active` / `ignored`; **ortalama döngü süresi**, **eşleşme toleransı (±)**, **stabilite eşiği** (boş = otomatik %5).

**Aralık filtresi:** Günlük/Haftalık/Aylık/Yıllık + isteğe bağlı başlangıç/bitiş → **Aralığı Uygula** → `GET /api/molds/usage`.

**Kalıp bazlı makine üretim detayı:** Her kalıp için makine başına döngü adedi; **Özet CSV** / **Döngü CSV** → `GET /api/molds/export?mold_id=...&kind=...` (**mold_id zorunlu**).

---

### 2.6 Analitik (`/analytics`)

Fabrika geneli (makine filtresi yok):

| Kontrol | İşlev |
|---------|--------|
| daily / weekly / monthly / yearly | `GET /api/analytics/summary?range=...` |
| Çizgi grafik | `GET /api/analytics/cycles_series?limit=400` — son döngülerin süreleri |
| Histogram | `GET /api/analytics/histogram?bins=16` |
| **Dışa aktar (JSON)** | Tarayıcıda `series` + `hist` indirir (sunucuya kaydetmez) |

---

### 2.7 Olaylar (`/events`)

`GET /api/events?limit=300` — son olaylar tablosu (zaman, tip, makine, JSON payload).

Yaygın olay tipleri (kodda üretilenler):

| Tip | Anlamı (kısa) |
|-----|----------------|
| `mold_auto_matched` | Uzun duruş sonrası veya üretimde kalıp otomatik eşleşti |
| `mold_suggestion` | Yeni isimsiz kalıp önerisi |
| `mold_unknown_prompt` | Kalıp belirsiz; onay gerekir |
| `mold_change_likely` | Kalıp değişimi olası |
| `no_movement` | Hareket zaman aşımı |
| `abnormal_cycle` | Döngü süresi kalıp ortalamasından çok sapma |
| `cycle_outlier`, `reflector_weak` | Seed/test veya düşük kalite uyarıları |

---

## 3. Admin paneli (port 8080)

Tek sayfa: `backend/admin_static/index.html`. API: çoğu istek `http://<host>:8000`, yerel `8080` uçları: sistem saati, Wi‑Fi AP, kamera OSD saati, üretim sıfırlama.

### 3.1 Makineler (canlı) tablosu

| Sütun | Açıklama |
|-------|----------|
| ID / Ad / Kamera | Makine ve bağlı kamera |
| Eşik | `fixed` → `prom≥{threshold_min}`; `adaptive` → canlı `threshold_active_min` |
| Sinyal (peak/bg) | Canlı peak, background, Δprominence, segment uzunluğu |

WS + her 1 sn HTTP yedek: `GET /api/live/snapshot`.

---

### 3.2 Raspberry Pi saati

| Buton / alan | İşlev |
|--------------|--------|
| Saat dilimi | `POST /api/system/time` (8080) |
| Tarih/saat + **Saati Ayarla** | Manuel saat; NTP kapanır |
| **NTP Aç** | `POST /api/system/time/ntp` |
| **Saati Yenile** | `GET /api/system/time` |

Döngü kayıtları Pi UTC saatini kullanır. Kamera OSD saati ayrı — **Kamera OSD Saatini Pi ile Eşitle**.

---

### 3.3 Fabrika Wi‑Fi (Access Point)

| Buton | İşlev |
|-------|--------|
| SSID / şifre | `POST /api/system/wifi-ap` (8080) — hostapd yapılandırması |
| **SSID / Şifreyi Kaydet** | AP yeniden başlatılabilir; bağlantı kopabilir |
| **Yenile** | `GET /api/system/wifi-ap` |

---

### 3.4 Kamera ayarları

| Buton / alan | API | Açıklama |
|--------------|-----|----------|
| RTSP URL, genişlik, FPS, Aktif | `PATCH /api/cameras/{id}` | Vision worker ~2 sn içinde günceller |
| **Kamera Ayarını Kaydet** | Yukarıdaki patch |
| **Kamera Test** | `POST /api/cameras/{id}/test` — RTSP tanımlı mı |
| **Kamera OSD Saatini Pi ile Eşitle** | `POST /api/cameras/{id}/sync-time` (8080) |

---

### 3.5 Makine parametreleri

| Alan | Anlamı |
|------|--------|
| Görüntü kamerası | Makinenin hangi RTSP akışından işlendiği |
| Threshold modu | `fixed` veya `adaptive` (prominence eşiği) |
| Prominence / offset | Reflektör parlaklık eşiği |
| Çizgi kalınlığı | 1D profil örnekleme bandı (px) |
| Reflektör uzunluk min/max | Segment uzunluğu filtresi |
| Open/Closed 1D | Eski kalibrasyon alanları (durum makinesi hareket yönüne göre OPEN/CLOSED atar) |
| Debounce / Sabit onay / Görünmezlik | Hareket ve bekleme zamanlaması |
| **Makine Parametre Kaydet** | `PATCH /api/machines/{id}` |
| **Uzunluk Kalibrasyon Başlat** | `POST /api/calibration/machines/{id}/learn_reflector_length` — süre boyunca reflektörü çizgi üzerinde gezdirin |
| **Sayım ve Kalıp Verisini Sıfırla** | `POST /api/settings/maintenance/reset-production-data` (8080) — tüm cycles, events, molds siler; makine/kamera ayarı kalır |
| **Snapshot Yenile** | JPEG önizlemeyi yeniler |

---

### 3.6 Takip çizgisi ayarı (canvas)

Sarı **takip çizgisi** + kalınlık bandı vision’da kullanılır. ROI dikdörtgeni sadece hizalama içindir.

| Etkileşim | İşlev |
|-----------|--------|
| Sürükleyerek çiz | Yeni ROI dikdörtgeni |
| Köşe / taşı / döndür | ROI düzenleme |
| Zoom slider / +/- | Görüntü yakınlaştırma |
| **Çizimi Temizle** | ROI’yi siler |
| **Çizgi Kaydet** | `POST /api/machines/{id}/roi` + `axis_p0` / `axis_p1` (normalize 0..1) |

**Yön (axis):** Sol→Sağ veya Sağ→Sol — OPEN/CLOSED yorumu.

Görüntü kamerası değişince otomatik `PATCH` ile kaydedilir.

---

## 4. Döngü sayımı nasıl çalışır? (kısa, kod tabanlı)

1. **Vision** (`orchestrator.py`): RTSP kareden 1D çizgi profili → reflektör bulunursa `position_01`.
2. **Durum makinesi** (`state_machine.py`): Hareket yönüne göre `OPEN` / `CLOSED` / `MOVING`; sabit kalma `stability_confirm_ms` (varsayılan ~500 ms).
3. **Döngü sayacı** (`cycle_tracker.py`): Tam tur = örn. OPEN → CLOSED → OPEN (veya tersi); süre döner.
4. **DB** (`mold_matcher.py`): Döngü kaydı + kalıp eşleştirme; `is_counted` false olabilir (`post_stop_pending`, `unknown_or_mold_change`, vb.).

**Panelde görünen sayılar** çoğunlukla `is_counted = true` filtreli kayıtlardır.

---

## 5. API özeti (port 8000)

| Önek | Açıklama |
|------|----------|
| `GET /api/health` | Sağlık kontrolü |
| `GET /api/live/snapshot` | Canlı snapshot (WS ile aynı yapı) |
| `WS /ws` | Canlı snapshot yayını |
| `GET/POST /api/cameras/...` | Kamera listesi, güncelleme, test, JPEG |
| `GET/PATCH /api/machines/...` | Makine CRUD, ROI, export, replay |
| `GET/PATCH/DELETE /api/molds/...` | Kalıp yönetimi, usage, export |
| `GET /api/analytics/...` | Özet, dashboard, TV board, histogram |
| `GET /api/events` | Olay listesi |
| `GET/PATCH /api/settings/{key}` | JSON ayarlar |
| `POST /api/settings/maintenance/reset-production-data` | Üretim geçmişi sıfırlama (8000’de de var) |
| `POST /api/calibration/machines/{id}/learn_reflector_length` | Reflektör uzunluk öğrenme |
| `POST /api/debug/fake_cycle` | **Geliştirme:** sahte döngü kuyruğa atar — üretimde kapatılmalı |

**8080 ek uçlar:** `admin_app.py` — sistem saati, NTP, Wi‑Fi AP, kamera sync-time, reset-production-data.

---

## 6. Tipik kurulum akışı

1. **8080:** Kamera RTSP + aktif; makine aktif; takip çizgisini çiz → **Çizgi Kaydet**.
2. Threshold / uzunluk kalibrasyonu; gerekirse **Uzunluk Kalibrasyon**.
3. **8000 Canlı:** reflektör ve yeşil nokta görünüyor mu kontrol et.
4. Üretim başlayınca **Pano / TV / Makine detay** üzerinden döngü ve kalıp takibi.
5. **Kalıplar** sayfasında candidate kalıplara ad ver veya yok say.

---

## 7. Bilinen ayrımlar (karışıklık önleme)

| Konu | Doğru anlama |
|------|----------------|
| Pano kartında “Döngü” | Son tur **süresi**, adet değil |
| TV “Son döngü” | Canlı süre (WS) |
| TV “Kalıp döngüsü” | Aktif kalıba bağlı DB sayımı; kalıp yoksa boş |
| `post_stop_pending` | Uzun duruş sonrası öğrenme; sayım politikası sürüme göre değişebilir |
| Settings sayfası (React) | Route yok — kullanılmıyor |
| Sanal veri | `tools/seed_*.py` manuel script; canlıda çalışmaz |

---

## 8. İlgili dosyalar

| Konu | Dosya |
|------|--------|
| Rotalar | `frontend/src/App.tsx` |
| Canlı veri | `frontend/src/hooks/useLiveSnapshot.tsx` |
| Vision | `backend/app/vision/orchestrator.py`, `line_pipeline.py`, `state_machine.py` |
| Kalıp mantığı | `backend/app/services/mold_matcher.py` |
| TV API | `backend/app/api/routers/analytics.py` → `tv_board` |
| Deploy | `deploy/README-DEPLOY.md` |

---

## 9. Gelecek özellikler ve planlanan güncellemeler

> **Bu bölüm yol haritasıdır.** Henüz kodda yoktur; öncelik ve tarih kesin değildir. Hayal gücü + sahadaki geri bildirimlerle birlikte düşünülmüştür.

### 9.1 Yakın vade (teknik borç ve net iyileştirmeler)

| Özellik | Amaç |
|---------|------|
| Pano kartı etiket düzeltmesi | “Döngü” yerine **Son tur süresi** + ayrı satırda **Bugünkü adet** — karışıklığı bitirmek |
| TV kalıp metrikleri fallback | Aktif kalıp yokken bugünkü toplam döngü / son bilinen kalıp özeti göstermek |
| `Settings.tsx` entegrasyonu veya kaldırma | 8000’de basit ayar sayfası ya da dosyayı temizlemek; admin ile çift ekranı azaltmak |
| `POST /api/debug/fake_cycle` kapatma | Üretimde env ile devre dışı; yalnız geliştirme modunda |
| Sentetik veri temizlik aracı | `tools/cleanup_synthetic_cycles.py` repoda; makine bazlı “gelecek tarihli / sıfır süreli” kayıt silme (admin butonu opsiyonel) |
| Admin canlı tablo genişletmesi | Son döngü süresi, bugünkü sayım, `state`, kalıp adı — tek bakışta teşhis |
| Otomatik eşik önerisi | `prominence < threshold` uyarısı + “eşiği X’e indir” tek tık önerisi |

### 9.2 Orta vade (operasyon ve raporlama)

| Özellik | Amaç |
|---------|------|
| Vardiya / plan tanımı | Sabah–öğle–gece vardiyası; TV ve raporlarda vardiya bazlı döngü |
| E-posta / Telegram / Teams bildirimi | `no_movement`, `abnormal_cycle`, kamera kopması, günlük özet |
| PDF / Excel rapor | Günlük fabrika özeti; müdür masasına otomatik gönderim |
| Çoklu fabrika / site | Tek panele birden fazla Pi veya merkezi sunucu; site seçici |
| Kullanıcı rolleri | Operatör (salt okunur), kalıp sorumlusu, admin; basit PIN veya LDAP |
| Kalıp QR / barkod | Kalıp değişiminde telefonla okut → `mold_id` anında bağlama |
| Döngü anında kısa video klibi | Şüpheli döngüde RTSP’ten 5 sn kesit; olay kaydına link |
| Gelişmiş replay UI | Makine detayda “bu döngüyü videoda göster” zaman çizelgesi |
| OEE benzeri KPI | Kullanılabilirlik × performans × kalite (basitleştirilmiş, döngü süresi sapmasına dayalı) |
| Hedef çevrim süresi | Kalıba hedef süre; TV’de yeşil/sarı/kırmızı bant |
| Toplu makine kalibrasyonu | Aynı kamera modeli için parametre şablonu kopyala |

### 9.3 Uzun vade (vizyon)

| Özellik | Amaç |
|---------|------|
| Edge AI kalıp tanıma | Reflektör + isteğe bağlı görüntü ile kalıp sınıflandırma; manuel adlandırmayı azaltmak |
| Tahminsel bakım | Döngü süresi trendi ve titreşim benzeri sinyallerle “servis öner” |
| ERP / MES entegrasyonu | SAP, Logo, Netsis vb. için iş emri ↔ kalıp ↔ üretim adedi senkronu |
| Mobil PWA | Telefonda pano, push bildirim, vardiya özeti |
| Sesli TV modu | Kritik olayda Türkçe sesli uyarı (büyük ekran atölye) |
| Çok dilli arayüz | TR / EN / DE fabrika personeli |
| Bulut yedekleme | SQLite → günlük şifreli yedek S3 / Nextcloud; felaket kurtarma sihirbazı |
| Karanlık / yüksek kontrast TV teması | Gece vardiyası için göz yormayan tam ekran |
| Federasyon paneli | 10+ makine, 3+ hat; harita ve ısı haritası (duruş yoğunluğu) |
| API anahtarı ve webhook | Dış sistemlerin döngü bitişinde anlık POST alması |
| Simülasyon modu | Eğitim için sanal makine (mevcut seed script’lerin güvenli UI sürümü) |
| Donanım genişlemesi | GPIO ile pres sinyali, ikinci reflektör, aydınlatma kontrolü |

### 9.4 Kalite ve güvenlik

| Özellik | Amaç |
|---------|------|
| Denetim günlüğü (audit log) | Kim hangi kalıbı sildi, eşiği değiştirdi, veriyi sıfırladı |
| Yedekleme öncesi onay | “Sayım sıfırla” için iki adımlı doğrulama + otomatik DB dump |
| TLS / ters vekil | Fabrika ağında HTTPS; Let’s Encrypt veya kurumsal sertifika |
| Veri saklama politikası UI | Admin’den “90 günden eski döngüleri arşivle/sil” (arka planda `data_retention` ile uyumlu) |

### 9.5 Nasıl takip edilir?

- Bu liste **öncelik sırası değildir**; ihtiyaç ve sahadaki aciliyet belirler.
- Bir madde hayata geçince **Bölüm 2–8** güncellenir; buradan ilgili satır silinir veya “✓ Tamamlandı” notu düşülür.
- Öneri / acil ihtiyaç: GitHub Issues veya fabrika sorumlusu ile netleştirilir.

---

*Son güncelleme: repodaki `main` dalı koduna göre (admin SessionLocal düzeltmesi, TV/pano ayrımları, mold export `mold_id` zorunlu). Bölüm 9 yol haritasıdır — henüz uygulanmamış özellikler içerir.*
