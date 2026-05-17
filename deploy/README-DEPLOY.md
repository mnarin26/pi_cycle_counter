# Injection Monitor — Raspberry Pi dağıtımı

## Mimari

- **pi-wifi (WPA2, sabit IP):**
  - Izleme paneli: `http://<AP_IP>:8000`
  - Ayar/Kalibrasyon paneli: `http://<AP_IP>:8080`
  - SSH: `ssh pi@<AP_IP>`
- **eth0 (DHCP):** İnternet çıkışı; Tailscale güncelleme ve uzaktan erişim.
- **Tailscale:**
  - Izleme paneli: `http://<magicdns veya 100.x>:8000`
  - Ayar/Kalibrasyon paneli: `http://<magicdns veya 100.x>:8080`
  - SSH aynı Pi üzerindeki servislere gider.

Uygulama iki porttan dinler:

- `8000`: Ana izleme paneli (`app.main`)
- `8080`: Ayar/kalibrasyon paneli (`admin_app`) — ROI cizim ve RTSP ayarlari buradan yapilir.

## Kurulum (özet)

```bash
cd /home/pi
git clone <repo> injection-monitor   # veya rsync/scp ile kopyalayın
cd injection-monitor/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ../frontend && npm ci && npm run build
```

Veritabanı ve loglar:

- SQLite: `backend/data/injection.db` (çalışma dizinine göre oluşur)
- CSV: `backend/logs/machine_<id>/YYYY-MM-DD.csv`

## systemd

```bash
sudo cp deploy/systemd/injection-monitor.service /etc/systemd/system/
sudo cp deploy/systemd/injection-monitor-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now injection-monitor.service
sudo systemctl enable --now injection-monitor-admin.service
```

`User=` ve yolları kendi kullanıcı dizininize göre düzenleyin.

## Ortam değişkenleri (isteğe bağlı)

`backend/.env` örneği (git’e eklemeyin):

```
CORS_ORIGINS=http://192.168.4.1:8000,http://100.x.y.z:8000
```

RTSP, ROI, threshold ve axis ayarlari `8080` panelinden yapilir; gizli bilgileri repoda tutmayin.

## Güvenlik

- SSH için parola yerine **anahtar** kullanın.
- Fabrika Wi-Fi WPA2 trafiği kablosuz gizlilik sağlar; uygulama katmanında ileride basit web kimliği eklenebilir.

## Geliştirme (PC)

```bash
# Terminal 1
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2
cd frontend && npm install && npm run dev
```

Vite `5173` portunda `/api` ve `/ws` isteklerini `8000`e proxyler.
