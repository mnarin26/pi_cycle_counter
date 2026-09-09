# Injection Monitor (pi_cycle_counter)

Raspberry Pi üzerinde enjeksiyon makinesi **çevrim sayımı**: RTSP kameralar + 1D çizgi profili (reflektör), FastAPI, SQLite, WebSocket, React paneli + 8080 admin.

**Kaynak dal:** `main` = rsp3b fabrika Pi’deki canlı yazılım (Eyl 2026 zigzag stack).  
**Python:** 3.10+ (Bookworm uyumlu).

## Ne yapar?

1. Kameradan sarı **takip çizgisi** boyunca reflektör konumunu okur (`position_01`, 0→1).
2. **Zigzag (peak/trough)** durum makinesi OPEN/CLOSED üretir.
3. Tam tur **A → B → A** = 1 çevrim (yarım strok sayılmaz).
4. Işık sıçraması gibi dik teleporterler `jump_abs` ile tutulur; küçük titreme `min_prominence` ile yok sayılır.

Varsayılan global eşikler (canlı): `jump_abs=0.30`, `min_prominence=0.12`.

## Portlar

| Port | Süreç | Rol |
|------|--------|-----|
| **8000** | `app.main` | Vision + sayım + izleme paneli (pano/TV/detay) + API + WS |
| **8080** | `admin_app` | Kalibrasyon, **canlı kamera görüntüsü**, çizgi, teshis (çoğu API 8000’e proxy) |

`/tv` oturumsuz kalabilir. **Otomatik kalıp matcher kapalı** (`AUTO_MOLD_MATCHING=false`); kalıp Telegram/manuel. Analitik sayfası yok.

## Hızlı başlangıç (PC)

```bash
git clone https://github.com/mnarin26/pi_cycle_counter.git
cd pi_cycle_counter/backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Ayrı terminalde admin:

```bash
cd backend && source .venv/bin/activate
uvicorn admin_app:app --host 0.0.0.0 --port 8080
```

Frontend üretim derlemesi (`Node 18+`):

```bash
cd frontend && npm ci && npm run build
```

Pi kurulumu: [deploy/README-DEPLOY.md](deploy/README-DEPLOY.md).  
Kullanım: [docs/KULLANIM-KILAVUZU.md](docs/KULLANIM-KILAVUZU.md).  
Başka Pi / ayar seed: [deploy/seed/README.md](deploy/seed/README.md).

## Telegram bot

Operatör kalıp atama/üretim QR ile (`backend/app/bot`). Token ve operatörler **8080** Telegram ayarlarından; git’e token koyulmaz.

Akış özeti: `/start` → Kalıp Ata (makine QR → kalıp QR) → seviye 1 ise Kalıp Üret.  
QR: `MACHINE:3` / `MOLD:042` (eşdeğer `MAKINE`/`KALIP`/`M`/`K`).

## Güvenlik notları

- `backend/.env`, Telegram token, RTSP şifreleri **commit edilmez**.
- Süper / günlük panel şifreleri ve `injection.db` Pi’ye özeldir; clone tek başına fabrika verisini taşımaz.
- Seed JSON içindeki RTSP kimlikleri maskelidir (`USER:PASS`).

## Repo yapısı (özet)

```
backend/app/vision/     # RTSP, çizgi profili, zigzag SM, orchestrator
backend/admin_static/   # 8080 tek sayfa UI
frontend/               # 8000 React paneli
deploy/                 # systemd + seed
docs/                   # kullanım kılavuzu
```
