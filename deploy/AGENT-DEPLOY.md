# Agent deploy playbook (Pi / rsp3b)

Cloud ajanlar kod yazar; **Pi’ye yükleme yalnızca yerel (SSH’li) ajan** yapar.
Bu dosya her iki ajanın da bozmadan deploy etmesi içindir.

Canlı host: **rsp3b** · Repo: `/home/pi/injection-monitor` · GitHub: `mnarin26/pi_cycle_counter`

---

## Rol ayrımı

| Ajan | Yapabilir | Yapamaz |
|------|-----------|---------|
| **Cloud ajan** | Kod, PR, review, aşağıdaki `DEPLOY` yorumunu yazmak | Pi SSH/SCP, `systemctl`, canlı DB’ye dokunmak |
| **Yerel deploy ajan** | Backup, SCP, ilgili servis restart, smoke test | Kör `git pull --force`, repo-wide sync, kendi başına büyük refactor |

Cloud ajan Pi’ye “deploy etmeye” çalışmasın. Yerel ajan cloud’un her commit’ini körce basmasın.

---

## Mimari (karıştırma)

```
Kameralar ──RTSP──► :8000  injection-monitor.service
                         ▲   (vision + zigzag sayım + DB — asıl CPU)
Tarayıcı admin ──► :8080 ──proxy──┘  injection-monitor-admin.service
Telegram bot ──► injection-monitor-bot.service   (kameraya bağımlı değil)
```

| Port / unit | Ne |
|-------------|-----|
| `8000` / `injection-monitor.service` | Vision + API + SQLite |
| `8080` / `injection-monitor-admin.service` | Admin UI + 8000 proxy |
| bot / `injection-monitor-bot.service` | Telegram long-poll |

- Vision **yalnızca 8000** sürecindedir. Admin HTML için 8000 restart gerekmez; vision fix için 8080 restart yetmez.
- **Asla overwrite etme:** `backend/data/injection.db`, `backend/logs/`, `backend/.env`, canlı ROI/kamera ayarları.
- İlk kurulum / systemd enable: [README-DEPLOY.md](./README-DEPLOY.md).

---

## Cloud → yerel: `DEPLOY` yorum formatı

Yerel ajan yalnızca bu bloktaki dosya ve servislere dokunur:

```text
DEPLOY
host: rsp3b
files:
  - local: backend/app/vision/orchestrator.py
    remote: /home/pi/injection-monitor/backend/app/vision/orchestrator.py
  - local: backend/admin_static/index.html
    remote: /home/pi/injection-monitor/backend/admin_static/index.html
restart:
  - injection-monitor.service
  - injection-monitor-admin.service
verify:
  - systemctl is-active
  - live snapshot process_ms / keeping_up
notes: hot-path touched — check process_ms
```

Belirsiz “deploy et / her şeyi güncelle” komutu **geçersiz**. Frontend `src` değiştiyse build/`dist` adımı yorumda açık yazılsın.

---

## Yerel ajan: adım adım

### 1) Minimal dosya taşı

- Sadece `DEPLOY.files` listesini `scp` et.
- **Yasak:** `git reset --hard` canlıda, `rm -rf`, tüm tree rsync, `data/` / `.env` / DB kopyası.
- Geçici çöp taşıma: `tools/_*.sh`, `_tmp_*`, `debug-*.log`, `shadow_wagon/out`, `*.tar` (rollback paketi hariç bilinçli komut).

### 2) Backup (her deploy öncesi)

```bash
ts=$(date +%Y%m%d-%H%M%S)
b=/home/pi/injection-monitor/backups/pre-deploy-$ts
mkdir -p "$b"
# değişecek her remote dosyayı:
#   cp -a /home/pi/injection-monitor/... "$b/"
ln -sfn "pre-deploy-$ts" /home/pi/injection-monitor/backups/pre-deploy-latest
```

### 3) Hangi servis?

| Değişen path | Restart |
|--------------|---------|
| `backend/app/vision/**`, `fault_log.py`, `line_pipeline.py`, `app/main.py`, `app/api/**` (vision-related) | **8000** |
| `admin_app.py`, `admin_static/**`, `admin_main_proxy.py` | **8080** |
| `backend/app/bot/**` | **bot** |
| `frontend/**` (build sonrası static) | genelde **8000** (operator UI); admin-only ise 8080 |

