# Kırlangıç Team Yer Kontrol İstasyonu

Kırlangıç Team YKİ, ArduPilot tabanlı hava araçlarını MAVLink üzerinden yönetmek, görev planlamak, uçuş telemetrisini izlemek, video akışı almak ve TEKNOFEST haberleşmesini sınamak için geliştirilmiş modüler bir masaüstü uygulamasıdır.

`v0.1-beta`, Qt arayüzü ile backend'i birbirinden ayırır. Aynı servisler masaüstü arayüzünden, gelecekteki bir web/CLI istemcisinden veya arka plan otomasyonundan kullanılabilir. Harita, görev, parametre, telemetri ve bağlantı durumu için tek bir merkezi veri kaynağı bulunur.

> Bu sürüm beta aşamasındadır. Gerçek uçuş öncesinde bağlantı adreslerini, uçuş kontrolcüsü parametrelerini ve görev planını güvenli bir test ortamında doğrulayın.

> **Video kanıt dalı:** `demo/video-evidence`, kamera tabanlı Kamikaze QR ve Target Lock gösterimini kanıtlamak için geçici simülasyonlar içerir. `data/settings.json` içindeki `backend.orin.simulate_connected=true` gerçek Orin heartbeat'i olmadan yerel görev akışlarını çalıştırır. Uçuş/entegrasyon ortamına geçmeden önce bu seçenek kapatılmalıdır.

## Öne çıkan özellikler

- ArduPilot `ardupilotmega` ve `common` dialect desteği
- Seri, UDP ve TCP MAVLink endpointleri
- Tek okuyuculu, filtrelenebilir ve thread-safe MAVLink router
- Heartbeat, attitude, konum, hız, batarya, GPS, radyo, görev ve rüzgâr telemetrisi
- Doğrulanmış ARM/DISARM, uçuş modu, LAND, GOTO, parametre ve görev işlemleri
- Waypoint, takeoff, loiter, RTL, land, güvenli bölge ve yasak bölge harita araçları
- Görevi uçuş kontrolcüsüne gönderme ve araçtan geri alma
- Vektör/uydu haritası, yerel tile cache ve kaynak sahipli TEKNOFEST katmanları
- GStreamer tabanlı RTSP, HTTP/MJPEG, RTP/UDP, V4L2/USB, dosya ve özel pipeline video girişi
- Bağlantı sağlığı, paket sayaçları, GPS özeti, batarya ve video aktarım durumu
- Şema tabanlı ayarlar ve komut katalogları
- Yapılandırılmış, dönen JSONL backend logları
- 27 rakip İHA, HSS, QR koordinatı ve yarışma endpointlerini sağlayan bağımsız sahte TEKNOFEST sunucusu
- Kamikaze görevinde WeChat/OpenCV QR çözümleme ve tek-seferlik yarışma bildirimi
- Target Lock görevinde YOLO/ONNX Runtime, ByteTrack, EKF ve şartname zamanlayıcısı
- ADB + scrcpy + V4L2 loopback ile Samsung telefonu USB kamera olarak kullanma
- Pixhawk üzerinden hedefli `V2_EXTENSION` paketleri kullanan bağımsız Orin agent'ı

## Gereksinimler

- Linux
- Python 3.11 veya üzeri
- Qt 6 / PyQt6 WebEngine
- Gerçek araç bağlantısı için `pymavlink` ve `pyserial`
- Gerçek video kaynağı için GStreamer 1.x ve PyGObject
- Video kanıt işleme için OpenCV contrib ve ONNX Runtime

Arch/EndeavourOS üzerinde gerekli GStreamer paketleri:

```bash
sudo pacman -S --needed \
  gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-plugins-ugly gst-libav python-gobject
```

## Kurulum

