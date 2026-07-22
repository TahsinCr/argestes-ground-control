"""Ultralytics PT ve ONNX Runtime hedef tespit adaptörü."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List

import numpy as np


logger = logging.getLogger(__name__)


class YoloInfer:
    """YOLO26 ağırlığını lazy-load ederek ortak detection biçimi üretir."""

    def __init__(
        self,
        engine_path: str,
        input_shape: tuple[int, int] = (640, 640),
        conf_threshold: float = 0.30,
        device: str | int = "auto",
    ) -> None:
        self.engine_path = str(engine_path)
        self.input_shape = tuple(int(value) for value in input_shape)
        self.conf_threshold = float(conf_threshold)
        self.device = device
        self._model: Any | None = None
        self._onnx_input_name = ""
        self._backend = ""
        self._load_attempted = False
        self.last_error = ""

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def backend(self) -> str:
        return self._backend

    def _load_model(self) -> None:
        if self._model is not None or self._load_attempted:
            return
        self._load_attempted = True
        path = Path(self.engine_path)
        try:
            if path.suffix.lower() == ".onnx":
                self._load_onnx(path)
                return
            try:
                from ultralytics import YOLO
            except ImportError:
                fallback = path.with_suffix(".onnx")
                if fallback.is_file():
                    logger.info(
                        "Ultralytics bulunamadı; PT'den türetilmiş ONNX kullanılıyor: %s",
                        fallback,
                    )
                    self._load_onnx(fallback)
                    return
                raise

            model = YOLO(str(path))
            selected_device = self._resolve_device()
            dummy = np.zeros((*self.input_shape[::-1], 3), dtype=np.uint8)
            _ = model.predict(
                dummy,
                verbose=False,
                imgsz=self.input_shape[0],
                conf=self.conf_threshold,
                device=selected_device,
            )
            self._model = model
            self._backend = "ultralytics-pt"
            logger.info("YOLO model hazır: %s (%s)", path, selected_device)
        except Exception as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            logger.error("Model yüklenemedi (%s): %s", path, self.last_error)

    def _load_onnx(self, path: Path) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "YOLO26 ONNX modeli için onnxruntime kurulu olmalı"
            ) from exc

        if not path.is_file():
            raise FileNotFoundError(f"Model dosyası bulunamadı: {path}")
        available = set(ort.get_available_providers())
        providers = [
            provider
            for provider in (
                "TensorrtExecutionProvider",
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            )
            if provider in available
        ]
        self._model = ort.InferenceSession(str(path), providers=providers)
        self._onnx_input_name = self._model.get_inputs()[0].name
        self._backend = "onnxruntime"
        self.last_error = ""
        logger.info("YOLO ONNX model hazır: %s (%s)", path, providers[0])

    def _resolve_device(self) -> str | int:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return 0 if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def infer(self, frame: Any) -> List[list[float | int]]:
        self._load_model()
        if self._model is None or frame is None:
            return []
        try:
            if self._backend == "onnxruntime":
                return self._infer_onnx(frame)
            return self._infer_ultralytics(frame)
        except Exception as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            logger.error("Inference hatası: %s", self.last_error)
            return []

    def _infer_ultralytics(self, frame: Any) -> List[list[float | int]]:
        results = self._model.predict(
            frame,
            verbose=False,
            imgsz=self.input_shape[0],
            conf=self.conf_threshold,
            device=self._resolve_device(),
        )
        detections: List[list[float | int]] = []
        for result in results:
            if result.boxes is None:
                continue
            boxes = result.boxes.xyxy.cpu().numpy()
            confidences = result.boxes.conf.cpu().numpy()
            class_ids = result.boxes.cls.cpu().numpy().astype(int)
            for box, confidence, class_id in zip(
                boxes,
                confidences,
                class_ids,
                strict=True,
            ):
                detections.append(
                    [
                        float(box[0]),
                        float(box[1]),
                        float(box[2]),
                        float(box[3]),
                        float(confidence),
                        int(class_id),
                    ]
                )
        return detections

    def _infer_onnx(self, frame: Any) -> List[list[float | int]]:
        import cv2

        frame_height, frame_width = frame.shape[:2]
        input_width, input_height = self.input_shape
        scale = min(
            input_width / float(frame_width),
            input_height / float(frame_height),
        )
        resized_width = max(1, round(frame_width * scale))
        resized_height = max(1, round(frame_height * scale))
        resized = cv2.resize(
            frame,
            (resized_width, resized_height),
            interpolation=cv2.INTER_LINEAR,
        )
        pad_width = input_width - resized_width
        pad_height = input_height - resized_height
        left = round(pad_width / 2.0 - 0.1)
        right = round(pad_width / 2.0 + 0.1)
        top = round(pad_height / 2.0 - 0.1)
        bottom = round(pad_height / 2.0 + 0.1)
        letterboxed = cv2.copyMakeBorder(
            resized,
            top,
            bottom,
            left,
            right,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        blob = cv2.dnn.blobFromImage(
            letterboxed,
            scalefactor=1.0 / 255.0,
            size=(input_width, input_height),
            swapRB=True,
            crop=False,
        )
        output = np.asarray(
            self._model.run(None, {self._onnx_input_name: blob})[0]
        )
        rows = output.reshape(-1, output.shape[-1])
        if rows.shape[1] != 6:
            raise RuntimeError(f"Beklenmeyen YOLO26 ONNX çıktısı: {output.shape}")
        detections: List[list[float | int]] = []
        for x1, y1, x2, y2, confidence, class_id in rows:
            if float(confidence) < self.conf_threshold:
                continue
            source_x1 = max(0.0, min(frame_width, (float(x1) - left) / scale))
            source_y1 = max(0.0, min(frame_height, (float(y1) - top) / scale))
            source_x2 = max(0.0, min(frame_width, (float(x2) - left) / scale))
            source_y2 = max(0.0, min(frame_height, (float(y2) - top) / scale))
            if source_x2 <= source_x1 or source_y2 <= source_y1:
                continue
            detections.append(
                [
                    source_x1,
                    source_y1,
                    source_x2,
                    source_y2,
                    float(confidence),
                    int(class_id),
                ]
            )
        self.last_error = ""
        return detections

    def __repr__(self) -> str:
        return f"YoloInfer(model={self.engine_path}, input={self.input_shape})"
