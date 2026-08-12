[![Python 3.11+][python-shield]][python-url]
[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![GPL-3.0 License][license-shield]][license-url]

[English][lang-en-url] | **Türkçe**

# Kırlangıç Team Yer Kontrol İstasyonu

Kırlangıç Team YKİ; uçuş cihazı bağlantısını, telemetriyi, uçuş planını, operatör komutlarını, video akışını ve TEKNOFEST haberleşmesini tek arayüzde toplar.

YKİ otonom görevi yürütmez. Haritada uçuş planı hazırlanır, araç veya araç üstü bilgisayar için komut üretilir ve sonuç protokol cevabıyla doğrulanır. Hedef kilitlenme, kamikaze, görüntü işleme ve benzeri karar algoritmaları araç üstü sistemin sorumluluğundadır.

## Kapsam

- ArduPilot tabanlı araçlarla seri, UDP ve TCP üzerinden MAVLink bağlantısı
- Uçuş telemetrisi, bağlantı sağlığı, GPS, batarya ve radyo bağlantı bilgileri
- ARM/DISARM, uçuş modu, LAND, GOTO, parametre ve uçuş planı aktarımı
- İstek kimliğiyle eşleştirilen Onboard görevleri, görev durumu, QR kurtarma ve uzak loglar
- Sekmeli hızlı komutlar ve araç üstü görevler için Başlat/Yeniden Başlat/İptal kontrolleri
- Takeoff, waypoint, loiter, RTL, land ve yerel güvenlik alanlarının harita üzerinde hazırlanması
- Araçtan canlı parametre/varsayılan değer ve uçuş planı alma
- RTSP, HTTP/MJPEG, RTP/UDP, V4L2, dosya ve özel GStreamer pipeline kaynakları
- Taze kare ile doğrulanan, isteğe bağlı kalıcı video otomatik bağlantısı
- TEKNOFEST sunucu bağlantısı, rakip telemetrisi, HSS ve QR referans verileri
- Aranabilir, seviye filtreli ve seçili dile çevrilen yapılandırılmış loglar
- Yüzde veya voltaj tabanlı batarya uyarısı ve ayrı, isteğe bağlı failsafe eşiği
- Harita planı ve onaylı home değişiklikleri için geri al/ileri al geçmişi
- Yeniden boyutlandırılabilir harita, video, log ve parametre çalışma alanları
- JSONL backend logları ve bağlantı menülerinden kalıcı ayar yönetimi

## Desteklenen ortamlar

Ekip için doğrulanan ana ortam Arch’tır. Ubuntu ve Windows kurulumları da desteklenir; gerçek donanım testi yapılırken kullanılan seri port ve video kaynağı ayrıca doğrulanmalıdır.

| Ortam | Önerilen sürüm | Not |
|---|---:|---|
| Arch Linux | Güncel | Ana geliştirme ortamı |
| Ubuntu | 24.04 LTS | Paket adları bu sürüme göredir |
| Windows | Windows 11 x64 | MSVC x86_64 GStreamer kurulumu kullanılır |
| Python | 3.11+ | Proje sanal ortamda çalıştırılır |

## GStreamer kurulumu

### Arch Linux

```bash
sudo pacman -S --needed \
  gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-plugins-ugly gst-libav python-gobject
```

### Ubuntu 24.04

```bash
sudo apt update
sudo apt install -y \
  python3-venv python3-dev python3-gi \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
```

### Windows 11