```bash
git clone <depo-adresi>
cd yki
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Uygulamayı başlatın:

```bash
source .venv/bin/activate
python main.py
```

İlk çalıştırmada kalıcı ayarlar `data/settings.json`, uygulama logları `data/logs/yki.jsonl`, görev arşivleri `data/missions/` ve harita önbelleği `data/map-cache/` altında oluşturulur. `data/settings.json` dosyasını elle düzenlemek yerine arayüzdeki Ayarlar ve bağlantı pencerelerini kullanmak önerilir.

## Hızlı bağlantı ayarları

Gerçek araç bağlantısı **Ayarlar > Backend > MAVLink** bölümünden veya alt çubuktaki Telemetri/Otopilot/GPS bağlantı penceresinden yönetilir. Bağlan/Yeniden Bağlan seçildiğinde endpoint ve baud önce kalıcılaştırılır, sonra aynı backend servisi yeni ayarla açılır.

Yaygın örnekler:

```text
Seri: /dev/ttyUSB0        baud: 57600
UDP:  udp:0.0.0.0:14550   baud alanı UDP için kullanılmaz
TCP:  tcp:127.0.0.1:5762  baud alanı TCP için kullanılmaz
```

Varsayılan MAVLink adresi `udp:127.0.0.1:14550`, kaynak kimliği `255/0`, heartbeat zaman aşımı `10 s` ve dialect `ardupilotmega` değeridir. Seri aygıtta izin sorunu varsa kullanıcıyı uygun gruba eklemek kalıcı çözümdür; geniş cihaz izinlerini yalnızca kontrollü test ortamında kullanın.

## Sahte TEKNOFEST sunucusu

Sunucuyu YKİ'dan ayrı bir terminalde çalıştırın:

```bash
source .venv/bin/activate
python fake_teknofest_server.py
```

Ardından ikinci terminalde `python main.py` komutunu çalıştırın. Varsayılan sunucu adresi `http://127.0.0.1:5000`, kullanıcı adı `kirlangic`, parola `kirlangic-demo` ve takım numarası `1`'dir.

```bash
python fake_teknofest_server.py \
  --host 0.0.0.0 --port 5000 --competitors 27 --team-number 1
```

Sunucu, son kabul ettiği kendi İHA konumunun yaklaşık 2 km çevresinde 27 hareketli rakip üretir. Rakiplerin örnekleme periyotları ayrıdır ve `zaman_farki` gerçek örnek yaşını milisaniye olarak taşır. HSS alanları, QR hedef koordinatı, sunucu saati, kilitlenme ve kamikaze endpointleri de simüle edilir. İstek ve yanıtlar sunucu konsolunda `RX`/`TX` yönleriyle loglanır.

Haberleşme veri sahipliği:

- **SİHA → YKİ → TEKNOFEST:** kendi telemetrisi, araç/GPS saati ve araç üstü sistemlerin oluşturduğu yarışma sonuçları
- **TEKNOFEST → YKİ:** rakip telemetrisi, HSS koordinatları, QR hedef koordinatı ve sunucu saati

YKİ eski bir telemetri snapshot'ını göndermez. Kendi İHA telemetrisi yalnızca araç bağlantısı açıkken, geçerli `GLOBAL_POSITION_INT` ve doğrudan ArduPilot `SYSTEM_TIME`/GPS zamanı tazeyken sunucuya aktarılır. Araç verisi kesilirse telemetri TX durur; sunucu oturumu ile HSS/rakip/QR alımı bağlantı sağlığı için devam edebilir.

## Video bağlantısı

Video kaynağı backend ayarlarında tür, URI, codec, gecikme ve kare zaman aşımıyla tanımlanır. UI yalnızca ortak `VideoFrame` akışını tüketir; GStreamer pipeline ayrıntıları video servisinde kalır.

Örnek kaynaklar:

```text
HTTP/MJPEG: http://192.168.1.4:4747/video
RTSP:       rtsp://192.168.1.10:8554/stream
V4L2/USB:   /dev/video0
TCP/UDP:    kaynak türü ve codec'e uygun URI
```

