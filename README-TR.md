[![Python 3.11+][python-shield]][python-url]
[![Lisans][license-shield]][license-url]

[English][lang-en-url] | **Türkçe**

# Kırlangıç Yer Kontrol İstasyonu

Kırlangıç Yer Kontrol İstasyonu (YKİ), MAVLink uyumlu araçların işletimi için
geliştirilmiş bir masaüstü uygulamasıdır. Araç bağlantısı, canlı telemetri,
uçuş planlama, parametreler, operatör komutları, harita ve kamera akışlarını
tek arayüzde birleştirir.

`main` dalı kasıtlı olarak araç ve ürün bağımsızıdır. Ortak yer-kontrol
altyapısını sağlar; ürüne özel otomasyonlar bu çalışma zamanına değil, ayrı bir
entegrasyon katmanına aittir.

## Özellikler

- Seri, UDP ve TCP için tek okuyuculu MAVLink bağlantısı
- Canlı telemetri, GPS, batarya, paket sağlığı ve radyo bağlantısı gösterimi
- Yeteneklere göre ARM/DISARM, mod, GOTO, TAKEOFF, LAND, RTL, HOLD ve görev
  kontrolleri
- Seçilebilir Linux joystick'leri için RC1–RC16 PWM kalibrasyonu, deadzone,
  expo, tersleme, buton modları ve güvenli override bırakma
- Uçuş planlama, geri al/ileri al, araç görevi eşitleme ve parametre yönetimi
- İsimlendirilmiş kamera profilleri ile 1, 2, 3 veya 4 bölmeli video duvarı
- RTSP, HTTP/MJPEG, RTP/UDP, V4L2, dosya ve özel GStreamer video kaynakları
- Kaynak, taşıma türü, codec, FPS, zaman damgası ve ağ arayüzü seçilebilen
  birden fazla video paylaşım hedefi
- Desteklenen donanım kodlayıcılarıyla kamera başına sabit 30 FPS MP4 kaydı ve
  geçici görüntü kaybında aynı dosyada duraklatıp devam etme
- Yalnız görünür kare içinde çizilen, kamera başına kalıcı nişangâh
- Yapılandırılmış loglar, harita önbelleği, arşivler ve Türkçe/İngilizce arayüz
- Pixhawk yüzdesi veya voltaj hesabıyla batarya yüzdesi

## Hızlı başlangıç

Projeyi sabitlenmiş ortak submodule'leriyle birlikte klonlayın:

```bash
git clone --recurse-submodules \
  https://github.com/Kirlangic-Team/ground-control-station.git
cd ground-control-station
git submodule sync --recursive
git submodule update --init --recursive
```

Linux'ta sistemdeki GStreamer bağlarını kullanabilen bir sanal ortam oluşturun:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

Uygulama Python 3.11+, Qt 6 ve `requirements.txt` bağımlılıklarını gerektirir.
Kamera kaynakları için ilgili taşıma türü ve codec'e uygun GStreamer eklentileri
de kurulmalıdır.

## Video bağımlılıkları

Arch Linux:

```bash
sudo pacman -S --needed \
  gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-plugins-ugly gst-libav python-gobject
```

Ubuntu:

```bash
sudo apt update
sudo apt install -y \
  python3-venv python3-dev python3-gi \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
```

Kurulumu kontrol etmek için:

```bash
gst-inspect-1.0 --version
gst-inspect-1.0 rtspsrc
gst-inspect-1.0 avdec_h264
```

## Bağlantı davranışı

MAVLink profillerini Ayarlar'dan veya telemetri menüsünden yapılandırın.
Yaygın uç noktalar:

```text
Seri: /dev/ttyUSB0     baud: 57600
UDP:  udpin:0.0.0.0:14550
TCP:  tcp:127.0.0.1:5762
```

Uç noktanın açılması tek başına sağlıklı bağlantı anlamına gelmez; hedef
heartbeat alınmalıdır. Durum göstergesi bağlanırken sarı, kullanılamaz veya
hatalıyken kırmızı, doğrulanmış sağlıklı durumda yeşildir.

Bağlantı denetleyicisi otomatik davranışı yönetir:

- `auto_connect`, uygulama açılışında yalnızca bir ilk bağlantı denemesine izin
  verir.
- `autoreconnect`, önceden canlı olan bir bağlantı koptuktan sonraki sınırlı
  yeniden denemelere izin verir.
- İkisi de kapalıysa arka planda bağlantı denemesi yapılmaz.
- Operatörün açıkça bağlantıyı kesmesi, bekleyen kurtarmayı iptal eder ve yeni
  bir operatör isteğine kadar hattı kapalı tutar.

