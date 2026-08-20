# GCS Target Selector — Kaptan / YKİ Entegrasyon Notu

Bu branch'in görevi **hedef seçimini Yer Kontrol İstasyonunda (GCS/YKİ) yapmak** ve operatörün seçtiği rakibin telemetrisini **siha-onboard/APN tarafına sürekli aktarmaktır**.

Bu doküman özellikle YKİ arayüzünü bağlayacak kişi için yazılmıştır. Kodun mantığını bilmek için bütün servisleri okumak zorunda değilsiniz.

---

## 1. En kısa sistem özeti

```text
TEKNOFEST sunucusu
  └─ rakip takım telemetrileri
          ↓
CompetitionService
          ↓
TargetSelectionService (GCS)
  ├─ kısa geçmiş tutar
  ├─ eski/geçersiz hedefleri eler
  ├─ geometri + erişilebilirlik ön puanı hesaplar
  ├─ en iyi adayları fizik tabanlı çoklu senaryoda simüle eder
  └─ TOP-5 listesi üretir
          ↓
YKİ OPERATÖRÜ bir takımı elle seçer
          ↓
OnboardMissionService.select_combat_target(team_no)
          ↓
combat.target.select        (bir kere, ACK beklenir)
          ↓
siha-onboard
          ↓
her yeni TEKNOFEST cevabında sadece seçili takım:
combat.target.telemetry     (sürekli, ACK beklenmez)
          ↓
APN / hedef durum kestirimi
```

**Otomatik hedef seçimi YOKTUR.** Top-5'in birinci sırasında olan takım yalnızca bir sıralama sonucudur. Aktif hedef ancak operatör `SEÇ` dediğinde oluşur.

---

## 2. Backend hangi veriyi kullanıyor?

Rakipler:

- `CompetitionService.competitors`
- `CompetitionService.competitors_updated`

Her rakip `CompetitorTelemetry` olarak gelir:

- takım numarası
- enlem
- boylam
- irtifa
- pitch
- heading
- roll
- hız
- veri yaşı (`zaman_farki`)
- alındığı zaman
- TEKNOFEST'ten gelen tam ham JSON (`raw`)

Kendi uçağımız:

- `TelemetryService.current`
- kendi enlem/boylam/irtifa
- ground speed
- heading
- climb rate
- telemetri tazelik bilgisi

Hedef seçici **siha-onboard içinde çalışmaz**. Sıralama GCS bilgisayarında yapılır.

---

## 3. Top-5 nasıl hesaplanıyor?

Bu branch, kurtarılabilen eski GCS target-selector implementasyonunu temel alır. Çalışan GCS sürümünde kullanılan akış şöyledir:

1. Her takım için kısa telemetri geçmişi tutulur.
2. Eski/stale rakipler elenir.
3. Rakibin heading/hız/roll/geçmiş hareketinden yaklaşık dönüş, ivme ve climb eğilimi kestirilir.
4. Hedefin kısa geleceği ileri sarılır.
5. Uçağın hedefin arka/takip geometrisine erişebilme kalitesi hesaplanır.
6. En yüksek ön puanlı en fazla 10 hedef detaylı simülasyona girer.
7. Sabit kanat için hız, bank, dönüş ve irtifa limitleri içeren küçük bir nokta-kütle simülasyonu çalışır.
8. Farklı hedef manevra senaryolarında 4 saniyelik sürekli takip/kilit penceresi aranır.
9. Sonuçlar puanlanır ve en iyi 5 takım döndürülür.

Bu simülasyon Gazebo/SITL değildir; hızlı matematiksel gelecek kontrolüdür.

### Önemli kurtarma notu

Daha sonraki bir denemede Monte-Carlo tabanlı daha gelişmiş bir selector üzerinde de çalışılmıştı. Ancak GCS için kurtarılabilen tam kaynak kod bu branch'teki **çoklu deterministik senaryo** sürümüdür. Kayıp kodu uydurup branch'e koymadık. Mevcut servis çalışır ve testlidir; daha sonra Monte-Carlo istenirse aynı servis arayüzünün arkasında değiştirilebilir.

---

## 4. Top-5 otomatik güncellenir, aktif hedef otomatik değişmez

`TargetSelectionService` GCS runtime ile başlar.

Yeni rakip telemetrisi geldiğinde:

```text
competitors_updated
       ↓
history güncellenir
       ↓
background worker
       ↓
yeni Top-5
       ↓
TargetSelectionService.updated
```

