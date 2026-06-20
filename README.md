# Injection Monitor

Endüstriyel enjeksiyon makinesi izleme: RTSP + OpenCV (ROI / parlak yansıtıcı), FastAPI, SQLite, WebSocket, React + Tailwind + Recharts.

**Python 3.10 veya üzeri** gerekir (SQLAlchemy 2.0 `Mapped[...]` / birleşik tip sözdizimi). Raspberry Pi OS Bookworm uyumludur.

## Hızlı başlangıç

```bash
cd backend && pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000
cd frontend && npm install && npm run build
```

Üretimde önce frontend derleyin (`Node.js 18+`); bu makinede `npm` yoksa derlemeyi başka bir bilgisayarda yapıp `frontend/dist` klasörünü Pi’ye kopyalayın.

Portlar:

- `8000`: Izleme paneli
- `8080`: Ayar / Kalibrasyon paneli (RTSP + ROI cizim)

Ayrıntılı Pi kurulumu: [deploy/README-DEPLOY.md](deploy/README-DEPLOY.md).

## Telegram bot kullanimi (QR akisi)

Bu projede operatör akisi Telegram bot ile ilerler (`@Alfamold_bot`):

1. `/start` ile menuyu ac
2. `📌 Kalip Ata`:
   - once makine QR foto (`MACHINE:3`, `MAKINE:3`, `M:3`)
   - sonra kalip QR foto (`MOLD:042`, `KALIP:042`, `K:042`)
3. `➕ Kalip Uret` (yalniz seviye 1):
   - kalip QR foto gonder
   - kalip adini yaz
4. `❌ Iptal` veya `/iptal` ile oturumu sifirla

Notlar:

- `KALIP URET` gibi buyuk harf yazimlari da desteklenir.
- QR okunmazsa daha yakin/net fotografla tekrar gonderin.
- Ayni anda tek bot instance calistirin; cift instance Telegram `409 Conflict` hatasi uretir.

## Guvenlik ve paylasim

- GitHub'a push etmeden once gizli verileri kontrol edin: bot token, sifre, API key, private key.
- `.env`, ham token metinleri, cihaz sifreleri ve SSH private key dosyalarini commitlemeyin.
- Log veya ekran goruntusu paylasirken token/sifre kisimlarini maskeleyin.