Bağlantı durumu yalnızca pipeline açılmasına göre değil, taze kare alımına göre belirlenir. Kare akışı kesilirse video servisi bağlantıyı düşürür; yeniden bağlanma alt bağlantı menüsünden tetiklenebilir.

## Video kanıt iş akışları

Görüntü işleme servisleri ham kare kaynağı ile Qt widget'ı arasında bulunur. UI yalnızca işlenmiş `VideoFrame` ve durum modellerini gösterir; görev mantığı, yarışma gönderimi ve log üretimi backend'de kalır.

### Kamikaze QR

**Kamikaze** görevi başlatıldığında `opencv-contrib-python-headless` içindeki WeChat QR algılayıcısı etkinleşir. OpenCV Zoo modelleri `data/models/wechat_qrcode/` altında tutulur. WeChat algılayıcısı görüntü kenarında geçersiz ROI üretirse yerleşik OpenCV QR okuyucusu yedek olarak kullanılır.

- Her görev oturumunda yalnızca ilk çözülen QR kabul edilir.
- QR'ın yakınında tek bir yeşil deşifre metni gösterilir; kutu çizilmez.
- Optik akış metni QR ile birlikte taşır, ancak kayıp kararı algılayıcıya aittir.
- QR görüntüden çıkınca metin kaldırılır.
- Kabul edilen değer oturum boyunca yeniden okunmaz veya yeniden gönderilmez.
- Aynı QR ancak Kamikaze görevi yeniden başlatıldıktan sonra yeni oturumda kabul edilebilir.
- Başarılı okuma sahte TEKNOFEST sunucusuna gider ve hem YKİ JSONL logunda hem sunucu konsolunda görünür.

Bu dalın yerel simülasyon kaynağı `opencv-kamikaze-simulation` olarak işaretlenir; gerçek sistemde sonuç verisinin Jetson/Orin üzerindeki görüntü işleme hattından gelmesi gerekir.

### Target Lock

**Target Lock** görevi, `TargetLockVideoService` üzerinden `target_tracker/` paketindeki YOLO çıkarımı, ByteTrack, EKF, vuruş alanı ve kilit zamanlayıcısını etkinleştirir. Çıkarım ayrı worker'da çalışır; video karesi bekletilmez ve görev nesli değiştiğinde geç kalan eski inference sonucu atılır.

Görsel durumlar:

- Sarı çerçeve: kilit modu etkin, süreç devam ediyor
- Kırmızı hedef kutusu: takip edilen uçak
- Yeşil çerçeve: kilit başarıyla tamamlandı; 1 saniye gösterilir
- Kırmızı çerçeve: 5 saniyelik deneme penceresi başarısız; 1 saniye gösterilir

Kilit koşulu, 5 saniyelik pencere içinde toplam 4 saniye geçerli hedef takibidir. Hedefin kısa süre kaybolması biriken geçerli süreyi silmez; pencere sonundaki toplam süre değerlendirilir. Hedef merkezi görüntünün yatay orta `%50`, dikey orta `%80` alanında olmalı ve hedefin en az bir ekseni görüntünün en az `%6`'sını kaplamalıdır. Başarılı olay merkezi telemetri hedef kutusunu günceller ve TEKNOFEST kilitlenme endpointine gönderilir.

Varsayılan model `data/models/target_lock/yolo26s.pt`, çalışma zamanı fallback'i aynı klasördeki ONNX dosyasıdır. Genel COCO `airplane` sınıfı yalnızca gösterim başlangıç modelidir; yarışma kamerası için özel SİHA veri kümesiyle eğitilmiş ve saha verisiyle doğrulanmış model kullanılmalıdır.

### Samsung telefonu USB kamera olarak kullanma

Galaxy S25 Ultra, ADB ve scrcpy ile `/dev/video20` V4L2 kaynağına aktarılabilir. Kernel modülü, kalıcı aygıt numarası ve kullanıcı erişimini kurmak için:

