"""カメラ (目)。OpenCV で CPU だけで在席を判定し、必要なときだけ Gemma に「見せる」。

- 在席判定: 顔 / 人物 / 大きな動き のどれか → 「いる」
  1 秒に 1 枚・320px に縮小して処理するので i7-7700 で軽い
  使える検出器だけを使う: OpenCV 4 系は Haar (顔) と HOG (人物)。OpenCV 5 系ではこれらが
  本体から外れたので、YuNet の ONNX (camera_face_model) を指定すれば顔検出に使う。
  どの版でもフレーム差分による動き検出は常に使う
- 「いる」は即座に、「いない」は長く続いてから認める (attention.py の非対称性)
- 映像は保存しない。Gemma に渡すときもメモリ上の JPEG を base64 で送るだけで、
  残るのは Gemma が書いた説明文だけ

依存: opencv-python
"""

import base64
import sys
import threading
import time


class PresenceSensor:
    name = "camera"

    def __init__(self, cfg, clock=time.monotonic):
        import cv2
        self.cv2, self.cfg, self.clock = cv2, cfg, clock
        self.cap = None
        self._lock = threading.Lock()      # スイッチ (画面のスレッド) と読み取り (本体のループ) が取り合わないように
        self._open()
        self._init_detectors()
        self.present = False
        self.frame = None
        self._prev = None
        self._next = 0.0
        self._n = 0
        self._last_seen = 0.0

    def _open(self):
        cv2 = self.cv2
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.cfg["camera_index"], backend)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"カメラ {self.cfg['camera_index']} を開けません")
        self.cap = cap

    @property
    def paused(self):
        return self.cap is None

    def pause(self):
        """カメラを手放す (点灯が消える)。切っている間は 1 枚も取らない。"""
        with self._lock:
            if self.cap is not None:
                self.cap.release()
            self.cap = None
            self.frame = self._prev = None
            self.present = False

    def resume(self):
        with self._lock:
            if self.cap is None:
                self._open()
                self._next = 0.0

    def _init_detectors(self):
        cv2 = self.cv2
        self.face = self.hog = self.yunet = None
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
            self.face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if hasattr(cv2, "HOGDescriptor"):
            self.hog = cv2.HOGDescriptor()
            self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        model = self.cfg.get("camera_face_model")
        if model and hasattr(cv2, "FaceDetectorYN"):
            self.yunet = cv2.FaceDetectorYN.create(model, "", (320, 240))
        self.detectors = [n for n, d in (("haar", self.face), ("hog", self.hog), ("yunet", self.yunet)) if d] + ["motion"]

    def _detect(self, frame):
        cv2 = self.cv2
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (320, int(320 * h / w)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        found = False
        if self.face is not None:
            found = len(self.face.detectMultiScale(gray, 1.2, 5, minSize=(30, 30))) > 0
        if not found and self.yunet is not None:
            self.yunet.setInputSize((small.shape[1], small.shape[0]))
            _, faces = self.yunet.detect(small)
            found = faces is not None and len(faces) > 0
        self._n += 1
        if not found and self.hog is not None and self._n % 3 == 0:
            rects, _ = self.hog.detectMultiScale(small, winStride=(8, 8))
            found = len(rects) > 0
        if not found and self._prev is not None:
            diff = cv2.absdiff(gray, self._prev)
            found = (diff > 25).mean() > self.cfg["camera_motion_ratio"]
        self._prev = gray
        return found

    def poll(self):
        now = self.clock()
        if self.cap is None or now < self._next:
            return []
        self._next = now + self.cfg["camera_interval_s"]
        with self._lock:
            if self.cap is None:
                return []
            ok, frame = self.cap.read()
        if not ok:
            return []
        self.frame = frame
        if self._detect(frame):
            self._last_seen = now
            if not self.present:
                self.present = True
                return [("person_appeared", "カメラに人が映った", {"present": True})]
        elif self.present and now - self._last_seen > self.cfg["camera_absent_s"]:
            self.present = False
            return [("person_left", "カメラから人がいなくなった", {"present": False})]
        return []

    def snapshot_b64(self):
        if self.cap is None or self.frame is None:
            return None
        ok, buf = self.cv2.imencode(".jpg", self.frame, [self.cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None
