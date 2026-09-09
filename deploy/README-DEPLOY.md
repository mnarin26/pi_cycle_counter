# Injection Monitor — Raspberry Pi dağıtımı

Bu belge **canlı rsp3b stack**’ine göredir (`main` dalı). Clone = yazılım; kamera/çizgi/şifreler ayrıca kurulur.

## Mimari

```
Kameralar (AP Wi‑Fi) ──RTSP──► Pi:8000 (vision + zigzag sayım + DB)
                                    ▲
Tarayıcı / TV ──────────────────────┤
                                    │
Tarayıcı admin ──► Pi:8080 ──proxy──┘
```

- **AP (ör. 192.168.4.1):** paneller + SSH (fabrika içi)
- **eth0 / Tailscale:** internet + uzaktan erişim (`http://100.x:8000` / `:8080`)
- Vision **yalnızca 8000** sürecindedir. 8080 ayrı process (~90 MB RAM); CPU yükü asıl 8000’dedir.

## Kurulum

```bash
cd /home/pi
git clone https://github.com/mnarin26/pi_cycle_counter.git injection-monitor
cd injection-monitor/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend (Node 18+ olan makinede derleyip dist kopyalanabilir)
cd ../frontend && npm ci && npm run build
```

İsteğe bağlı `backend/.env` (git’e ekleme):

```
CORS_ORIGINS=http://192.168.4.1:8000,http://100.x.y.z:8000
AUTO_MOLD_MATCHING=false
```

Veri:

- SQLite: `backend/data/injection.db` (ilk çalışmada oluşur; **repoda yok**)
- Loglar: `backend/logs/` (repoda yok)

## Servisleri başlatma

### systemd (önerilen, boot’ta otomatik)

```bash
sudo cp deploy/systemd/injection-monitor.service /etc/systemd/system/
sudo cp deploy/systemd/injection-monitor-admin.service /etc/systemd/system/
sudo cp deploy/systemd/injection-monitor-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now injection-monitor.service
sudo systemctl enable --now injection-monitor-admin.service
sudo systemctl enable --now injection-monitor-bot.service   # Telegram kullanılacaksa
```

Unit içindeki `User=` / yolları kendi dizinine göre düzenle.

### Elle (geçici)

```bash
cd /home/pi/injection-monitor/backend
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 >> logs/main.log 2>&1 &
nohup .venv/bin/uvicorn admin_app:app --host 0.0.0.0 --port 8080 >> logs/admin.log 2>&1 &
```

Not: Canlı rsp3b’de bir süre systemd unit’ler disable iken elle uvicorn kullanıldı; üretimde enable etmek reboot sonrası panelleri ayağa kaldırır.

## İlk kalibrasyon (yeni Pi / yeni kamera)

1. **8080** → giriş (süper şifre veya bottan günlük şifre).
2. Kamera: RTSP, `target_width` (canlıda sıkça **480**), FPS, aktif.
3. Makine: kameraya bağla → sarı **takip çizgisini** çiz → **Çizgi Kaydet**.
4. Gerekirse reflektör uzunluk kalibrasyonu.
5. **8000** Canlı / Pano: `pos` ve OPEN/CLOSED akıyor mu bak.
6. Seed referansı: [seed/README.md](seed/README.md) — aynı fabrika kameraları için yol gösterir; şifreleri yeniden gir.

## Sayım motoru (canlı)

- Dosya: `backend/app/vision/state_machine.py` (peak/trough zigzag).
- Tam çevrim: **A→B→A** (`cycle_tracker`).
- Global: `jump_abs≈0.30` (ışık teleport), `min_prominence≈0.12` (min salınım).
- Eski dwell / `move_eps` “uçta bekle” sayımı **kullanılmaz** (alanlar UI uyumu için kalabilir).

## Wi‑Fi AP

Fabrika AP (hostapd) kamera ağı içindir. `192.168.4.1` reboot sonrası düşerse `cyw-ap-addr` benzeri bir unit ile IP geri yüklenmeli (rsp3b’de böyle kuruldu). Ayrıntı sahaya özeldir; SSID/şifre **8080 → Wi‑Fi AP**.

## Başka Pi’ye taşıma — gerçekçi beklenti

| Taşınır (git) | Taşınmaz / yeniden |
|---------------|-------------------|
| Kod, admin UI, frontend | `injection.db` (çevrim geçmişi, oturumlar) |
| systemd unit şablonları | RTSP kullanıcı/şifre |
| Seed JSON (maskeli ayar) | Telegram token, panel şifreleri |
| | Çizgi/ROI (kamera açısı değişince) |
| | Tailscale / AP IP |

Yani: **yazılım çalışır**; fabrika kalibrasyonu ve sırlar ikinci adımdır.

## Geliştirme (PC)

```bash
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# ayrı terminal
uvicorn admin_app:app --reload --host 0.0.0.0 --port 8080
cd frontend && npm run dev   # Vite → /api ve /ws proxy 8000
```
