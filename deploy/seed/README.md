# Seed / canlı ayar exportu

`pi_live_machines_cameras.json` — **rsp3b** canlı SQLite’tan alınmış anlık görüntü (çevrim geçmişi **yok**).

## İçerik

- Etkin kameralar: genelde **1–2** (`target_width` vb.)
- Etkin makineler: **AF-1 … AF-9** (çizgi uçları, kalınlık, threshold alanları)
- RTSP kullanıcı/şifre → `USER:PASS` maskeli
- Telegram token → maskeli
- DB’de pasif şablon kamera/makine satırları da olabilir; kurulurken yalnız etkin olanlara bak

## Yeni Pi’de kullanım

1. Repoyu kur, venv + frontend build, 8000/8080 başlat.
2. Boş `injection.db` oluşur.
3. **8080**’den kameraları ekle (gerçek RTSP + şifre).
4. Çizgi/kalınlık için bu JSON’u **referans** al; gerekirse aynı normalize `axis_p0/p1` değerlerini panelden gir.
5. Otomatik import scripti zorunlu değildir — amaç dokümantasyon + hızlı kopyalama.

Bu dosya **tak-çalıştır fabrika klonu değildir**. Aynı fiziksel kameralar/aynı ağ olmadan sayım tutmayabilir.
