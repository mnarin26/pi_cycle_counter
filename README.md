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
