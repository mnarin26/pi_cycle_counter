# Seed / canlı ayar exportu

`pi_live_machines_cameras.json` — rsp3b canlı DB'den (çevrim geçmişi yok).

- RTSP içindeki kullanıcı/şifre `USER:PASS` olarak maskeli.
- Telegram token maskeli.
- Etkin makineler: AF-1..9, etkin kameralar: 1–2.

Yeni Pi'de:

1. Kod + venv + frontend build (veya hazır `frontend/dist`)
2. İlk çalıştırmada boş `injection.db` oluşur
3. 8080'den kameraları / çizgileri yeniden kalibre et **veya** bu JSON'u referans alıp elle/import script ile yükle
4. `.env`, Telegram token, Wi‑Fi AP, Tailscale fabrika ağına göre ayrı kurulur

Bu dosya tek başına "tak-çalıştır fabrika kopyası" değildir; kalibrasyon ve sırlar Pi'ye özeldir.
