[![Python 3.11+][python-shield]][python-url]
[![Lisans][license-shield]][license-url]

[English][lang-en-url] | **Türkçe**

# Kırlangıç Yer Kontrol İstasyonu

Eklenti ana makinesine sahip masaüstü yer kontrol uygulaması. Dahili MAVLink
protokol eklentisi ve isteğe bağlı Araç Bağlantısı eklentisi mevcut araç bağlantısı,
telemetri/GPS ve otopilot işlevlerini sağlar. Harita/otonomi, parametreler, batarya,
araç dinamiği, yapılandırılabilir telemetri, hızlı komutlar, kamera, joystick ve
sunucu işlevleri eklentilere aittir. Ortak uygulama yerleşim, ayarlar, loglama,
eklenti yaşam döngüsü ve normalize veri sözleşmelerini yönetir.

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
pip install -r extensions/mavlink/requirements.txt
pip install -r extensions/map_autonomy/requirements.txt
python main.py
```

Uygulama Python 3.11+ ve Qt 6 gerektirir. Protokol ve WebEngine bağımlılıkları
ilgili eklentinin requirements dosyasından kurulur. Uygulamayı aynı sanal ortamdan
başlatın; Qt ile Qt WebEngine sürümlerini uyumlu tutun. Linux'ta
başlatmadan önce PyQt6 ve Qt WebEngine için gereken sistem paketlerini kurun.

## MAVLink bağlantıları

Eklentiler → Araç Bağlantısı → Ayarlar üzerinden isimli bağlantı profilleri ekleyip seçin. Yaygın
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
                         └── yerleşim, ayarlar, loglar ve normalize veri hub'ları
```

`src/container.py` composition root'tur. `src/extension/api` sürümlü eklenti
sözleşmesini, `src/extension/host` statik manifest keşfini, yetenek kontrolünü
ve etkin eklentilerin kurulumunu sağlar. Eklentiler `src.core` herkese açık
paketlerini doğrudan kullanabilir. Protokol eklentisi ortak araç sözleşmelerini
ve çözümlenmiş telemetri verisini sağlar. Araç Bağlantısı eklentisi bağlantının
yaşam döngüsünü ve profil düzenlemeyi protokolden bağımsız `ConnectionSource`
ve `ConnectionProfiles` sözleşmeleriyle yönetir. En az bir protokol sağlayıcısı etkin
kalmalıdır. Kamera ve Araç Bağlantısı eklentileri uygulama kapanmadan açılıp
kapatılabilir; yalnız kendi servisleri ve arayüz katkıları yeniden kurulur. Ayarlar
yanındaki Eklentiler düğmesi pencereye göre boyutlanan arama, kategori/durum
filtreli dialog açar. Sekme kabuğu ileride mağazayı da barındırabilir; şu an mağaza
eklenmemiştir. Mevcut
MAVLink ayarları `data/settings.json` içinde korunur; eklenti durumu ve kendi
ayarları aynı dosyanın `extensions.<kimlik>.enabled` ve
`extensions.<kimlik>.config` alanlarında tutulur; devre dışı bırakılınca silinmez.
Eski `data/extensions/` config dosyaları içe alındıktan sonra kaldırılır;
ilgisiz veri dosyalarına dokunulmaz.
Ham mesaj abonelikleri ve heartbeat çözümlemesi protokol paketine, parametre
mesajlarının işlenmesi parametre eklentisinin protokol adaptörüne aittir;
ortak `VehicleService` bunları zorunlu kılmaz.
MAVLink çözümlenmiş veri akışını, GPS ve otopilot göstergelerini yönetir.
Araç Bağlantısı bağlantı çipini ve kalite göstergesini sağlar;
kamera durum kontrolleri kamera paketinde kalır.
`registrar.status_widget(..., placement="connections" | "metrics")` katkıyı
sol bağlantı grubuna veya bataryanın yanına yerleştirir. Bağlantı, GPS ve
otopilot göstergeleri yalnız durum gösterir; tıklayınca pencere açmaz.
Bağlantı profilleri Araç Bağlantısı eklentisinin ayarlarından düzenlenir. MAVLink'in
gelişmiş ayarları dahil tüm eklenti ayarları, yalnız ortak Ayarlar penceresinde
kendi kategorilerinde düzenlenir. Alt bardaki mevcut kamera bağlantı/yayın
popup'ları korunur. Dialect seçicisi kurulu pymavlink dialect'lerini listeler;
değişiklik bağlı hattı arka planda yeniden yapılandırır, kapalı bir hattı kendiliğinden
etkinleştirmez.
Her eklenti `i18n/tr.json` ve `i18n/en.json` dil paketlerini kendi içinde taşır.
Kurulu paketler doğrulanıp bir kez birleştirilir; çeviri sırasında disk okunmaz.
Yinelenen anahtarlar reddedilir, keşif yenilemesinde katalog yeniden oluşturulur.
Eski `telemetry` etkinlik tercihi `vehicle_connection` kimliğine taşınır;
kapalı durum ve mevcut bağlantı profilleri korunur.
Kamera eklentisi varsayılan olarak kapalıdır. Kamera bağlantı ve çıkış profilleri
`data/settings.json` içindeki `extensions.camera.config` alanında saklanır; eski
`data/settings.json` kamera profilleri silinmeden içe alınır. Kamera paneli,
bağlantı, kayıt ve yayın kontrolleri yalnız eklenti etkinse görünür. Yeni
kurulan eklentiler Yenile ile bulunur ve kullanıcı etkinleştirmedikçe kapalı
kalır. Yeniden yükle yalnız ilgili bağımsız eklentiyi başlatır. Etkin araç
protokolü, ortak araç servisleri ona bağlıyken tek başına yeniden yüklenemez.

