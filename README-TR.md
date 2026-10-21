[![Python 3.11+][python-shield]][python-url]
[![Lisans][license-shield]][license-url]

[English](README.md) | **Türkçe**

# Kırlangıç Yer Kontrol İstasyonu

Kırlangıç, eklenti ana makinesi üzerine kurulu bir masaüstü yer kontrol istasyonudur. Güncel araç bağlantısı MAVLink kullanır; uygulama ve özellik paketleri, araç protokolleri ile operatör araçlarının birbirinden bağımsız geliştirilebilmesi için ayrılmıştır.

Ortak uygulama çalışma alanını, ayarları, loglamayı, eklenti yaşam döngüsünü ve normalize veri sözleşmelerini yönetir. Araç bağlantısı, harita, kamera ve diğer operatör özellikleri eklenti paketlerinde bulunur.

## Hızlı başlangıç

```bash
git clone --recurse-submodules \
  https://github.com/TahsinCr/ground-control-station.git
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

Python 3.11+ ve Qt 6 gerekir. Protokol ve WebEngine bağımlılıklarını ilgili eklentinin requirements dosyasından kurun ve uygulamayı aynı sanal ortamdan başlatın. Qt ile Qt WebEngine sürümleri uyumlu olmalıdır. Linux'ta PyQt6 ve Qt WebEngine için sistem paketleri de gerekebilir.

## Araca bağlanma

Araç bağlantısı kontrolünden isimli bir MAVLink profili oluşturun veya seçin. Yaygın uç noktalar:

| Bağlantı | Uç nokta | Örnek baud |
| --- | --- | --- |
| Seri | `/dev/ttyUSB0` | `57600` |
| UDP girişi | `udpin:0.0.0.0:14550` | — |
| TCP | `tcp:127.0.0.1:5762` | — |

Bağlantı, araç heartbeat'i alındığında sağlıklı kabul edilir. `auto_connect` ilk bağlantı denemesini, `autoreconnect` kurulmuş bağlantı koptuktan sonraki kurtarmayı yönetir. İkisi de kapalıysa otomatik bağlantı denenmez. Operatörün bağlantıyı kesmesi, yeni bir bağlantı isteğine kadar kurtarmayı durdurur.

MAVLink taşımasını ortak istemci ve router okur. Servisler bu katmandan gelen olayları tüketir; arayüz ikinci bir okuyucu oluşturmaz ve paket kodlamaz.

## Mimari

### Uygulama ve eklenti ana makinesi

Uygulama bileşenleri `src/container.py` içinde birleştirilir. Sürümlü eklenti sözleşmesi `src/extension/api`, manifest keşfi, yetenek denetimi ve etkin eklentilerin kurulumu ise `src/extension/host` altındadır. Eklentiler `src.core` içindeki herkese açık paketleri doğrudan kullanabilir.

Ortak uygulama çalışma alanı, ayarlar, loglama ve normalize veri servislerini sunar. Protokol eklentisi araç verisini ortak sözleşmelere uyarlar. Araç Bağlantısı eklentisi bağlantı yaşam döngüsünü ve profilleri protokolden bağımsız `ConnectionSource` ve `ConnectionProfiles` sözleşmeleriyle yönetir. En az bir araç protokolü etkin kalmalıdır.

### Eklenti yapısı

Her paket `extensions/<kimlik>/` altında bulunur. `manifest.json` dosyası API sürümünü, giriş noktasını ve sağlanan ya da gereken yetenekleri tanımlar. Eklenti fabrikası `configure(registrar, context)` ve isteğe bağlı `start(context)`/`stop()` metotlarına sahip bir nesne döndürür. `registrar.singleton(...)` uygulamaya servis sağlar; aynı servisin birden çok eklenti tarafından sahiplenilmesi reddedilir.

Eklentiler `registrar.visual_view(...)` ile panel, `registrar.status_widget(...)` ile alt bar göstergesi, `registrar.settings_page(factory)` ile ayar sayfası ekler. Ayar sayfaları yalnızca kullanıcı ilgili kategoriyi açtığında yüklenir. Kapanışta timer, worker ve abonelikleri temizlemek için `prepare_shutdown()` sunabilirler. Devre dışı eklenti servis başlatmaz ve arayüz kodunu yüklemez. Eklenti yöneticisi keşif ve etkinleştirmeyi yapar; ayarlar ortak Ayarlar penceresinde kalır.

Ortak araç servisleri etkin protokole bağlı olduğundan protokol eklentisi tek başına yeniden yüklenemez. Kamera ve Araç Bağlantısı çalışma sırasında açılıp kapatılabilir; yalnızca kendi servisleri ve arayüz katkıları yeniden kurulur.

### Ayarlar ve çeviriler

Uygulama ayarları ve eklenti durumu `data/settings.json` içinde tutulur. Her eklentinin durumu ve yapılandırması `extensions.<kimlik>.enabled` ile `extensions.<kimlik>.config` alanlarındadır. Eklentiyi kapatmak ayarlarını silmez. Eski `data/extensions/` dosyaları içe aktarıldıktan sonra kaldırılır; ilgisiz veriler korunur.

Her eklentinin kendi `i18n/tr.json` ve `i18n/en.json` kataloğu vardır. Ana makine katalogları doğrular, birleştirip önbelleğe alır ve yinelenen anahtarları reddeder.

## Çalışma alanı

Harita, araç dinamiği, araç telemetrisi, hızlı komutlar, loglar, parametreler ve eklentilerin sağladığı görünümler ayrı panellerdir. Panelleri **Görünüm → Paneller** menüsünden açıp kapatın. Bir paneli taşımak, bölmek veya sekmelemek için üstündeki küçük tutamağı sürükleyin; yanındaki menü yerleşim ve görünürlük seçeneklerini açar. Ayrı bir yerleşim düzenleme modu yoktur.

Sol çubuğun üst kısmı kayıtlı görünümleri, alt kısmı etkin görünümün panel kategorilerini gösterir. Her görünüm panel görünürlüğünü, bölme ve sekme düzenini, boyutları ve kategorileri `data/workspace-layout.json` dosyasında saklar. Görünüm değiştirmek pencereyi yeniden boyutlandırmaz. Eski tek yerleşimli dosyalar başlangıç görünümüne taşınır. Aynı bölmedeki paneller ilgili boyutu paylaşır; ayrı dallar bağımsız boyutlandırılır.

Panel menüsünden içerik boyutunu sabitleyebilirsiniz. Bu tercih görünümle birlikte kaydedilir. Boyutu sabitlenmiş bir panel, uygulama penceresinin küçülebileceği alt sınırı yükseltebilir.

## Dahili ve isteğe bağlı paketler

MAVLink paketi mevcut protokolü, çözümlenmiş araç verisini, GPS'i ve otopilot göstergelerini sağlar. Parametre paketi `mavlink.parameters` yeteneğini kullanır ve kendi protokol adaptörünü içerir. Ortak `VehicleService`, RC veya görev metotlarını zorunlu kılmaz; isteğe bağlı `RcControl` ve `MissionControl` sözleşmeleri ayrıca duyurulur.

Diğer paketler Harita ve Otonomi (`map_autonomy`), Kamera, Joystick, Batarya, Araç Dinamiği, Telemetri Göstergesi, Hızlı Komutlar ve SIHA Teknofest Sunucusu'dur. Ayarları ortak Ayarlar penceresindeki kendi kategorilerinde yönetilir. Kamera ve yarışma sunucusu kontrolleri, ilgili eklenti etkin olduğunda alt çubuk popup'ı olarak görünür.

Harita ve Otonomi haritayı, web dosyalarını, karo önbelleğini ve görev planlayıcısını yönetir. Joystick şu anda MAVLink RC adaptörünü kullanır; joystick kapatıldığında RC override değerleri bırakılır. Telemetri Göstergesi 58 alanın seçilip sıralanmasını, liste veya gruplu ızgara düzeninde gösterilmesini sağlar. SIHA Teknofest Sunucusu birden fazla HTTP profilini destekler ve alışverişleri `data/teknofest/history.sqlite3` içinde saklar. Profil otomatik bağlantıya ayarlanmadıkça veya operatör bağlanmadıkça sunucuya istek göndermez.

Yeni bir araç protokolü eklemek için gereken adaptör ve yetenekleri sağlayın. Özellik arayüzleri normalize veri kullanmalı, taşıma ayrıntılarına bağlanmamalıdır. Üretim mock telemetrisi kaldırılmıştır; testler kendi sahtelerini sağlar.

## Ayarlar, loglar ve testler

Ayarlar `data/settings.json` içinde tutulur. Her oturum `data/logs/gcs_<oturum-tarihi>_<oturum-saati>.json` konumuna JSON logu yazar. Eklenti kayıtları ortak logda `extension_id` bilgisiyle yer alır.

Testleri çalıştırın:

```bash
python run_tests.py
```

Qt testleri offscreen platformda çalışır. Saha kullanımı öncesinde hedef araçla MAVLink bağlantısını ve görev aktarımını doğrulayın.

## Lisans

Copyright © 2026 TahsinCr.

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-blue
[python-url]: https://www.python.org/downloads/
[license-shield]: https://img.shields.io/badge/Lisans-GPL--3.0-blue
[license-url]: LICENSE
