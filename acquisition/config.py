"""Load config/camera.yaml into typed settings, and write values back without losing comments."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "camera.yaml"


@dataclass
class CameraConfig:
    model: str
    serial: str | None
    ip: str | None
    line_rate_hz: float
    lines_per_frame: int
    trigger: str
    exposure_us: float | None
    width_px: int
    pixel_format: str
    packet_size_bytes: int
    reverse_x: bool


@dataclass
class SegmentationConfig:
    method: str
    birds_per_min: float
    period_lines: float | None
    window_factor: float
    track_gain: float
    min_contrast: float
    profile_sign: int
    tile_lines: int

    def nominal_period(self, line_rate_hz: float) -> float:
        if self.period_lines:
            return float(self.period_lines)
        return line_rate_hz * 60.0 / self.birds_per_min


@dataclass
class StorageConfig:
    data_dir: Path
    image_format: str
    jpeg_quality: int
    rotate: bool
    flip_vertical: bool
    flip_horizontal: bool
    queue_size: int


@dataclass
class Config:
    camera: CameraConfig
    segmentation: SegmentationConfig
    storage: StorageConfig


def load_config(path: str | Path = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cam, seg, sto = raw["camera"], raw["segmentation"], raw["storage"]
    return Config(
        camera=CameraConfig(
            model=cam["model"],
            serial=cam.get("serial"),
            ip=cam.get("ip"),
            line_rate_hz=float(cam["line_rate_hz"]),
            lines_per_frame=int(cam.get("lines_per_frame", 256)),
            trigger=cam.get("trigger", "free_run"),
            exposure_us=cam.get("exposure_us"),
            width_px=int(cam.get("width_px", 4096)),
            pixel_format=cam.get("pixel_format", "RGB8"),
            packet_size_bytes=int(cam.get("packet_size_bytes", 1500)),
            reverse_x=bool(cam.get("reverse_x", False)),
        ),
        segmentation=SegmentationConfig(
            method=seg.get("method", "periodic"),
            birds_per_min=float(seg.get("birds_per_min", 156)),
            period_lines=seg.get("period_lines"),
            window_factor=float(seg.get("window_factor", 1.5)),
            track_gain=float(seg.get("track_gain", 0.3)),
            min_contrast=float(seg.get("min_contrast", 2.0)),
            profile_sign=int(seg.get("profile_sign", 1)),
            tile_lines=int(seg.get("tile_lines", 2000)),
        ),
        storage=StorageConfig(
            data_dir=Path(sto.get("data_dir", "./data")),
            image_format=sto.get("image_format", "jpg"),
            jpeg_quality=int(sto.get("jpeg_quality", 92)),
            rotate=bool(sto.get("rotate", True)),
            flip_vertical=bool(sto.get("flip_vertical", False)),
            flip_horizontal=bool(sto.get("flip_horizontal", False)),
            queue_size=int(sto.get("queue_size", 16)),
        ),
    )


def format_value(v: Any) -> str:
    """One YAML scalar, written so that load_config reads back the same value."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(float(f"{v:.6g}"))  # keeps the ".0" so YAML reads a float
    s = str(v)
    return s if re.fullmatch(r"[A-Za-z0-9_./-]+", s) else json.dumps(s)


def save_values(path: str | Path, updates: dict[str, dict[str, Any]]) -> None:
    """Set `section.key: value` in a YAML file in place, keeping comments and layout.

    Only rewrites the value part of existing lines; a key missing from its section is
    appended to that section.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    for section, values in updates.items():
        m = re.search(rf"^{re.escape(section)}:[ \t]*(?:#.*)?\n((?:[ \t]+.*\n|[ \t]*\n)*)",
                      text, re.M)
        if m is None:
            raise KeyError(f"section {section!r} not found in {path}")
        body = m.group(1)
        for key, value in values.items():
            v = format_value(value)
            line = re.compile(rf"^([ \t]+{re.escape(key)}:[ \t]*)([^#\n]*?)([ \t]*(?:#.*)?)$", re.M)
            if line.search(body):
                body = line.sub(lambda mm: f"{mm.group(1)}{v}{mm.group(3)}", body, count=1)
            else:
                content = body.rstrip("\n")
                body = f"{content}\n  {key}: {v}{body[len(content):] or chr(10)}"
        text = text[:m.start(1)] + body + text[m.end(1):]
    path.write_text(text, encoding="utf-8")