[GStreamer’ın resmi Windows indirme sayfasından](https://gstreamer.freedesktop.org/download/#windows) 64 bit **MSVC** paketini kurun. GStreamer 1.28 ve sonrasında kurulum türü olarak **Development** seçildiğinde runtime ve geliştirme dosyaları birlikte yüklenir. Eski sürümlerde runtime ve development kurucularının ikisi de kurulmalıdır.

Kurulumun `bin` dizinini kullanıcı veya sistem `PATH` değişkenine ekleyin. Varsayılan sistem kurulumu için yol genellikle şöyledir:

```text
C:\Program Files\gstreamer\1.0\msvc_x86_64\bin
```

Yeni bir PowerShell açıp kurulumu kontrol edin:

```powershell
gst-inspect-1.0 --version
gst-inspect-1.0 rtspsrc
gst-inspect-1.0 avdec_h264
```

Mevcut `usb` video kaynağı Linux `v4l2src` kullanır. Windows’ta ağ akışları ve özel pipeline kullanılabilir; doğrudan USB kamera için `ksvideosrc` desteği eklenmeden `usb` kaynağı seçilmemelidir.

Linux kurulumu sonrasında aynı kontrolleri terminalden çalıştırın:

```bash
gst-inspect-1.0 --version
gst-inspect-1.0 rtspsrc
gst-inspect-1.0 avdec_h264
gst-inspect-1.0 v4l2src
```

## Python ortamı

Projeyi yeniden kullanılabilir core submodule’üyle birlikte klonlayın:

```bash
git clone --recurse-submodules \
  https://github.com/Kirlangic-Team/ground-control-station.git
cd ground-control-station
```

Mevcut bir çalışma kopyasında YKİ’yi kurmadan veya çalıştırmadan önce kayıtlı
adresi eşitleyip sabitlenmiş core sürümünü başlatın:

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

Core kaynakları bağımsız olarak
[`TahsinCr/vehicle-autonomy-core`](https://github.com/TahsinCr/vehicle-autonomy-core)
reposunda geliştirilir. Ana repo tekrarlanabilir kurulum için belirli bir core
commitine sabitlenir.

### Linux

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

`--system-site-packages`, dağıtım paket yöneticisiyle kurulan GI/GStreamer
bağlarının sanal ortamdan kullanılmasını sağlar.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

PyGObject kurulumu sistemdeki GStreamer ile aynı mimariyi kullanmalıdır. Windows’ta 64 bit Python ile 64 bit MSVC GStreamer’ı karıştırmayın.

## Ortak core çalışma akışı

`src/core`, YKİ reposunun kaydettiği kesin commit üzerinden kullanılır. Normal
YKİ geliştirmesinde bu dizin değiştirilmemelidir. YKİ’yi incelenmiş daha yeni
bir core sürümüne taşımak için:

```bash
git -C src/core fetch origin
git -C src/core checkout <core-commit>
git status --short
```

Ana repo farkında `src/core` tek bir Git-link değişikliği olarak görünmelidir.
Core kaynak değişiklikleri, testleri ve sürüm notları core reposunda tutulur;
bu repoya yalnızca ortaya çıkan commit işaretçisi girer. İşaretçiyi stage
etmeden önce iki test paketini de çalıştırın.

Branch değişiminden sonra `src.core` import edilemiyorsa core dosyalarını
kopyalamak yerine submodule’ü yeniden başlatın:

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

## Araç bağlantısı

Bağlantı ayarları **Ayarlar > Backend > MAVLink** bölümünden veya alt çubuktaki Telemetri/Otopilot/GPS menüsünden değiştirilir. Bağlan veya Yeniden Bağlan seçildiğinde ayarlar önce kaydedilir, ardından backend yeni endpoint ile açılır.

```text
Seri: /dev/ttyUSB0         baud: 57600
UDP:  udp:0.0.0.0:14550
TCP:  tcp:127.0.0.1:5762
```

Windows seri portu `COM3` gibi doğrudan yazılır. UDP ve TCP bağlantılarında baud değeri kullanılmaz.

Bir bağlantının açılması tek başına başarı sayılmaz. Araç durumu hedef system/component heartbeat’iyle, video durumu taze kareyle, GPS durumu geçerli fix verisiyle doğrulanır.

Alt çubuğun sağındaki gösterge, ölçülen MAVLink paket teslim oranını
hesaplanan RFD radyo sağlığından ayırır. Kompakt alanda alınan/gönderilen
paket, RF ve gönderim tamponu bilgileri yer alır. Yerel/uzak RSSI, gürültü,
SNR, hata düzeltme ve paket kaybı ayrıntıları için yalnızca
**Komut/Telemetri** yazısının üzerinde bekleyin. Yüzde ve sayaçların temaya
ait canlı durum rengi değişmez.

## Video bağlantısı

Video ayarlarına **Ayarlar > Backend > Video** bölümünden veya alt çubuktaki
Video bağlantı menüsünden erişilir. Bağlanmadan önce kaynak tipi, URI veya
cihaz, codec, gecikme ve kare zaman aşımı seçilir.

**Bağlan** veya **Yeniden Bağlan**, GStreamer pipeline’ını oluşturmadan önce
değiştirilen değerleri kaydeder. **Uygulama açıldığında otomatik bağlan**
seçeneğini yalnızca normalde erişilebilir olan kaynaklarda etkinleştirin.
Seçenek varsayılan olarak kapalıdır; böylece bulunmayan bir USB kamera veya ağ
akışı arka planda gereksiz yakalama işlemi başlatmaz.

Bağlantı göstergesi yalnızca taze video kareleri geldiğinde sağlıklı duruma
geçer. Pipeline’ın açılması tek başına kamera bağlantısı sayılmaz. Kamera kısa
süreli olarak arayüzden hızlı kare üretirse backend yakalamaya devam eder, Qt
ise yalnızca en yeni kareyi çizer; gecikmiş kareler kuyrukta birikmez.

Kompakt video göstergesinde kalite, bitrate, FPS ve çözünürlük bulunur. Kaynak,
codec, kare yaşı, gecikme ve zaman aşımı ayrıntıları için **Video Aktarımı**
yazısının üzerinde bekleyin.

## Yüksek hızlı telemetri ve arayüz

MAVLink alım hızı arayüz yenileme hızından bağımsızdır. Backend gelen bütün
paketleri işler; Qt adaptörü her ekran karesinde en güncel snapshot’ı gösterir.
Bu yöntem telemetriyi yavaşlatmadan eski UI olaylarının kuyrukta birikmesini
engeller.

Uçuş dinamiği göstergesi kaynak zaman damgalarını ve örnekleme aralığındaki
değişimi kullanarak ara kareleri üretir. Harita değişmeyen telemetriyi tekrar
işlemez ve araç ikonunu her yön değişiminde yeniden oluşturmaz.

Harita verisi kalıcı QWebChannel hattından geçer ve tarayıcı animasyon karesi
başına en fazla bir kez uygulanır. Parametre snapshot’ları ile log patlamaları da
toplu işlenir; parametre tablosu mevcut satırları yerinde günceller. Bu görsel
sınırlar backend MAVLink alımını veya komut onaylarını yavaşlatmaz.

## Harita planlama

Harita araç çubuğundaki Tek Sefer/Döngü düğmesi, yüklenen waypoint rotasının
bir kez tamamlanmasını veya sürekli tekrarlanmasını belirler. Bu seçim ARM,
uçuş modu ve plan aktarımından bağımsızdır.

Nokta listesi aynı araç çubuğu düğmesiyle açılıp kapatılır. Takeoff, waypoint,
loiter, RTL ve land öğeleri tutma noktasından sürüklenerek sıralanabilir.

Haritadaki kalıcı `H` işaretçisi uçuş cihazının home/merkez referansını
gösterir. İşaretçi sürüklenerek veya sağ tık menüsündeki
**Burayı merkez noktası yap** seçilerek konum güncellenir. Değişiklik
`MAV_CMD_DO_SET_HOME` onayıyla doğrulanır; işaretçi görev noktalarından ayrı
olduğu için silinemez veya planla birlikte temizlenemez.

HSS, QR ve sunucu sahipli diğer referans katmanları düzenlenmez, silinmez veya
uçuş planı arşivine yazılmaz.

Harita düzenlemelerini geri almak için `Ctrl+Z`, ileri almak için
`Ctrl+Shift+Z` kullanılır. Nokta ekleme, silme, sürükleme, sıralama, alan
düzenleme ve Tek Sefer/Döngü değişiklikleri yerel olarak geri yüklenir;
**Görevi Gönder** seçilmeden araca aktarılmaz. Home değişiklikleri doğrudan araç
komutu olduğu için istisnadır: geri alma ve ileri alma işlemi de onaylı bir
home komutu gerektirir.

Haritanın normal sağ tık menüsünde yalnızca GOTO, araca göre ortalama ve
home/merkez ayarlama işlemleri bulunur. Düzenlenebilir bir rota noktasına sağ
tıklandığında ayrıca Sil seçeneği gösterilir. Harita ölçeği araç çubuğundan
bağımsız olarak zoom bilgisinin yanında görünür.

## Yeniden boyutlandırılabilir çalışma alanı

Yan panel, harita/video panelleri, görsel ve tanılama satırları ile
log/parametre panelleri aralarındaki ayırıcılardan sürüklenerek
boyutlandırılabilir. WebEngine, video ve büyük tabloların sürükleme sırasında
donmaması için pahalı yerleşim güncellemeleri ayırıcı bırakıldığında
uygulanır. Çerçevesiz ana pencere de tüm kenar ve köşelerden
boyutlandırılabilir; minimum panel ölçüleri kritik kontrollerin tamamen
kapanmasını engeller. Linux/X11'de pencere yöneticisi Qt'nin yerel
boyutlandırma talebini kabul edip sürüklemeyi başlatmazsa uygulamanın sınırlı
manuel yolu kullanılır; diğer platformlarda compositor yönetimi korunur.

## Batarya uyarısı ve failsafe

**Ayarlar > Backend > Batarya** bölümünde iki ayrı politika bulunur:

- Batarya göstergesi Pixhawk yüzdesini kullanabilir veya 4S LiPo yüzdesini
  voltajdan tahmin edebilir. Tam dolu voltajı, rezerv voltajı ve rezerv noktasının
  karşılık geldiği yüzde ayarlanır; yüzde sıfır voltajı otomatik hesaplanır.
  Medyan ve asimetrik zaman filtreleri kısa motor yükü düşüşlerini azaltırken
  kalıcı deşarjı gizlemez.
- Uyarı eşiği yüzde veya voltaj üzerinden çalışır ve alt bardaki batarya
  göstergesini kırmızı yanıp sönen alarm durumuna getirir.
- Failsafe eşiği uyarıdan bağımsızdır. Yalnızca etkinleştirilmişse, araç
  silahlıyken ve kendi eşiği aşılmışsa yapılandırılan uçuş modunu ister.

Alarm ilk etkinleştiğinde bir kez loglanır; batarya yüzdesindeki her bir puanlık
değişimde güncel durum yeniden kaydedilir.

Kompakt batarya ayarları için alt çubuktaki batarya bilgisine tıklayın. Akım
ölçümü kullanılamıyorsa bu yüzde yalnızca gösterge amaçlıdır; uçuş güvenliğinde
Pixhawk voltaj eşikleri esas alınmalıdır.

## Sahte TEKNOFEST sunucusu

Haberleşme testi için sunucuyu YKİ’dan ayrı çalıştırın:

```bash
python fake_teknofest_server.py
```

Varsayılan adres `http://127.0.0.1:5000`, kullanıcı `kirlangic`, parola
`kirlangic-demo`, takım numarası `1` ve konsol dili İngilizcedir. Türkçe
konsol logları için:

```bash
python fake_teknofest_server.py --lang tr
```

```bash
python fake_teknofest_server.py \
  --host 0.0.0.0 --port 5000 --competitors 27 --team-number 1
```

Sunucu; son kabul edilen kendi İHA konumu çevresinde 27 rakip, dört HSS alanı, QR koordinatı ve sunucu saati üretir. İstek ve yanıtlar sunucu terminalinde `RX` ve `TX` yönleriyle görünür.

Kendi İHA telemetrisi yalnızca araç bağlantısı açıkken, konum ve araç/GPS zamanı tazeyken gönderilir. Telemetri hattı kesildiğinde eski snapshot sunucuya basılmaz. Rakip, HSS ve QR alımı kendi bağlantı yaşam döngüsünde devam edebilir.

## Mimari sınırlar

`PlannerService`, YKİ planlayıcısının ve harita referans verisinin tek
sahibidir. Görev yürütmez, otonom karar vermez ve araç üstü algoritma
çalıştırmaz. Kullanıcı planını atomik olarak günceller, kaynak sahipli verileri
ayırır ve aktarım için tutarlı snapshot sağlar. Sözleşme ile uygulama ayrı
`planner` dosyalarına bölünmez; servis `services/planner.py` içinde tek yerde
bulunur.

Temel sorumluluklar:

- `core`: araca ve arayüze bağlı olmayan altyapı; event, dependency ve MAVLink çekirdeği
- `models`: veri, doğrulama ve veriye ait dönüşümler
- `services/contracts`: implementasyondan bağımsız servis sözleşmeleri
- `services/vehicle`: `pymavlink`, telemetri ve araç parametresi akışları
- `services`: planlayıcı, komut, bağlantı ve yarışma gibi ana backend servisleri
- `ui`: Qt, Leaflet, widget ve görsel durum; iş kuralı içermez

`src/core` bir Git submodule’dür. Core değişiklikleri önce kendi reposunda
test edilip commit edilmelidir; YKİ tarafında yalnızca sabitlenen submodule
commit’i güncellenir. Ortak core dosyalarını ana repoya kopyalamayın.

Backend servis sözleşmeleri core `EventBus` yapısını kullanır. Normal servis
politikası, hatalı bir dinleyiciyi izole ederek telemetri, video ve durum
üreticilerinin çalışmasını sürdürür. Onayı yerel teslimata bağlı olan akışlar
`ErrorPolicy.RAISE` kullanmalıdır; Orin uzak log alımı sıra numarasını ilerletmeden
önce bu politikayı uygular.

Bağımlılık yönü:

```text
ui → services → models → core
core/mavlink → services/vehicle → servis sözleşmeleri
```

`DispatchCommandService`, operatör isteğini sınıflandırır ve doğru hedefe iletir. ArduPilot komutları `VehicleService` üzerinden Pixhawk’a, araç üstü uygulama komutları ilgili uygulama kanalına gider. UI butonları doğrudan MAVLink paketi üretmez.

Haritadaki HSS, QR hedefi, güvenli alan ve yasak alan verileri ArduPilot görevi değildir. Açık bir dönüştürücü veya araç üstü işleyici olmadan `MISSION_ITEM_INT` olarak gönderilmez.

## MAVLink kuralları

Uygulamada MAVLink socket’ini yalnızca `MavlinkMessageRouter` okur. Yeni bir servis ikinci `recv_match()` döngüsü açmamalıdır.

Araç adaptörü ile isteğe bağlı Orin uygulama kanalı tek bir `MavlinkClient`
ve router kullanır. Core içindeki `MavlinkRuntime` bağımsız araçlar için kolay
varsayılandır; YKİ ise Orin kanalını uçuş telemetri bağlantısını değiştirmeden
açıp yapılandırabilmek için sahipliği açık tutar.

- Mesaj tüketimi `VehicleService.subscribe()` veya çekirdekte `client.subscribe()` ile yapılır.
- İstek cevabı beklenirken gönderim öncesi router sequence sınırı alınır.
- Bütün gönderimler ortak send kilidinden geçer.
- ARM/LAND ve genel komutlar `COMMAND_ACK` ile doğrulanır.
- Mod değişimi yeni `HEARTBEAT` içindeki modla doğrulanır.
- Parametre yazımı aynı anahtar/değeri taşıyan `PARAM_VALUE` ile doğrulanır.
- Uçuş planı aktarımı `MISSION_REQUEST(_INT)` ve `MISSION_ACK` akışı tamamlanmadan başarılı sayılmaz.

Normal ARM başarısız olursa zorla ARM yalnızca operatör onayıyla denenir. Arka planda otomatik zorla ARM yapılmaz.

## SİHA Onboard v0.2-beta bağlantısı

YKİ, Vehicle Autonomy Core v1.2 uygulama kanalını mevcut MAVLink bağlantısı
üzerinden kullanır. İkinci seri port, UDP socket veya receive döngüsü açılmaz.
`V2_EXTENSION`; ağ ID `77`, mesaj tipi `42000`, YKİ system ID `255`, onboard
system ID `1` ve onboard component ID `191` ile çalışır. Uygulama heartbeat'i
saniyede bir gönderilir ve 10 saniyede zaman aşımına uğrar. Bu değerler
Onboard/Orin ayarlarından değiştirilebilir.

Core v1.2, tamamlanmamış uygulama paketlerinin birleştirme alanını ve MAVLink
async köprü kuyruklarını sınırlar. Böylece hatalı, yinelenen veya ani yoğun
uygulama trafiği normal telemetri yolunu değiştirmeden bellek kullanımını
sınırsız büyütemez.

`OnboardMissionService`; `task.start`, `task.pause`, `task.resume`, `task.stop`,
`task.cancel`, `mission.status`, `mission.list`, `mission.events` ve `chain.start`
isteklerini ortak kanaldan gönderir. Cevaplar yalnızca packet/request ID birebir
eşleştiğinde isteği tamamlar. `system.ack` yalnızca komutun kabul edildiğini,
`mission.result` ise nihai görev sonucunu gösterir. Hata, timeout ve transport
kopması ayrı tutulur; timeout olan `task.start` otomatik tekrarlanmaz.

Kamikaze eylemi `KAMIKAZE` göndermeden önce operatör onayı ister. ACK içinde
`waiting_for_mode=true` varsa panel GUIDED seçilmesini ister. Phase bilgisi
`mission.transition` paketinden gelir. Görevler sekmesi Başlat/Yeniden Başlat
ve İptal eylemlerini sunar; yeniden başlatma yeni `task.start` isteğinden önce
başarılı iptal ACK'ini bekler.

QR sonucu üç kaynaktan kurtarılır:

1. canlı `kamikaze.event` bildirimleri (`QR_DECODED` ve
   `QR_DECODED_DURING_PULL_UP`);
2. `mission.result.snapshot.result.qr_text`;
3. yeniden bağlantı sonrası cursor tabanlı `mission.events` uzlaştırması.

QR içeriği çalıştırılmayan düz metindir. Aktif Kamikaze göreviyle
ilişkilendirilir, tekrarlardan arındırılır ve merkezi loga yazılır. Canlı
event ile sonuç farklıysa iki değer de korunur ve uyumsuzluk loglanır. QR
kurtarma backend sorumluluğu olarak kalır ve kompakt görev seçicisinde tekrar
gösterilmez.

Onboard `logs.push` istekleri en fazla 16 kayıtlı batch olarak doğrulanır. ACK,
batch'in tamamı merkezi log deposuna girdikten sonra verilir. Session/sequence
cursor'u tekrar gönderimde ikinci log satırı oluşmasını engeller. Gizli bilgi
belirten detail alanları maskelenir. Qt olayları sınırlı batch'lerle işleyerek log
akışının UI thread'ini meşgul etmesini engeller.

Video MAVLink'ten bağımsızdır. HDMI capture kartı USB veya GStreamer pipeline
kaynağı olarak seçilip 1920x1080/60 FPS istenmelidir. Video servisi yalnızca son
kareyi sınırlı yapıda tutar, görev kontrollerini bloklamadan yeniden bağlanır ve
kare gelmediğinde akışı sağlıksız gösterir.

Gerçek donanım kontrol listesi:

1. YKİ'yi Pixhawk/RFD'ye bağlayıp önce normal telemetriyi doğrulayın.
2. Onboard bağlantısını açıp system `1`, component `191`, network `77` ve
   message type `42000` değerlerini ayarlardan kontrol edin.
3. Onboard v0.2-beta'yı başlatıp sürüm, RTT ve saniyelik heartbeat'i ikinci
   MAVLink endpoint'i açılmadan doğrulayın.
4. Kamikaze'yi başlatın, ACK'in yalnızca kabulü gösterdiğini kontrol edin,
   GUIDED seçip `starting → running` geçişini izleyin.
5. İki QR türünü gönderin, bir kez yeniden bağlanın ve canlı, sonuç ve
   geçmiş kurtarma yollarını UI/log panelinde doğrulayın.
6. Aynı `logs.push` batch'ini tekrar gönderip ikinci log satırı olmadan ACK
   verildiğini kontrol edin.
7. HDMI capture kartını 1080p/60 bağlayın, sinyali kesin ve MAVLink kontrolleri
   akıcı kalırken video reconnect davranışını doğrulayın.

## Backend’i UI olmadan kullanma

```python
from pathlib import Path

from src.models.mission import MapOverlay
from src.services.runtime import create_runtime_services

runtime = create_runtime_services(Path.cwd())
runtime.start()

runtime.facade.upsert_mission_item(
    MapOverlay(
        overlay_id="wp-1",
        overlay_type="waypoint",
        label="WP 1",
        points=[[39.86, 32.74, 120.0]],
        color="#74d99f",
    )
)

state = runtime.facade.mission_snapshot()
runtime.stop()
```

Sürekli veri gereken istemciler servis olaylarına abone olabilir:

```python
runtime.planner_service.changed.subscribe(on_planner_changed)
runtime.telemetry_service.updated.subscribe(on_telemetry_changed)
```

## Genişletme rehberi

Yeni özellik eklerken önce verinin ve kararın sahibini belirleyin. UI yalnızca girdi toplar ve sonucu gösterir; bağlantı, doğrulama ve iş akışı backend’de kalır.

### Yeni telemetri alanı

1. Alanı `TelemetrySnapshot` içinde doğru birim ve bilinmeyen değer davranışıyla tanımlayın.
2. MAVLink mesajını yalnızca `services/vehicle` katmanında çözümleyin.
3. Veri birden fazla mesajdan birleşiyorsa tazelik zamanlarını ayrı tutun.
4. UI adaptörüne ham MAVLink mesajı değil, güncellenmiş model gönderin.
5. Aynı bilgi başka panelde varsa tekrar göstermeyin.
6. Birim dönüşümü, eksik mesaj ve başka system ID’den gelen paket için test ekleyin.

### Yeni araç komutu

1. Komut kodunu ve parametrelerini `CommandRequest` üzerinden tanımlayın.
2. Yönlendirme kararını `DispatchCommandService` içine ekleyin.
3. Araç yeteneği eksikse önce `VehicleService` sözleşmesini genişletin.
4. MAVLink sabiti, hedef system/component ve ACK filtresi `services/vehicle` içinde kalmalıdır.
5. UI girdisini `ui/config/commands.py` kataloğuna ekleyin.
6. Başarı mesajını yalnızca ACK veya doğrulanmış araç durumu sonrasında üretin.
7. Kabul, ret, timeout ve bağlantı kaybı testlerini birlikte yazın.

### Yeni araç üstü görev tetikleyicisi

Hedef kilitlenme veya kamikaze gibi algoritma YKİ servisine eklenmez.

1. UI kataloğu yalnızca görev talebini üretir.
2. `DispatchCommandService` talebi araç üstü uygulama kanalına yönlendirir.
3. Orin/Jetson tarafı komutu alır, algoritmayı başlatır ve ACK döner.
4. İşlem sonucu ayrı bir sonuç modeliyle YKİ’ya gelir.
5. TEKNOFEST gönderimi yalnızca araç üstü kaynak kimliği doğrulanan sonuçtan yapılır.
6. YKİ bağlantı yokken görevi başarılı göstermemeli veya sonuç üretmemelidir.

### Yeni harita verisi

1. Nokta, alan veya canlı temas için mevcut `MapOverlay`/`MapContact` modelini kullanın.
2. Yeni türü `ui/config/map.py` içinde geometri, düzenlenebilirlik ve aktarım davranışıyla tanımlayın.
3. Dış kaynaktan gelen öğelere `metadata.source` ve `read_only` bilgisi verin.
4. Kaynak güncellemesini `replace_source_overlays()` veya `replace_contacts()` ile atomik yapın.
5. Araca aktarılmayacak öğeleri açıkça `mission_upload: false` olarak işaretleyin.
6. Silme, arşivleme, eşzamanlı güncelleme ve kaynak izolasyonu testlerini ekleyin.

### Yeni ayar

1. Kalıcı değeri `models/settings.py` içindeki doğru `backend` veya `ui` grubuna ekleyin.
2. Tip ve aralık doğrulamasını modelde yapın.
3. Alanı `ui/config/settings_schema.py` içine ekleyin.
4. Aynı anahtarı `ui/i18n/tr.json` ve `ui/i18n/en.json` dil paketlerine ekleyin;
   paket anahtarlarının birebir eşleşmesi testlerle zorunlu tutulur.
5. Çalışma zamanında etkili olacaksa `runtime.py` içindeki ayar aboneliğinden ilgili servisin `configure()` metodunu çağırın.
6. Eski ayar dosyasından geçiş ve hatalı değer testlerini yazın.

### Yeni backend servisi

1. Sözleşmeyi `services/contracts` altında tanımlayın.
2. Servisin kullandığı modelleri ve olayları belirleyin.
3. Ana backend servisini doğrudan `services/<servis>.py` altında tutun.
   Telemetri, parametre ve protokol adaptörleri `vehicle` altında bulunur.
4. Bağımlılıkları constructor üzerinden açıkça verin; servis içinden global container çözmeyin.
5. Altyapı servisini `infrastructure_dependencies.py`, ortak araç adaptörünü
   `vehicle/dependencies.py`, uygulama davranışını ise
   `application_dependencies.py` içinde kaydedin.
6. Somut tip ile sözleşmesini aynı fabrika üzerinden kaydedin; servis içinden
   global container çözmeyin.
7. Aktif kaynak sahibi ise çözümlenen servisi `RuntimeServices` alanlarına ve
   yaşam döngüsü sırasına ekleyin.
8. Dış arayüz gerekiyorsa `BackendFacade` üzerinden dar bir API açın.
9. Qt adaptörü veya sinyali yalnızca UI gerçekten tüketecekse ekleyin.
10. Donanım olmadan çalışan contract testi ve hata yolu testi ekleyin.

### Entegrasyon kontrol listesi

- `core` içinde PyQt6, `pymavlink` alan servisi veya proje adı bulunmuyor.
- `models` servis veya UI import etmiyor.
- Üst seviye servisler doğrudan `core.mavlink` import etmiyor.
- Aynı fiziksel bağlantı için ikinci okuyucu açılmıyor.
- UI başarılı sonucu backend doğrulamasından önce göstermiyor.
- Bağlantı kesilince eski veri dış sisteme gönderilmiyor.
- Dış kaynaklı harita öğeleri kullanıcı planını silmiyor.
- Yeni aksiyon yapılandırılmış log üretiyor; yalnızca butona basılması başarı logu oluşturmuyor.
- Tam test paketi ve ilgili donanım smoke testi çalıştırılıyor.

## Loglar

Backend olayları `data/logs/yki.jsonl` dosyasına yazılır. Araç kimliği, bağlantı geçişi, parametre değişikliği, uçuş planı aktarımı, video pipeline durumu ve doğrulanmış komut sonucu UI kapalı olsa da kaydedilir.

TEKNOFEST HTTP trafiği YKİ logunda ve sahte sunucu konsolunda yön bilgisiyle izlenir. Sorun araştırırken UI mesajı yerine ilgili backend action ve correlation bilgisi esas alınmalıdır.

Log panelinde metin araması ve seviye filtresi bulunur. Operatör geçmişi
incelerken yeni kayıtlar görünümü aşağı zorlamaz; otomatik takip yalnızca
scroll zaten en alttayken devam eder. Yapılandırılmış kayıtlar saklanan ham
mesaj değiştirilmeden seçili arayüz dilinde görüntülenir.

## Dil paketleri

Arayüz metinleri `src/ui/i18n/en.json` ve `src/ui/i18n/tr.json` dosyalarında
tutulur. İki paketin anahtarları birebir aynı olmalıdır. Yeni metin eklerken
her iki JSON dosyasını birlikte güncelleyin; eksik anahtar ve geçersiz JSON
testlerde hata üretir.

## Test

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python -B src/core/tests/run.py
```

İlk komut; uygulama mimarisi, sabitlenmiş core sınırı, DI bileşimi, ortak
araç/Orin istemcisi, Onboard görev/QR akışı, uzak loglar, parametre
varsayılanları, komut tekrarları, eşzamanlı planlama, TEKNOFEST haberleşmesi,
batarya voltaj tahmini ve politikaları, dil paketleri, GStreamer, harita geçmişi,
yüksek hızlı veri birleştirme, ayırıcılar, çerçevesiz pencere ve offscreen Qt
açılışı için **194 YKİ testi** çalıştırır. İkinci komut core'un
dependency, event, mission ve MAVLink iç yapılarına ait **125 testi**
çalıştırır. Paketleri ayrı tutmak aynı alt seviye testlerin iki repoda
birden bakımını önler.

Sürüm geçmişi [değişiklik günlüğünde][changelog-url] tutulur.

## Lisans

Copyright © 2026 TahsinCr.

Bu proje yalnızca GNU General Public License v3.0 (`GPL-3.0-only`) kapsamında
lisanslanmıştır. Kod kullanılabilir, incelenebilir, değiştirilebilir ve
dağıtılabilir; dağıtılan kopya ve türevlerde kaynak kod, aynı lisans ve mevcut
telif/lisans bildirimleri korunmalıdır. Ayrıntılı koşullar [LICENSE](LICENSE),
telif kaydı ise [COPYRIGHT](COPYRIGHT) dosyasındadır.

<!-- Badge images -->

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white
[contributors-shield]: https://img.shields.io/github/contributors/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[forks-shield]: https://img.shields.io/github/forks/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[stars-shield]: https://img.shields.io/github/stars/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[issues-shield]: https://img.shields.io/github/issues/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/Kirlangic-Team/ground-control-station.svg?style=for-the-badge

<!-- Project links -->

[python-url]: https://docs.python.org/3.11/
[contributors-url]: https://github.com/Kirlangic-Team/ground-control-station/graphs/contributors
[forks-url]: https://github.com/Kirlangic-Team/ground-control-station/network/members
[stars-url]: https://github.com/Kirlangic-Team/ground-control-station/stargazers
[issues-url]: https://github.com/Kirlangic-Team/ground-control-station/issues
[license-url]: https://github.com/Kirlangic-Team/ground-control-station/blob/main/LICENSE
[changelog-url]: https://github.com/Kirlangic-Team/ground-control-station/blob/main/CHANGELOG.md
[lang-en-url]: https://github.com/Kirlangic-Team/ground-control-station/blob/main/README.md