Yeni eklenti `extensions/<kimlik>/` altında bulunur. `manifest.json` dosyasında
`api_major: 1`, `entry_point` ve `provides`/`requires` yetenek listeleri
tanımlanır. Fabrika `configure(registrar, context)` ve isteğe bağlı
`start(context)`/`stop()` metotlarına sahip nesne döndürür.
`registrar.singleton(token, factory, dependencies=...)` ile servis sağlanır;
aynı servisin birden fazla eklenti tarafından kaydedilmesi reddedilir. Dahili
eklentiler çalışan örnek olarak kullanılabilir. `registrar.visual_view(...)`
ortak yerleşimde bir panel, `status_widget(...)` alt barda gösterge sağlar.
`registrar.settings_page(factory)` eklentiye ait tek ayar sayfasını tanımlar.
Fabrika Qt üst widget'ını alır; yalnız kullanıcı ilgili Ayarlar kategorisini
seçtiğinde UI iş parçacığında çağrılır. Sayfa, kapanışta abonelik/timer/worker
temizliği için `prepare_shutdown()` sağlayabilir. Devre dışı eklentinin kodu
yüklenmeden ve servisi başlatılmadan tipli config editörü sunulur.
Eski `sidebar_view` ve `settings_ui` katkıları kaldırıldı; bunların yerine
`visual_view` ve `settings_page` kullanılmalıdır. Eklenti yönetimi yalnız
etkinleştirme ve keşif yapar; ikinci bir ayar yolu oluşturmaz.

## Çalışma alanı

Harita merkezde kalır. Araç dinamiği, telemetri, hızlı komutlar, loglar,
parametreler ve eklentilerin getirdiği görünümler bağımsız panellerdir.
Görünüm > Paneller menüsünden panelleri açıp kapatabilir, ayraçlardan
boyutlandırabilirsin. Taşıma, sekmeleme ve ayrı pencereye alma için panelin
üzerindeyken beliren küçük tutamak/menü kullanılır; ayrı bir Yerleşimi düzenle
modu yoktur. Haritaya odaklanma, varsayılan yerleşim ve Koyu/Açık tema aynı
Görünüm menüsünde kalır.

Sol barın üst bölümü kayıtlı görünümleri seçer; farklı stilli alt bölüm aktif
görünüme ait panel kategorilerini topluca açıp kapatır. Görünümlerin altındaki
artı düğmesi, ortak isim ve aranabilir standart ikon seçicisiyle görünüm veya
kategori oluşturur. Yeni görünüm boş başlar ve panel kullanımını anlatan kısa
yönergeler gösterir. Kategori oluştururken açık panellerin üzerine tıklayıp
sağ alttaki Ekle veya İptal düğmesini kullanırsın. Bu sırada panel içindeki
araç komutları çalıştırılmaz. Kısmen açık kategori tüm mevcut üyelerini açar;
tamamen açık kategori birlikte kapanır. Sağ tık ile isim/ikon değiştirilebilir
veya öğe silinebilir; son görünüm silinemez.

