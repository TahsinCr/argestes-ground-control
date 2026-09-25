[![Python 3.11+][python-shield]][python-url]
[![Lisans][license-shield]][license-url]

[English][lang-en-url] | **Türkçe**

# Kırlangıç Yer Kontrol İstasyonu

Eklenti ana makinesine sahip masaüstü yer kontrol uygulaması. Dahili MAVLink
protokol eklentisi ve isteğe bağlı telemetri eklentisi mevcut araç bağlantısı,
telemetri/GPS, otopilot ve parametre işlevlerini sağlar. Ortak uygulamada görev
planlama, harita, loglar, batarya ve cihaz durumu korunur.

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
Qt arayüzü → UI adaptörleri → uygulama sözleşmeleri → eklenti ana makinesi
                         │                              └── extensions/
                         └── ayarlar, loglar, planlayıcı, harita ve batarya
```

`src/container.py` composition root'tur. `src/extension/api` sürümlü eklenti
sözleşmesini, `src/extension/host` statik manifest keşfini, yetenek kontrolünü
ve etkin eklentilerin kurulumunu sağlar. Eklentiler `src.core` herkese açık
paketlerini doğrudan kullanabilir. Protokol eklentisi ortak araç sözleşmelerini
ve telemetri kaynağı fabrikasını sağlar; telemetri eklentisi bunu kullanır ve
protokolden bağımsız olarak kapatılabilir. En az bir protokol sağlayıcısı etkin
kalmalıdır. Eklenti açma/kapama yeniden başlatınca uygulanır. Sol etkinlik
çubuğunda arama, kategori/durum filtresi ve eklenti kontrolleri vardır. Mevcut
MAVLink ayarları `data/settings.json` içinde korunur; eklenti durumu ve kendi
ayarları `data/extensions/` altında tutulur, devre dışı bırakılınca silinmez.

Yeni eklenti `extensions/<kimlik>/` altında bulunur. `manifest.json` dosyasında
`api_major: 1`, `entry_point` ve `provides`/`requires` yetenek listeleri
tanımlanır. Fabrika `configure(registrar, context)` ve isteğe bağlı
`start(context)`/`stop()` metotlarına sahip nesne döndürür.
`registrar.singleton(token, factory, dependencies=...)` ile servis sağlanır;
aynı servisin birden fazla eklenti tarafından kaydedilmesi reddedilir. Dahili
eklentiler çalışan örnek olarak kullanılabilir. Eklenti ayrıca
`registrar.sidebar_view(kimlik, başlık, factory)` ile sol yan panel katkısı
ekleyebilir. `factory`, Qt üst widget'ını alır ve paneli UI iş parçacığında
oluşturur; devre dışı eklentinin arayüz kodu içe aktarılmaz.

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
