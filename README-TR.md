# Kırlangıç Yer Kontrol İstasyonu

[![Python 3.11+][python-shield]][python-url]
[![Lisans: GPL-3.0-only][license-shield]][license-url]

[English](README.md) · **Türkçe** · [Değişiklik geçmişi](CHANGELOG.md)

Kırlangıç; araç takibi, görev planlama ve canlı kamera görüntüleriyle çalışmak
için geliştirilen bir masaüstü yer kontrol istasyonudur. Özelleştirilebilir panel
düzenini bir eklenti sistemiyle birleştirir. Operatörler kullanacakları araçları
seçebilir; geliştiriciler yeni entegrasyonları uygulamanın tamamını yeniden
şekillendirmeden ekleyebilir.

Güncel araç protokolü MAVLink’tir. Harita, kamera, joystick ve yarışma sunucusu
bağlantıları ayrı eklenti paketlerinde bulunur. Ortak uygulama çalışma alanını,
ayarları, loglamayı ve eklentilerin yaşam döngüsünü yönetir.

[Kurulum](#kurulum) · [Araç bağlantısı](#araç-bağlantısı) ·
[Çalışma alanı](#çalışma-alanı) · [Eklentiler](#eklentiler) ·
[Eklenti geliştirme](#eklenti-geliştirme) · [Sorun giderme](#sorun-giderme)

## Neler yapabilirsiniz?

* Araç tutumunu, telemetriyi, bataryayı ve bağlantı durumunu izleyebilirsiniz.
* Harita üzerinde görev planlayabilir, parametreleri inceleyebilir ve aktif
  aracın sunduğu komutları kullanabilirsiniz.
* Panelleri yan yana veya sekmelerde düzenleyebilir, farklı çalışma alanları
  kaydedebilir ve panel gruplarını tek işlemle açıp kapatabilirsiniz.
* Birden fazla kamera görüntüsü, ayrı kayıtlar ve canlı video çıkışlarıyla
  çalışabilirsiniz.
* RC kontrolü için joystick eksenlerini, PWM aralıklarını ve buton eylemlerini
  yapılandırabilirsiniz.
* SIHA Teknofest sunucularına bağlanabilir ve veri alışverişinin geçmişini
  yerel olarak saklayabilirsiniz.

Kullanılabilir panel ve kontroller, etkin eklentilere ve aktif araç sağlayıcısının
sunduğu yeteneklere bağlıdır.

## Kurulum

### Gereksinimler

Uygulama Python 3.11 veya üzerini, Git ve Qt 6’yı gerektirir. Geliştirme ve
donanım entegrasyonları ağırlıklı olarak Linux üzerinde denenmektedir. Cihaz
erişimi, video eklentileri ve pencere yöneticisinin davranışı kullanılan sisteme
göre değişir.

Ana requirements dosyasını ve kullanacağınız eklentilerin bağımlılıklarını kurun.
MAVLink eklentisi `pymavlink` ve `pyserial`, Harita ve Otonomi ise Qt WebEngine
gerektirir. Kamera için gereken yerel GStreamer bileşenleri aşağıda açıklanıyor.

### Depoyu hazırlama

Terminalde şu komutları çalıştırın:

```bash
git clone --recurse-submodules https://github.com/TahsinCr/ground-control-station.git
cd ground-control-station

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r extensions/mavlink/requirements.txt
python -m pip install -r extensions/map_autonomy/requirements.txt

python main.py
```

Vehicle Autonomy Core, Git alt modülü olarak bulunur. Elinizde Core’u içermeyen
bir kopya varsa uygulamayı başlatmadan önce
`git submodule update --init --recursive` komutunu çalıştırın. Qt ile Qt WebEngine
sürümlerini uyumlu tutun; kurulum ve uygulamayı başlatma işlemlerinde aynı Python
ortamını kullanın.

### Kamera bağımlılıkları

Kamera eklentisi, PyGObject üzerinden GStreamer 1.0 kullanır. Video kaynaklarınızın
ve codec’lerinizin gerektirdiği çalışma zamanını, Python bağlarını ve eklentileri
kurun. Sisteme göre değişen bu bileşenler ana Python requirements dosyasıyla
kurulmaz. Python bağları, uygulamanın sanal ortamından içe aktarılabilmelidir.

Yerel kayıt için GStreamer’ın erişebildiği, desteklenen bir NVIDIA veya VA-API
donanımsal kodlayıcı gerekir. Ekran kartının bulunması tek başına yeterli değildir;
sürücü ve kodlayıcı eklentisi de kullanılabilir olmalıdır. Ağ yayını yazılımsal
kodlayıcıya dönebilir; yerel kayıt ise desteklenen bir donanımsal kodlayıcı
bulamazsa hata bildirir.

## Araç bağlantısı

Alt çubuktaki araç bağlantısı kontrolünü açın, isimli bir profil oluşturun ve
aracınızın veya telemetri yönlendiricinizin kullandığı uç noktayı girin. Seri
cihazlarda baud hızını da seçin.

| Bağlantı | Örnek uç nokta | Anlamı |
| --- | --- | --- |
| Seri | `/dev/ttyUSB0` | Yerel seri cihazı açar; baud hızı ayrıca belirlenir. |
| UDP girişi | `udpin:0.0.0.0:14550` | Tüm yerel arayüzlerde 14550 portunu dinler. |
| UDP çıkışı | `udpout:192.168.1.30:14550` | Uzak makinenin belirtilen portuna gönderir. |
| TCP | `tcp:127.0.0.1:5762` | Bir TCP sunucusuna bağlanır. |

UDP girişinde araç veya yönlendirici, telemetriyi yer istasyonunun erişilebilir
adresine ve dinlenen porta göndermelidir. `0.0.0.0` yerel dinleme adresidir;
uzak hedef olarak kullanılmaz. UDP çıkış profili ise yanıt verebilen bir karşı
uç gerektirir; yalnızca bu modu seçmek telemetri oturumu kurmaz.

Sağlıklı MAVLink bağlantısı için araçtan heartbeat alınması gerekir. Bağlantı
profili ile MAVLink ayarlarının görevleri ayrıdır:

* `auto_connect`, uygulama açılışındaki ilk bağlantı denemesine izin verir.
* `autoreconnect`, kurulmuş bağlantı koptuktan sonraki kurtarmaya izin verir.
* Kullanıcının bağlantıyı kesmesi, yeni bağlantı isteğine kadar kurtarmayı durdurur.

Dialect ve protokol seçenekleri **MAVLink** ayar kategorisinde bulunur.
**Araç Bağlantısı**; profilleri, bağlantı kontrollerini ve kalite göstergelerini
yönetir. GPS ve otopilot göstergeleri protokol durumunu bildirir; bağlantı
düzenleyicisi açmaz.

## Çalışma alanı

### Panelleri düzenleme

Tekil panelleri **Görünüm → Paneller** menüsünden açıp kapatın. Fareyi panelin
üzerine getirdiğinizde tutamaç ve menüsü görünür. Alanı bölmek için tutamacı
başka bir panelin kenarına, sekme oluşturmak için merkezine sürükleyin.
Hedef önizlemesi, panelin nereye yerleşeceğini gösterir.

Yanındaki menü; yerleştirme, görünürlük ve içerik boyutunu sabitleme seçeneklerini
sunar. Ayrı bir yerleşim düzenleme modu yoktur. Aynı bölmedeki paneller ilgili
boyutu paylaşır; ilişkisiz yerleşim dalları bağımsız boyutlandırılabilir.
Boyut kilidi görünümle kaydedilir ve pencerenin minimum boyutunu artırabilir.

### Görünümler ve panel kategorileri

Sol çubuğun üst kısmından kayıtlı çalışma alanı görünümleri seçilir. Her görünüm
kendi açık panellerini, yerleşimini, boyutlarını ve kategorilerini saklar.
Görünüm değiştirmek uygulama penceresini yeniden boyutlandırmaz. Eski tek
yerleşimli dosyalar başlangıç görünümüne taşınır.

Görünüm veya kategori oluşturmak için görünüm listesinin altındaki **+**
düğmesini kullanın. Yeni görünüm, isim ve ikon seçiminin ardından boş bir
çalışma alanıyla açılır. Panellerini Görünüm menüsünden ekleyin. Mevcut görünüme
sağ tıklayarak ismini veya ikonunu değiştirebilir, görünümü silebilirsiniz;
son kalan görünüm silinemez.

Kategoriler sol çubuğun alt bölümünde görünür ve aktif görünüme aittir.
Kategori oluştururken isim ve ikon seçin, gruba dahil edilecek görünür panelleri
işaretleyin ve **Ekle** ile onaylayın. Kategori, eklenti servislerini yönetmeden
üyelerini birlikte açıp kapatır.

Panel görünürlüğü ile eklenti etkinliği ayrı kontrollerdir. Örneğin kamera
panelini kapatmak, Kamera eklentisini devre dışı bırakmaz.

### Görüntüleme tercihleri

Açık ve koyu tema Ayarlar’dan seçilebilir. **Araç Telemetrisi** kategorisinde
58 alan arasından seçim yapabilir, sıralamayı değiştirebilir, liste veya ızgara
kullanabilir, bir ila dört sütun seçebilir ve alanları kategorilerine göre
gruplayabilirsiniz.

## Eklentiler

Eklenti yöneticisini alt çubukta Ayarlar’ın yanındaki düğmeden açın. Arama ve
filtrelerle kurulu paketleri bulabilir, etkinlik ve bağımlılık durumlarını
inceleyebilirsiniz. Her paketin yapılandırması ortak Ayarlar penceresindeki
kendi kategorisinde bulunur.

Depoyla birlikte gelen paketler aşağıdadır. Varsayılanlar yeni yapılandırmalar
için geçerlidir; kayıtlı etkinlik tercihleri önceliklidir.

| Paket | Sorumluluk | Varsayılan |
| --- | --- | --- |
| [MAVLink](extensions/mavlink/) | Araç protokolü, çözümlenen veriler, komutlar, GPS ve otopilot durumu. | Etkin |
| [Araç Bağlantısı](extensions/vehicle_connection/) | Bağlantı profilleri, yaşam döngüsü, kurtarma ve bağlantı göstergeleri. | Etkin |
| [Harita ve Otonomi](extensions/map_autonomy/) | Harita, görev planlama, planlayıcı API’si ve karo önbelleği. | Etkin |
| [Batarya](extensions/battery/) | Batarya göstergesi, tahmin ve alarmlar. | Etkin |
| [Parametreler](extensions/parameters/) | `mavlink.parameters` üzerinden parametre inceleme ve düzenleme. | Etkin |
| [Araç Dinamiği](extensions/flight_dynamics/) | Tutum, hız ve irtifa göstergeleri. | Etkin |
| [Araç Telemetrisi](extensions/telemetry_display/) | Özelleştirilebilir telemetri alanları. | Etkin |
| [Hızlı Komutlar](extensions/quick_commands/) | Aktif araç sağlayıcısının sunduğu komutlar ve modlar. | Etkin |
| [Kamera](extensions/camera/) | Kamera görüntüleri, bağlantı kontrolleri, kayıt ve canlı çıkışlar. | Devre dışı |
| [Joystick](extensions/joystick/) | RC kalibrasyonu, kanal eşlemesi ve buton eylemleri. | Devre dışı |
| [SIHA Teknofest Sunucusu](extensions/siha_teknofest_server/) | Sunucu profilleri, telemetri gönderimi ve SQLite geçmişi. | Devre dışı |

### Etkinleştirme ve yeniden yükleme

Ana makine, etkinlik değişikliklerini uygulamadan önce bildirilen yetenekleri
denetler. Kamera ve Araç Bağlantısı dahil bağımsız değiştirilebilen paketler,
uygulama çalışırken açılıp kapatılabilir. Bir paketi kapatmak servislerini ve
arayüz katkılarını kaldırır; ayarlarını korur.

Ayar değişiklikleri ilgili paketi yeniden yüklenmek üzere işaretler. Aktif
bağımlıları bulunan bir sağlayıcı tek başına yeniden yüklenemez; en az bir araç
protokolü sağlayıcısının etkin kalması gerekir. Uygulamayla şu anda MAVLink
sağlayıcısı gelir. Başka bir protokol için kendi uygulaması ve gerekli
adaptörleri eklenmelidir.

### Kamera, joystick ve sunucu araçları

Kamera etkin olduğunda bağlantı kontrolleri alt çubukta görünür. Her kaynak için
ayrı kayıt veya ağ çıkışı tanımlanabilir. Kamera panelinin kayıt eylemi kamera
kayıtlarını birlikte yönetir; çıkış ayarlarından hedefler ayrı ayrı açılıp
kapatılabilir. Kayıtlar sabit 30 FPS çıkış kullanır, kaynağın görüntüsü
güncelliğini yitirdiğinde duraklar ve görüntüler döndüğünde aynı dosyada devam
eder. MP4 dosyasını tamamlamak için kaydı normal biçimde durdurun.

Joystick; cihaz seçimi, RC kanal atamaları, PWM kalibrasyonu, ölü bölge, expo,
ters çevirme ve buton eylemleri sunar. Mevcut RC adaptörü MAVLink’i destekler.
Joystick kontrolü devre dışı bırakıldığında RC override değerleri bırakılır.

SIHA Teknofest Sunucusu’nun profil yöneticisi çalışma alanı paneli yerine alt
çubuktan açılır. Profiller bağımsız HTTP bağlantılarını destekler; paket ortak
telemetri verilerini gönderir ve alışverişi SQLite üzerinde kaydeder. İstekler,
kullanıcı bağlandığında veya profil otomatik bağlantıya ayarlandığında başlar.

## Mimari

### Ortak uygulama ve Core

[Uygulama bileşimi](src/container.py), ortak servisleri ve eklenti ana makinesini
bir araya getirir. `src/`; çalışma alanı davranışını, yapılandırmayı, oturum
loglamasını ve ortak araç verisi sözleşmelerini içerir.

[Vehicle Autonomy Core](src/core/), alt modül olarak kullanılan ayrı bir depodur.
Uygulama ve eklentiler, herkese açık `src.core` paketlerini doğrudan içe
aktarabilir. Eklenti API’si uygulamayla bütünleşmeyi yönetir; Core’a erişimi
sarmalayan veya kısıtlayan bir katman değildir.

### Protokol ve özellik sınırları

[Eklenti API’si](src/extension/api/) katkıları ve sağlayıcı sözleşmelerini
tanımlar. [Ana makine](src/extension/host/) manifest dosyalarını keşfeder,
bağımlılıkları denetler, servisleri birleştirir ve yaşam döngülerini yönetir.

Protokol paketleri mesajları ortak verilere dönüştürür ve desteklenen işlemleri
sunar. Özellik paketleri bu sözleşmeleri tüketir veya protokol adaptörlerini
kendi içlerinde tutar. Taşıma okumasını MAVLink’in ortak istemcisi ve router’ı
yönetir; arayüz bileşenleri rakip okuyucular oluşturmaz.

`VehicleService`, her sağlayıcıyı RC veya görev desteğine zorlamayan ortak araç
arayüzüdür. İsteğe bağlı `RcControl` ve `MissionControl` sözleşmeleri bu işlemleri
ayrı tanımlar. Parametreler şu anda `mavlink.parameters`, Harita ve Otonomi ise
`vehicle.navigation` yeteneğini gerektirir. Harita paketi ayrıca
[PlannerMapApi](extensions/map_autonomy/api/planner.py), web dosyaları ve
önbelleğin yaşam döngüsünü yönetir.

## Eklenti geliştirme

### Paket ve manifest

`extensions/<kimlik>/` altında `__init__.py` içeren, içe aktarılabilir bir
Python paketi oluşturun.
Küçük harf ve alt çizgi içeren bir kimlik kullanın; kimlik dizin adıyla
eşleşmelidir. Giriş noktasını, API ana sürümünü, gereken ve sağlanan yetenekleri
tanımlayan bir `manifest.json` ekleyin.

Temel bir manifest örneği:

```json
{
  "identifier": "example_tool",
  "name": "Example Tool",
  "description": "An operator tool",
  "category": "Tools",
  "version": "1.0.0",
  "api_major": 1,
  "entry_point": "extensions.example_tool.plugin:create_extension",
  "requires": [],
  "provides": ["tools.example"],
  "default_enabled": false,
  "settings": {}
}
```

`plugin.py` içinde `create_extension()` sağlayın. Dönen nesne
`configure(registrar, context)` metodunu uygulamalı; isteğe bağlı olarak
`start(context)` ve `stop()` metotlarını sunmalıdır. Yapılandırma aşamasında
katkıları bildirin, çalışma zamanı işlerini başlangıçta başlatın; kapanışta
worker, zamanlayıcı, abonelik ve bağlantıları temizleyin.

### Servisler ve arayüz katkıları

| Kayıt | Amaç |
| --- | --- |
| `registrar.singleton(...)` | Servisi ve bağımlılıklarını kaydeder; yinelenen sahiplik reddedilir. |
| `registrar.visual_view(...)` | Ortak çalışma alanına panel ekler. |
| `registrar.status_widget(...)` | Alt çubuğa sıralı bağlantı veya ölçüm göstergesi ekler. |
| `registrar.settings_page(factory)` | Kategorisi seçildiğinde yüklenen ayar sayfasını sağlar. |

Paket yapılandırması `context.settings` içinde bulunur; ortak servisler
`context.services` üzerinden çözülür. Eklenti kimliğinin otomatik eklenmesi
için loglama servisini de buradan alın. Ayar sayfaları, kendi kaynaklarını
temizlemek için `prepare_shutdown()` sunabilir.

Arayüz bileşenlerini ve kaynak dosyalarını sahibi olan pakette tutun.
Paketinize özel anahtarlarla `i18n/en.json` ve `i18n/tr.json` katalogları
sağlayın. Uygulama bunları doğrular, birleştirir ve önbelleğe alır.
Başka paketin anahtarlarını yeniden kullanmayın.

Paketin gerçekten ihtiyaç duyduğu yetenekleri bildirin. Başka bir araç
protokolünü desteklemek yeni adaptör gerektirebilir; ilişkisiz özellik
panellerini değiştirmeyi gerektirmemelidir.
[Joystick](extensions/joystick/plugin.py) ve
[Araç Bağlantısı](extensions/vehicle_connection/plugin.py) eklentileri, mevcut
servis ve arayüz bütünleştirme örneklerini içerir.

## Yapılandırma ve kayıtlı veriler

| Konum | İçerik |
| --- | --- |
| `data/settings.json` | Uygulama tercihleri, eklenti yapılandırmaları ve etkinlik durumları. |
| `data/workspace-layout.json` | Kayıtlı görünümler, panel düzenleri, görünürlük, boyutlar ve kategoriler. |
| `data/logs/gcs_<oturum-tarihi>_<oturum-saati>.json` | Uygulama ve eklentilerin ortak oturum logu. |
| `data/teknofest/history.sqlite3` | SIHA Teknofest Sunucusu veri alışverişi geçmişi. |

Her eklenti, ortak ayar dosyasında `extensions.<kimlik>` altında `enabled` ve
`config` alanlarını saklar. Devre dışı bırakılması yapılandırmasını silmez.
Eski eklenti dosyaları ve değişen kimlikler; bilinmeyen ayarlar ve kapalı
tercihleri korunarak taşınır.

Eklenti loglarında `extension_id` bulunur. Bağlantı olaylarını, yaşam döngüsü
değişikliklerini ve paket hatalarını birlikte incelemek için oturum logunu
kullanın. Depoyu paylaşırken veya hata bildirirken yerel ayarları, kimlik
bilgilerini ve operasyon loglarını makineye özel veriler olarak değerlendirin.

## Geliştirme ve doğrulama

Test edilecek özelliklerin bağımlılıklarını kurduktan sonra çalıştırın:

```bash
python run_tests.py
```

Bu komut, uygulama ve Core’un `unittest` takımlarını birlikte çalıştırır;
varsayılan olarak Qt’nin offscreen platformunu seçer. Gerçek harita duman testi,
WebEngine kaynaklarını temizlemek için ayrı bir Qt sürecinde çalışır.

Yazılım testleri sözleşmeleri ve arayüz davranışını kapsar. Araç bağlantısı,
görev aktarımı, kamera kodlayıcıları ve joystick cihazları hedef donanımla ayrıca
doğrulanmalıdır. Kayıtlı değişiklikler ve doğrulama sonuçları için
[değişiklik geçmişine](CHANGELOG.md) bakın.

## Sorun giderme

### Harita, WebEngine bulunamadığını söylüyor

`extensions/map_autonomy/requirements.txt` dosyasını `main.py` için kullanılan
ortama kurun. Sistem Python’u ile `.venv/bin/python` farklı Qt paketlerine sahip
olabilir. Qt ile Qt WebEngine sürümlerinin eşleştiğini kontrol edin.

### UDP profiline telemetri gelmiyor

Profilin giriş/çıkış yönünü, dinleme portunu ve hedef adresini kontrol edin.
Giriş profilinde gönderici yer istasyonunu hedeflemeli; ilgili arayüz ve
güvenlik duvarı bu trafiğe izin vermelidir. Aynı porta bağlanan başka bir süreç
de veri alımını etkileyebilir.

### Panel veya eklenti görünmüyor

Önce eklentinin etkinliğini ve bağımlılık durumunu kontrol edin. Ardından
**Görünüm → Paneller** menüsüne ve aktif kayıtlı görünüme bakın. Gizli panel ile
devre dışı eklenti farklı durumlardır. GPS ve otopilot göstergeleri yalnız
durum bildirir; bağlantı düzenleme araç bağlantısı kontrolünden yapılır.

### Kayıt için kodlayıcı bulunamıyor

GStreamer çalışma zamanını, uygulamanın ortamında PyGObject’ın içe
aktarılabildiğini ve donanımsal kodlayıcı eklentilerini kontrol edin.
Yerel kayıt desteklenen bir donanımsal kodlayıcı gerektirir; sessizce CPU
kodlamasına geçmez.

### Core modülü eksik görünüyor

Depo kökünde `git submodule update --init --recursive` çalıştırın.
Alt modül alınmadan oluşturulan depo kopyası Core’un içeriğini barındırmaz.

## Lisans

Kırlangıç, [GPL-3.0-only](LICENSE) ile lisanslanmıştır.
Copyright © 2026 TahsinCr. Atıf bilgileri için [COPYRIGHT](COPYRIGHT) dosyasına bakın.

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-blue
[python-url]: https://www.python.org/downloads/
[license-shield]: https://img.shields.io/badge/Lisans-GPL--3.0--only-blue
[license-url]: LICENSE
