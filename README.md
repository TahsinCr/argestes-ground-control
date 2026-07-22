# Kirlangic Team Yer Kontrol İstasyonu

Arayüzü backend'den ayrılmış, ArduPilot/MAVLink tabanlı ve yeni veri kaynakları ile arayüzlere açık modüler YKİ uygulaması.

Mevcut PyQt6 görünümü, tema değerleri, Leaflet haritası ve kullanıcı akışları korunmuştur. Teknik paketler marka öneki taşımadan doğrudan `core`, `models`, `services` ve `ui` adlarını kullanır.

## Güvenli geri dönüş noktası

Mimari dönüşümden önceki çalışan durum Git'e kaydedildi:

```text
f435832 chore: preserve working YKI baseline
```

Bu sürümü incelemek için `git show f435832`, ayrı bir dalda açmak için `git switch -c eski-yki f435832` kullanılabilir.

Geçici WeChat QR video işleme özelliğinden hemen önceki güncel geri dönüş noktası:

```text
5995134 chore: checkpoint before temporary QR processing
```

## Kurulum ve çalıştırma

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Sahte TEKNOFEST yarışma sunucusu

Haberleşme sunucusu YKİ'dan ayrı bir süreçtir. İlk terminalde sunucuyu, ikinci terminalde uygulamayı başlatın:

```bash
source .venv/bin/activate
python fake_teknofest_server.py
```

```bash
source .venv/bin/activate
python main.py
```

Varsayılan adres `http://127.0.0.1:5000`, kullanıcı `kirlangic`, parola `kirlangic-demo` ve takım numarası `1`'dir. Bunlar hem Ayarlar > TEKNOFEST Sunucusu bölümünden hem de alt bardaki TEKNOFEST bağlantı menüsünden değiştirilebilir. Gerçek yarışma sunucusuna geçerken kod değil yalnızca bu ayarlar değişir.

Sunucu son kabul ettiği kendi İHA konumunun 2 km yakınında 27 hareketli rakip üretir. Ayrıca dört HSS alanı, QR koordinatı, sunucu saati, kilitlenme ve kamikaze endpointlerini simüle eder. Her rakibin veri üretim periyodu ayrıdır; `zaman_farki` son rakip örneğinin gerçek milisaniye yaşıdır. Bütün istek ve yanıtlar sunucu terminalinde `RX`/`TX` yönüyle görünür.

Haberleşme sahipliği bilinçli olarak ikiye ayrılmıştır:

- `SİHA -> YKİ -> TEKNOFEST`: kendi telemetrisi, doğrudan SİHA/GPS saati, görüntü işlemenin hedef kutusu, başarılı kilitlenme ve kamikaze/okunmuş QR metni.
- `TEKNOFEST -> YKİ`: 27 rakip telemetrisi, HSS koordinatları, QR hedef koordinatı ve sunucu saati.

Normal uçuş yapısında YKİ kilitlenme, hedef kutusu, GPS saati veya kamikaze QR metni üretmez; bunlar araç/Jetson backend'inden gelmelidir. Geçici kamera testi için etkin olan `simulate_connected` kipinde ise `KAMIKAZE` görevi YKİ'daki OpenCV/WeChat QR akışını açar ve ilk okunan QR, kaynağı `opencv-kamikaze-simulation` olarak açıkça işaretlenerek sahte sunucuya gönderilir. Gerçek Orin devreye alındığında bu geçici ayar kapatılmalıdır. Kendi SİHA telemetrisi yalnızca `VehicleService.connected` doğrudan araç bağlantısını doğrularken, telemetri/otopilot hattı açıkken, geçerli `GLOBAL_POSITION_INT` konumu ve doğrudan ArduPilot `SYSTEM_TIME`/GPS zamanı tazeyken gönderilir. Telemetri modelinde eski bir snapshot kalmış olsa bile araç bağlantısı kapalıysa `/api/telemetri_gonder` çağrılmaz. Bu araç verilerinden biri kesilir veya bayatlarsa TX durur; TEKNOFEST oturumu ile HSS/QR alımı ise bağlantı sağlığı için devam eder. Varsayılan tazelik penceresi telemetri gönderim periyodunun üç katıdır (en az 2 saniye; varsayılan ayarda 3 saniye).

Komut satırından adres ve senaryo da ayarlanabilir:

```bash
python fake_teknofest_server.py --host 0.0.0.0 --port 5000 --competitors 27 --team-number 1
```

