[![Python 3.11+][python-shield]][python-url]
[![Lisans][license-shield]][license-url]

[English][lang-en-url] | **Türkçe**

# Kırlangıç Yer Kontrol İstasyonu

MAVLink uyumlu araçlar için masaüstü yer kontrol uygulaması. Mevcut ortak
uygulama; telemetri ve GPS'i, ArduPilot/MAVLink bağlantı yönetimini, görev
planlamayı, parametreleri, haritayı, logları, batarya durumunu ve bağlantı/cihaz
sağlığını sağlar.

Uygulama araçtan bağımsız ortak bir yer kontrol temeli olarak geliştirilmektedir.

## Hızlı başlangıç

```bash
git clone --recurse-submodules \
  https://github.com/Kirlangic-Team/ground-control-station.git
cd ground-control-station
git submodule update --init --recursive
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

Uygulama Python 3.11+, Qt 6, `pymavlink` ve `pyserial` gerektirir. Linux'ta
başlatmadan önce PyQt6 ve Qt WebEngine için gereken sistem paketlerini kurun.

## MAVLink bağlantıları

Telemetri bağlantı kontrolünden isimli MAVLink profilleri ekleyip seçin. Yaygın
uç noktalar:

```text
Seri: /dev/ttyUSB0     baud: 57600
UDP:  udpin:0.0.0.0:14550
TCP:  tcp:127.0.0.1:5762
```

Uç noktanın açılması tek başına sağlıklı bağlantı sayılmaz; araç heartbeat'i
alınmalıdır. `auto_connect` ilk bağlantı denemesini, `autoreconnect` ise canlı
bağlantı koptuktan sonraki kurtarmayı yönetir. İkisi de kapalıysa arka planda
bağlantı denenmez. Operatörün bağlantıyı kesmesi, yeniden bağlanma isteğine kadar
kurtarmayı durdurur.

MAVLink hattını yalnız ortak istemci/router okur. Servisler olayları tüketir;
arayüz ikinci bir okuyucu oluşturmaz ve ham paket kodlamaz.

## Mimari

```text
Qt arayüzü → UI adaptörleri → uygulama servisleri → araç adaptörü → Core
                                      ├── MAVLink, telemetri ve parametreler
                                      ├── planlayıcı ve araç komutları
                                      └── ayarlar, loglar, harita önbelleği, batarya
```

`src/container.py` composition root'tur. Bağımlılıklar constructor üzerinden
aktarılır. `src/core` ortak olay, bağımlılık ve MAVLink altyapısını;
`src/models` arayüzden bağımsız modelleri; `src/services` uygulama ve araç
davranışlarını; `src/ui` Qt sunum katmanını içerir.

## Ayarlar, loglar ve testler

Ayarlar `data/settings.json`, yapılandırılmış loglar `data/logs/yki.jsonl`
içinde tutulur. Testleri çalıştırmak için:

```bash
python run_tests.py
```

Qt testleri offscreen platformda çalışır. Saha kullanımından önce hedef araçla
MAVLink bağlantısını ve görev aktarımını doğrulayın.

## Lisans

Copyright © 2026 TahsinCr.
