"""Camera service behind the UI.

One thread ("camera") owns the camera: it grabs, keeps a rolling preview and, while
capturing, feeds the segmenter and the async writer. Everything that touches the camera
(settings, PRNU, user sets) is queued to that thread with `call()`, so pypylon is only
used from one thread and an HTTP request can never stall the grab loop. Preview images
and statistics are computed on the request threads from copies.
"""
from __future__ import annotations

import concurrent.futures
import logging
import queue
import shutil
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from acquisition.camera import BaslerLineScan, Block
from acquisition.config import Config, SegmentationConfig, load_config, save_values
from acquisition.segmenter import BirdWindow, make_segmenter
from acquisition.writer import AsyncWriter, orient

from . import calibration
from .simcam import SCENES, SimCamera

log = logging.getLogger(__name__)

PREVIEW_LINES = 4096  # rolling preview, ~1.4 s at 3 kHz
PREVIEW_STEP = 4  # the preview keeps every 4th sensor pixel
RECENT = 12  # thumbnails of the last saved images
BOARD_LINES = 2048  # lines in a saved calibration-board image
STALL_S = 3.0  # no good block for this long while grabbing = stream stuck; restart it

# config field → (camera node, type, may change during a capture run)
CAMERA_FIELDS: dict[str, tuple[str, type, bool]] = {
    "exposure_us": ("ExposureTime", float, True),
    "line_rate_hz": ("AcquisitionLineRate", float, False),
    "lines_per_frame": ("Height", int, False),
    "packet_size_bytes": ("GevSCPSPacketSize", int, False),
    "reverse_x": ("ReverseX", bool, False),
}
LIVE_NODES = {"ExposureTime", "AcquisitionLineRate"}  # settable while grabbing
SEGMENTATION_TYPES: dict[str, type] = {
    "method": str, "birds_per_min": float, "period_lines": float, "window_factor": float,
    "track_gain": float, "min_contrast": float, "profile_sign": int, "tile_lines": int,
}
STORAGE_TYPES: dict[str, type] = {
    "jpeg_quality": int, "rotate": bool, "flip_vertical": bool, "flip_horizontal": bool,
}
RANGES = {
    "exposure_us": (1, 1_000_000), "line_rate_hz": (100, 14_000), "lines_per_frame": (16, 8192),
    "packet_size_bytes": (576, 9000), "birds_per_min": (1, 400), "period_lines": (10, 1_000_000),
    "window_factor": (1.0, 3.0), "track_gain": (0.0, 1.0), "min_contrast": (0.0, 255.0),
    "tile_lines": (16, 100_000), "jpeg_quality": (1, 100),
}
CHOICES = {"method": ("periodic", "tiles"), "profile_sign": (1, -1)}
RECORD_KINDS = ("focus", "flat", "exposure", "shift_check")


class UserError(ValueError):
    """A request the operator can fix; the message is shown in the UI as-is."""


@dataclass
class CaptureRun:
    mode: str
    started: float
    writer: AsyncWriter
    cut: int = 0


def _coerce(name: str, value: Any, typ: type) -> Any:
    if name == "period_lines" and value in (None, "", "null"):
        return None
    try:
        if typ is bool:
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("true", "1", "on", "yes"):
                return True
            if text in ("false", "0", "off", "no"):
                return False
            raise ValueError(value)
        if typ is int:
            f = float(value)
            if f != int(f):
                raise ValueError(value)
            v = int(f)
        else:
            v = typ(value)
    except (TypeError, ValueError):
        raise UserError(f"invalid value for {name}: {value!r}") from None
    if name in RANGES:
        lo, hi = RANGES[name]
        if not lo <= v <= hi:
            raise UserError(f"{name} must be between {lo} and {hi} (got {v})")
    if name in CHOICES and v not in CHOICES[name]:
        raise UserError(f"{name} must be one of: {', '.join(map(str, CHOICES[name]))}")
    return v


