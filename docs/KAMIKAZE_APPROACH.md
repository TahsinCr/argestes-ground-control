# Kamikaze Approach — P2 → P1 alignment handoff

Bu modül yalnız GCS tarafında yaklaşma geometrisi üretir; MAVLink/GOTO komutu göndermez.
Navigasyon entegrasyonu `navigation_handoff()` çıktısını kullanır.

## Tek config kaynağı

Varsayılan görev değerleri `src/models/kamikaze_approach.py` içindeki
`KamikazeApproachConfig` sınıfında tek yerde tutulur:

- `start_altitude_m = 110.0`
- `pullup_altitude_m = 70.0`
- `target_altitude_m = 0.0`
- `dive_angle_deg = 50.0`
- `corridor_width_m = 0.50`
- `alignment_nominal_m = 50.0`
- `alignment_min_m = 40.0`
- `alignment_max_m = 100.0`
- `alignment_search_step_m = 1.0`
- `invalidate_if_no_alignment_point = True`

Sabit 92.3 m / 58.7 m / 33.6 m değerleri koda gömülmez. Her plan oluşturulurken:

- `P1 radius = (start_altitude - target_altitude) / tan(dive_angle)`
- `pull-up radius = (pullup_altitude - target_altitude) / tan(dive_angle)`
- `P1 → pull-up horizontal = P1 radius - pull-up radius`

110 m / 70 m / 50° varsayılanında yaklaşık:

- QR → P1: `92.301 m`
- QR → pull-up: `58.737 m`
- P1 → pull-up yatay: `33.564 m`

Başlangıç irtifası, pull-up irtifası veya dalış açısı değiştirilirse bütün bu noktalar ve
QR → P1 koridor poligonu otomatik yeniden hesaplanır.

## P2 hizalama noktası

P2, P1'in QR'dan dışarı doğru aynı radyal üzerindeki hizalama noktasıdır. Normal mesafe
P2 → P1 = 50 m'dir.

Nominal P2 HSS içinde/sınırında ise aynı doğru üzerinde şu sıra denenir:

`50, 51, 52, ... 100, 49, 48, ... 40 m`

Yani önce daha fazla hizalama mesafesi korunur; sonra zorunluysa P1'e yaklaşılır.
HSS güvenliği mevcut HSS yarıçapı + `hss_margin_m` ile kontrol edilir.

P2 → P1 segmentinin ortasında HSS bulunması koridoru yeniden yönlendirmez ve elemez.
Bu bölümde amaç P2'den sonra fixed-wing yönünü bozmadan P1'e düz girmektir.

40–100 m aralığında güvenli P2 bulunamazsa varsayılan davranış koridoru geçersiz
saymaktır. `invalidate_if_no_alignment_point=False` yapılırsa nominal P2 risk bilgisiyle
çıktıda kalır ve `alignment_point_valid=False` olur.

## Handoff

Seçilen koridor için `plan.navigation_handoff()` iki navigasyon noktası döndürür:

1. `alignment_point` / `alignment_goto_parameters` = P2
2. `dive_entry_point` / `dive_entry_goto_parameters` = P1

Ayrıca `waypoints` alanı aynı sırayı açıkça taşır. P1 ve P2 aynı QR radyalındadır;
uçak önce P2'ye, sonra P1'e gittiğinde P1'e koridor yönünde ulaşır.

`pullup_point` 50° dalış doğrusu üzerindeki 70 m AGL geometrik noktadır. Planner bu
noktada pull-up komutu vermez; yalnız geometriyi handoff/telemetri/UI için sağlar.

Auto seçim ve operatör manuel seçimi aynı önceden hesaplanmış P1/P2 geometrisini kullanır.
