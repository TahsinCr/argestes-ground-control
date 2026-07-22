"""Görüntülü Takip - CLI Giriş Noktası.

Kullanım:
    python main.py --camera-source 0 --engine-path /path/to/model.engine
    python main.py --debug

Bu dosya sadece komut satırı arayüzüdür. Tüm iş mantığı `core/` paketindedir.
"""

import argparse
import logging
import multiprocessing
import signal
import sys
from pathlib import Path

from core.config import load_config
from core.pipeline import GoruntuluTakipPipeline

logger = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser(description="Görüntülü Takip Sistemi")
    parser.add_argument("--camera-source", type=int, default=0, help="Kamera indeksi veya RTSP/CSI yolu")
    parser.add_argument("--camera-config", default="config/camera.yaml", help="Kamera YAML konfigürasyon yolu")
    parser.add_argument("--control-config", default="config/control.yaml", help="Kontrol YAML konfigürasyon yolu")
    parser.add_argument("--model-config", default="config/model.yaml", help="Model YAML konfigürasyon yolu")
    parser.add_argument("--engine-path", default=None, help="TensorRT engine dosya yolu (YAML'ı geçersiz kılar)")
    parser.add_argument("--mavlink-conn", default=None, help="MAVLink bağlantı stringi (YAML'ı geçersiz kılar)")
    parser.add_argument("--sunucu-url", default=None, help="Yer istasyonu sunucu URL'si")
    parser.add_argument("--video-dir", default="kayitlar", help="Video kayıt dizini")
    parser.add_argument("--debug", action="store_true", help="Debug log seviyesini etkinleştir")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug modu etkin.")

    # Konfigürasyonu yükle
    config = load_config(
        camera_yaml=args.camera_config,
        control_yaml=args.control_config,
        model_yaml=args.model_config,
    )

    # CLI argümanları ile override
    config.camera_source = args.camera_source
    if args.engine_path:
        config.model.engine_path = args.engine_path
    if args.mavlink_conn:
        config.mavlink_connection = args.mavlink_conn
    if args.sunucu_url:
        config.sunucu_base_url = args.sunucu_url
    config.video_output_dir = args.video_dir

    # Kayıt dizinini oluştur
    Path(config.video_output_dir).mkdir(parents=True, exist_ok=True)

    # Pipeline'ı oluştur
    pipeline = GoruntuluTakipPipeline(config)

    # Sinyal yakalayıcıları kur
    def _signal_handler(signum, frame):
        logger.info("Sinyal alındı (%s), kapatılıyor...", signum)
        pipeline.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("=" * 60)
    logger.info("Görüntülü Takip Sistemi başlatılıyor...")
    logger.info("  Kamera: kaynak=%d, %dx%d", config.camera_source, config.camera.width, config.camera.height)
    logger.info("  Model: %s", config.model.engine_path)
    logger.info("  MAVLink: %s", config.mavlink_connection)
    logger.info("  Sunucu: %s", config.sunucu_base_url)
    logger.info("  Kayıt dizini: %s", config.video_output_dir)
    logger.info("=" * 60)

    try:
        import asyncio
        asyncio.run(pipeline.run())
    except KeyboardInterrupt:
        logger.info("Kullanıcı tarafından durduruldu.")
    except Exception as exc:
        logger.exception("Beklenmeyen hata: %s", exc)
        sys.exit(1)
    finally:
        pipeline.shutdown()
        logger.info("Görüntülü Takip Sistemi kapatıldı.")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()