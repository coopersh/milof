"""Live preview + knobs: see what the camera sees and tune exposure / line rate.

A rolling strip of the most recent lines plus stats (mean, max, clip high/low) and a
luminance histogram, with trackbars for exposure and line rate. Needed to aim and focus
the rig at the line, where re-running capture scripts is too slow.

Keys:
    q / Esc   quit
    s         snapshot the current rolling strip to data/calibration/
    w         write the current exposure / line rate back to config/camera.yaml
              (regex substitution: leaves comments and other keys untouched)

Needs a GUI build of OpenCV (`opencv-python`, not `-headless`); the error message says so.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .camera import SimulatedLineScan
from .config import Config

log = logging.getLogger(__name__)

WINDOW = "milof viewer"
STRIP_LINES = 1024
DISPLAY_WIDTH = 1024
RENDER_HZ = 30.0


class _Rolling:
    """Fixed-height circular-ish buffer: newest lines at the bottom, oldest at the top."""

    def __init__(self, lines: int, width: int):
        self.buf = np.zeros((lines, width, 3), dtype=np.uint8)

    def push(self, block: np.ndarray) -> None:
        n = block.shape[0]
        if n >= self.buf.shape[0]:
            self.buf[:] = block[-self.buf.shape[0]:]
        else:
            self.buf[:-n] = self.buf[n:]
            self.buf[-n:] = block


def _histogram_image(frame: np.ndarray, h: int = 100, w: int = 256) -> np.ndarray:
    import cv2
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hist = cv2.calcHist([g], [0], None, [w], [0, 256]).flatten()
    img = np.zeros((h, w, 3), dtype=np.uint8)
    if hist.max() > 0:
        heights = (hist / hist.max() * (h - 1)).astype(int)
        for x in range(w):
            cv2.line(img, (x, h - 1), (x, h - 1 - heights[x]), (255, 255, 255))
    return img


def _overlay(disp: np.ndarray, text: str, hist: np.ndarray) -> np.ndarray:
    import cv2
    cv2.putText(disp, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 255, 255), 1, cv2.LINE_AA)
    hy, hx = hist.shape[:2]
    y0, x0 = disp.shape[0] - hy - 10, disp.shape[1] - hx - 10
    disp[y0:y0 + hy, x0:x0 + hx] = cv2.addWeighted(
        disp[y0:y0 + hy, x0:x0 + hx], 0.3, hist, 0.7, 0)
    return disp


def _write_knobs_to_config(path: Path, line_rate_hz: float, exposure_us: float) -> None:
    """Regex-replace just the two scalar values; leaves comments and other keys as-is."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^(\s*line_rate_hz:\s*)\S+",
                  lambda m: f"{m.group(1)}{line_rate_hz:.1f}", text, flags=re.M)
    text = re.sub(r"^(\s*exposure_us:\s*)\S+",
                  lambda m: f"{m.group(1)}{exposure_us:.1f}", text, flags=re.M)
    path.write_text(text, encoding="utf-8")


def _import_cv2_gui():
    import cv2
    try:
        cv2.namedWindow("__probe__", cv2.WINDOW_NORMAL)
        cv2.destroyWindow("__probe__")
    except cv2.error as e:
        raise SystemExit(
            "the viewer needs a GUI build of OpenCV. install opencv-python (not headless):\n"
            "  pip uninstall -y opencv-python-headless && pip install opencv-python\n"
            f"(opencv error: {e})"
        )
    return cv2


def run(cfg: Config, config_path: Path, sim: bool, seconds: float | None) -> None:
    cv2 = _import_cv2_gui()
    if sim:
        _run_sim(cfg, cv2, seconds)
    else:
        _run_camera(cfg, config_path, cv2, seconds)


def _run_sim(cfg: Config, cv2, seconds: float | None) -> None:
    period = cfg.segmentation.nominal_period(cfg.camera.line_rate_hz)
    src = SimulatedLineScan(
        width=cfg.camera.width_px, lines_per_frame=cfg.camera.lines_per_frame,
        period=period, speed_error=0.02, line_rate_hz=cfg.camera.line_rate_hz)
    roll = _Rolling(STRIP_LINES, cfg.camera.width_px)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    t0 = time.monotonic()
    last_render = 0.0
    for block in src.blocks():
        roll.push(block.lines)
        now = time.monotonic()
        if now - last_render < 1.0 / RENDER_HZ:
            continue
        last_render = now
        _render(cv2, roll.buf, {
            "exposure us": "--", "line rate Hz": f"{cfg.camera.line_rate_hz:.0f}",
            "mode": "SIM"})
        k = cv2.waitKey(1) & 0xFF
        if k in (ord('q'), 27):
            break
        if seconds is not None and now - t0 >= seconds:
            break
    cv2.destroyAllWindows()