```bash
./install_galaxy_usb_camera.sh
```

Entegrasyonu kaldırmak için:

```bash
./remove_galaxy_usb_camera.sh
```

Kurulum herkese yazma izni veren `chmod 666` kullanmaz; udev/grup tabanlı erişim tanımlar. YKİ ayarlarında video türü `usb`, URI `/dev/video20` olmalıdır. Telefon ayarlarından ADB seri numarası, ön/arka kamera, çözünürlük, FPS, bitrate ve yeniden deneme aralığı değiştirilebilir. Backend scrcpy sürecini ve gerçek kare akışını ayrı ayrı izler; ikisinden biri yoksa Video bağlantısı yeşile dönmez.

```bash
adb devices -l
v4l2-ctl --device=/dev/video20 --all
```

## Orin MAVLink uygulama kanalı

Fiziksel yol `YKİ → RFD → Pixhawk MAVLink router → Orin` şeklindedir. YKİ Orin seri portunu ayrıca açmaz; mevcut `MavlinkClient` tek okuyucu kalır. Uygulama paketleri aynı bağlantı üzerinde hedefli ve CRC doğrulamalı `V2_EXTENSION` mesajları olarak taşınır.

Varsayılan kimlikler:

```text
Uygulama ağ kimliği: 77
Yerel mesaj tipi:    42000
YKİ system/component: 255/0
Araç system ID:       1
Orin component ID:    191
```

Orin standart MAVLink heartbeat'ini component `191` ile, uygulama sağlık heartbeat'ini varsayılan olarak 5 saniyede bir yollar. Alt bardaki ORIN durumu gerçek kullanımda yalnızca taze uygulama heartbeat'iyle aktif olur; telemetri hattının açık olması tek başına yeterli değildir.

Agent yapılandırmasını doğrulayıp elle başlatma:

```bash
python3 -m pip install "pymavlink>=2.4.49,<3"
python3 orin_mavlink_agent.py --config config.json --check-config
python3 orin_mavlink_agent.py --config config.json
```

Kalıcı Orin dizini ve systemd unit'i `/home/kirlangic/kirlangic-orin-link` yolunu kullanır:

```bash
./install_orin_autostart.sh
sudo systemctl --no-pager --full status kirlangic-orin-link.service
./remove_orin_autostart.sh
```

Kaldırma scripti yalnızca otomatik başlangıcı kaldırır; agent ve config dosyalarını silmez. Seri portu aynı anda tek süreç açmalıdır. Başka bir süreç portun sahibiyse agent'a `mavlink-router` üzerinden ayrılmış UDP çıkışı verilmelidir. Pixhawk `SERIALx_PROTOCOL` MAVLink2 ve port baud değeri `config.json` ile aynı olmalıdır.

`config.json` içindeki `commands` başlangıçta boştur. Geçici bir görev scripti eklemek için dosyası var olan, mutlak veya config'e göre çözülebilen bir komut tanımlayın:

```json
{
  "commands": {
    "CAMERA_START": ["/usr/bin/python3", "/home/kirlangic/apps/camera_start.py"]
  }
}
```

Komutlar shell üzerinden geçirilmez. Payload JSON'u stdin ve `ORIN_PAYLOAD_JSON` ortam değişkeniyle verilir. Uzun süreli üretim görevleri için script eşlemesi yerine `OrinMavlinkAgent.register_task_handler()` ile işleyici kaydetmek önerilir:

```python
def start_camera(parameters):
    camera.start(parameters)
    return True, "camera started", {"execution": "running"}

agent.register_task_handler("CAMERA_START", start_camera)
```

UI dışındaki istemciler aynı kanalı facade üzerinden kullanabilir:

```python
receipt = runtime.facade.issue_orin("camera.control", {"mode": "track"})
subscription = runtime.facade.subscribe_orin(on_orin_message)
```

## Mimari

