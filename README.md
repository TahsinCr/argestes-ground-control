# Kuzey Takimi Yer Kontrol Istasyonu

PyQt6 ile gelistirilmis, gorev odakli ve backend'den bagimsiz ilk frontend iskeleti.

## Calistirma

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Ilk Surum Kapsami

- Moduler ana pencere
- Harita paneli (`QWebEngine + Leaflet`)
- Vektorel / uydu katman gecisi
- Waypoint, kalkis, inis, loiter, HSS, yasak bolge ve guvenli bolge cizim akisi
- Harita uzerinden nokta listesi, radius/sira duzenleme, gorev plani kaydetme/yukleme
- 16:9 video paneli
- Yapay ufuk gostergesi
- Telemetri panelleri ve hizli komutlar
- Parametre editoru
- Tema ve dil ayarlari
- Mock veri servisleri ve cache klasoru yonetimi

## Kod Organizasyonu

- `kuzey_gcs/app.py`: uygulama giris noktasi
- `kuzey_gcs/services/runtime.py`: servis olusturma ve uygulama runtime kompozisyonu
- `kuzey_gcs/services/storage.py`: JSON ve klasor kalicilik yardimcilari
- `kuzey_gcs/models/`: telemetri, gorev, log, ayar ve parametre modelleri
- `kuzey_gcs/config/defaults.py`: varsayilan parametre katalogu
- `kuzey_gcs/config/settings_schema.py`: ayarlar diyalogu alan semasi
- `kuzey_gcs/themes/`: palette, stylesheet ve tema uygulama katmanlari
- `kuzey_gcs/views/`: pencere ve dialog seviyesindeki UI kabuklari
- `kuzey_gcs/widgets/`: tekrar kullanilabilir panel ve gostergeler

## Gelistirici Notlari

- `kuzey_gcs/models/domain.py` geriye donuk uyumluluk icin tutulur; yeni kodda dogrudan alt model modullerini kullanmak tercih edilir.
- Yeni bir ayar eklemek icin once `SettingsModel`, sonra `config/settings_schema.py`, gerekiyorsa da `config/i18n.py` guncellenir.
- Yeni bir servis modu eklemek icin `services/runtime.py` icindeki servis fabrikasina bir dal eklemek yeterlidir.
- Tema tokenlari `themes/palette.py`, genel stil ise `themes/stylesheet.py` icinde tutulur.

## Notlar

- Harita cache'i `QWebEngineProfile` disk cache ile tutulur.
- Gezilen bolgelerin kaba indeks bilgisi `data/cache-index.json` icinde izlenir.
- Sonraki adimda servisler `MAVLink`, `RTSP`, `UDP`, `REST` veya serial backend'lere degistirilebilir.