def _run_camera(cfg: Config, config_path: Path, cv2, seconds: float | None) -> None:
    from pypylon import pylon

    tl = pylon.TlFactory.GetInstance()
    devices = tl.EnumerateDevices()
    if not devices:
        raise SystemExit("no Basler camera found — check PoE, cable, NIC IP")
    cam = pylon.InstantCamera(tl.CreateDevice(devices[0]))
    cam.Open()

    def _try(name, value):
        try:
            getattr(cam, name).Value = value
        except Exception as e:  # noqa: BLE001
            log.warning("could not set %s=%r: %s", name, value, e)

    _try("GevSCPSPacketSize", cfg.camera.packet_size_bytes)
    _try("PixelFormat", cfg.camera.pixel_format)
    _try("Width", cfg.camera.width_px)
    _try("Height", cfg.camera.lines_per_frame)
    for selector in ("LineStart", "FrameStart"):
        _try("TriggerSelector", selector)
        _try("TriggerMode", "Off")
    _try("AcquisitionLineRateEnable", True)
    _try("AcquisitionLineRate", cfg.camera.line_rate_hz)
    if cfg.camera.exposure_us is not None:
        _try("ExposureTime", float(cfg.camera.exposure_us))

    conv = pylon.ImageFormatConverter()
    conv.OutputPixelFormat = pylon.PixelType_BGR8packed

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)

    def _on_exposure(v):
        if v > 0:
            _try("ExposureTime", float(v))

    def _on_line_rate(v):
        if v >= 100:
            _try("AcquisitionLineRate", float(v))

    cv2.createTrackbar("exposure us", WINDOW,
                       int(cam.ExposureTime.Value), 20000, _on_exposure)
    cv2.createTrackbar("line rate Hz", WINDOW,
                       int(cam.AcquisitionLineRate.Value), 14000, _on_line_rate)

    roll = _Rolling(STRIP_LINES, cfg.camera.width_px)
    snapshot_dir = Path("data/calibration")
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
    t0 = time.monotonic()
    last_render = 0.0
    try:
        while cam.IsGrabbing():
            res = cam.RetrieveResult(1000, pylon.TimeoutHandling_Return)
            if res is not None and res.IsValid():
                try:
                    if res.GrabSucceeded():
                        arr = conv.Convert(res).GetArray()
                        roll.push(arr)
                finally:
                    res.Release()

            now = time.monotonic()
            if now - last_render < 1.0 / RENDER_HZ:
                k = cv2.waitKey(1) & 0xFF  # keep the window responsive
            else:
                last_render = now
                exp_us = float(cam.ExposureTime.Value)
                lr = float(cam.AcquisitionLineRate.Value)
                _render(cv2, roll.buf, {
                    "exposure us": f"{exp_us:.0f}",
                    "line rate Hz": f"{lr:.0f}",
                    "mode": "CAM"})
                k = cv2.waitKey(1) & 0xFF

            if k in (ord('q'), 27):
                break
            if k == ord('s'):
                p = snapshot_dir / f"snap_{datetime.now():%Y%m%d_%H%M%S}.jpg"
                cv2.imwrite(str(p), roll.buf,
                            [cv2.IMWRITE_JPEG_QUALITY, cfg.storage.jpeg_quality])
                log.info("snapshot saved: %s", p)
            if k == ord('w'):
                _write_knobs_to_config(config_path,
                                       float(cam.AcquisitionLineRate.Value),
                                       float(cam.ExposureTime.Value))
                log.info("wrote exposure_us=%.0f line_rate_hz=%.0f to %s",
                         cam.ExposureTime.Value, cam.AcquisitionLineRate.Value, config_path)
            if seconds is not None and now - t0 >= seconds:
                break
    finally:
        cam.StopGrabbing()
        cam.Close()
        cv2.destroyAllWindows()


def _render(cv2, frame: np.ndarray, knobs: dict) -> None:
    disp = frame
    if disp.shape[1] > DISPLAY_WIDTH:
        scale = DISPLAY_WIDTH / disp.shape[1]
        disp = cv2.resize(disp, (DISPLAY_WIDTH, int(disp.shape[0] * scale)))
    else:
        disp = disp.copy()
    gmean = float(frame.mean())
    gmax = int(frame.max())
    gmin = int(frame.min())
    clip_hi = float((frame == 255).mean() * 100)
    clip_lo = float((frame == 0).mean() * 100)
    text = (f"[{knobs.get('mode','')}]  mean {gmean:5.1f}  min {gmin:3d}  max {gmax:3d}  "
            f"clip hi {clip_hi:5.2f}%  lo {clip_lo:5.2f}%  "
            f"exp {knobs['exposure us']}us  rate {knobs['line rate Hz']}Hz  "
            f"[s] snap  [w] write cfg  [q] quit")
    hist = _histogram_image(frame)
    _overlay(disp, text, hist)
    cv2.imshow(WINDOW, disp)
