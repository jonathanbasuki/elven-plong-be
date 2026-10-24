"""YOLO person detector. Ported from plong-model/counter.py."""

import numpy as np
from ultralytics import YOLO


class Detector:
    def __init__(
        self, model: str = "yolo26n.pt", imgsz: int = 640, device: str | None = None
    ):
        self.model = YOLO(model)
        self.person = next(
            k for k, v in self.model.names.items() if v == "person"
        )  # don't hardcode 0
        self.imgsz, self.device = imgsz, device

    def __call__(
        self, frame: np.ndarray, conf: float
    ) -> tuple[np.ndarray, np.ndarray, float]:
        """Returns (boxes Nx4 xyxy pixels, confidences N, inference ms)."""
        r = self.model.predict(
            frame, imgsz=self.imgsz, conf=conf, device=self.device, verbose=False
        )[0]
        keep = r.boxes.cls.cpu().numpy().astype(int) == self.person
        return (
            r.boxes.xyxy.cpu().numpy()[keep],
            r.boxes.conf.cpu().numpy()[keep],
            r.speed["inference"],
        )
