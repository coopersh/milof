"""Simulated camera behind the same control surface as BaslerLineScan, so the UI (and its
calibration wizard) can be exercised without hardware.

Scenes: "birds" (moving line), "white" (flat sheet), "stripes" (focus chart). Until PRNU
is executed every scene carries lens/light fall-off and per-pixel gain noise, so the
flat-field step has something visible to correct. Brightness follows ExposureTime, capped
at what the line rate allows, like the real camera.
"""
from __future__ import annotations

import time

import numpy as np

from acquisition.camera import Block, SimulatedLineScan
from acquisition.config import CameraConfig

SCENES = ("birds", "white", "stripes")
REFERENCE_EXPOSURE_US = 300.0  # exposure at which a scene renders at its nominal brightness


class SimCamera:
    def __init__(self, cfg: CameraConfig, nominal_period: float, realtime: bool = True):
        self.cfg = cfg
        self.realtime = realtime
        self.scene = "birds"
        self.prnu_done = False
        # test hook: "grab" = delivers nothing until the grab restarts,
        # "device" = nothing until the camera is reopened
        self.stall: str | None = None
        self._birds = SimulatedLineScan(width=cfg.width_px, period=nominal_period,
                                        speed_error=0.02)
        self._open = False
        self._grabbing = False
        self._line = 0
        self._next_due = 0.0
        x = np.linspace(-1.0, 1.0, cfg.width_px)
        rng = np.random.default_rng(1)
        self._falloff = (1 - 0.3 * x ** 2) * (1 + 0.03 * rng.standard_normal(cfg.width_px))
        self._nodes = {
            "ExposureTime": cfg.exposure_us or REFERENCE_EXPOSURE_US,
            "AcquisitionLineRate": float(cfg.line_rate_hz),
            "Height": cfg.lines_per_frame,
            "Width": cfg.width_px,
            "PixelFormat": cfg.pixel_format,
            "ReverseX": cfg.reverse_x,
            "GevSCPSPacketSize": cfg.packet_size_bytes,
            "BslColorSpace": "sRgb",
            "BslLightSourcePreset": "Daylight5000K",
            "ShadingCorrectionSelector": "DSNU",
            "ShadingCorrectionMode": "Off",
            "UserSetSelector": "Default",
            "UserSetDefault": "Default",
        }
        self._enums = {
            "PixelFormat": ["RGB8", "BGR8", "Mono8"],
            "BslColorSpace": ["Off", "sRgb"],
            "BslLightSourcePreset": ["Off", "Daylight5000K", "Tungsten2800K"],
            "ShadingCorrectionSelector": ["DSNU", "PRNU"],
            "ShadingCorrectionMode": ["Off", "User"],
            "UserSetSelector": ["Default", "UserSet1", "UserSet2"],
            "UserSetDefault": ["Default", "UserSet1", "UserSet2"],
        }
        self._commands = {"ShadingCorrectionCreate", "UserSetSave", "UserSetLoad"}
        self._limits = {
            "ExposureTime": (1.0, 1_000_000.0),
            "AcquisitionLineRate": (100.0, 14_000.0),
            "Height": (1.0, 8192.0),
            "GevSCPSPacketSize": (220.0, 8192.0),
        }

    # --- lifecycle, same as BaslerLineScan ---

    def open(self) -> None:
        self._open = True
        if self.stall == "device":
            self.stall = None

    def close(self) -> None:
        self._grabbing = False
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def grabbing(self) -> bool:
        return self._grabbing

    def start(self) -> None:
        self._grabbing = True
        if self.stall == "grab":
            self.stall = None
        self._next_due = time.monotonic()

    def stop(self) -> None:
        self._grabbing = False

    def timeout_ms(self) -> int:
        return 1000

    def retrieve(self, timeout_ms: int) -> Block | None:
        if self.stall:
            time.sleep(0.05)
            raise TimeoutError("sim stall")
        n = int(self._nodes["Height"])
        if self.realtime:
            self._next_due += n / float(self._nodes["AcquisitionLineRate"])
            delay = self._next_due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -1.0:
                self._next_due = time.monotonic()  # was paused: resume, don't burst
        block = Block(self._line, self._render(self._line, n))
        self._line += n
        return block

    def _render(self, start: int, n: int) -> np.ndarray:
        w = self.cfg.width_px
        exposure = min(float(self._nodes["ExposureTime"]),
                       1e6 / float(self._nodes["AcquisitionLineRate"]))
        gain = np.full(w, exposure / REFERENCE_EXPOSURE_US, dtype=np.float32)
        if not self.prnu_done:
            gain *= self._falloff.astype(np.float32)
        if self.scene in ("white", "stripes"):
            if self.scene == "white":
                level = np.full(w, 200.0, dtype=np.float32)
            else:
                level = np.where((np.arange(w) // 32) % 2 == 0, 230.0, 30.0).astype(np.float32)
            line = np.clip(level * gain, 0, 255).astype(np.uint8)
            img = np.broadcast_to(line[None, :, None], (n, w, 3)).copy()
        else:
            img = self._birds.render(start, n).astype(np.float32)
            img *= (0.85 * gain)[None, :, None]
            img = np.clip(img, 0, 255, out=img).astype(np.uint8)
        if self._nodes["ReverseX"]:
            img = np.ascontiguousarray(img[:, ::-1])
        return img

    # --- node access, same names/semantics as BaslerLineScan ---

    def get(self, name: str):
        if name not in self._nodes:
            raise KeyError(f"sim camera has no node {name!r}")
        return self._nodes[name]

    def set(self, name: str, value) -> None:
        if name not in self._nodes:
            raise KeyError(f"sim camera has no node {name!r}")
        if name in self._enums and value not in self._enums[name]:
            raise ValueError(f"{value!r} is not one of {self._enums[name]}")
        if self._grabbing and name in ("Height", "Width", "PixelFormat", "GevSCPSPacketSize"):
            raise RuntimeError(f"{name} cannot change while grabbing")
        self._nodes[name] = value

    def limits(self, name: str) -> tuple[float, float] | None:
        return self._limits.get(name)

    def execute(self, name: str, wait_s: float = 0.0) -> None:
        if name not in self._commands:
            raise KeyError(f"sim camera has no command {name!r}")
        if name == "ShadingCorrectionCreate":
            if (self._nodes["ShadingCorrectionSelector"], self._nodes["ShadingCorrectionMode"]) \
                    != ("PRNU", "User"):
                raise RuntimeError("select PRNU and mode User before creating the correction")
            time.sleep(0.5)
            self.prnu_done = True

    def node_kinds(self) -> dict[str, str]:
        kinds = {name: "enum" for name in self._enums}
        kinds.update({name: "command" for name in self._commands})
        for name, value in self._nodes.items():
            kinds.setdefault(name, "bool" if isinstance(value, bool) else
                             "int" if isinstance(value, int) else "float")
        return kinds

    def enum_options(self, name: str) -> list[str]:
        return list(self._enums[name])