GStreamer çalışma zamanı EndeavourOS/Arch üzerinde şu paketlerle sağlanır (bu çalışma alanında kurulu ve doğrulanmıştır):

```bash
sudo pacman -S --needed gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav python-gobject
```

`PyGObject` proje sanal ortamına da kurulmuştur. RTSP, H.264/RTP UDP, V4L2 USB, dosya ve özel pipeline kaynakları UI'dan bağımsız `VideoFrame` akışına dönüştürülür.
Bu çalışma alanında sudo gerektiren `gst-plugins-good` sistem kurulumu yerine gerekli ve sürüm uyumlu RTSP/RTP/V4L2 pluginleri `~/.local/share/kirlangic-yki/gstreamer-1.0` altına kurulmuştur; video servisi bu kullanıcı yolunu başlangıçta otomatik tarar.

Video akışında `opencv-contrib-python-headless` içindeki WeChat QR algılayıcısı yalnızca Kamikaze görevi etkinleştirildiğinde çalışır. Resmi OpenCV Zoo modelleri `data/models/wechat_qrcode` altında tutulur. WeChat'in görüntü kenarındaki QR'larda oluşturabildiği geçersiz ROI hatası güvenli kenar payıyla engellenir; yine de hata oluşursa yerleşik OpenCV QR okuyucusu aynı kareyi yedek olarak çözer. Her Kamikaze oturumunda yalnızca ilk çözülen QR izlenir ve diğer QR sonuçları yok sayılır. Çözülen QR için kutu çizilmez; yalnızca QR'ın yakınında yeşil deşifre metni gösterilir. Optik akış taramalar arasındaki konumu yumuşatır ancak algılayıcının kayıp kararını uzatmaz; QR görüntüden çıkınca metin de kaldırılır. Kabul edilen QR metni görev oturumu boyunca bellekte tutulduğu için aynı QR yeniden gönderilmez; bellek ancak Kamikaze görevi yeniden başlatıldığında sıfırlanır. `qr.*`, `kamikaze.qr_*` ve `competition.kamikaze_*` geçişleri backend/UI loguna yazılır; sahte sunucu terminalinde `RX KAMIKAZE`, `RX KAMIKAZE_QR` ve `TX KAMIKAZE_ACK` satırları görünür. Kamera akışı kesilirse video kaynağı yapılandırılmış kare zaman aşımı aralığında otomatik olarak tekrar başlatılır; manuel yeniden bağlanma düğmesi de kullanılabilir.

### Target Lock görüntü işleme

Otonom görevlerden **Target Lock** uygulandığında kamera akışına UI'dan bağımsız `TargetLockVideoService` eklenir. Servis `target_tracker` içindeki YOLO, ByteTrack, EKF, vuruş alanı ve kilit zamanlayıcısını kullanır. Ağır model çıkarımı ayrı bir worker'da yapıldığı için video karesi dondurulmaz; görev değiştirildiğinde devam eden eski inference sonucu atılır.

Kilit modu boyunca görüntünün yatay orta `%50`, dikey orta `%80` bölgesini gösteren çerçeve normalde sarıdır. Uçak hedefi kırmızı, en fazla 3 piksel kalınlığındaki kutuyla gösterilir. İlk geçerli hedefle 5 saniyelik deneme penceresi başlar. Hedef merkezi bu alanda ve hedefin en az bir ekseni görüntünün en az `%6`'sı iken biriken süre sayılır; kısa kopmalar sayacı sıfırlamaz. Bu pencere içinde toplam 4 saniye birikirse kilit başarılıdır. Kilit başarılı olursa çerçeve ve durum metni 1 saniye yeşil, 5 saniyelik pencere toplam 4 saniyeye ulaşmadan biterse 1 saniye kırmızı olur; ardından çerçeve yeniden sarıya döner. Kilit tamamlandığında merkezi telemetri hedef kutusu güncellenir ve olay en geç 2 saniyelik gönderim penceresinde TEKNOFEST servisine aktarılır. `target_lock.*` ile `competition.lock_*` olayları YKİ loguna, `RX KILITLENME` ve `TX KILITLENME_ACK` satırları sahte sunucu terminaline düşer.