```text
main.py
└── src/app.py                         # Qt uygulama yaşam döngüsü
    └── src/services/runtime.py         # Tek kompozisyon kökü

src/
├── core/                              # Araca ve UI'a bağımsız altyapı
│   ├── abstracts.py                   # Model/servis sözleşmelerinin temeli
│   ├── dependency.py                  # Singleton/scoped/transient DI
│   ├── events.py                      # Thread-safe olay akışı
│   └── mavlink/                       # Endpoint, bağlantı, router, filtre ve kanal
├── models/                            # Veri, doğrulama, hesaplama ve dönüşümler
├── services/
│   ├── contracts/                     # Soyut servis portları
│   ├── vehicle/                       # MAVLink'i alan servislerinden saklayan adaptör
│   ├── processing/                    # Görev, telemetri ve parametre durumu
│   ├── orchestration/                 # Komut ve bağlantı kullanım senaryoları
│   ├── competition.py                 # TEKNOFEST HTTP istemcisi
│   ├── video.py                       # UI bağımsız GStreamer servisi
│   ├── facade.py                      # Alternatif arayüzler için kararlı API
│   └── runtime.py                     # Bağımlılıkların oluşturulduğu tek yer
└── ui/
    ├── adapters/                      # EventStream ↔ Qt signal köprüleri
    ├── config/                        # Ayar, komut ve harita şemaları
    ├── themes/                        # Tema ve stylesheet
    ├── views/                         # Pencere/dialog kabukları
    ├── widgets/                       # Tekrar kullanılabilir widget'lar
    └── web/                           # Leaflet görev haritası

target_tracker/                        # Video kanıt hedef algılama/takip bileşenleri
├── perception/                        # YOLO/ONNX çıkarımı ve postprocess
├── tracking/                          # ByteTrack, EKF ve yeniden algılama
├── lock_logic/                        # Vuruş alanı, zamanlayıcı ve durum makinesi
├── control/                           # PID, anti-windup ve menzil döngüsü
├── comms/                             # MAVLink/APN/sunucu adaptörleri
└── recording/                         # Video ve zaman senkronizasyonu
```

Bağımlılık yönü `ui → services → models → core` şeklindedir. Araç haberleşmesi `core/mavlink → services/vehicle → alan servisleri` zincirini izler. Alan servisleri `pymavlink` veya somut MAVLink client import etmez; `VehicleService` sözleşmesini kullanır. Somut implementasyonlar yalnızca `services/runtime.py` içinde birleştirilir.

### Katman sorumlulukları

- **core:** İKA, SİHA, CLI veya sunucu uygulamasında yeniden kullanılabilen, alan ve UI bağımsız yapılar
- **models:** Verinin kendisi ve veriye doğal olarak ait doğrulama, mesafe, dönüşüm gibi davranışlar
- **services/contracts:** Telemetri, görev, araç, komut, parametre, yarışma, video, cache, arşiv ve log yeteneklerinin portları
- **services/vehicle:** Düşük seviyeli MAVLink ayrıntılarını kapsülleyen çift yönlü uçuş cihazı adaptörü
- **services/processing:** Merkezi ve eşzamanlı erişilebilen görev/telemetri/parametre durumu
- **services/orchestration:** Birden çok servisi bir kullanım senaryosunda yöneten üst katman
- **ui:** Qt, widget, tema ve Leaflet entegrasyonu; iş kurallarının sahibi değildir

Servisler bağımlılıklarını constructor üzerinden alır. `DependencyContainer` kompozisyon ve scope yönetimi içindir; servis içinden global service locator olarak kullanılmaz.

## Merkezi görev ve harita akışı

```text
Qt/Leaflet ─────────┐
REST/CLI/otomasyon ─┼─> MissionService ─> MissionState(revision N+1)
Görev motoru ───────┘          │
                               ├─> Qt haritası
                               ├─> alternatif arayüz
                               ├─> log/telemetri servisi
                               └─> MAVLink görev adaptörü
```

