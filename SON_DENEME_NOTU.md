# Son Deneme Notu (Cizgi Tabanli Takip)

Tarih: 2026-05-14

## Bu adimda ne duzeltildi

- Admin ekraninda eksen (axis) hesaplama tek fonksiyona alindi: `axisFromPoly(...)`.
- Kaydetme sirasinda (`Cizgi Kaydet`) eksen artik eski aci/projeksiyon formulunden degil, ekranda cizilen sari cizgiyle **ayni** mantiktan uretiliyor.
- Boylece ROI'nin kisa kenar/genislik ayari detector yolunu degistirmesin hedeflendi.
- Ekran metinleri ROI odakli dilden cizgi odakli dile cevrildi.
- Idle gorunumde karmasayi azaltmak icin ROI siniri gizlendi; aktif takip cizgisi ve kalinlik bandi esas gorunum yapildi.

## Beklenen davranis

- Degerler artik temel olarak `axis_p0/axis_p1 + line_thickness + threshold` ile degismeli.
- ROI sadece cizgiyi hizalamak icin yardimci olmalı; detector ROI maskesi kullanmiyor.

## Not

- Bu deneme sonrasi sahada kontrol: ayni cizgiyi koruyup sadece ROI genisligini degistirince `peak/bg` ve `Δ` degerlerinin dramatik kaymamasi beklenir.

## 2026-05-17 ek not (kalibrasyon)

- Yeni kalibrasyon endpointi eklendi: `POST /api/calibration/machines/{id}/learn_reflector_length`
- Akis:
  - Cizgiyi ayarla
  - `Uzunluk Kalibrasyon Baslat` butonuna bas
  - Reflektoru cizgi boyunca gezdir
- Sistem kalibrasyon suresince `segment_len` ornekleri toplar ve robust aralik ogrenir:
  - `reflector_len_min = P10 - margin`
  - `reflector_len_max = P90 + margin`
- Canli tespitte bu aralik disindaki parlak adaylar reddedilir (`found=false`), yani reflektor yokken rastgele en parlak yeri secme azaltilir.
