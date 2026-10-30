"""Line sources: the Basler racer 2 S over GigE, and a simulator for bench tests.

Both yield `Block`s: consecutive groups of scan lines, shape (n_lines, width, 3) BGR uint8,
with the global index of their first line. Axis 0 is time (direction of travel), axis 1 is
the sensor line (along the hanging bird).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterator

import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)


@dataclass
class Block:
    start_line: int
    lines: np.ndarray
    lost_frames: int = 0


class BaslerLineScan:
    """racer 2 S via pypylon. Free-run line trigger only (no encoder fitted yet).

    Not thread-safe: call every method from the thread that grabs.
    """

    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self._cam = None
        self._converter = None
        self._line = 0
        self._last_id = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self) -> None:
        from pypylon import pylon

        if self.cfg.trigger != "free_run":
            raise NotImplementedError(
                f"trigger={self.cfg.trigger!r}: only free_run is implemented (no encoder fitted)"
            )
        tl = pylon.TlFactory.GetInstance()
        devices = tl.EnumerateDevices()
        if not devices:
            raise RuntimeError("no Basler camera found — check PoE injector, cable and NIC IP")
        dev = self._pick(devices)
        self._cam = pylon.InstantCamera(tl.CreateDevice(dev))
        self._cam.Open()
        log.info("opened %s (serial %s)", dev.GetModelName(), dev.GetSerialNumber())
        self._configure()
        self._converter = pylon.ImageFormatConverter()
        self._converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        self._converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

    def _pick(self, devices):
        for d in devices:
            if self.cfg.serial and d.GetSerialNumber() == str(self.cfg.serial):
                return d
        if not self.cfg.serial and self.cfg.ip:
            for d in devices:
                if d.GetIpAddress() == self.cfg.ip:
                    return d
        if self.cfg.serial:
            raise RuntimeError(f"camera serial {self.cfg.serial} not found")
        log.warning("camera ip %s not found, using first device", self.cfg.ip)
        return devices[0]

    def _set(self, name: str, value) -> None:
        # Node names differ between camera families; log and carry on rather than fail,
        # then check the result in pylon Viewer.
        try:
            getattr(self._cam, name).Value = value
        except Exception as e:  # noqa: BLE001 — genicam raises many types
            log.warning("could not set %s=%r: %s", name, value, e)

    def _configure(self) -> None:
        c = self.cfg
        self._set("GevSCPSPacketSize", c.packet_size_bytes)  # 9000 needs jumbo frames; 1500 for a NIC without
        self._set("PixelFormat", c.pixel_format)
        self._set("ReverseX", c.reverse_x)
        self._set("Width", c.width_px)
        self._set("Height", c.lines_per_frame)
        for selector in ("LineStart", "FrameStart"):
            self._set("TriggerSelector", selector)
            self._set("TriggerMode", "Off")
        self._set("AcquisitionLineRateEnable", True)
        self._set("AcquisitionLineRate", c.line_rate_hz)
        if c.exposure_us is not None:
            self._set("ExposureTime", float(c.exposure_us))

    @property
    def is_open(self) -> bool:
        return self._cam is not None

    @property
    def grabbing(self) -> bool:
        return self._cam is not None and self._cam.IsGrabbing()

    def start(self) -> None:
        from pypylon import pylon

        self._last_id = None
        self._cam.StartGrabbing(pylon.GrabStrategy_OneByOne)

    def stop(self) -> None:
        if self.grabbing:
            self._cam.StopGrabbing()

    def timeout_ms(self) -> int:
        frame_ms = 1000.0 * self.cfg.lines_per_frame / self.cfg.line_rate_hz
        return int(max(1000, 5 * frame_ms))

    def retrieve(self, timeout_ms: int) -> Block | None:
        """Next block, or None if that grab failed. Raises TimeoutError if nothing arrived."""
        from pypylon import pylon

        res = self._cam.RetrieveResult(timeout_ms, pylon.TimeoutHandling_Return)
        if res is None or not res.IsValid():
            raise TimeoutError(f"no frame within {timeout_ms} ms")
        try:
            if not res.GrabSucceeded():
                log.error("grab failed: %s", res.GetErrorDescription())
                return None
            block_id = getattr(res, "BlockID", None)
            lost = 0
            if block_id is not None and self._last_id is not None and block_id > self._last_id + 1:
                lost = block_id - self._last_id - 1
                self._line += lost * self.cfg.lines_per_frame  # keep line index = time
                log.warning("lost %d frame(s) before block %d", lost, block_id)
            self._last_id = block_id
            arr = self._converter.Convert(res).GetArray().copy()
            block = Block(self._line, arr, lost)
            self._line += arr.shape[0]
            return block
        finally:
            res.Release()

    def blocks(self) -> Iterator[Block]:
        self.start()
        try:
            while True:
                block = self.retrieve(self.timeout_ms())
                if block is not None:
                    yield block
        finally:
            self.stop()

    # --- GenICam node access (names differ between Basler families, hence by name) ---

    def get(self, name: str):
        return getattr(self._cam, name).Value

    def set(self, name: str, value) -> None:
        getattr(self._cam, name).Value = value

    def limits(self, name: str) -> tuple[float, float] | None:
        node = getattr(self._cam, name)
        try:
            return float(node.Min), float(node.Max)
        except Exception:  # noqa: BLE001 — not a numeric node
            return None

    def execute(self, name: str, wait_s: float = 0.0) -> None:
        """Run a command node; with wait_s, poll IsDone until it finishes."""
        node = getattr(self._cam, name)
        node.Execute()
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                if node.IsDone():
                    return
            except Exception:  # noqa: BLE001 — command without IsDone support
                return
            time.sleep(0.1)
        if wait_s:
            raise TimeoutError(f"{name} did not finish within {wait_s:.0f} s")

    def node_kinds(self) -> dict[str, str]:
        """{node name: enum|command|int|float|bool|string|other} for every node."""
        from pypylon import genicam

        by_interface = {
            genicam.intfIEnumeration: "enum", genicam.intfICommand: "command",
            genicam.intfIInteger: "int", genicam.intfIFloat: "float",
            genicam.intfIBoolean: "bool", genicam.intfIString: "string",
        }
        kinds = {}
        for node in self._cam.GetNodeMap().GetNodes():
            try:
                kinds[node.GetName()] = by_interface.get(node.GetPrincipalInterfaceType(), "other")
            except Exception:  # noqa: BLE001 — skip nodes the wrapper can't describe
                continue
        return kinds

    def enum_options(self, name: str) -> list[str]:
        from pypylon import genicam

        node = self._cam.GetNodeMap().GetNode(name)
        return [e.GetSymbolic() for e in node.GetEntries() if genicam.IsAvailable(e)]

    def close(self) -> None:
        if self._cam is not None:
            self.stop()
            self._cam.Close()
            self._cam = None


class SimulatedLineScan:
    """Synthetic stream of overlapping bright 'birds' on a dark background.

    Birds are ellipses of length `bird_factor * period` (>1 → neighbours overlap), centred
    every `period * (1 + speed_error)` lines — `speed_error` mimics the real line running
    faster/slower than the nominal period the segmenter is told.
    """

    def __init__(
        self,
        width: int = 512,
        lines_per_frame: int = 256,
        period: float = 1154.0,
        bird_factor: float = 1.25,
        speed_error: float = 0.0,
        phase: float = 0.0,
        line_rate_hz: float | None = None,
        seed: int = 0,
    ):
        self.width = width
        self.lines_per_frame = lines_per_frame
        self.true_period = period * (1.0 + speed_error)
        self.bird_len = bird_factor * period
        self.phase = phase
        self.line_rate_hz = line_rate_hz  # set → pace blocks in real time
        self._rng = np.random.default_rng(seed)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def bird_center(self, k: int) -> float:
        return self.phase + (k + 0.5) * self.true_period

    def render(self, start: int, n: int) -> np.ndarray:
        """Lines [start, start + n) of the stream."""
        t = np.arange(start, start + n, dtype=float)
        k0 = np.floor((t - self.phase) / self.true_period).astype(int)
        half_h = np.zeros(n)
        for dk in (-1, 0, 1):
            c = self.phase + (k0 + dk + 0.5) * self.true_period
            d = (t - c) / (self.bird_len / 2)
            h = np.where(np.abs(d) < 1, 0.4 * self.width * np.sqrt(np.clip(1 - d * d, 0, 1)), 0)
            half_h = np.maximum(half_h, h)
        y = np.arange(self.width) - self.width / 2
        mask = np.abs(y)[None, :] < half_h[:, None]
        img = np.full((n, self.width, 3), 20, dtype=np.uint8)
        img[mask] = (150, 180, 230)  # BGR skin tone
        noise = self._rng.integers(-6, 7, size=img.shape, dtype=np.int16)
        return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    def blocks(self, total_lines: int | None = None) -> Iterator[Block]:
        line = 0
        while total_lines is None or line < total_lines:
            n = self.lines_per_frame
            if total_lines is not None:
                n = min(n, total_lines - line)
            yield Block(line, self.render(line, n))
            line += n
            if self.line_rate_hz:
                time.sleep(n / self.line_rate_hz)