```bash
# sudo interaktif prompt ajanı asar; -S kullan
echo 'SUDO_PASS' | sudo -S systemctl restart injection-monitor.service
# gerekirse:
# echo 'SUDO_PASS' | sudo -S systemctl restart injection-monitor-admin.service
# echo 'SUDO_PASS' | sudo -S systemctl restart injection-monitor-bot.service
sleep 3
systemctl is-active injection-monitor.service injection-monitor-admin.service injection-monitor-bot.service
ps -eo pid,lstart,cmd | grep -E 'uvicorn.*(8000|8080)|app.bot.runner' | grep -v grep
```

Restart sonrası PID/start time yeni olmalı; eski process kalmasın.

### 4) Doğrulama (şartsız)

**8000 / vision** (özellikle hot-path dosyaları):

1. Servis `active`.
2. Kameralar enerjiliyse `cameras[].keeping_up == true`.
3. Cam yükü: `cpu_proxy` / `total_ms` kabaca **&lt; ~50 ms**.
4. Makine `process_ms` kabaca **&lt; ~15 ms** (30–50+ = regress → **hemen revert**).
5. `journalctl -u injection-monitor -n 40` — sürekli traceback olmamalı.

**8080 / admin:** `systemctl is-active`; UI için kullanıcıya **Ctrl+F5**.

**Bot:**

- Long-poll (`getUpdates`) kullanıyoruz; webhook ile çakışır → **409 Conflict**.
- Açılışta `deleteWebhook` beklenir; log’da `getUpdates` **200**.
- Kamerasız da bot cevap vermeli. 409’da 8000’i restart etme; fazla `app.bot.runner` öldür + webhook temizle.
- Token’ı log/yorum/commit’e yazma.

### 5) Regress → revert

```bash
# backups/pre-deploy-latest içinden dosyaları geri kopyala
echo 'SUDO_PASS' | sudo -S systemctl restart <ilgili-servisler>
# metrikleri tekrar ölç
```

---

## Vision hot-path (sistemi bozmanın en sık yolu)

Kural dosyası: [`.cursor/rules/vision-hotpath-perf.mdc`](../.cursor/rules/vision-hotpath-perf.mdc).

- `fault_log.resolve()` sadece **lost → found**; her found frame’de değil.
- `note_active()`: throttle içindeyse **0 SQL** (önce memory).
- Kamera worker: **yeni kare yoksa** gray/probe yok.
- Probe `map_x`/`map_y` cache; her frame’de geometry rebuild yok.
- Band/JPEG (E001/E002): yalnızca `would_write` / probe-info on-demand — **her frame değil**.

Bu dosyalara dokunan deploy’da metrik doğrulama zorunlu. Regress varsa özelliği tutup “sonra bakarız” yok — **revert**.

---

## Windows / PowerShell tuzakları

- SSH üzerinde satır içi `python -c "..."` tırnakları PowerShell’de kırılır.
- **Doğru:** script dosyası yaz → `scp` → Pi’de `PYTHONPATH=... .venv/bin/python /tmp/script.py`.
- Interaktif yasak: `sudo` (şifresiz `-S` dışı), `git rebase -i`, `ssh` password prompt.
- CRLF-only “dirty” dosyaları commit/deploy etme.

---

## Hızlı sağlık komutları

```bash
systemctl is-active injection-monitor.service injection-monitor-admin.service injection-monitor-bot.service
journalctl -u injection-monitor -n 30 --no-pager
journalctl -u injection-monitor-bot -n 20 --no-pager
# process_ms / keeping_up: tools/verify_probe_info.py veya /api/live/snapshot
curl -s http://127.0.0.1:8000/api/live/snapshot | head -c 2000
pgrep -af 'app.bot.runner'   # tek process olmalı
```

---

## Tek cümlelik özet

**Minimal SCP → backup → sadece ilgili systemd restart → metrik/log doğrula → regress’te backup’tan revert.**  
Cloud kod yazar; yerel bu protokolü uygular; ikisi de kör `git pull` ile canlıyı ve DB’yi ezmez.