Resmi Ultralytics `yolo26s.pt` ağırlığı ve aynı ağırlıktan üretilen ONNX dosyası `data/models/target_lock` altındadır. YKİ sanal ortamında `ultralytics`/PyTorch zorunlu değildir; PT yükleyicisi yoksa servis kardeş `.onnx` dosyasına ve ONNX Runtime'a otomatik geçer. Bu COCO `airplane` sınıfı kullanan genel amaçlı bir başlangıç modelidir; yarışma kamerasının irtifa, açı ve hedef tiplerinde güvenilirlik için ayrı bir SİHA veri kümesiyle fine-tune edilmelidir. Kaynak, checksum ve export bilgisi [model notunda](data/models/target_lock/README.md) kayıtlıdır.

### Samsung telefonu USB kamera olarak kullanma

Galaxy S25 Ultra, ADB ve scrcpy aracılığıyla `/dev/video20` V4L2 kaynağına aktarılır. Kernel aygıtını ilk kez oluşturmak ve her açılışta aynı numara/etiketle yüklenmesini sağlamak için bir kez şunu çalıştırın:

```bash
./install_galaxy_usb_camera.sh
```

Kurucu `modules-load.d`, `modprobe.d` ve kullanıcıya özel erişim sağlayan udev kuralını ekler; aygıtı herkese yazılabilir yapan `chmod 666` kullanmaz. Geçici entegrasyonu kaldırmak için:

```bash
./remove_galaxy_usb_camera.sh
```

YKİ içinde **Ayarlar > Samsung USB Kamera** bölümünden ADB seri numarası, arka/ön kamera, çözünürlük, FPS, bitrate ve yeniden deneme aralığı değiştirilebilir. Video kaynağı `usb`, URI `/dev/video20` olmalıdır. Varsayılan profil `1920x1080`, `30 FPS`, `16 Mbps` ve arka kameradır.

Uygulama açılırken backend yetkilendirilmiş ADB cihazını kontrol eder, scrcpy kamera sürecini UI'dan bağımsız başlatır ve kapanırsa otomatik yeniden başlatır. Video servisi de `/dev/video20` karelerini ayrıca izler ve kare akışı kesilirse yeniden bağlanır. Alt bardaki **Video** bağlantı penceresi hem scrcpy/ADB köprüsünü hem gerçek kare, FPS, bitrate ve kalite durumunu gösterir. Telefon bağlı değilse durum yeşile dönmez.

Komut satırı kontrolü için:

```bash
adb devices -l
v4l2-ctl --device=/dev/video20 --all
```

Gerçek araç bağlantısı Ayarlar > Backend > MAVLink bölümünden etkinleştirilir. Alt bardaki Telemetri/Otopilot/GPS bağlantı penceresinde endpoint veya baud değiştirilip Bağlan/Yeniden Bağlan seçildiğinde ayar önce kalıcılaştırılır, ardından aynı MAVLink servisi yeni fiziksel portla yeniden açılır. `pymavlink` yalnızca gerçek MAVLink bağlantısı açılırken yüklenir; UI ve backend testleri araç olmadan çalışabilir.

### Orin uygulama kanalı

Orin için ikinci bir seri okuyucu açılmaz. YKI'nın mevcut RFD/Pixhawk MAVLink
hattı üzerinde hedefli `V2_EXTENSION` paketleri kullanılır. Alt bardaki **ORIN**
kutusu, telemetri bağlı olsa bile Orin'in kendi uygulama heartbeat'i alınmadan
yeşile dönmez. Kutunun popup formu ortak MAVLink endpoint/baud ayarlarını
Telemetri popup'ıyla paylaşır; üst sağlık alanı Orin heartbeat yaşını, aktif
görevi ve son komut ACK gecikmesini gösterir.

Şu an `data/settings.json` içindeki `backend.orin.simulate_connected=true`
ayarı nedeniyle ORIN kutusu açıkça **GEÇİCİ SİMÜLASYON** olarak yeşil
gösterilir ve görev komutları yerel iş akışlarını tetikler. Gerçek Orin
heartbeat hattına dönmek için bu ayarı kapatıp YKİ'yı yeniden başlatın.

Araç tarafındaki tek dosyalık bağımsız ajan:

```bash
python orin_mavlink_agent.py --config config.json
```

Orin'e kurulan yapı `config.json` kullanır ve systemd ile açılışta başlar:

```bash
python orin_mavlink_agent.py --config config.json --check-config
./install_orin_autostart.sh
./remove_orin_autostart.sh
```