def _writable_dir(value: Any) -> Path:
    """The folder for captured data: created if missing, and checked writable now rather
    than at the first bird of a capture run."""
    text = str(value or "").strip().strip('"')
    if not text:
        raise UserError("data folder cannot be empty")
    path = Path(text).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise UserError(f"cannot write to {path}: {e.strerror or e}") from None
    return path


def _existing_ancestor(path: Path) -> Path:
    path = path.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


class CameraService:
    def __init__(self, cfg: Config, config_path: Path, sim: bool = False, realtime: bool = True):
        self.cfg = cfg
        self.config_path = Path(config_path)
        self.sim = sim
        nominal = cfg.segmentation.nominal_period(cfg.camera.line_rate_hz)
        self.cam = SimCamera(cfg.camera, nominal, realtime) if sim else BaslerLineScan(cfg.camera)
        self._set_calib_dir()
        self.nodes: calibration.Nodes | None = None
        self.device = ""
        self.error: str | None = None
        self.connected = False
        self.grabbing = False
        self.blocks = 0
        self.lost_frames = 0
        self.timeouts = 0
        self.stream_restarts = 0
        self.stall_s = STALL_S
        self.last_capture: dict | None = None
        self._last_good = time.monotonic()  # last good block, or when grabbing (re)started
        self._stalls = 0  # recoveries since the last good block

        self._cmds: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._lock = threading.Lock()  # guards the preview ring and _last_lines
        self._ring: np.ndarray | None = None
        self._ring_pos = 0
        self._ring_filled = 0
        self._ring_end_line = 0
        self._last_lines: np.ndarray | None = None
        self._arrivals: deque[tuple[float, int]] = deque(maxlen=64)
        self._seg = None
        self._seg_origin: int | None = None
        self._show_cuts = False
        self._cuts: deque[dict] = deque(maxlen=64)
        self._capture: CaptureRun | None = None
        self._recent: deque[dict] = deque(maxlen=RECENT)
        self._snap: list | None = None  # [lines still needed, parts, future]
        self._file_cfg: tuple[float, Config] | None = None  # (mtime, config as on file)

    # --- lifecycle ---

    def start(self) -> "CameraService":
        self._thread.start()
        return self

    def shutdown(self) -> None:
        if self._capture is not None:
            self.stop_capture()
        self._stop.set()
        self._thread.join(timeout=5)

    def call(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        """Run `fn` on the camera thread and return its result."""
        if threading.current_thread() is self._thread:
            return fn()
        if timeout is None:
            timeout = 10.0 + self.cam.timeout_ms() / 1000.0
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._cmds.put((fn, fut))
        return fut.result(timeout)

    def _run(self) -> None:
        self._connect()
        while not self._stop.is_set():
            busy = time.monotonic()
            self._drain()
            self._last_good += time.monotonic() - busy  # a long command is not a stall
            self.grabbing = self.cam.grabbing
            if not self.grabbing:
                self._last_good = time.monotonic()
                time.sleep(0.05)
                continue
            if time.monotonic() - self._last_good > self.stall_s:
                self._recover()
                continue
            try:
                block = self.cam.retrieve(self.cam.timeout_ms())
            except TimeoutError:
                self.timeouts += 1
                continue
            except Exception as e:  # noqa: BLE001 — keep the loop alive; operator can reconnect
                self.error = f"grab error: {e}"
                log.exception("grab failed")
                time.sleep(0.5)
                continue
            if block is not None:
                self._on_block(block)
        try:
            self.cam.close()
        except Exception:  # noqa: BLE001
            log.exception("closing the camera failed")

    def _recover(self) -> None:
        """The camera can stay "grabbing" yet deliver nothing after a burst of network
        errors (seen at link-up on the USB NIC). Restart the grab; if that did not help,
        reopen the camera. Lost time shows up as a jump in line index, not a silent stop."""
        self._stalls += 1
        self.stream_restarts += 1
        idle = time.monotonic() - self._last_good
        try:
            if self._stalls == 1:
                log.warning("no image for %.1f s — restarting the grab", idle)
                self.cam.stop()
                self.cam.start()
            else:
                log.warning("still no image after a grab restart — reopening the camera")
                try:
                    self.cam.close()
                except Exception:  # noqa: BLE001
                    log.exception("close before reopen failed")
                self._connect()
        except Exception:  # noqa: BLE001 — leave it to the next stall check / the operator
            log.exception("stream recovery failed")
        self._last_good = time.monotonic()

    def _drain(self) -> None:
        while True:
            try:
                fn, fut = self._cmds.get_nowait()
            except queue.Empty:
                return
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001 — hand every failure to the caller
                fut.set_exception(e)

    def _connect(self) -> None:
        try:
            self.cam.open()
            self.cam.start()
        except Exception as e:  # noqa: BLE001
            self.connected = False
            self.error = f"no camera connection: {e}"
            log.warning("camera not available: %s", e)
            return
        self.connected = True
        self.error = None
        if self.sim:
            self.device = "simulator"
        else:
            parts = []
            for node in ("DeviceModelName", "DeviceSerialNumber"):
                try:
                    parts.append(str(self.cam.get(node)))
                except Exception:  # noqa: BLE001
                    pass
            self.device = " ".join(parts) or self.cfg.camera.model
        self._last_good = time.monotonic()
        try:
            self.nodes = calibration.discover(self.cam)
        except Exception:  # noqa: BLE001 — the wizard reports missing nodes
            log.exception("node discovery failed")
            self.nodes = None

    def reconnect(self) -> None:
        def op():
            if self._capture is not None:
                raise UserError("stop capture before reconnecting")
            try:
                self.cam.close()
            except Exception:  # noqa: BLE001
                log.exception("close before reconnect failed")
            self._connect()
        self.call(op)

    # --- per block (camera thread) ---

    def _on_block(self, block: Block) -> None:
        lines = block.lines
        self.blocks += 1
        self._last_good = time.monotonic()
        self._stalls = 0
        self.lost_frames += block.lost_frames
        self._arrivals.append((time.monotonic(), lines.shape[0]))
        small = lines[:, ::PREVIEW_STEP]
        with self._lock:
            if self._ring is None or self._ring.shape[1:] != small.shape[1:]:
                self._ring = np.zeros((PREVIEW_LINES,) + small.shape[1:], np.uint8)
                self._ring_pos = self._ring_filled = 0
            n = min(len(small), PREVIEW_LINES)
            src = small[-n:]
            first = min(n, PREVIEW_LINES - self._ring_pos)
            self._ring[self._ring_pos:self._ring_pos + first] = src[:first]
            self._ring[:n - first] = src[first:]
            self._ring_pos = (self._ring_pos + n) % PREVIEW_LINES
            self._ring_filled = min(PREVIEW_LINES, self._ring_filled + n)
            self._ring_end_line = block.start_line + lines.shape[0]
            self._last_lines = lines

        if self._snap is not None:
            self._snap[1].append(lines)
            self._snap[0] -= lines.shape[0]
            if self._snap[0] <= 0:
                fut = self._snap[2]
                parts = self._snap[1]
                self._snap = None
                fut.set_result(parts)

        if self._seg is not None:
            if self._seg_origin is None:
                self._seg_origin = block.start_line
            rebased = Block(block.start_line - self._seg_origin, lines, block.lost_frames)
            for win in self._seg.push(rebased):
                start = win.start_line + self._seg_origin
                self._cuts.append({
                    "seq": win.seq, "kind": win.kind, "start": start,
                    "end": start + win.lines.shape[0], "contrast": round(win.contrast, 2),
                    "error": round(win.error_lines, 1), "period": round(win.period, 1),
                })
                if self._capture is not None:
                    self._capture.cut += 1
                    self._capture.writer.submit(win)

    def _on_written(self, win: BirdWindow, path: Path, img: np.ndarray) -> None:
        """Writer thread: keep a small thumbnail of each saved image for the UI."""
        h = 260
        w = max(1, int(round(img.shape[1] * h / img.shape[0])))
        thumb = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self._recent.append({
                "seq": win.seq, "kind": win.kind,
                "time": datetime.fromtimestamp(win.timestamp).strftime("%H:%M:%S"),
                "contrast": round(win.contrast, 2), "error": round(win.error_lines, 1),
                "file": str(path), "jpeg": buf.tobytes(),
            })

    # --- preview and measurements (request threads) ---

    def _strip(self) -> tuple[np.ndarray, int] | None:
        """Copy of the preview ring in time order (oldest first), zero-padded; and its end line."""
        with self._lock:
            if self._ring is None or self._ring_filled == 0:
                return None
            if self._ring_filled < PREVIEW_LINES:
                strip = np.zeros_like(self._ring)
                strip[-self._ring_filled:] = self._ring[:self._ring_filled]
            else:
                strip = np.concatenate((self._ring[self._ring_pos:], self._ring[:self._ring_pos]))
            return strip, self._ring_end_line

    def preview_jpeg(self, width: int = 900) -> bytes | None:
        got = self._strip()
        if got is None:
            return None
        strip, end_line = got
        if self._seg is not None:
            first_line = end_line - len(strip)
            band = max(2, len(strip) // 400)
            for c in list(self._cuts):
                centre = (c["start"] + c["end"]) // 2 - first_line
                if 0 <= centre < len(strip):
                    strip[max(0, centre - band):centre + band] = (0, 210, 255)
        # Output keeps the saved images' layout (orient) with square pixels: the preview
        # dropped every PREVIEW_STEP-th sensor pixel, so scale the sensor axis back up.
        rotate = self.cfg.storage.rotate
        sensor_px = strip.shape[1] * PREVIEW_STEP
        ratio = sensor_px / len(strip) if rotate else len(strip) / sensor_px
        width = int(np.clip(width, 200, 2000))
        height = int(np.clip(width * ratio, 50, 2000))
        # resize before orienting: transposing the small image is much cheaper
        small = cv2.resize(strip, (height, width) if rotate else (width, height),
                           interpolation=cv2.INTER_AREA)
        img = orient(small, self.cfg.storage)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def stats(self) -> dict | None:
        with self._lock:
            if self._ring is None or self._ring_filled == 0:
                return None
            sample = self._ring[:self._ring_filled:8, ::2].copy()  # ~0.25 Mpx is plenty for stats
            last = self._last_lines
        gray = sample.mean(axis=2, dtype=np.float32)
        hist = np.bincount(gray.astype(np.uint8).ravel(), minlength=256).reshape(64, 4).sum(axis=1)
        out: dict[str, Any] = {
            "mean": round(float(gray.mean()), 1),
            "min": int(sample.min()),
            "max": int(sample.max()),
            "rgb_mean": [round(float(x), 1) for x in sample.reshape(-1, 3).mean(axis=0)[::-1]],
            "clip_hi": round(float((sample == 255).any(axis=2).mean() * 100), 2),
            "clip_lo": round(float((sample == 0).all(axis=2).mean() * 100), 2),
            "hist": (hist / max(1, hist.max())).round(4).tolist(),
        }
        if last is not None:
            prof = last[::4].mean(axis=0, dtype=np.float32)  # (sensor, 3) BGR, over lines
            g = prof.mean(axis=1)
            k = 32
            smooth = np.convolve(g, np.ones(k) / k, mode="valid")
            core = smooth[int(len(smooth) * 0.05):int(len(smooth) * 0.95)]
            spread = float(core.max() - core.min())
            out["flat"] = {
                "mean": round(float(core.mean()), 1),
                "spread": round(spread, 1),
                "spread_pct": round(spread / max(float(core.mean()), 1.0) * 100, 1),
            }
            out["sharpness"] = round(float(np.abs(np.diff(g)).mean() / max(g.mean(), 1.0) * 100), 2)
            bins = 512
            step = max(1, len(g) // bins)
            p = prof[:step * (len(g) // step)].reshape(-1, step, 3).mean(axis=1)
            out["profile"] = {"r": p[:, 2].round(1).tolist(), "g": p[:, 1].round(1).tolist(),
                              "b": p[:, 0].round(1).tolist(), "sensor_px": len(g)}
        return out

    def _measured_rate(self) -> float | None:
        arr = list(self._arrivals)
        if len(arr) < 4:
            return None
        span = arr[-1][0] - arr[0][0]
        return round(sum(n for _, n in arr[1:]) / span, 1) if span > 0 else None

    def _rate_warning(self) -> str | None:
        """The camera silently slows the line rate to fit the exposure; say so."""
        c = self.cfg.camera
        if c.exposure_us is None:
            return None
        max_rate = 1e6 / c.exposure_us
        if max_rate >= c.line_rate_hz:
            return None
        return (f"Exposure {c.exposure_us:.0f} µs limits the camera to {max_rate:.0f} lines/s "
                f"instead of {c.line_rate_hz:.0f}, so images are squashed along the motion. "
                f"Lower exposure to at most {1e6 / c.line_rate_hz:.0f} µs (add light / open the "
                f"aperture), or lower the line rate.")

    def status(self) -> dict:
        cap = self._capture
        disk = shutil.disk_usage(_existing_ancestor(self.cfg.storage.data_dir))
        return {
            "source": "sim" if self.sim else "camera",
            "device": self.device,
            "connected": self.connected,
            "grabbing": self.grabbing,
            "error": self.error,
            "warning": self._rate_warning(),
            "unsaved": self.unsaved_settings(),
            "blocks": self.blocks,
            "lost_frames": self.lost_frames,
            "timeouts": self.timeouts,
            "stream_restarts": self.stream_restarts,
            "line_rate_measured": self._measured_rate(),
            "show_cuts": self._show_cuts,
            "last_cut": self._cuts[-1] if self._cuts else None,
            "capture": None if cap is None else {
                "mode": cap.mode,
                "seconds": round(time.time() - cap.started),
                "cut": cap.cut,
                "written": cap.writer.written,
                "dropped": cap.writer.dropped,
                "failed": cap.writer.failed,
            },
            "last_capture": self.last_capture,
            "data_dir": str(self.cfg.storage.data_dir.resolve()),
            "disk_free_gb": round(disk.free / 1e9, 1),
            "sim_scene": self.cam.scene if self.sim else None,
            "sim_scenes": list(SCENES) if self.sim else None,
        }

    def recent(self) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "jpeg"} for r in list(self._recent)]

    def recent_jpeg(self, seq: int) -> bytes | None:
        for r in list(self._recent):
            if r["seq"] == seq:
                return r["jpeg"]
        return None

    # --- settings ---

    def settings(self) -> dict:
        def read() -> dict:
            out = {}
            for name, (node, typ, during_capture) in CAMERA_FIELDS.items():
                entry = {"node": node, "value": getattr(self.cfg.camera, name), "limits": None,
                         "during_capture": during_capture}
                if self.connected:
                    try:
                        entry["value"] = typ(self.cam.get(node))
                        entry["limits"] = self.cam.limits(node)
                    except Exception as e:  # noqa: BLE001 — show which node is missing
                        entry["error"] = str(e)
                out[name] = entry
            return out

        c, s = self.cfg.camera, self.cfg.segmentation
        camera = self.call(read)
        line_rate = float(camera["line_rate_hz"]["value"] or c.line_rate_hz)
        period = s.nominal_period(line_rate)
        return {
            "camera": camera,
            "camera_info": {"model": c.model, "serial": c.serial, "ip": c.ip,
                            "width_px": c.width_px, "pixel_format": c.pixel_format},
            "segmentation": {f.name: getattr(s, f.name) for f in fields(SegmentationConfig)},
            "storage": {**{k: getattr(self.cfg.storage, k) for k in STORAGE_TYPES},
                        "data_dir": str(self.cfg.storage.data_dir),
                        "data_dir_abs": str(self.cfg.storage.data_dir.resolve())},
            "derived": {
                "exposure_max_us": round(1e6 / line_rate, 1),
                "nominal_period_lines": round(period, 1),
                "bird_ms": round(1000 * period / line_rate, 1),
                "window_lines": int(round(period * s.window_factor)),
            },
        }

    def set_camera(self, values: dict) -> dict:
        parsed = {}
        for name, value in values.items():
            if name not in CAMERA_FIELDS:
                raise UserError(f"unknown camera field: {name}")
            parsed[name] = _coerce(name, value, CAMERA_FIELDS[name][1])
        if self._capture is not None:
            blocked = [n for n in parsed if not CAMERA_FIELDS[n][2]]
            if blocked:
                raise UserError(f"stop capture before changing {', '.join(blocked)}")

        def apply() -> dict:
            if not self.connected:
                for name, value in parsed.items():
                    setattr(self.cfg.camera, name, value)
                return {}
            restart = self.cam.grabbing and any(
                CAMERA_FIELDS[n][0] not in LIVE_NODES for n in parsed)
            if restart:
                self.cam.stop()
            errors = {}
            try:
                for name, value in parsed.items():
                    node, typ, _ = CAMERA_FIELDS[name]
                    try:
                        self.cam.set(node, value)
                        setattr(self.cfg.camera, name, typ(self.cam.get(node)))
                    except Exception as e:  # noqa: BLE001 — report per field
                        errors[name] = str(e)
            finally:
                if restart:
                    self.cam.start()
            if "line_rate_hz" in parsed and self._seg is not None and self._capture is None:
                self._new_preview_segmenter()
            return errors

        errors = self.call(apply)
        if errors:
            raise UserError("; ".join(f"{k}: {v}" for k, v in errors.items()))
        return self.settings()

    def set_segmentation(self, values: dict) -> dict:
        if self._capture is not None:
            raise UserError("stop capture before changing segmentation settings")
        parsed = {}
        for name, value in values.items():
            if name not in SEGMENTATION_TYPES:
                raise UserError(f"unknown segmentation field: {name}")
            parsed[name] = _coerce(name, value, SEGMENTATION_TYPES[name])

        def apply() -> None:
            for name, value in parsed.items():
                setattr(self.cfg.segmentation, name, value)
            if self._seg is not None:
                self._new_preview_segmenter()

        self.call(apply)
        return self.settings()

    def set_storage(self, values: dict) -> dict:
        if self._capture is not None:
            raise UserError("stop capture before changing storage settings")
        parsed = {}
        for name, value in values.items():
            if name == "data_dir":
                parsed[name] = _writable_dir(value)
            elif name in STORAGE_TYPES:
                parsed[name] = _coerce(name, value, STORAGE_TYPES[name])
            else:
                raise UserError(f"unknown storage field: {name}")
        for name, value in parsed.items():
            setattr(self.cfg.storage, name, value)
        if "data_dir" in parsed:
            self._set_calib_dir()
        return self.settings()

    def _set_calib_dir(self) -> None:
        # the simulator keeps its own log: its focus reference must never be the real one's
        self.calib_dir = self.cfg.storage.data_dir / ("calibration_sim" if self.sim else "calibration")
        self.calib_log = calibration.CalibrationLog(self.calib_dir / "calibration_log.jsonl")

    def _saved_values(self) -> dict:
        c, s, st = self.cfg.camera, self.cfg.segmentation, self.cfg.storage
        return {
            "camera": {name: getattr(c, name) for name in CAMERA_FIELDS},
            "segmentation": {f.name: getattr(s, f.name) for f in fields(SegmentationConfig)},
            "storage": {**{name: getattr(st, name) for name in STORAGE_TYPES},
                        "data_dir": st.data_dir.as_posix()},
        }

    def unsaved_settings(self) -> list[str]:
        """Settings that differ from config/camera.yaml, as "section.field"."""
        try:
            mtime = self.config_path.stat().st_mtime
            if self._file_cfg is None or self._file_cfg[0] != mtime:
                self._file_cfg = (mtime, load_config(self.config_path))
            on_file = self._file_cfg[1]
        except Exception:  # noqa: BLE001 — an unreadable file means nothing is saved
            return ["config file unreadable"]
        on_file = {"camera": on_file.camera, "segmentation": on_file.segmentation,
                   "storage": on_file.storage}
        out = []
        for section, values in self._saved_values().items():
            for name, value in values.items():
                saved = getattr(on_file[section], name)
                if name == "data_dir":
                    saved = saved.as_posix()
                # the camera reads some floats back slightly off what was written
                close = (isinstance(value, float) and isinstance(saved, (int, float))
                         and abs(saved - value) <= 1e-3 * max(1.0, abs(value)))
                if saved != value and not close:
                    out.append(f"{section}.{name}")
        return out

    def save_config(self) -> dict:
        backup_dir = self.cfg.storage.data_dir / "config_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"camera_{datetime.now():%Y%m%d_%H%M%S}.yaml"
        shutil.copy2(self.config_path, backup)
        save_values(self.config_path, self._saved_values())
        return {"path": str(self.config_path), "backup": str(backup)}

    # --- segmentation preview and capture ---

    def _new_preview_segmenter(self) -> None:
        self._seg = make_segmenter(self.cfg.segmentation, self.cfg.camera.line_rate_hz, "birds")
        self._seg_origin = None
        self._cuts.clear()

    def set_show_cuts(self, on: bool) -> None:
        def apply() -> None:
            self._show_cuts = on
            if self._capture is not None:
                return  # the capture run owns the segmenter
            if on:
                self._new_preview_segmenter()
            else:
                self._seg = None
                self._cuts.clear()
        self.call(apply)

    def start_capture(self, mode: str) -> None:
        if mode not in ("birds", "tiles"):
            raise UserError(f"unknown capture mode: {mode}")
        if self._capture is not None:
            raise UserError("already capturing")
        if not self.grabbing:
            raise UserError("camera is not streaming — check the connection")
        writer = AsyncWriter(self.cfg.storage, on_written=self._on_written).start()
        run = CaptureRun(mode=mode, started=time.time(), writer=writer)

        def attach() -> None:
            self._seg = make_segmenter(self.cfg.segmentation, self.cfg.camera.line_rate_hz, mode)
            self._seg_origin = None
            self._cuts.clear()
            self._capture = run

        try:
            self.call(attach)
        except BaseException:
            writer.stop()
            raise

    def stop_capture(self) -> dict:
        def detach() -> CaptureRun | None:
            run = self._capture
            self._capture = None
            if self._show_cuts:
                self._new_preview_segmenter()
            else:
                self._seg = None
                self._cuts.clear()
            return run

        run = self.call(detach)
        if run is None:
            raise UserError("no capture running")
        run.writer.stop()  # drains what is queued; may take a moment
        self.last_capture = {
            "mode": run.mode,
            "started": datetime.fromtimestamp(run.started).strftime("%Y-%m-%d %H:%M:%S"),
            "seconds": round(time.time() - run.started),
            "cut": run.cut,
            "written": run.writer.written,
            "dropped": run.writer.dropped,
            "failed": run.writer.failed,
        }
        return self.last_capture

    # --- calibration ---

    def set_sim_scene(self, scene: str) -> None:
        if not self.sim:
            raise UserError("only available in the simulator")
        if scene not in SCENES:
            raise UserError(f"unknown scene: {scene}")
        self.call(lambda: setattr(self.cam, "scene", scene))

    def calibration_info(self) -> dict:
        nodes = self.nodes
        focus = [e for e in self.calib_log.entries(1000) if e["action"] == "focus"]
        return {
            "nodes": asdict(nodes) if nodes else None,
            "missing_prnu": nodes.missing_for_prnu() if nodes else ["camera"],
            "missing_user_set": nodes.missing_for_user_set() if nodes else ["camera"],
            "focus_reference": focus[-1].get("sharpness") if focus else None,
            "focus_previous": focus[-2].get("sharpness") if len(focus) > 1 else None,
            "log": self.calib_log.entries(30),
        }

    def _require_idle(self) -> None:
        if self._capture is not None:
            raise UserError("stop capture before calibrating")
        if not self.connected or self.nodes is None:
            raise UserError("camera is not connected")

    def run_prnu(self) -> dict:
        self._require_idle()
        before = (self.stats() or {}).get("flat")
        nodes = self.nodes

        def op() -> list[str]:
            try:
                return calibration.run_prnu(self.cam, nodes)
            except TimeoutError:
                raise
            except Exception as first:  # noqa: BLE001 — some nodes are locked while grabbing
                if not self.cam.grabbing:
                    raise
                log.info("PRNU while grabbing failed (%s); retrying with acquisition stopped", first)
                self.cam.stop()
                try:
                    return calibration.run_prnu(self.cam, nodes)
                finally:
                    self.cam.start()

        steps = self.call(op, timeout=120)
        after = self._fresh_stats().get("flat")
        mode = self.call(lambda: calibration.read_back(self.cam, nodes.shading_mode))
        return self.calib_log.append("prnu", steps=steps, flat_before=before, flat_after=after,
                                     checks=calibration.check_prnu(nodes, mode, before, after))

    def _fresh_stats(self, blocks: int = 4, timeout: float = 5.0) -> dict:
        """Stats from lines grabbed after now — e.g. once a new correction is active."""
        start = self.blocks
        end = time.monotonic() + timeout
        while self.blocks < start + blocks and time.monotonic() < end:
            time.sleep(0.05)
        return self.stats() or {}

    def save_user_set(self) -> dict:
        self._require_idle()
        nodes = self.nodes

        def op() -> list[str]:
            was = self.cam.grabbing
            self.cam.stop()
            try:
                return calibration.save_user_set(self.cam, nodes)
            finally:
                if was:
                    self.cam.start()

        steps = self.call(op, timeout=60)
        default = self.call(lambda: calibration.read_back(self.cam, nodes.user_set_default))
        return self.calib_log.append("user_set_saved", steps=steps,
                                     checks=calibration.check_user_set(nodes, default))

    def record(self, kind: str, note: str = "") -> dict:
        if kind not in RECORD_KINDS:
            raise UserError(f"unknown record kind: {kind}")
        st = self.stats() or {}
        return self.calib_log.append(
            kind, note=note, sharpness=st.get("sharpness"), flat=st.get("flat"),
            rgb_mean=st.get("rgb_mean"), mean=st.get("mean"),
            exposure_us=self.cfg.camera.exposure_us, line_rate_hz=self.cfg.camera.line_rate_hz)

    def save_board_image(self) -> dict:
        if not self.grabbing:
            raise UserError("camera is not streaming")
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def arm() -> None:
            self._snap = [BOARD_LINES, [], fut]

        self.call(arm)
        timeout = 10.0 + BOARD_LINES / max(self.cfg.camera.line_rate_hz, 1.0) * 3
        parts = fut.result(timeout)
        lines = np.concatenate(parts)[:BOARD_LINES]
        self.calib_dir.mkdir(parents=True, exist_ok=True)
        path = self.calib_dir / f"board_{datetime.now():%Y%m%d_%H%M%S}.png"
        if not cv2.imwrite(str(path), orient(lines, self.cfg.storage)):
            raise OSError(f"could not write {path}")
        return self.calib_log.append("board", file=str(path),
                                     line_rate_hz=self.cfg.camera.line_rate_hz,
                                     checks=calibration.check_board(lines.mean(axis=2)))

    def board_preview_jpeg(self, width: int = 440) -> bytes | None:
        """The last saved board image, small, for the wizard to show."""
        entry = self.calib_log.last("board")
        img = cv2.imread(entry["file"]) if entry else None
        if img is None:
            return None
        h, w = img.shape[:2]
        small = cv2.resize(img, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None