Yalnız ortak `MavlinkClient` ve router MAVLink hattını okuyabilir. Yeni
servisler onun olaylarını tüketmeli; ikinci bir okuyucu, seri bağlantı veya ACK
tüketicisi oluşturmamalıdır.

## Kameralar ve video paylaşımı

Kamera bağlantıları isimli profil olarak saklanır. Bir kaynak video duvarında
yalnız bir bölmede gösterilebilir; başka bölmede görünen kaynak seçildiğinde
iki bölme yer değiştirir. Video duvarı 1, 2, 3 ve 4 görünümü destekler.

Video paylaşımı masaüstü görüntüsü değil, yalnız kamera karelerini yayınlar.
Her hedef için kaynak, taşıma türü, codec, FPS, sabit kare hızı, zaman damgası
ve ağ arayüzü seçilir. Sınırlı yayın hattı sayesinde yavaş bir alıcı kamera
yakalama veya arayüz yolunu yavaşlatamaz.

Aynı çıkış düzenleyicisi yerel kayıt hedefleri de oluşturabilir. Kayıtlar
kamera başına ayrı, sabit 30 FPS MP4 dosyalarıdır ve desteklenen bir NVIDIA
veya VA-API donanım kodlayıcısı gerektirir. Geçici kamera kesintisi yalnız o
kameranın zaman çizelgesini duraklatır; görüntü dönünce aynı dosyaya yazım
devam eder. Kamera panelindeki kayıt düğmesi yapılandırılmış tüm kamera
kayıtlarını birlikte yönetirken her hedef Video Share içinden bağımsız olarak
açılıp kapatılabilir.

Her kamera profili kendi nişan noktasını saklayabilir. Nişangâh yalnız işlenen
görüntü sınırları içinde çizilir; kamera geçişleri ve uygulama yeniden
başlatmaları boyunca korunur.

## Mimari

```text
Qt arayüzü → UI adaptörleri → uygulama servisleri → araç adaptörleri → core
                                        │
                                        ├── MAVLink bağlantısı / telemetri / parametreler
                                        ├── planlayıcı ve yetenek-temelli komutlar
                                        ├── kamera yakalama ve video paylaşımı
                                        └── ayarlar / loglar / önbellek / arşivler
```

`src/container.py` tek composition root'tur. Altyapı, araç adaptörleri,
uygulama servisleri ve çalışma zamanı yaşam döngüsünü tek ve açık bir yerde
bağlar. Servisler bağımlılıklarını constructor üzerinden alır; global container
çözümlemesi yapmaz.

- `src/core`: olaylar, bağımlılık ilkelleri ve MAVLink altyapısı
- `src/communication`: ortak, taşıma-bağımsız sözleşme modelleri
- `src/models`: arayüzden bağımsız doğrulanmış veri modelleri
- `src/services/vehicle`: bağlantı denetimi, MAVLink, telemetri ve parametreler
- `src/services/joystick`: fiziksel kumanda keşfi ve RC PWM dönüşümü
- `src/services`: komutlar, planlayıcı, ayarlar, video, loglar, önbellek ve arşivler
- `src/ui`: Qt görünümleri, bileşenleri ve sunum adaptörleri

Arayüz kodu ham MAVLink paketi üretmez veya bloklayıcı iş çalıştırmaz. Araç
eylemleri servis düzeyindeki komut tanımlarıyla uygulanır; operatöre ancak ACK
veya doğrulanmış durum değişikliğinden sonra başarılı olarak gösterilir.

## Ayarlar, loglar ve testler

Kalıcı ayarlar `data/settings.json` içinde tutulur. Yeni bir ayar eklerken
doğrulanmış modeli, ayarlar arayüzünü ve iki dil dosyasını birlikte güncelleyin.
Yapılandırılmış olaylar `data/logs/yki.jsonl` içine; plan ve oturum verileri
`data/` altına yazılır.

Tüm testleri çalıştırmak için:

```bash
python run_tests.py
```

Qt testleri offscreen platformda çalışır. Saha kullanımından önce hedef
MAVLink uç noktası, kamera kaynağı ve görev aktarım hattıyla donanım smoke testi
yapın.

## Lisans

Copyright © 2026 TahsinCr.

GNU Genel Kamu Lisansı sürüm 3 (`GPL-3.0-only`) ile lisanslanmıştır. Ayrıntılar
için [LICENSE](LICENSE) ve [COPYRIGHT](COPYRIGHT) dosyalarına bakın.

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white
[python-url]: https://www.python.org/
[license-shield]: https://img.shields.io/badge/License-GPL--3.0--only-3DA639?style=for-the-badge
[license-url]: LICENSE
[lang-en-url]: README.md
