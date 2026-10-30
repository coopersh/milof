"""Write bird images + a daily CSV log on a background thread.

The capture thread only calls `submit()`, which never blocks: if the queue is full the
image is dropped and counted. Orientation, JPEG encoding and disk I/O all happen here.
"""
from __future__ import annotations

import csv
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .config import StorageConfig
from .segmenter import BirdWindow

log = logging.getLogger(__name__)

LOG_NAME = "capture_log.csv"
LOG_FIELDS = ["timestamp", "seq", "kind", "start_line", "n_lines", "contrast",
              "error_lines", "period", "file"]


def orient(lines: np.ndarray, cfg: StorageConfig) -> np.ndarray:
    """Stream layout (time, sensor) → saved-image layout per the storage config."""
    img = lines.transpose(1, 0, 2) if cfg.rotate else lines
    if cfg.flip_vertical:
        img = img[::-1]
    if cfg.flip_horizontal:
        img = img[:, ::-1]
    return np.ascontiguousarray(img)


class AsyncWriter:
    def __init__(self, cfg: StorageConfig,
                 on_written: Callable[[BirdWindow, Path, np.ndarray], None] | None = None):
        """`on_written(window, path, oriented_image)` runs on the writer thread after each save."""
        self.cfg = cfg
        self._q: queue.Queue[BirdWindow | None] = queue.Queue(maxsize=cfg.queue_size)
        self._thread = threading.Thread(target=self._run, name="writer", daemon=True)
        self._on_written = on_written
        self.written = 0
        self.dropped = 0
        self.failed = 0

    def start(self) -> "AsyncWriter":
        self._thread.start()
        return self

    def submit(self, win: BirdWindow) -> bool:
        try:
            self._q.put_nowait(win)
            return True
        except queue.Full:
            self.dropped += 1
            log.warning("writer queue full, dropped %s %d", win.kind, win.seq)
            return False

    def stop(self) -> None:
        self._q.put(None)  # drains what is queued, then exits
        self._thread.join()

    def orient(self, lines: np.ndarray) -> np.ndarray:
        return orient(lines, self.cfg)

    def _run(self) -> None:
        while (win := self._q.get()) is not None:
            try:
                path, img = self._write(win)
                self.written += 1
            except Exception:  # noqa: BLE001 — never kill the writer thread
                self.failed += 1
                log.exception("failed to write %s %d", win.kind, win.seq)
                continue
            if self._on_written is not None:
                try:
                    self._on_written(win, path, img)
                except Exception:  # noqa: BLE001 — a listener bug must not stop saving
                    log.exception("on_written callback failed for %s %d", win.kind, win.seq)

    def _write(self, win: BirdWindow) -> tuple[Path, np.ndarray]:
        ts = datetime.fromtimestamp(win.timestamp)
        day_dir = self.cfg.data_dir / ts.strftime("%Y%m%d")
        img_dir = day_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        name = f"{win.kind}_{ts.strftime('%H%M%S_%f')[:-3]}_{win.seq:07d}.{self.cfg.image_format}"
        params = []
        if self.cfg.image_format.lower() in ("jpg", "jpeg"):
            params = [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality]
        img = self.orient(win.lines)
        if not cv2.imwrite(str(img_dir / name), img, params):
            raise OSError(f"cv2.imwrite failed for {name}")
        self._log(day_dir / LOG_NAME, {
            "timestamp": ts.isoformat(timespec="milliseconds"),
            "seq": win.seq,
            "kind": win.kind,
            "start_line": win.start_line,
            "n_lines": win.lines.shape[0],
            "contrast": f"{win.contrast:.2f}",
            "error_lines": f"{win.error_lines:.1f}",
            "period": f"{win.period:.1f}",
            "file": f"images/{name}",
        })
        return img_dir / name, img

    @staticmethod
    def _log(path: Path, row: dict) -> None:
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