`config.json` içindeki `commands` alanına `"KOMUT": "çalıştırılacak_script.py"`
eklenebilir. Scriptler shell üzerinden geçirilmez; JSON parametreleri stdin ve
ortam değişkeniyle alır.

Kurulum, Pixhawk routing kimlikleri ve kamera/görev işleyicisi ekleme örnekleri
için [docs/ORIN_MAVLINK_KANALI.md](docs/ORIN_MAVLINK_KANALI.md) rehberine bakın.

## Mimari

```text
src/
├── core/                    # İKA, SİHA ve diğer projelerde kullanılabilir altyapı
│   ├── abstracts.py         # Evrensel servis/değer sözleşmeleri
│   ├── dependency.py        # Singleton, scoped, transient DI konteyneri
│   ├── events.py            # Qt'den bağımsız thread-safe olay akışı
│   └── mavlink/             # Bağlantı, tek-okuyucu router ve hedefli uygulama kanalı
├── models/                  # Veri + doğrulama + hesaplama/dönüşüm davranışları
├── services/
│   ├── contracts/           # Bütün servislerin soyut portları
│   ├── vehicle/             # Core MAVLink'i alan servislerinden saklayan araç adaptörü
│   ├── processing/          # Görev, telemetri, QR ve hedef kilit işleme servisleri
│   ├── orchestration/       # Birden çok yeteneği yöneten üst seviye akışlar
│   ├── video.py             # UI'dan bağımsız GStreamer video servisi
│   ├── competition.py       # TEKNOFEST HTTP oturumu ve rakip telemetrisi
│   ├── facade.py            # Web, CLI, Qt veya otomasyon için kararlı backend API
│   └── runtime.py           # Tek kompozisyon noktası
└── ui/
    ├── adapters/            # Backend EventStream <-> Qt signal köprüleri
    ├── config/              # Ayar, komut ve harita UI şemaları
    ├── themes/              # Korunan tema ve stylesheet
    ├── views/               # Pencere/dialog kabukları
    ├── widgets/             # Tekrar kullanılabilir widget'lar
    └── web/                 # Korunan Leaflet haritası
```

Bağımlılık yönü `ui -> services -> models -> core` şeklindedir. Uygulama MAVLink zinciri `core/mavlink -> services/vehicle -> alan servisleri` olarak sabittir. `processing` ve `orchestration` MAVLink client veya dialect import etmez; yalnızca `VehicleService` sözleşmesini kullanır. Bu kurallar testlerle korunur.

Ayrıntılı sahiplik, veri akışları ve genişletme tarifleri için [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) dosyasına bakın.
Filtreli MAVLink client/router kullanımı için [docs/MAVLINK.md](docs/MAVLINK.md) rehberine bakın.

## UI olmadan backend kullanımı

```python
from pathlib import Path

from src.models.mission import MapOverlay
from src.services.runtime import create_runtime_services

runtime = create_runtime_services(Path.cwd())
runtime.start()

runtime.facade.upsert_mission_item(
    MapOverlay(
        overlay_id="safe-1",
        overlay_type="safe_zone",
        label="Güvenli Alan",
        points=[[39.86, 32.74], [39.87, 32.75], [39.85, 32.76]],
        color="#73a95b",
    )
)
runtime.facade.set_setting("backend.mission.radii.rtl", 90)

snapshot = runtime.facade.mission_snapshot()
runtime.stop()
```

Aynı servis durumu Qt haritası, ileride eklenecek REST/WebSocket arayüzü veya arka plan görev motoru tarafından okunup değiştirilebilir. UI'dan ve dış tetikleyiciden gelen değişiklikler aynı atomik `MissionStateService` üzerinden yayınlanır.

## Şema üzerinden genişletme

- Ayar alanı: veri sahibini `models/settings.py` içine ekleyin; widget üretimi için yalnızca `ui/config/settings_schema.py` şemasına bir `FieldSpec` ve `ui/config/i18n.py` metinlerini ekleyin.
- Otonom komut/buton: `ui/config/commands.py` kataloğuna kayıt ekleyin. Araç davranışını `VehicleService` implementasyonunda veya `MavlinkVehicleService(custom_handlers=...)` ile sağlayın.
- Harita aracı: `ui/config/map.py` içine tip, geometri, rota ve düzenlenebilirlik bilgisini ekleyin. Merkezi model `MapOverlay` olduğu için aynı veri UI dışından da yazılabilir.
- Parametre: araç bağlanınca tam `PARAM_VALUE` kataloğu çekilir. UI/REST/CLI değişikliği `PARAM_SET` olarak gönderilir ve geri gelen aynı değer doğrulanmadan başarılı sayılmaz.
- Yeni servis: önce `services/contracts` altında portu tanımlayın; araç mesajı/komutu gerekiyorsa `VehicleService` kullanın ve yalnızca `services/runtime.py` içinde bağlayın.

