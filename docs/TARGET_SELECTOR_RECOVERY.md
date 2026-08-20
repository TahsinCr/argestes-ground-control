# Target Selector Recovery Note

Bu branch, kaybolan target-selector çalışmasından kurtarılabilen GCS kaynaklarını temel alır.

Kurtarılan ana davranışlar:

- GCS-side `TargetSelectionService`
- kısa rakip history
- stale telemetry eleme
- tail/capture geometri ön puanı
- en fazla 10 aday için sabit-kanat çoklu senaryo simülasyonu
- Top-5 sonuç modeli
- operatör kontrollü `select_combat_target()`
- `combat.target.select`
- `combat.target.telemetry`
- `combat.target.clear`
- seçili takımın tam TEKNOFEST `raw` telemetrisini Orin'e taşıma
- Qt target-selection adapter/panel referansı

Bu revizyonda eklenenler:

- Top-5 yeni TEKNOFEST frame'lerinde background/latest-wins olarak otomatik güncellenir.
- Otomatik active target seçimi yoktur.
- Public `team_history()` / `histories_snapshot()` eklendi.
- Qt adapter'a `results_updated` signal'ı eklendi.
- Seçili telemetry stale/missing olursa `valid=false` stream durumu gönderilir.
- Eski koordinatların APN'ye taze veri gibi tekrar gönderilmesi engellendi.
- APN için canonical alanlar + tam `raw` payload korunur.
- UI/onboard entegrasyon sözleşmesi `TARGET_SELECTOR_README.md` içinde açıklandı.