Her görünümün split/sekme düzeni, boyutları, görünür panelleri ve kategorileri
`data/workspace-layout.json` içinde gecikmeli birleştirilmiş yazımlarla ve
kapanışta kaydedilir. Görünüm değişimi pencereyi yeniden boyutlandırmaz.
Eski tek yerleşim dosyası başlangıç görünümüne taşınır. Boş alan oluşturan sahte dock'lar
kullanılmaz; bölmenin gerçek satır/sütun ilişkisi boyutları belirler. İlgisiz
yerleşim dalları kendi boyutlarını korur.

## İsteğe bağlı entegrasyonlar

* **Harita ve Otonomi** (`map_autonomy`) panel, web dosyaları, tile cache,
  planlayıcı, tercihler ve görev görünümünün sahibidir. Geliştirici API'si
  `extensions.map_autonomy.api.planner.PlannerMapApi` konumundadır.
  Normalize telemetri ve seyrüsefer servislerini kullanır; şu an sağlayıcı
  MAVLink'tir. Uygulama yeniden başlatılmadan kapatılıp açılabilir.
* **Joystick** RC1–RC16 atama, PWM kalibrasyonu, otomatik algılama ve buton
  aksiyonlarını getirir. Varsayılan olarak kapalıdır ve şu an MAVLink RC adaptörü
  seçilir. Kumandanın aktivasyonu eklentinin etkin olmasından ayrıdır; kapatılınca
  RC override bırakılır. Protokol seçimi cihaz/UI kodundan ayrı tutulur ve
  `src.extension.api.protocol.ActiveProtocol` üzerinden yapılır.
* **SİHA TEKNOFEST Sunucusu** (`siha_teknofest_server`) bağımsız HTTP sunucu profilleri, sabit 1 Hz telemetri zamanlaması,
  sunucu saati, QR/HSS sorguları ve gerçek kilitlenme/kamikaze sonuçlarını gönderme
  metotlarını sağlar. Normalize telemetri kullanır; MAVLink kimliğine bağlı değildir.
  Sunucu alışverişleri WAL kullanan `data/teknofest/history.sqlite3` veritabanına
  kaydedilir; giriş parolaları kaydedilmez. İlk geçerli konumdan sonra araç hattı
  kopsa da son veri tahmini sunucu saatiyle gönderilmeye devam eder. Otomatik
  bağlantı işaretlenmedikçe veya kullanıcı bağlanmadıkça sunucuya istek yapılmaz.
  Eklenti varsayılan olarak kapalıdır.

Örnek ortak yapı:

```json
{
  "extensions": {
    "camera": { "enabled": false, "config": {} },
    "joystick": { "enabled": false, "config": {} },
    "siha_teknofest_server": { "enabled": false, "config": { "servers": [] } }
  }
}
```

Eski typed backend alanları uyumluluk için korunur. MAVLink paketi protokol
ayarlarını ve bağlantı profillerini ortak eklenti bölümüne yansıtır; eski
düzenleyiciler diğer eklentilerin configlerini ezmez.

Araç dinamiği, telemetri göstergesi, hızlı komutlar ve batarya ayrı paketlerdir;
normalize veri veya genel komut yeteneklerini kullanır. Telemetri ayarlarından
görünecek alanlar seçilir. Parametre paketi şu an `mavlink.parameters` yeteneğini
gerektirir; MAVLink adaptörü kendi içindedir. `VehicleService` RC ve görev metotlarını
zorunlu kılmaz; `src.extension.api.vehicle` içindeki `RcControl` ve `MissionControl`
portları ayrı sunulur. Yeni protokol için gereken port/adaptör ve yetenek eklenir;
özellik arayüzüne taşıma protokolü sızdırılmaz. Üretim mock telemetri kaldırılmıştır;
testler kendi sahtelerini enjekte eder. Eski `map` ve `teknofest` tercihleri kapalı
durum ve bilinmeyen ayarlar korunarak taşınır. Yerleşimde Qt'nin kaydedilmiş bölme
ve sekme durumu esastır; açılışta eski piksel boyutları bunun üzerine uygulanmaz.

## Ayarlar, loglar ve testler

Ayarlar `data/settings.json`, loglar
`data/logs/gcs_<oturum-tarihi>_<oturum-saati>.json` içinde tutulur. Oturum dosyası
geçerli bir JSON dizisidir. Eklentiler aynı, belleği sınırlı log servisini kullanır;
ana makine enjekte edilen logger'lara ve Python loglarına `extension_id` ekler.
Testleri çalıştırmak için:

```bash
python run_tests.py
```

Qt testleri offscreen platformda çalışır. Saha kullanımından önce hedef araçla
MAVLink bağlantısını ve görev aktarımını doğrulayın.

## Lisans

Copyright © 2026 TahsinCr.