Worker **latest-wins** mantığına sahiptir. Gelen yeni telemetry UI thread'inde ağır simülasyon çalıştırmaz.

Aktif hedef ayrı bir durumdur:

```text
Top-5 değişebilir
Aktif takım = operatörün seçtiği takım
```

Örneğin operatör Team 17'yi seçtiyse sonraki Top-5'te Team 8 birinci olsa bile aktif takım kendi kendine Team 8 olmaz.

---

## 5. YKİ arayüzünü yapan kişi neye bağlanacak?

Qt tarafında hazır servisler:

### Top-5 güncellemesi

```python
qt_runtime.target_selection_service.results_updated
```

Bu signal her yeni otomatik Top-5 sonucunu taşır.

İlk ekran açılırken:

```python
qt_runtime.target_selection_service.last_result
```

okunabilir.

İstenirse elle tekrar hesaplatmak için:

```python
qt_runtime.target_selection_service.rank_targets()
```

hala vardır.

### Operatör hedef seçimi

```python
qt_runtime.onboard_service.select_combat_target(team_number)
```

Örnek:

```python
qt_runtime.onboard_service.select_combat_target(17)
```

**Bu çağrı yapılmadan hiçbir rakip APN hedefi olarak aktive edilmez.**

### Hedef iptali

```python
qt_runtime.onboard_service.clear_combat_target()
```

### Aktif hedef durumunu izleme

```python
qt_runtime.onboard_service.state_changed
```

State içinden:

```text
combat_target_active
combat_target_team_number
combat_target
combat_target_last_update_at
```

okunabilir.

---

## 6. Arayüzde önerilen görünüm

Top-5 tablosunda en az:

```text
Sıra
Takım No
Final Puan
4 sn kilit olasılığı
Mesafe
İrtifa farkı
Veri yaşı
Tahmini erişim zamanı
```

gösterilsin.

Ayrıntı panelinde mevcut modelin şu alanları vardır:

```text
pre_score
tail_access_score
data_confidence
distance_m
altitude_delta_m
data_age_ms
merge_eta_s
best_lock_start_s
best_lock_duration_s
heading_alignment_deg
turn_required_deg
simulation_quality
reason
```

Operatörün seçtiği satır ile **aktif hedef** farklı renk/durum olarak gösterilsin. Sadece tabloda bir satıra tıklanması aktif hedef anlamına gelmemeli; aktif hedef ancak `select_combat_target()` başarılı ACK döndürdüğünde değişmelidir.

---

## 7. Haritada geçmiş path nasıl alınır?

Backend kısa geçmişi public API ile verir:

```python
history = target_selection_service.backend.team_history(team_number)
```

veya backend'e doğrudan erişiliyorsa:

```python
history = runtime.target_selection_service.team_history(team_number)
```

Bütün takımlar:

```python
histories = target_selection_service.backend.histories_snapshot()
```

UI bu noktaları sırayla çizerek rakibin son kısa yolunu gösterebilir.

---

## 8. GCS → siha-onboard mesaj sözleşmesi

### A) Operatör hedef seçtiğinde

Packet:

```text
combat.target.select
```

Bu bir command/request'tir ve GCS ACK bekler.

Örnek payload:

```json
{
  "valid": true,
  "team_number": 17,
  "latitude_deg": 39.925533,
  "longitude_deg": 32.866287,
  "altitude_agl_m": 112.5,
  "pitch_deg": 2.4,
  "heading_deg": 84.0,
  "roll_deg": -3.2,
  "speed_m_s": 24.7,
  "battery_percent": 76,
  "autonomous": 1,
  "locked": 0,
  "data_age_ms": 180,
  "source_sample_time_utc": "...Z",
  "gcs_received_at_utc": "...Z",
  "gcs_emitted_at_utc": "...Z",
  "source": "teknofest-gcs",
  "stream_sequence": 0,
  "raw": { "...": "TEKNOFEST'in tam rakip satırı" }
}
```

Onboard tarafı kabul ederse normal `system.ack` dönmelidir.

### B) Hedef seçildikten sonra her yeni rakip telemetry frame'inde

Packet:

```text
combat.target.telemetry
```

Bu event tipi gönderimdir; GCS her pakette ACK beklemez.

Sadece operatörün seçtiği takım gönderilir.

APN için temel olarak gereken veriler üst seviyede hazırdır:

- hedef konumu
- hedef irtifası
- heading
- speed
- roll/pitch
- kaynak timestamp
- data age

