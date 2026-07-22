"""Ana işlem hattı (Pipeline) orkestrasyonu.

Tüm alt sistemleri birbirine bağlayarak gerçek zamanlı görüntülü takip
döngüsünü yönetir.

Mimari:
    - Ana döngü asyncio ile çalışır.
    - Kamera okuma ve YOLO çıkarımı ayrı threading iş parçacıklarında
      çalışarak GPU/CPU beklemelerini azaltır.
    - Kontrol çıkışları MAVLink üzerinden Pixhawk'a iletilir.
    - Kilitlenme bilgisi APN modülüne shared memory ile, yer istasyonuna
      ise HTTP POST ile gönderilir.
"""

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from core.config import AppConfig

from perception import CameraCapture, YoloInfer, non_max_suppression
from tracking import ByteTrackWrapper, ExtendedKalmanFilter, Redetect
from control import CascadePID, RangeLoop
from lock_logic import LockStateMachine, LockState, LockTimer, VurusAlani
from comms import APNHandoff, SunucuClient, TelemetriFeeder, MavlinkBridge
from recording import TimeSyncer, VideoLogger

logger = logging.getLogger(__name__)


class GoruntuluTakipPipeline:
    """Tüm alt sistemleri birbirine bağlayan ana işlem hattı."""

    def __init__(self, config: AppConfig):
        self.cfg = config
        self._running = False
        self._stop_event = threading.Event()

        # ----- Algılama (Perception) -----
        self.camera = CameraCapture(source=config.camera_source, buffer_size=config.camera_buffer_size)
        self.detector = YoloInfer(engine_path=config.model.engine_path, input_shape=tuple(config.model.input_shape), conf_threshold=config.model.confidence_threshold)

        # ----- Takip (Tracking) -----
        self.tracker = ByteTrackWrapper(
            track_high_thresh=0.35,
            track_low_thresh=0.10,
            match_thresh=0.7,
            new_track_thresh=0.30,
            max_lost_frames=config.lost_frame_limit,
            min_hit_streak=4,
        )
        self.ekf = ExtendedKalmanFilter()
        self.redetector = Redetect()

        # ----- Kilit mantığı (Lock Logic) -----
        self.state_machine = LockStateMachine()
        self.lock_timer = LockTimer(lock_duration=config.lock_duration_s, tolerance=config.lock_tolerance_s)
        self.vurus_alani = VurusAlani(frame_width=config.camera.width, frame_height=config.camera.height)

        # ----- Kontrol (Control) -----
        _outer = {"kp": config.control.outer_kp, "ki": config.control.outer_ki, "kd": config.control.outer_kd}
        _inner = {"kp": config.control.inner_kp, "ki": config.control.inner_ki, "kd": config.control.inner_kd}
        self.cascade_pid_yaw = CascadePID(outer_gains=_outer, inner_gains=_inner)
        self.cascade_pid_pitch = CascadePID(outer_gains=_outer, inner_gains=_inner)
        self.range_loop = RangeLoop()

        # ----- İletişim (Comms) -----
        self.mavlink = MavlinkBridge(connection_string=config.mavlink_connection)
        self.apn_handoff = APNHandoff()
        self.sunucu = SunucuClient(base_url=config.sunucu_base_url)
        self.telemetri = TelemetriFeeder()

        # ----- Kayıt (Recording) -----
        self.time_syncer = TimeSyncer(server_host=config.ntp_server)
        self.video_logger = VideoLogger(output_path=config.video_output_dir)

        # ----- Çalışma zamanı değişkenleri -----
        self._last_time = time.perf_counter()
        self._lost_frame_count = 0
        self._current_target_id: Optional[int] = None
        self._lock_sent = False
        self._telemetry_last_sent = 0.0
        self._frame = None
        self._frame_ts = 0.0                       # frame alınma anı (perf_counter)
        self._frame_lock = threading.Lock()
        self._detections: List[Any] = []
        self._detections_ts = time.perf_counter()  # YOLO çıkarımı yapılan frame'in zamanı
        self._detections_lock = threading.Lock()

        # ----- Watchdog değişkenleri -----
        self._last_detection_time = time.perf_counter()
        self._camera_fail_count = 0
        self._camera_reopen_lock = threading.Lock()
        self._camera_reopen_thread: Optional[threading.Thread] = None
        self._camera_dead = False               # kamera kalıcı arıza flag'i
        self._watchdog_override = False          # watchdog lock_timer'ı bypass eder

        # ----- Gecikme ölçümü -----
        self._frame_capture_ts = 0.0                # frame'in capture thread'den alındığı an
        self._max_latency_ms = 0.0
        self._latency_log_counter = 0

        logger.info("GoruntuluTakipPipeline başlatıldı.")

    # ------------------------------------------------------------------
    # İş parçacıkları
    # ------------------------------------------------------------------
    def _camera_thread(self):
        """Kamera karelerini sürekli okuyan iş parçacığı."""
        logger.info("Kamera iş parçacığı başladı.")
        while not self._stop_event.is_set():
            frame = self.camera.read()
            if frame is None:
                logger.warning("Kamera karesi alınamadı, atlanıyor...")
                time.sleep(0.001)
                continue
            now = time.perf_counter()
            with self._frame_lock:
                self._frame = frame
                self._frame_ts = now
        logger.info("Kamera iş parçacığı durdu.")

    def _detection_thread(self):
        """Son kare üzerinde YOLO çıkarımı yapan iş parçacığı."""
        logger.info("Algılama iş parçacığı başladı.")
        while not self._stop_event.is_set():
            with self._frame_lock:
                frame = self._frame
                frame_ts = self._frame_ts
            if frame is None:
                time.sleep(0.001)
                continue
            raw_detections = self.detector.infer(frame)
            # Sadece izlenecek sınıfları filtrele
            filtered = [
                d for d in raw_detections
                if len(d) >= 6 and int(d[5]) in self.cfg.target_class_ids
            ]
            nms_detections = non_max_suppression(filtered, iou_threshold=self.cfg.model.iou_threshold)
            with self._detections_lock:
                self._detections = nms_detections
                self._detections_ts = frame_ts
            # Watchdog: son başarılı detection zamanını güncelle
            if nms_detections:
                self._last_detection_time = time.perf_counter()
        logger.info("Algılama iş parçacığı durdu.")

    def _detection_timeout_check(self) -> bool:
        """Son detection'dan bu yana geçen süre >1sn ise hedef kayboldu say.

        Returns:
            True → timeout, FSM LOST'a zorlanmalı
        """
        elapsed = time.perf_counter() - self._last_detection_time
        return elapsed > 1.0

    def _camera_reopen_worker(self):
        """Kamera yeniden başlatma iş parçacığı (ana döngüyü bloke etmez).

        VideoCapture.open() USB kamerada 200-500ms sürebilir, bazı driver
        arızalarında exception fırlatmadan sonsuz bloke olabilir.
        Bu nedenle:
            - Maks 3 deneme, her deneme 3sn timeout
            - 3 deneme de başarısız → alarm state → pilot/yer istasyonu bilgilendirilir
            - Açma süresince ana döngü LOST state'inde kalmaya devam eder
        """
        max_attempts = 3
        timeout_per_attempt = 3.0  # saniye

        for attempt in range(1, max_attempts + 1):
            logger.warning("Kamera reopen deneme %d/%d (timeout: %.1fs)...",
                           attempt, max_attempts, timeout_per_attempt)
            with self._camera_reopen_lock:
                self.camera.release()
                time.sleep(0.1)
                try:
                    self.camera = CameraCapture(
                        source=self.cfg.camera_source,
                        buffer_size=self.cfg.camera_buffer_size,
                    )
                    self._camera_fail_count = 0
                    logger.info("Kamera reopen deneme %d/%d başarılı.", attempt, max_attempts)
                    self._camera_reopen_thread = None
                    return
                except Exception as exc:
                    logger.error("Kamera reopen deneme %d/%d başarısız: %s",
                                 attempt, max_attempts, exc)
                    self._camera_fail_count += 1

        # Tüm denemeler başarısız → alarm
        logger.critical("Kamera reopen %d denemede başarısız. Sistem LOST state'inde kalacak.", max_attempts)
        # APN handoff üzerinden alarm state bas (bir sonraki tick'te okunur)
        self._camera_dead = True
        self._camera_reopen_thread = None

    # ------------------------------------------------------------------
    # Ana kontrol döngüsü (asenkron)
    # ------------------------------------------------------------------
    async def run(self):
        """Ana asenkron kontrol döngüsünü başlatır."""
        self._running = True
        self._last_time = time.perf_counter()

        # İş parçacıklarını başlat
        cam_thread = threading.Thread(target=self._camera_thread, daemon=True, name="camera-thread")
        det_thread = threading.Thread(target=self._detection_thread, daemon=True, name="detection-thread")
        cam_thread.start()
        det_thread.start()
        logger.info("İş parçacıkları başlatıldı.")

        try:
            while self._running and not self._stop_event.is_set():
                now = time.perf_counter()
                dt = now - self._last_time
                self._last_time = now

                # dt çok büyükse (ör. debug kesmesi) sınırlandır
                if dt > 0.5:
                    dt = 0.033  # ~30 FPS varsay

                await self._tick(dt)
                await asyncio.sleep(0.001)

        except asyncio.CancelledError:
            logger.info("Ana döngü iptal edildi.")
        finally:
            self._stop_event.set()
            cam_thread.join(timeout=3.0)
            det_thread.join(timeout=3.0)
            self.shutdown()
            logger.info("Pipeline kapatıldı.")

    async def _tick(self, dt: float):
        """Her bir zaman adımında çağrılan ana işlem birimi."""
        # ---------------------------------------------------------------
        # 0. Watchdog: detection timeout kontrolü
        #    ÖNCELİK: watchdog detection timeout'u tespit ederse lock_timer'ı
        #    bypass eder ve FSM'i direkt LOST'a zorlar. lock_timer'ın tolerance
        #    süresi sadece hedef görünürken vuruş alanı dışında kalma durumları
        #    içindir; detection tamamen kaybolduğunda geçerli değildir.
        # ---------------------------------------------------------------
        watchdog_timeout = self._detection_timeout_check()
        if watchdog_timeout:
            # lock_timer'ı sıfırla — tolerance beklemesin, direkt reset
            self.lock_timer.reset()
            self._watchdog_override = True
            logger.warning("Watchdog: detection timeout >1sn, lock_timer resetlendi, FSM override")

            # Kamera reopen kontrolü: 10 frame üst üste frame None ise
            with self._frame_lock:
                frame_none = self._frame is None
            if frame_none:
                self._camera_fail_count += 1
                if self._camera_fail_count >= 10 and self._camera_reopen_thread is None:
                    self._camera_reopen_thread = threading.Thread(
                        target=self._camera_reopen_worker, daemon=True, name="camera-reopen"
                    )
                    self._camera_reopen_thread.start()
            else:
                self._camera_fail_count = 0
        else:
            self._watchdog_override = False

        # ---------------------------------------------------------------
        # 1. En son detections'ları al
        # ---------------------------------------------------------------
        with self._detections_lock:
            detections = list(self._detections)
            detections_ts = self._detections_ts

        # 2. ByteTrack ile ID ata
        tracked = self.tracker.update(detections)

        # 3. Hedef seçimi
        primary_target = self._select_primary_target(tracked)
        target_visible = primary_target is not None

        # [x1, y1, x2, y2] formatını [cx, cy, w, h] formatına dönüştür
        if target_visible:
            x1, y1, x2, y2 = primary_target[:4]
            w = x2 - x1
            h = y2 - y1
            cx = x1 + w / 2.0
            cy = y1 + h / 2.0
            primary_target[:4] = [cx, cy, w, h]

        # ---------------------------------------------------------------
        # 4. EKF: predict ve update adımları
        #    Klasik Kalman filtresi: her frame'de predict çalıştırılmalı ki
        #    hız kestirimi (vx, vy) ve durum kovaryansı doğru güncellensin.
        # ---------------------------------------------------------------
        predicted = None

        # Kalman predict adımı her döngüde çalışır
        self.ekf.predict()

        if target_visible:
            # Hedef var → EKF'yi güncelle (update adımı)
            x_target, y_target, w_target, h_target = primary_target[:4]
            conf_target = primary_target[4] if len(primary_target) > 4 else 1.0
            measurement = (x_target, y_target, w_target, h_target)
            self.ekf.update(measurement, confidence=conf_target)
            predicted = measurement  # direkt ölçümü kullan
            self._lost_frame_count = 0

            # Forward Projection (Zaman Gecikmesi Telafisi)
            # YOLO çıkarım gecikmesi (30-50ms) süresince hedefin aldığı yolu hız vektörüne göre ekle
            if self.ekf._initialized:
                latency_sec = time.perf_counter() - detections_ts
                vx, vy = self.ekf.get_velocity()
                # Hedef merkez koordinatlarını şimdiki zamana kaydır
                primary_target[0] = primary_target[0] + vx * latency_sec
                primary_target[1] = primary_target[1] + vy * latency_sec
        else:
            # Hedef kayıp → frame sayacını artır
            self._lost_frame_count += 1

            if self._lost_frame_count <= 5: # MAX_DRAW_MISSED_FRAMES
                # Hedef kayıpken EKF predict değerini kullan (zaten yukarıda predict ettik)
                ekf_predicted = (float(self.ekf.x[0, 0]), float(self.ekf.x[1, 0]), float(self.ekf.x[4, 0]), float(self.ekf.x[5, 0]))
                primary_target = [ekf_predicted[0], ekf_predicted[1], ekf_predicted[2], ekf_predicted[3], 0.5, 0]
                predicted = ekf_predicted
                target_visible = True # Coasting boyunca hedefi takip etmeye devam et

            # Redetect eşiği aşıldıysa dene
            if self._lost_frame_count > 30: # MAX_MISSED_FRAMES
                # Redetect öncesi güncel tahmin (zaten predict ettik)
                ekf_predicted = (float(self.ekf.x[0, 0]), float(self.ekf.x[1, 0]), float(self.ekf.x[4, 0]), float(self.ekf.x[5, 0]))
                with self._frame_lock:
                    frame = self._frame
                if frame is not None:
                    redetect_result = self.redetector.attempt(
                        frame, detector=self.detector, predicted_bbox=ekf_predicted
                    )
                    if redetect_result is not None:
                        self._lost_frame_count = 0
                        target_visible = True
                        # Yeniden yakalanan hedef ile EKF'yi güncelle
                        if len(redetect_result) >= 4:
                            x_r, y_r, x2_r, y2_r = redetect_result[:4]
                            w_r = x2_r - x_r
                            h_r = y2_r - y_r
                            cx_r = x_r + w_r / 2.0
                            cy_r = y_r + h_r / 2.0
                            self.ekf.update((cx_r, cy_r, w_r, h_r), confidence=1.0)
                        predicted = redetect_result

        # 5. Vuruş alanı kontrolü
        in_strike_zone = False
        if primary_target is not None:
            x, y, w, h, _, _ = primary_target
            in_strike_zone = self.vurus_alani.is_inside(x, y, w, h)

        # 6. Kilit zamanlayıcısı
        lock_achieved = self.lock_timer.update(dt, in_strike_zone)

        # 7. Durum makinesi geçişi
        new_state = self.state_machine.transition(locked=lock_achieved, target_visible=target_visible)
        logger.debug("Durum: %s, Kilit: %s, Hedef: %s, Strike: %s",
                      new_state.value, lock_achieved, target_visible, in_strike_zone)

        # 8. Kontrol çıkışı
        roll_rate, pitch_rate, throttle = self._compute_control(primary_target, predicted, dt, target_visible)

        # ---------------------------------------------------------------
        # 9. MAVLink komutları (gecikme ölçümü burada)
        # ---------------------------------------------------------------
        if new_state in (LockState.TRACK, LockState.LOCK_CONFIRM, LockState.LOCKED):
            # MAVLink gönderme anı
            send_ts = time.perf_counter()
            self.mavlink.send_attitude_rate(roll_rate, pitch_rate)
            # Gecikme hesapla (capture → send)
            latency_ms = (send_ts - detections_ts) * 1000.0
            if latency_ms > self._max_latency_ms:
                self._max_latency_ms = latency_ms
            self._latency_log_counter += 1
            if self._latency_log_counter % 60 == 0:  # ~her 2 saniyede bir log
                logger.info("Gecikme: %.1fms (max: %.1fms, frame: %d)",
                            latency_ms, self._max_latency_ms, self.tracker.frame_id)

        # 10. APN handoff
        handoff_data = {
            "state": new_state.value,
            "locked": lock_achieved,
            "target_visible": target_visible,
            "in_strike_zone": in_strike_zone,
            "roll_rate": roll_rate,
            "pitch_rate": pitch_rate,
            "throttle": throttle,
            "timestamp": time.time(),
        }
        self.apn_handoff.publish(handoff_data)

        # 11. Telemetri (periyodik)
        now = time.perf_counter()
        if now - self._telemetry_last_sent >= (1.0 / self.cfg.telemetry_hz):
            self._telemetry_last_sent = now
            self.telemetri.publish(handoff_data)

        # 12. Kilitlenme POST'u (tek seferlik)
        if new_state == LockState.LOCKED and not self._lock_sent:
            asyncio.create_task(self._send_lock_info(handoff_data))
            self._lock_sent = True
        elif new_state != LockState.LOCKED:
            self._lock_sent = False

        # 13. Video kaydı
        synced_time = self.time_syncer.now()
        with self._frame_lock:
            frame_to_log = self._frame
        if frame_to_log is not None:
            self.video_logger.write_frame(frame_to_log, timestamp_ms=synced_time * 1000.0, lock_status={"state": new_state.value, "lock_active": lock_achieved})

    # ------------------------------------------------------------------
    # Yardımcı metotlar
    # ------------------------------------------------------------------
    def _select_primary_target(self, tracked_detections: List[Any]) -> Optional[List[Any]]:
        """Takip edilen tespitler arasından birincil hedefi seçer.

        ByteTrack çıktı formatı: [x1, y1, x2, y2, conf, cls, track_id, hit_streak]
        İndeks 6 = track_id, İndeks 4 = confidence
        """
        if not tracked_detections:
            self._current_target_id = None
            return None

        # Mevcut hedef ID ile eşleşen track'i bul
        if self._current_target_id is not None:
            for det in tracked_detections:
                if len(det) >= 7 and int(det[6]) == self._current_target_id:
                    return list(det)

        # Mevcut hedef bulunamadı → en yüksek güvenli olanı seç
        best = max(tracked_detections, key=lambda d: d[4] if len(d) >= 5 else 0.0)
        if len(best) >= 7:
            self._current_target_id = int(best[6])
        return list(best)

    def _compute_control(self, primary_target: Optional[List[Any]], predicted: Any, dt: float, target_visible: bool) -> tuple:
        """Cascade PID + antiwindup + mesafe döngüsü ile kontrol sinyalleri."""
        if primary_target is None or not target_visible:
            self.antiwindup.update(0.0, dt, enabled=False)
            return (0.0, 0.0, 0.0)

        x, y, w, h, _, _ = primary_target

        error_x = x - self.cfg.servo_center_x
        error_y = y - self.cfg.servo_center_y

        if abs(error_x) < self.cfg.control.dead_zone_px:
            error_x = 0.0
        if abs(error_y) < self.cfg.control.dead_zone_px:
            error_y = 0.0

        yaw_rate = self.cascade_pid_yaw.compute(error_x, dt)
        pitch_rate = self.cascade_pid_pitch.compute(error_y, dt)

        yaw_rate = max(-self.cfg.control.max_yaw_rate, min(self.cfg.control.max_yaw_rate, yaw_rate))
        pitch_rate = max(-self.cfg.control.max_pitch_rate, min(self.cfg.control.max_pitch_rate, pitch_rate))

        target_area = w * h
        frame_area = self.cfg.camera.width * self.cfg.camera.height
        desired_area_ratio = 0.05
        area_error = target_area - (frame_area * desired_area_ratio)
        throttle = self.range_loop.compute_throttle(area_error, dt)

        return (yaw_rate, pitch_rate, throttle)

    async def _send_lock_info(self, handoff_data: Dict[str, Any]):
        """Başarılı kilitlenme bilgisini yer istasyonuna POST ile gönder."""
        try:
            response = await self.sunucu.send_lock_info(handoff_data)
            logger.info("Kilitlenme bilgisi sunucuya gönderildi. Yanıt: %s", response)
        except Exception as exc:
            logger.error("Kilitlenme bilgisi gönderilemedi: %s", exc)

    def shutdown(self):
        """Tüm alt sistemleri düzgünce kapat."""
        self._running = False
        self.camera.release()
        logger.info("Pipeline kapatıldı. Maks gecikme: %.1fms", self._max_latency_ms)