`MissionStateService`, her mutasyonda yeni revision ve kopyalanmış snapshot yayınlar. Waypoint, takeoff, RTL, land, loiter, güvenli/yasak alan ve rota güncellemeleri tek kilit altında atomiktir.

Görev öğeleri `MapOverlay`, canlı araç ve rakipler `MapContact` ile temsil edilir. Canlı temaslar görev arşivine yazılmaz. TEKNOFEST HSS ve QR verileri `metadata.source` ile sahiplenilir; `replace_source_overlays` yalnızca ilgili dış kaynak grubunu yeniler ve operatörün görev planına dokunmaz. HSS ve güvenlik poligonları doğrudan ArduPilot mission komutuna çevrilmez.

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

snapshot = runtime.facade.mission_snapshot()
runtime.stop()
```

Canlı yayınlar için servis sözleşmelerine abone olunabilir:

```python
runtime.mission_service.changed.subscribe(
    lambda state: websocket.broadcast(state.to_dict())
)
runtime.telemetry_service.updated.subscribe(
    lambda value: websocket.broadcast(value.to_dict())
)
```

## MAVLink çekirdeği

`MavlinkClient`, connection ve tek-okuyucu router yaşam döngüsünü birlikte yönetir:

```python
from src.core.mavlink import MavlinkClient, MavlinkEndpoint, MavlinkMessageFilter

