# Goruntulu Takip Sistemi
---

## Icerik
- [Mimari](#mimari)
- [Gereksinimler](#gereksinimler)
- [Kurulum](#kurulum)
- [Kullanim](#kullanim)
- [Konfigurasyon](#konfigurasyon)
- [Alt Sistem Detaylari](#alt-sistem-detaylari)
- [Parametre Referansi](#parametre-referansi)
- [Iletisim Mimarisi](#iletisim-mimarisi)
- [Hata Yonetimi](#hata-yonetimi)

---

## Mimari

Kod yapisi 7 ana paketten olusur:

1. **core/** - Pipeline orkestrasyonu + konfigurasyon yukleme
2. **perception/** - Kamera okuma, YOLO inference, postprocess
3. **tracking/** - ByteTrack, EKF, redetect
4. **lock_logic/** - Durum makinesi, kilit timer, vurus alani
5. **control/** - Cascade PID, antiwindup, range loop
6. **comms/** - APN handoff (shared memory), MAVLink, sunucu, telemetri
7. **recording/** - Video kaydi, NTP senkronizasyonu

### Veri Akisi

```
Kamera (V4L2/CSI)
    |
    v
[CameraThread] ---- frame ----> [DetectionThread]
    |                               |
    |                               v
    |                          YOLO26s Infer
    |                          + NMS filtreleme
    |                               |
    |                          detections
    |                               |
    |                          [_detections_lock]
    |                               |
    +-------------------------------+
                                    |
                                    v
                          Ana Dongu (_tick)
                                    |
                                    +-- ByteTrack ID ata
                                    +-- Hedef sec (max confidence)
                                    +-- EKF predict + update
                                    +-- Forward projection (latency compensation)
                                    +-- Vurus alani kontrolu
                                    +-- Lock timer guncelle
                                    +-- Durum makinesi gecisi
                                    +-- Cascade PID (yaw/pitch/throttle)
                                    +-- MAVLink attitude_rate gonder
                                    +-- APN handoff (shared memory)
                                    +-- Telemetri publish
                                    +-- Video kaydi
```

---

## Gereksinimler

### Donanim
| Bilesen               | Model / Ozellik                    |
|------------------------|------------------------------------|
| Tek Kart Bilgisayar    | NVIDIA Jetson Nano / Orin NX       |
| Kamera                 | Raspberry Pi HQ + 16mm lens        |
| Otopilot               | Pixhawk (PX4/ArduPilot)           |

### Yazilim
| Bagimlilik        | Versiyon      | Aciklama                      |
|--------------------|---------------|-------------------------------|
| Python             | 3.10+         | Ana gelistirme dili            |
| TensorRT           | 8.6+          | YOLO engine calistirma         |
| OpenCV             | 4.8+          | Kamera okuma, goruntu isleme   |
| ONNX Runtime       | 1.24+         | YKI CPU/GPU ONNX inference     |
| NumPy              | 1.24+         | Matematiksel islemler          |
| PyYAML             | 6.0+          | Konfigurasyon okuma            |
| pymavlink          | 2.4+          | MAVLink protokolu              |
| httpx / aiohttp    | -             | HTTP POST (sunucu bildirimi)   |

---



## Kullanim

```bash
# Basit kullanim (kamera indeksi 0, varsayilan config)
python main.py

# Parametreli kullanim
python main.py \
    --camera-source 0 \
    --engine-path data/models/target_lock/yolo26s.pt \
    --mavlink-conn "udp:127.0.0.1:14550" \
    --sunucu-url "http://192.168.1.100:8000" \
    --video-dir kayitlar \
    --debug
```

### CLI Argumanlari
| Arguman | Varsayilan | Aciklama |
|---------|-----------|----------|
| --camera-source | 0 | Kamera indeksi (0=USB) veya CSI/RTSP yolu |
| --camera-config | config/camera.yaml | Kamera YAML yolu |
| --control-config | config/control.yaml | Kontrol YAML yolu |
| --model-config | config/model.yaml | Model YAML yolu |
| --engine-path | None | TensorRT engine yolu (YAML override) |
| --mavlink-conn | None | MAVLink baglanti stringi (YAML override) |
| --sunucu-url | None | Yer istasyonu sunucu URL'si |
| --video-dir | kayitlar | Video kayit dizini |
| --debug | False | Debug log seviyesi |

---

## Konfigurasyon

### Kamera (config/camera.yaml)
```yaml
camera:
  model: Raspberry Pi HQ
  lens: 16mm
  focal_length_mm: 16.0
  resolution:
    width: 640
    height: 480
  intrinsics:
    fx: 460.0
    fy: 460.0
    cx: 320.0
    cy: 240.0
  distortion:
    k1: -0.25
    k2: 0.12
    p1: 0.0
    p2: 0.0
```

### Kontrol (config/control.yaml)
```yaml
control:
  outer_loop:
    kp: 0.04
    ki: 0.001
    kd: 0.002
  inner_loop:
    kp: 0.25
    ki: 0.008
    kd: 0.012
```

### Model (config/model.yaml)
```yaml
model:
  engine_path: "data/models/target_lock/yolo26s.pt"
  input_shape: [640, 640]
  confidence_threshold: 0.30
  iou_threshold: 0.45
  use_dla: false
  dla_core: 0
```

---

## Alt Sistem Detaylari

### 1. Algilama (Perception)
- **Dosyalar:** perception/yolo_infer.py, capture.py, postprocess.py
- **Resmi Ultralytics YOLO26s** PT agirligi; YKI'da ONNX Runtime fallback
- **640x640** giris boyutu
- **Jetson Orin:** PT agirligindan TensorRT engine export edilebilir
- **Sinif filtresi:** COCO `airplane` (`target_class_ids=[4]`)
- **Capture:** V4L2/CSI kamera okuma, thread-safe frame buffer (8 frame kapasite)

### 2. Takip (Tracking)
- **Dosyalar:** tracking/bytetrack_wrapper.py, ekf.py, redetect.py
- **ByteTrack** coklu nesne takibi:
  - Iki asamali esik: high=0.35 / low=0.10
  - Yeni track: confidence >= 0.30
  - IOU esleme: 0.70
  - Min hit streak: 4 (onayli track)
  - Max lost: 30 frame (sonra redetect)
- **EKF (Extended Kalman Filter):**
  - State: [x, y, w, h, vx, vy, vw, vh] (8 boyut)
  - Process noise (Q): [0.5, 0.5, 0.1, 0.1, 1.0, 1.0, 0.2, 0.2]
  - Measurement noise (R): [2.5, 2.5, 5.0, 5.0]
  - Min confidence for update: 0.6
  - Forward projection: detection latency (~30-50ms) telafisi
  - Coasting: 5 frame EKF predict ile devam
- **Redetect:** 30 frame kayip sonrasi, EKF predicted bbox cevresinde ROI crop + YOLO

### 3. Kilit Mantigi (Lock Logic)
- **Dosyalar:** lock_logic/state_machine.py, lock_timer.py, vurus_alani.py
- **Durum Makinesi (6-state):**
  - ACQUIRE -> Hedef algilaniyor, henuz takip baslamadi
  - TRACK -> ByteTrack ID atandi, kontrol aktif
  - LOCK_CONFIRM -> Vurus alaninda 4sn, kilit onayi
  - LOCKED -> Hedef kilitlendi, sunucu POST gonderildi
  - HANDOVER -> Hedef kayip, kilit hala gecerli
  - LOST -> Hedef tamamen kayip, yeni hedef bekle

- **Lock Timer:** 5 saniyelik pencere icinde 4 saniye gecerli kilit
  - Vurus alani icindeki gecerli sure birikir
  - Tek kesintinin 200 ms'yi asmasi kilit adayini sifirlar
  - Kilitlenme: gecerli sure en az 4 saniye

- **Vurus Alani (VurusAlani):**
  - Yatay bolge: frame'in ortadaki %50'si (iki yanda %25 marj)
  - Dikey bolge: frame'in ortadaki %80'i (ust ve altta %10 marj)
  - Hedef merkezi bu bolgede olmali
  - Hedefin en az bir ekseni frame'in en az %5'i; YKI guven payiyla %6 kullanir

### 4. Kontrol (Control)
- **Dosyalar:** control/cascade_pid.py, antiwindup.py, range_loop.py
- **Cascade PID** (yaw ve pitch ayri):
  - Outer loop: piksel hatasi -> hedef acisal hiz
  - Inner loop: hedef acisal hiz -> servo PWM
  - Dead zone: +/-5 piksel (merkez)
  - Max yaw rate: 45 deg/s
  - Max pitch rate: 30 deg/s
- **Antiwindup:** Integral term saturation korumasi
- **Range Loop:** Hedef alani referans = frame'in %5'i -> throttle

### 5. Iletisim (Comms)
- **Dosyalar:** comms/apn_handoff.py, mavlink_bridge.py, sunucu_client.py, telemetri_feeder.py
- **APN Handoff:** Shared memory (4KB) uzerinden Jetson <-> APN iletisimi
- **MAVLink:** Pixhawk'a attitude_rate komutlari (yaw_rate, pitch_rate)
- **Sunucu Client:** HTTP POST ile kilitlenme bilgisi
- **Telemetri Feeder:** 2Hz periyodik telemetri publish

### 6. Kayit (Recording)
- **Dosyalar:** recording/video_logger.py, time_syncer.py
- **Video Logger:** MP4 formatinda, frame timestamp + lock status overlay
- **Time Syncer:** NTP senkronizasyonu (pool.ntp.org)

---

## Parametre Referansi

### AppConfig (core/config.py)
| Parametre | Varsayilan | Aciklama |
|-----------|-----------|----------|
| camera_source | 0 | Kamera indeksi veya CSI/RTSP yolu |
| camera_buffer_size | 8 | Frame buffer kapasitesi |
| lock_duration_s | 4.0 | Kilit icin gereken sure (sn) |
| lock_tolerance_s | 1.0 | Vurus alani disi tolerans (sn) |
| lock_strike_horizontal_pct | 0.25 | Sag/sol vurus alani marji |
| lock_strike_vertical_pct | 0.10 | Ust/alt vurus alani marji |
| lock_strike_size_pct | 0.05 | Sartnamedeki minimum hedef eksen orani |
| lost_frame_limit | 30 | Max kayip frame (redetect esigi) |
| telemetry_hz | 2.0 | Telemetri yayin frekansi |
| mavlink_connection | udp:127.0.0.1:14550 | MAVLink baglanti stringi |
| sunucu_base_url | http://127.0.0.1:8000 | Yer istasyonu URL'si |
| apn_shm_name | goruntulu_takip_shm | Shared memory segment adi |
| video_output_dir | kayitlar | Video kayit dizini |
| ntp_server | pool.ntp.org | NTP sunucu adresi |
| servo_center_x | 960 | Servo merkez x (piksel) |
| servo_center_y | 540 | Servo merkez y (piksel) |
| target_class_ids | [4] | Izlenecek COCO airplane sinif ID'si |

### CameraConfig
| Parametre | Varsayilan | Aciklama |
|-----------|-----------|----------|
| model | Raspberry Pi HQ | Kamera modeli |
| lens | 16mm | Lens |
| focal_length_mm | 16.0 | Fokal uzunluk (mm) |
| width, height | 640, 480 | Frame cozunurlugu |
| fx, fy | 460.0, 460.0 | Fokal uzunluk (piksel) |
| cx, cy | 320.0, 240.0 | Principal point |
| k1, k2 | -0.25, 0.12 | Radyal distorsiyon |
| p1, p2 | 0.0, 0.0 | Tangential distorsiyon |

### ControlConfig
| Parametre | Varsayilan | Aciklama |
|-----------|-----------|----------|
| outer_kp | 0.04 | Dis dongu oransal |
| outer_ki | 0.001 | Dis dongu integral |
| outer_kd | 0.002 | Dis dongu turev |
| inner_kp | 0.25 | Ic dongu oransal |
| inner_ki | 0.008 | Ic dongu integral |
| inner_kd | 0.012 | Ic dongu turev |
| max_yaw_rate | 45.0 | Max yaw hizi (deg/s) |
| max_pitch_rate | 30.0 | Max pitch hizi (deg/s) |
| dead_zone_px | 5.0 | Merkez olu bolge (piksel) |

### ModelConfig
| Parametre | Varsayilan | Aciklama |
|-----------|-----------|----------|
| engine_path | data/models/target_lock/yolo26s.pt | PT, ONNX veya TensorRT model yolu |
| input_shape | [640, 640] | Model giris boyutu |
| confidence_threshold | 0.30 | Min guven skoru |
| iou_threshold | 0.45 | NMS IOU esigi |
| use_dla | False | DLA kullanimi |
| dla_core | 0 | DLA core indeksi |

### ByteTrack Parametreleri
| Parametre | Varsayilan | Aciklama |
|-----------|-----------|----------|
| track_high_thresh | 0.35 | Birinci asama esigi |
| track_low_thresh | 0.10 | Ikinci asama esigi |
| match_thresh | 0.70 | IOU esleme esigi |
| new_track_thresh | 0.30 | Yeni track acma esigi |
| max_lost_frames | 30 | Max kayip frame |
| min_hit_streak | 4 | Onayli track icin min eslesme |

### EKF Parametreleri
| Parametre | Deger | Aciklama |
|-----------|-------|----------|
| state_dim | 8 | [x, y, w, h, vx, vy, vw, vh] |
| Q diag | [0.5, 0.5, 0.1, 0.1, 1.0, 1.0, 0.2, 0.2] | Surec gurultusu |
| R diag | [2.5, 2.5, 5.0, 5.0] | Olcum gurultusu |
| min_conf_for_update | 0.6 | EKF guncelleme icin min guven |
| max_draw_missed_frames | 5 | Coasting frame sayisi |

---

## Iletisim Mimarisi

### Shared Memory Layout (APN <-> Jetson, 4KB)
| Offset | Boyut | Alan | Format | Aciklama |
|--------|-------|------|--------|----------|
| 0 | 4 | magic | uint32 | 0xDEADBEEF |
| 4 | 4 | frame_seq | uint32 | Sira numarasi |
| 8 | 2 | target_x | int16 | Hedef merkez x (px) |
| 10 | 2 | target_y | int16 | Hedef merkez y (px) |
| 12 | 2 | target_w | uint16 | Hedef genislik (px) |
| 14 | 2 | target_h | uint16 | Hedef yukseklik (px) |
| 16 | 4 | target_id | uint32 | ByteTrack ID |
| 20 | 4 | confidence | float | Guven skoru |
| 24 | 1 | lock_active | bool | Kilit durumu |
| 25 | 4 | yaw_rate | float | Normalize yaw (-1..1) |
| 29 | 4 | pitch_rate | float | Normalize pitch (-1..1) |
| 33 | 4 | throttle | float | Gaz (0..1) |
| 37 | 8 | timestamp_ms | double | Zaman damgasi (ms) |
| 45 | 4 | flags | uint32 | Bitfield |
| 49-4095 | - | reserved | - | Gelecek kullanim |

### MAVLink Komutlari
- **Mesaj:** ATTITUDE_RATE (set_attitude_rate)
- **Gonderim:** TRACK / LOCK_CONFIRM / LOCKED state'lerinde
- **Frekans:** ~30Hz (her _tick'te)
- **Icerik:** yaw_rate (deg/s), pitch_rate (deg/s)

---

## Hata Yonetimi

### Detection Watchdog
- **Timeout esigi:** 1 saniye (son detection'dan itibaren)
- **Aktivasyon:** lock_timer reset -> FSM LOST override
- **Kamera reopen:** 10 frame uste none -> reopen thread
- **Max deneme:** 3 (her biri 3sn timeout)
- **Basarisizlik:** _camera_dead = True (alarm state)

### Pipeline Hata Yonetimi
| Hata | Davranis |
|------|----------|
| Detection timeout | Lock timer reset, FSM LOST, throttle 0 |
| Kamera arizasi | Reopen thread (3 deneme), sonra alarm |
| MAVLink baglanti hatasi | Log + devam (graceful degradation) |
| Sunucu POST hatasi | Log + tekrar dene (sonraki LOCKED) |
| Shared memory hatasi | Fallback mode (belleksiz calisma) |

---

## Proje Dosya Yapisi

```
Goruntulu_Takip/
+-- main.py                     # CLI giris noktasi
+-- best.pt                     # YOLO agirlik dosyasi
+-- README.md                   # Bu dosya
+-- PARAMETRELER.txt            # Tum degisken listesi
+-- config/
|   +-- camera.yaml             # Kamera kalibrasyon
|   +-- control.yaml            # PID kazanclari
|   +-- model.yaml              # YOLO engine ayarlari
|   +-- system_params.txt       # Tum sistem parametreleri
+-- core/
|   +-- __init__.py
|   +-- config.py               # AppConfig + YAML yukleme
|   +-- pipeline.py             # Ana islem hatti
+-- perception/
|   +-- __init__.py
|   +-- capture.py              # Kamera okuma
|   +-- postprocess.py          # NMS + cikti isleme
|   +-- yolo_infer.py           # YOLO TensorRT inference
+-- tracking/
|   +-- __init__.py
|   +-- bytetrack_wrapper.py    # ByteTrack implementasyonu
|   +-- ekf.py                  # Extended Kalman Filter
|   +-- redetect.py             # Hedef yeniden yakalama
+-- control/
|   +-- __init__.py
|   +-- antiwindup.py           # Integral windup korumasi
|   +-- cascade_pid.py          # Cascade PID kontrolcu
|   +-- range_loop.py           # Mesafe/throttle dongusu
+-- lock_logic/
|   +-- __init__.py
|   +-- lock_timer.py           # Kilit zamanlayicisi
|   +-- state_machine.py        # 6-state FSM
|   +-- vurus_alani.py          # Vurus alani geometrisi
+-- comms/
|   +-- __init__.py
|   +-- apn_handoff.py          # Shared memory iletisimi
|   +-- mavlink_bridge.py       # MAVLink komutlari
|   +-- sunucu_client.py        # Sunucu HTTP POST
|   +-- telemetri_feeder.py     # Telemetri yayini
+-- recording/
|   +-- __init__.py
|   +-- time_syncer.py          # NTP senkronizasyonu
|   +-- video_logger.py         # Video kaydi
+-- kayitlar/                   # Video kayit ciktisi
```
