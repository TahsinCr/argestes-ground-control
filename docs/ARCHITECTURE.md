# Mimari ve genişletme rehberi

## 1. Sınırlar ve sahiplik

`core`, belirli bir araç veya arayüz bilmeden tekrar kullanılabilecek kodları içerir. Buradaki bir sınıf İKA, SİHA, CLI veya sunucu uygulamasına kopyalanmadan import edilebilmelidir. Bu nedenle `core` yalnızca Python standart kütüphanesine ve kendi alt modüllerine bağımlıdır.

`models`, sistemin taşıdığı veriyi ve o veriye doğal olarak ait davranışı sahiplenir. Örneğin `Point.distance_to`, koordinat modelinin davranışıdır; bağlantı açmak veya widget güncellemek model davranışı değildir.

`services/contracts`, dışarıdan kullanılabilen yetenekleri tanımlar. Her alternatif implementasyon aynı sözleşmeyi sağlar:

- `TelemetryService`: son telemetri + güncelleme akışı
- `MissionService`: görev/harita durumu + atomik mutasyonlar
- `VehicleService`: uçuş cihazı bağlantısı, komut, parametre ve görev aktarım soyutlaması
- `CommandService`: doğrulanan araç aksiyonlarının orkestrasyonu
- `ParameterService`: araç/uygulama parametreleri
- `CompetitionService`: TEKNOFEST oturumu, sunucu saati ve rakip telemetrisi
- `VideoService`, `TileCacheService`, `SettingsRepository`, `MissionArchiveRepository`, `LogService`

Servis implementasyon grupları şunlardır:

- `vehicle`: `core/mavlink` ayrıntılarını tek yerde kapsülleyen çift yönlü uçuş cihazı adaptörü
- `processing`: merkezi görev, telemetri ve çift yönlü parametre durum servisleri
- `orchestration`: bir kullanım senaryosunu sözleşmeler üzerinden yöneten üst seviye servisler
- `video.py`: GStreamer kaynaklarını ortak video modeline taşıyan UI bağımsız servis

Alan servisleri `core/mavlink` veya `pymavlink` import etmez; `VehicleService` kullanır. Somut implementasyonların birleştiği tek yer `services/runtime.py` kompozisyon köküdür.

`ui`, Qt'ye ait bütün kodu sahiplenir. Backend olayları `ui/adapters` içinde Qt signal'larına dönüştürülür. Backend tarafında `PyQt6`, `QTimer`, `QObject` veya widget bulunmaz.

## 2. Merkezi harita veri akışı

```text
Qt/Leaflet değişikliği ─┐
REST/CLI/otomasyon ─────┼─> MissionService ─> MissionState(revision N+1)
Görev motoru ───────────┘          │
                                  ├─> Qt haritası
                                  ├─> başka bir web arayüzü
                                  ├─> log/telemetri servisi
                                  └─> görev yükleme adaptörü
```

`MissionStateService` her değişiklikte yeni bir revision üretir ve kopyalanmış snapshot yayınlar. UI ile arka plan aynı anda waypoint, RTL, land, loiter, HSS, güvenli bölge, yasak bölge, rota veya temas yazdığında liste güncellemeleri tek kilit altında yapılır; son yazan işlem önceki bağımsız öğeleri kaybetmez.

Harita öğesi biçimi `MapOverlay` ile sabittir:

- `overlay_id`: kararlı benzersiz kimlik
- `overlay_type`: waypoint, land, rtl, safe_zone vb.
- `points`: `[latitude, longitude, altitude?]` listesi
- `radius_m`: noktasal öğelerde yarıçap
- `metadata`: rota sırası, özel servis alanları veya kaynağa özgü ek veri

Canlı araç/rakip temasları `MapContact` ile tutulur. Bunlar görev arşivine yazılmaz; görev yüklenirken canlı temasların silinmemesi de servis seviyesinde korunur.

TEKNOFEST HSS ve QR koordinatları `metadata.source` ile sahiplenilen `MapOverlay` öğeleridir. `replace_source_overlays` yalnızca ilgili sunucu grubunu değiştirir; kullanıcı waypoint, güvenli alan veya rota verileri korunur.

## 3. Alternatif arayüz bağlama

Qt dışındaki bir arayüz `RuntimeServices.facade` kullanabilir. Sürekli güncelleme gereken yerde doğrudan sözleşme olaylarına abone olunur:

```python
runtime.mission_service.changed.subscribe(
    lambda state: websocket.broadcast(state.to_dict())
)
runtime.telemetry_service.updated.subscribe(
    lambda value: websocket.broadcast(value.to_dict())
)
```

Yeni arayüz backend modelini tekrar üretmemeli; `models` sınıflarını veya bunların `to_dict()` çıktısını kullanmalıdır. Böylece Qt, web ve otomasyon aynı revision ve doğrulama kurallarını görür.

## 4. Yeni servis ekleme

Örnek: yarışma sunucusundan rakip konumlarını alan servis.

1. Genel yetenek mevcut değilse `services/contracts` altında soyut port tanımlanır.
2. Servis `services/processing` veya kendi anlamlı alan klasöründe tutulur; teknik input/output klasörü açılmaz.
3. Gelen veri `MapContact` modeline çevrilip enjekte edilen `MissionService.replace_contacts` portuna tek atomik grup olarak gönderilir.
4. Somut sınıflar yalnızca `services/runtime.py` içinde oluşturulur.
5. Yaşam döngüsü `RuntimeServices.start/stop` içine eklenir.
6. Contract ve veri akışı için araç gerektirmeyen bir test eklenir.