TEKNOFEST akışı `CompetitionService` portu arkasındadır. HTTP istemcisi UI bilmez; sunucudan gelen rakipler tek seferde `MissionService.replace_contacts(..., source="teknofest")` ile merkezi harita durumuna yazılır. HSS ve QR hedefi de kaynak sahipli `replace_source_overlays` ile kullanıcının görev planına dokunmadan yenilenir. Bu nedenle Qt haritası, gelecekteki web arayüzü veya başka bir tetikleyici aynı sunucu verilerini okuyabilir.

## MAVLink kapsamı

Tek bir `MavlinkMessageRouter` soketi okur; telemetri, görev protokolü ve parametre servisi aynı mesaj akışını yarışmadan paylaşır. `MavlinkMessageFilter`, MAVLink'in kendi `get_type`, kaynak system/component, message-id ve native `condition` yapılarını kullanır. Tip bazlı routing sayesinde yalnızca ilgili servis callback'leri çalıştırılır. Şu bağlantılar hazırdır:

- heartbeat, konum, attitude, hız, batarya, GPS, radio, görev ve rüzgâr telemetrisi
- seçili seri/UDP endpoint'inden `RADIO_STATUS` geliyorsa yerel/uzak RF sinyal-gürültü marjı ve TX tamponundan üretilen bağlantı yüzdesi; modem bu mesajı üretmiyorsa aynı hattın MAVLink sıra numaralarındaki güncel paket teslim/kayıp oranı otomatik yedektir, RC kumanda RSSI'si bu değere karıştırılmaz
- haritanın katman seçicisi altında kısa GPS özeti: fix, uydu sayısı ve mevcutsa HDOP
- arm/disarm, doğrulanmış mod değişimi ve yatay GOTO
- haritadaki **Görevi Gönder** kontrolüyle güncel backend görevini ayrıca yükleme; ARM/DISARM ve mod değişiklikleri görev planını aktarmaz
- başlangıçta tam parametre listesi alma; doğrulanmış `PARAM_SET`
- takeoff, waypoint, loiter, RTL ve land öğelerini `MISSION_ITEM_INT` ile yükleme ve araç görevini geri alma
- ARM/LAND/mission komutlarında `COMMAND_ACK`, modda heartbeat ve görev yüklemede `MISSION_ACK` doğrulaması
- normal ARM başarısızsa yalnızca operatörün açık popup onayıyla `MAV_CMD_COMPONENT_ARM_DISARM` parametre 2=`21196` zorla ARM; otomatik zorla deneme yoktur
- heartbeat watchdog ile link kaybı, komut kilitleme ve bağlantı geri dönüşü
- Orin component `191` için CRC doğrulamalı/parçalanabilir çift yönlü uygulama paketleri; `TARGET_LOCK`, `KAMIKAZE`, `WAYPOINT_TRACK` ve `SURVEY_AREA` görevleri Orin ACK'i olmadan başarılı sayılmaz

Backend olayları `data/logs/yki.jsonl` dosyasına yapılandırılmış ve dönen JSONL kayıtları olarak yazılır. Araç kimliği, parametre değişiklikleri, görev senkronizasyonu, bağlantılar, video pipeline durumları ve doğrulanmış komut sonuçları UI açık olmasa da kaydedilir.

Güvenli/güvensiz alanlar ve HSS gibi uygulama katmanı öğeleri harita/backend durumunda tutulur; doğrudan ArduPilot mission komutuna çevrilmez. Bunlar özel bir görev motoru veya `custom_handlers` ile tüketilebilir.

## Testler

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests -v
```

Testler core/DI, katman sınırları, MAVLink dönüşümleri, eski ayar göçü, eşzamanlı harita yazımı, arşiv, şema tabanlı UI, stil/Leaflet bütünlüğü ve offscreen ana pencere açılışını kapsar.

`siha-src/` yalnızca ilk mimari fikrin referansıdır; çalışan uygulama onu import etmez.