client = MavlinkClient(MavlinkEndpoint("udp:127.0.0.1:14550"))
subscription = client.subscribe(
    on_attitude,
    MavlinkMessageFilter.for_types(
        "ATTITUDE",
        source_systems={1},
        source_components={1},
        condition="ATTITUDE.roll > 0",
        predicate=lambda message: abs(message.pitch) < 0.5,
    ),
)
client.start()
latest = client.latest("ATTITUDE")
subscription.cancel()
client.stop()
```

Filtreler MAVLink'in `get_type()`, `get_srcSystem()`, `get_srcComponent()`, `get_msgId()` ve native condition yapılarını kullanır. Router metadata'yı bir kez çıkarır ve abonelikleri mesaj tipine göre indeksler.

### Request/response sınırı

Yeni istekten önce router sequence alınır; böylece cache'teki eski ACK yeni işlemin cevabı sanılmaz:

```python
boundary = client.router.sequence
client.call_mav("command_long_send", ...)
ack = client.wait_for(
    MavlinkMessageFilter.for_types(
        "COMMAND_ACK",
        predicate=lambda message: message.command == expected_command,
    ),
    after_sequence=boundary,
    timeout=3.0,
)
```

Uygulama runtime'ında aynı socket üzerinde doğrudan `recv_match()`/`receive()` ile ikinci okuyucu açılmamalıdır. Bütün tüketiciler `client.subscribe()` veya `client.wait_for()` kullanır; bütün yazmalar aynı send kilidindeki `client.call_mav()` üzerinden geçer.

Router cache ve geçmişi sınırlı `deque` yapılarıyla tutar. Callback hatası diğer abonelikleri durdurmaz. `MavlinkAsyncChannel`, aynı router'a bağlanır; kuyruk dolduğunda en eski mesajı düşürür ve sayacı görünür kılar.

### Doğrulanan işlemler

- ARM, LAND ve genel komutlar: `COMMAND_ACK`
- Mod değişimi: istenen modu taşıyan yeni `HEARTBEAT`
- Parametre yazımı: aynı anahtar/değeri taşıyan `PARAM_VALUE`
- Görev yükleme: `MISSION_REQUEST(_INT)` akışı ve `MISSION_ACK`
- Bağlantı sağlığı: hedef system/component heartbeat watchdog

Normal ARM başarısız olduğunda zorla ARM yalnızca operatörün açık onayıyla, `MAV_CMD_COMPONENT_ARM_DISARM` parametre 2 değeri `21196` kullanılarak denenir. Otomatik zorla ARM yapılmaz.

Araç bağlandığında parametre kataloğu `PARAM_REQUEST_LIST` ile alınır. Görev planı `MISSION_ITEM_INT` ile yüklenir; desteklenen öğeler takeoff, waypoint, loiter, RTL ve land'dir. ARM/DISARM veya mod değişimi görev planını kendiliğinden göndermez.

## Genişletme rehberi

### Yeni servis

1. Yeteneğin portunu `src/services/contracts/` altında tanımlayın.
2. Model ve doğrulamaları `src/models/` altında tutun.
3. Implementasyonu `processing`, `orchestration`, `vehicle` veya anlamlı bir alan modülüne ekleyin.
4. Bağımlılıkları constructor ile verin.
5. Somut servisi yalnızca `src/services/runtime.py` içinde oluşturun.
6. `RuntimeServices.start()`/`stop()` yaşam döngüsüne ekleyin.
7. Donanım gerektirmeyen sözleşme ve veri akışı testleri yazın.

### Yeni ayar

Backend verisini `src/models/settings.py`, görünüm tanımını `src/ui/config/settings_schema.py`, metinleri `src/ui/config/i18n.py` içinde ekleyin. `combo`, `check`, `spin` ve `text` alanları için dialog/widget kodu değiştirilmez. Backend ve UI ayarları kalıcı JSON içinde ayrı köklerde tutulur.

### Yeni komut veya harita aracı

- Otonom komut sunumu: `src/ui/config/commands.py`
- Araç davranışı: `VehicleService` implementasyonu veya enjekte edilen custom handler
- Harita tipi/geometri/düzenlenebilirlik: `src/ui/config/map.py`
- Merkezi veri: `MapOverlay`/`MapContact` ve `MissionService`

UI kataloğundaki bir görev komutu yarışma sonucu değildir. Sunucuya gönderilecek başarılı kilitlenme/kamikaze gibi sonuçlar yalnızca araç üstü backend'in oluşturduğu doğrulanmış modellerden gelmelidir.

## Loglama ve gözlemlenebilirlik

Gerçek backend olayları `data/logs/yki.jsonl` içinde zaman damgası, kaynak, action, seviye ve ayrıntılarla saklanır. Log dosyası döndürülür; uzun çalışmada sınırsız büyümez. Araç kimliği, bağlantı geçişleri, parametre değişiklikleri, görev senkronizasyonu, video pipeline durumu ve doğrulanmış komut sonuçları UI kapalı olsa da kaydedilir.

Bir butona basılması tek başına başarı logu üretmez. Başarı, mümkün olan en güçlü protokol cevabı alındıktan sonra yazılır. TEKNOFEST HTTP trafiği istemci JSONL logunda ve sahte sunucu terminalinde yön bilgisiyle izlenebilir.

## Testler

Tüm testleri bytecode üretmeden çalıştırın:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests -v
```

Test paketi katman sınırlarını, DI yaşam döngüsünü, MAVLink filtre/router davranışını, görev dönüşümlerini, parametre doğrulamasını, bağlantı sağlıklarını, ayar göçünü, eşzamanlı harita yazımlarını, arşivi, TEKNOFEST haberleşmesini, GStreamer pipeline üretimini, Leaflet/stylesheet bütünlüğünü ve offscreen Qt ana pencere açılışını kapsar.

Video kanıt dalındaki QR, Target Lock, telefon kamera ve Orin agent kapsamıyla birlikte yerel paket **128 testten** geçer.

## Dalın amacı

`demo/video-evidence` kamera tabanlı QR ve hedef kilitlenme kanıtlarını, telefon kamera köprüsünü ve Orin simülasyonunu saklayan gösterim dalıdır. Ana beta sürümü ve `v0.1-beta` etiketi `main` dalındadır. Bu dal gerçek uçuş yapılandırması olarak doğrudan dağıtılmamalıdır.
