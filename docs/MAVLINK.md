# Modüler MAVLink kullanımı

## Önerilen yüzey: MavlinkClient

`MavlinkClient`, connection ve tek-okuyucu router yaşam döngüsünü birlikte yönetir:

```python
from src.core.mavlink import (
    MavlinkClient,
    MavlinkEndpoint,
    MavlinkMessageFilter,
)

client = MavlinkClient(MavlinkEndpoint("udp:127.0.0.1:14550"))

attitude_filter = MavlinkMessageFilter.for_types(
    "ATTITUDE",
    source_systems={1},
    source_components={1},
    condition="ATTITUDE.roll > 0",
    predicate=lambda message: abs(message.pitch) < 0.5,
)

subscription = client.subscribe(on_attitude, attitude_filter)
client.start()

latest = client.latest("ATTITUDE")
subscription.cancel()
client.stop()
```

Filtre şu temel pymavlink/MAVLink alanlarını kullanır:

- `message.get_type()`
- `message.get_srcSystem()`
- `message.get_srcComponent()`
- `message.get_msgId()`
- `mavutil.evaluate_condition(condition, connection.messages)`
- isteğe bağlı Python predicate

Router mesaj metadata'sını bir kez `MavlinkMessageEnvelope` içine çıkarır. Abonelikler mesaj tipine göre indekslendiği için örneğin `PARAM_VALUE` geldiğinde telemetri decoder callback'i çağrılmaz.

## Bekleme ve request/response sınırı

Komut cevabını beklemeden önce router sequence alınmalıdır. Böylece cache'teki eski bir cevap yeni istek cevabı sanılmaz:

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

Görev yükleme servisi aynı modeli `MISSION_REQUEST_INT` ve `MISSION_ACK` için kullanır.

## Cache, geçmiş ve sağlık

```python
heartbeat = client.latest(
    MavlinkMessageFilter.for_types("HEARTBEAT", source_systems=1)
)

recent = client.router.history({"ATTITUDE", "GLOBAL_POSITION_INT"}, limit=20)
stats = client.router.stats

client.router.errors.subscribe(
    lambda error: logger.error(error.phase, error.error)
)
```

Cache mesaj tipi başına sınırlıdır. Router geçmişi de sabit boyutlu `deque` kullanır; uzun çalışan uygulamada sınırsız bellek büyümesi oluşturmaz. Hatalı bir callback diğer aboneleri veya router thread'ini durdurmaz.

## Native recv_match filtresi

Router kullanılmayan küçük bir araçta pymavlink filtresine doğrudan erişilebilir:

```python
connection.start()
message = connection.receive(
    message_types={"HEARTBEAT", "SYS_STATUS"},
    condition="SYS_STATUS.battery_remaining < 20",
    blocking=True,
    timeout=2.0,
)
```

Uygulama runtime'ında bu yol kullanılmamalıdır. Aynı socket üzerinde hem router hem doğrudan `receive()` çağrılırsa iki okuyucu birbirinin mesajını tüketebilir. Çalışan uygulamada tüm filtreleme `client.subscribe()` veya `client.wait_for()` üzerinden yapılır.

## Gönderim ve dialect sabitleri

Bütün yazmalar aynı send kilidinden geçirilir:

```python
mavlink = client.mavlink
client.call_mav(
    "command_long_send",
    client.connection.target_system,
    client.connection.target_component,
    mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
    0,
    1, 0, 0, 0, 0, 0, 0,
)
```

`client.mavlink`, endpoint üzerinde seçilen dialect'in sabitlerini ve temel mesaj sınıflarını verir. Uygulamada bu düşük seviyeli yüzeyi yalnızca `services/vehicle` adaptörü kullanır.

## Uygulama servislerinin araç bağlantısı

Telemetri, parametre, görev veya yeni bir alan servisi `MavlinkClient` import etmez. `VehicleService` üzerinden mesaj aboneliği veya anlamlı araç işlemi açar:

```python
class WindService:
    def __init__(self, vehicle):
        self._vehicle = vehicle
        self._subscription = None

    def start(self):
        self._subscription = self._vehicle.subscribe(
            ("WIND", "WIND_COV"),
            self._consume,
        )

    def stop(self):
        if self._subscription:
            self._subscription.cancel()
            self._subscription = None

    def _consume(self, message):
        ...
```

Callback router thread'inde çalıştığı için kısa tutulmalıdır. Ağ isteği, ağır görüntü işleme veya disk işi ayrı worker'a aktarılmalıdır.

Tam zincir şöyledir:

```text
core/mavlink -> MavlinkVehicleService -> VehicleService sözleşmesi -> alan servisleri
```

Araç bağlandığında parametre kataloğu `PARAM_REQUEST_LIST` ile alınır. Parametre değişikliği ancak uçuş cihazından aynı anahtar/değeri taşıyan `PARAM_VALUE` geri geldiğinde tamamlanır. Görev planı `MISSION_ITEM_INT` ile yüklenir ve `MISSION_ACK` kabulü beklenir; bağlantı başlangıcında araç görevi backend harita durumuna geri alınabilir.

## Asyncio servisine bağlama

Referanstaki async channel yaklaşımı tek-router kuralına uyarlanmıştır. Kanal ikinci bir socket okuyucusu açmaz:

```python
channel = MavlinkAsyncChannel(
    client.router,
    MavlinkMessageFilter.for_types("GPS_RAW_INT"),
    maxsize=64,
)

channel.start()  # çalışan asyncio loop içinde
message = await channel.receive(timeout=1.0)
channel.stop()
```

Kuyruk dolarsa en eski mesaj düşürülür; `dropped_messages` sayacı bunu gözlemlemek için kullanılabilir. Router/client yaşam döngüsü kanalın dışında ve kompozisyon kökünde kalır.
