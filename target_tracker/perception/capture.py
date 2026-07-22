"""RingBuffer ile non-blocking kamera frame okuma."""

import cv2


class CameraCapture:
    def __init__(self, source=0, buffer_size=8):
        self.source = source
        self.buffer_size = buffer_size
        self.cap = cv2.VideoCapture(source)

    def read(self):
        ret, frame = self.cap.read()
        if not ret:
            return None
        return frame

    def release(self):
        self.cap.release()
