"""Konfigürasyon veri yapıları ve YAML yükleme.

Sorumluluk:
- AppConfig, CameraConfig, ControlConfig, ModelConfig dataclass tanımları
- config/camera.yaml, config/control.yaml, config/model.yaml dosyalarından
  otomatik yükleme ve CLI override desteği
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Konfigürasyon veri yapıları
# ---------------------------------------------------------------------------
@dataclass
class CameraConfig:
    """Kamera intrinsik parametreleri (Raspberry Pi HQ + 16mm lens)."""
    model: str = "Raspberry Pi HQ"
    lens: str = "16mm"
    focal_length_mm: float = 16.0
    width: int = 1920
    height: int = 1080
    fx: float = 1380.0
    fy: float = 1380.0
    cx: float = 960.0
    cy: float = 540.0
    k1: float = -0.25
    k2: float = 0.12
    p1: float = 0.0
    p2: float = 0.0


@dataclass
class ControlConfig:
    """Cascade PID döngü kazançları (1718mm kanat / 3.3kg ağırlık)."""
    outer_kp: float = 0.08
    outer_ki: float = 0.004
    outer_kd: float = 0.002
    inner_kp: float = 0.15
    inner_ki: float = 0.007
    inner_kd: float = 0.003
    max_yaw_rate: float = 45.0       # derece/saniye
    max_pitch_rate: float = 30.0
    dead_zone_px: float = 5.0        # merkez etrafında ölü bölge (piksel)


@dataclass
class ModelConfig:
    """YOLO26 model yolu ve isteğe bağlı Jetson DLA ayarları."""
    engine_path: str = "data/models/target_lock/yolo26s.pt"
    input_shape: List[int] = field(default_factory=lambda: [640, 640])
    confidence_threshold: float = 0.3
    iou_threshold: float = 0.45
    use_dla: bool = False
    dla_core: int = 0


@dataclass
class AppConfig:
    """Tüm uygulama konfigürasyonunu birleştiren ana config."""
    camera: CameraConfig = field(default_factory=CameraConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    camera_source: int = 0
    camera_buffer_size: int = 8
    lock_duration_s: float = 4.0
    lock_tolerance_s: float = 1.0
    lock_strike_horizontal_pct: float = 0.25
    lock_strike_vertical_pct: float = 0.10
    lock_strike_size_pct: float = 0.05
    lost_frame_limit: int = 30
    telemetry_hz: float = 2.0
    mavlink_connection: str = "udp:127.0.0.1:14550"
    sunucu_base_url: str = "http://127.0.0.1:8000"
    apn_shm_name: str = "goruntulu_takip_shm"
    video_output_dir: str = "kayitlar"
    ntp_server: str = "pool.ntp.org"
    servo_center_x: int = 960
    servo_center_y: int = 540
    # COCO airplane sinifi. Yarismaya ozel model kullanilirsa degistirilebilir.
    target_class_ids: List[int] = field(default_factory=lambda: [4])


# ---------------------------------------------------------------------------
# YAMl yükleme
# ---------------------------------------------------------------------------
def _load_yaml(path: str) -> Dict:
    """Bir YAML dosyasını okuyup dict olarak döndürür."""
    p = Path(path)
    if not p.exists():
        logger.warning("Konfigürasyon dosyası bulunamadı: %s, varsayılanlar kullanılacak.", path)
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(
    camera_yaml: str = "config/camera.yaml",
    control_yaml: str = "config/control.yaml",
    model_yaml: str = "config/model.yaml",
) -> AppConfig:
    """Tüm YAML konfigürasyonlarını okuyup AppConfig nesnesi oluşturur.

    Her bir dosya opsiyoneldir; bulunamayan dosyalar için varsayılan değerler
    kullanılır. Komut satırı argümanları bu fonksiyon dönüşünden sonra
    override edilmelidir.
    """
    cfg = AppConfig()

    cam_raw = _load_yaml(camera_yaml).get("camera", {})
    if cam_raw:
        cfg.camera = CameraConfig(
            model=cam_raw.get("model", cfg.camera.model),
            lens=cam_raw.get("lens", cfg.camera.lens),
            focal_length_mm=cam_raw.get("focal_length_mm", cfg.camera.focal_length_mm),
            width=cam_raw.get("resolution", {}).get("width", cfg.camera.width),
            height=cam_raw.get("resolution", {}).get("height", cfg.camera.height),
            fx=cam_raw.get("intrinsics", {}).get("fx", cfg.camera.fx),
            fy=cam_raw.get("intrinsics", {}).get("fy", cfg.camera.fy),
            cx=cam_raw.get("intrinsics", {}).get("cx", cfg.camera.cx),
            cy=cam_raw.get("intrinsics", {}).get("cy", cfg.camera.cy),
            k1=cam_raw.get("distortion", {}).get("k1", cfg.camera.k1),
            k2=cam_raw.get("distortion", {}).get("k2", cfg.camera.k2),
            p1=cam_raw.get("distortion", {}).get("p1", cfg.camera.p1),
            p2=cam_raw.get("distortion", {}).get("p2", cfg.camera.p2),
        )
        cfg.servo_center_x = cfg.camera.width // 2
        cfg.servo_center_y = cfg.camera.height // 2

    ctrl_raw = _load_yaml(control_yaml).get("control", {})
    if ctrl_raw:
        outer = ctrl_raw.get("outer_loop", {})
        inner = ctrl_raw.get("inner_loop", {})
        cfg.control = ControlConfig(
            outer_kp=outer.get("kp", cfg.control.outer_kp),
            outer_ki=outer.get("ki", cfg.control.outer_ki),
            outer_kd=outer.get("kd", cfg.control.outer_kd),
            inner_kp=inner.get("kp", cfg.control.inner_kp),
            inner_ki=inner.get("ki", cfg.control.inner_ki),
            inner_kd=inner.get("kd", cfg.control.inner_kd),
        )

    mod_raw = _load_yaml(model_yaml).get("model", {})
    if mod_raw:
        cfg.model = ModelConfig(
            engine_path=mod_raw.get("engine_path", cfg.model.engine_path),
            input_shape=mod_raw.get("input_shape", cfg.model.input_shape),
            confidence_threshold=mod_raw.get("confidence_threshold", cfg.model.confidence_threshold),
            iou_threshold=mod_raw.get("iou_threshold", cfg.model.iou_threshold),
            use_dla=mod_raw.get("use_dla", cfg.model.use_dla),
            dla_core=mod_raw.get("dla_core", cfg.model.dla_core),
        )

    logger.info("Konfigürasyon yüklendi: %dx%d, motor=%s", cfg.camera.width, cfg.camera.height, cfg.model.engine_path)
    return cfg