Alt servis üst servis nesnesini konteynerden gizlice çözmemelidir. Bağımlılık constructor üzerinden açıkça verilmelidir. `DependencyContainer` uygulama/eklenti kompozisyonu ve scope yönetimi içindir; servis içinden global service locator olarak kullanılmaz.

## 5. Ayar sahipliği

`SettingsModel` iki ayrı kök taşır:

- `backend`: MAVLink, TEKNOFEST sunucusu, video kaynağı, cache ve görev varsayılanları
- `ui`: dil, tema, harita görünümü ve görsel iz limitleri

Kalıcı JSON aynı ayrımı korur. Eski düz `data/settings.json` otomatik okunur ve ilk kayıt işleminde yeni iç içe biçime yazılır.

Bir UI ayarı backend davranışını doğrudan değiştirmemeli; bir backend ayarı da widget nesnesi taşımamalıdır. Ayar dialogu `SETTINGS_SECTIONS` şemasını dolaşır. Desteklenen `combo`, `check`, `spin` ve `text` tipleri için dialog kodu değiştirilmez.

## 6. Komut ve görev genişletme

UI komut kataloğu yalnızca sunumu tanımlar. Genel ArduPilot davranışları `MavlinkVehicleService` içinde bulunur. Yarışmaya özgü `TARGET_LOCK` veya `KAMIKAZE` gibi komutlar için iki seçenek vardır:

- alternatif bir `VehicleService` implementasyonu yazmak;
- `MavlinkVehicleService(custom_handlers={...})` ile uygulamaya özgü handler enjekte etmek.

Görev gönderiminde ArduPilot'un desteklediği rota öğeleri mission protokolüne çevrilir. Güvenlik poligonları veya HSS verisi özel servisler tarafından işlenir; anlamı bilinmeden uçuş kontrolcüsüne gönderilmez.

UI'daki `TARGET_LOCK` veya `KAMIKAZE` bir SİHA görev komutudur; yarışma sonucu değildir. Başarılı sonuç paketleri yalnızca araç/Jetson servisinin oluşturduğu `LockEvent` ve `KamikazeEvent` modelleriyle `CompetitionService` portuna verilir. Bu modeller `ui`, `yki` ve `gcs` kaynaklarını reddeder. Aynı şekilde telemetri `gps_saati`, YKİ sistem saatinden değil SİHA'nın `SYSTEM_TIME` veya geçerli GPS UNIX zamanından alınır. `CompetitionService`, `VehicleService.connected` durumunu birincil kapı olarak kullanır ve son konum ile araç saati paketlerinin yerel alım zamanını ayrı izler. Araç bağlantısı veya telemetri hattı kapandığında ya da bu kaynaklardan biri tazelik penceresini aştığında eski snapshot'ı yeniden göndermek yerine SİHA telemetri TX'ini durdurur. Sunucu oturumu ve yalnızca sunucudan alınan HSS/QR akışı bu durumdan ayrıdır.

## 7. Doğrulama ve log akışı

Bir butona basılması başarı değildir. Araç işlemleri mümkün olan en güçlü protokol cevabıyla doğrulanır:

- ARM/LAND/mission aksiyonları: `COMMAND_ACK`
- mod değişimi: istenen modu taşıyan yeni `HEARTBEAT`
- parametre yazımı: aynı anahtar/değeri taşıyan `PARAM_VALUE`
- görev yükleme: `MISSION_REQUEST(_INT)` akışı ve son `MISSION_ACK`

Heartbeat watchdog zaman aşımında `VehicleService.connected` durumunu kapatır; alan servisleri bağlantı kaybını görür ve yeni komutlar reddedilir. Aynı system/component heartbeat'i geri geldiğinde araç durumu tekrar açılır, parametre ve görev senkronizasyonları yeniden tetiklenir.

Başarılı veya başarısız gerçek backend olayları `data/logs/yki.jsonl` içinde action, cihaz kimliği, ayrıntılar ve zaman damgasıyla tutulur. UI yalnızca bu merkezi akışı görüntüler; boş veya yalnızca hazırlanmış UI aksiyonları başarı logu üretmez.

TEKNOFEST HTTP trafiği hem istemci JSONL logunda hem sahte sunucu terminalinde `TX`/`RX` olarak ayrılır. Telemetri logları giden kendi SİHA paketini ve gelen rakip listesini; HSS/QR logları istek ile cevabı; kilitlenme/kamikaze logları SİHA kaynak kimliği ile sunucu onayını ayrı kaydeder.

## 8. Koruma testleri

`tests/test_architecture.py` ters katman importlarını, yalnızca vehicle adaptörünün MAVLink çekirdeğini bilmesini ve korunması gereken stylesheet/Leaflet dosyalarının hash'lerini kontrol eder. `tests/test_mission.py` paralel yazımları test eder. `tests/test_vehicle_services.py` parametre doğrulaması, görev dönüşümü, kalıcı log ve gerçek GStreamer kare akışını test eder. `tests/test_ui_smoke.py` gerçek ana pencereyi Qt WebEngine ile offscreen açar.

Bu testler geçmeden eski yol veya uyumluluk modülleri kaldırılmamalıdır.
