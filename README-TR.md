[![Python 3.11+][python-shield]][python-url]
[![Lisans][license-shield]][license-url]

[English][lang-en-url] | **Türkçe**

# Kırlangıç Yer Kontrol İstasyonu

Eklenti ana makinesine sahip masaüstü yer kontrol uygulaması. Dahili MAVLink
protokol eklentisi ve isteğe bağlı Araç Bağlantısı eklentisi mevcut araç bağlantısı,
telemetri/GPS, otopilot ve parametre işlevlerini sağlar. İsteğe bağlı kamera
eklentisi çoklu kamera panelini, kaydı ve canlı yayın çıkışını getirir. Ortak uygulamada görev
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
                         └── ayarlar, loglar, planlayıcı, harita ve batarya
```

`src/container.py` composition root'tur. `src/extension/api` sürümlü eklenti
sözleşmesini, `src/extension/host` statik manifest keşfini, yetenek kontrolünü
ve etkin eklentilerin kurulumunu sağlar. Eklentiler `src.core` herkese açık
paketlerini doğrudan kullanabilir. Protokol eklentisi ortak araç sözleşmelerini
ve çözümlenmiş telemetri verisini sağlar. Araç Bağlantısı eklentisi bağlantının
yaşam döngüsünü ve profil düzenlemeyi protokolden bağımsız `ConnectionSource`
ve `ConnectionProfiles` sözleşmeleriyle yönetir. En az bir protokol sağlayıcısı etkin
kalmalıdır. Kamera ve Araç Bağlantısı eklentileri uygulama kapanmadan açılıp
kapatılabilir; yalnız kendi servisleri ve arayüz katkıları yeniden kurulur. Sol etkinlik
çubuğunda arama, kategori/durum filtresi ve eklenti kontrolleri vardır. Mevcut
MAVLink ayarları `data/settings.json` içinde korunur; eklenti durumu ve kendi
ayarları aynı dosyanın `extensions.<kimlik>.enabled` ve
`extensions.<kimlik>.config` alanlarında tutulur; devre dışı bırakılınca silinmez.
Eski `data/extensions/` config dosyaları içe alındıktan sonra kaldırılır;
ilgisiz veri dosyalarına dokunulmaz.
Ham mesaj abonelikleri, heartbeat çözümlemesi ve parametre mesajlarının işlenmesi
protokol paketine aittir; ortak `VehicleService` bunları zorunlu kılmaz.
MAVLink çözümlenmiş veri akışını, GPS ve otopilot göstergelerini yönetir.
Araç Bağlantısı bağlantı çipini ve kalite göstergesini sağlar;
kamera durum kontrolleri kamera paketinde kalır.
`registrar.status_widget(..., placement="connections" | "metrics")` katkıyı
sol bağlantı grubuna veya bataryanın yanına yerleştirir. Bağlantı, GPS ve
otopilot göstergeleri yalnız durum gösterir; tıklayınca pencere açmaz.
Bağlantı profilleri Araç Bağlantısı eklentisinin ayarlarından düzenlenir. MAVLink'in
gelişmiş ayarları ortak ayarlar penceresi yerine Eklentiler panelindeki kendi
ayarlar eyleminden açılır.
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
eklentiler çalışan örnek olarak kullanılabilir. Eklenti ayrıca
`registrar.sidebar_view(kimlik, başlık, factory)` ile sol yan panel katkısı
ekleyebilir. `factory`, Qt üst widget'ını alır ve paneli UI iş parçacığında
oluşturur; devre dışı eklentinin arayüz kodu içe aktarılmaz.
`visual_view`, `status_widget` ve `settings_ui` ile eklenti ana çalışma alanına,
alt durum çubuğuna ve kendi ayarlarına arayüz katkısı sağlayabilir. Kamera
eklentisi bu sınırları kullanır; kamera işlevleri ortak pencereye gömülü değildir.

## Çalışma alanı

Harita merkezde kalır. Araç dinamiği, telemetri, hızlı komutlar, loglar,
parametreler ve eklentilerin getirdiği görünümler bağımsız panellerdir.
Normal kullanımda panel başlıkları görünmez; üst çubuktaki Görünüm > Paneller
menüsünden panelleri araç, alt panel ve eklenti gruplarına göre gösterip
gizleyebilir, ayraçlardan boyutlandırabilirsin. Görünüm menüsündeki Yerleşimi
düzenle seçeneği taşıma, sekmeleme ve ayrı pencereye alma tutamaklarını geçici
olarak açar. Aynı menü haritaya odaklanma, varsayılan yerleşim ve Koyu/Açık
tema seçeneklerini de içerir. Panel açıp kapatmak kayıtlı yerleşimi değiştirmez.
Görünürlük ve yerleşim uygulama kapanırken
`data/workspace-layout.json` içine kaydedilir. Boş alan oluşturan sahte dock'lar
kullanılmaz; bölmenin gerçek satır/sütun ilişkisi boyutları belirler. İlgisiz
yerleşim dalları kendi boyutlarını korur.

## İsteğe bağlı entegrasyonlar

* **Harita** kendi panelini, web dosyalarını, tercihlerini ve görev görünümünü
  sağlar. Normalize telemetri ve seyrüsefer servislerini kullanır; şu an sağlayıcı
  MAVLink'tir. Uygulama yeniden başlatılmadan kapatılıp açılabilir.
* **Joystick** RC1–RC16 atama, PWM kalibrasyonu, otomatik algılama ve buton
  aksiyonlarını getirir. Varsayılan olarak kapalıdır ve şu an MAVLink RC adaptörü
  seçilir. Kumandanın aktivasyonu eklentinin etkin olmasından ayrıdır; kapatılınca
  RC override bırakılır. Protokol seçimi cihaz/UI kodundan ayrı tutulur ve
  `src.extension.api.protocol.ActiveProtocol` üzerinden yapılır.
* **TEKNOFEST** bağımsız HTTP sunucu profilleri, sabit 1 Hz telemetri zamanlaması,
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
    "teknofest": { "enabled": false, "config": { "servers": [] } }
  }
}
```

Eski typed backend alanları uyumluluk için korunur. MAVLink paketi protokol
ayarlarını ve bağlantı profillerini ortak eklenti bölümüne yansıtır; eski
düzenleyiciler diğer eklentilerin configlerini ezmez.

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