Ayrıca `raw` içinde sunucudan gelen bütün alanlar korunur. Böylece APN'nin hedef hareket estimator'ı gerekirse batarya/otonom/kilit/bbox/GPS saati gibi ek alanlara ulaşabilir.

### C) Operatör hedefi iptal ettiğinde

Packet:

```text
combat.target.clear
```

Payload:

```json
{
  "team_number": 17,
  "source": "gcs-operator"
}
```

Onboard APN aktif hedefini temizlemelidir.

---

## 9. Stale / LOST davranışı

Seçili hedefin eski koordinatını sonsuza kadar APN'ye yollamak tehlikelidir.

Bu branch'te varsayılan üst yaş:

```text
2500 ms
```

Seçili takım son yarışma frame'inde yoksa veya telemetry yaşı bu limiti aşarsa GCS:

```text
combat.target.telemetry
```

ile:

```json
{
  "valid": false,
  "team_number": 17,
  "reason": "stale-target-telemetry"
}
```

veya:

```json
{
  "valid": false,
  "team_number": 17,
  "reason": "target-not-present-in-latest-competition-frame"
}
```

gönderir.

Aynı stale durumu 250 ms'de bir spam edilmez; durum değişiminde bir kere bildirilir. Yeni geçerli telemetry gelirse normal stream tekrar devam eder.

UI açısından:

- Takım seçimi ekranda korunabilir.
- Ama stream `valid=false` ise "HEDEF TELEMETRİSİ KAYIP/ESKİ" gösterilmeli.
- APN eski pozisyonu yeni bilgi gibi kullanmamalıdır.

---

## 10. siha-onboard tarafında nereye bağlanacak?

Mevcut elimizdeki siha-onboard snapshot'ında APN implementasyonu veya `combat.target.*` receiver bulunamadığı için burada rastgele `VisionService` sınıfına bağlantı uydurulmadı.

Kaptanın onboard entegrasyonunda yapılması gereken mantık:

```text
MessageService / application channel
  ├─ combat.target.select
  ├─ combat.target.telemetry
  └─ combat.target.clear
          ↓
SelectedTargetState / TargetEstimator
          ↓
APN
```

APN'nin görüntü servisine mi, ayrı pursuit service'e mi bağlı olacağı siha-onboard'daki gerçek APN mimarisi belirlendiğinde çözülmelidir. **GCS tarafındaki transport contract değişmek zorunda değildir.**

Önerilen onboard davranışı:

- `select`: team id'yi aktif hedef yap, ilk sample'ı kaydet, ACK dön.
- `telemetry valid=true`: aynı team ise estimator'a yeni sample ver.
- `telemetry valid=false`: APN'yi stale/lost durumuna geçir; eski konumu yeni sample gibi kullanma.
- `clear`: aktif team ve estimator state'ini temizle, ACK dön.
- farklı team id'den yanlışlıkla gelen stream'i reddet/ignore et.
- stream sequence geriye gidiyorsa eski paketi ignore et.

---

## 11. Önemli dosyalar

Backend:

```text
src/models/competition.py
src/models/target_selection.py
src/services/competition.py
src/services/target_selection.py
src/services/onboard.py
src/services/runtime.py
src/services/application_dependencies.py
```

Qt adapter:

```text
src/ui/adapters/services.py
src/ui/adapters/runtime.py
```

Mevcut eski UI referansı:

```text
src/ui/widgets/target_selection.py
```

Testler:

```text
tests/test_target_selection.py
tests/test_onboard_protocol.py
```

---

## 12. UI geliştiricisi için 30 saniyelik checklist

1. `results_updated` signal'ını Top-5 tabloya bağla.
2. Satıra tıklama sadece preview olsun.
3. `SEÇ` butonu `select_combat_target(team_no)` çağırsın.
4. ACK başarılı olmadan "aktif hedef" yazma.
5. `state_changed` ile gerçek `combat_target_team_number` değerini göster.
6. `clear_combat_target()` için iptal butonu koy.
7. `combat_target["valid"] == false` durumunu kırmızı/uyarı olarak göster.
8. `team_history()` ile istenirse son path'i haritada çiz.
9. Top-5 değişse bile aktif takımı otomatik değiştirme.
10. UI thread'inde target simulation çalıştırma; backend zaten background hesaplıyor.

---

## 13. Test durumu

Target selector + onboard message protocol testleri birlikte çalıştırılmalıdır:

```bash
PYTHONPATH=. python -m pytest -q \
  tests/test_target_selection.py \
  tests/test_onboard_protocol.py
```

Bu branch hazırlanırken bu test grubu başarılı çalıştırılmıştır.
