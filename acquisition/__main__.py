"""Acquisition CLI.

    python -m acquisition capture [--sim] [--mode birds|tiles] [--seconds N]
    python -m acquisition calibrate [--sim] [--seconds N]
    python -m acquisition view [--sim] [--seconds N]

`capture` grabs, cuts one image per bird (or fixed tiles) and saves them under
data/YYYYMMDD/. `calibrate` grabs for a while and reports the bird period actually seen
in the stream, to check line_rate_hz / birds_per_min before a real run. `view` opens a
live preview with exposure / line-rate knobs, for aiming and tuning the rig at the line
(needs a GUI build of opencv — opencv-python, not -headless).
"""
from __future__ import annotations

import argparse
import logging
import time

import numpy as np

from .camera import BaslerLineScan, SimulatedLineScan
from .config import DEFAULT_CONFIG, Config, load_config
from .segmenter import PeriodicSegmenter, TileSegmenter, estimate_period, line_profile
from .writer import AsyncWriter

log = logging.getLogger("acquisition")


def make_source(cfg: Config, sim: bool):
    if sim:
        period = cfg.segmentation.nominal_period(cfg.camera.line_rate_hz)
        return SimulatedLineScan(lines_per_frame=cfg.camera.lines_per_frame, period=period,
                                 speed_error=0.02, line_rate_hz=cfg.camera.line_rate_hz)
    return BaslerLineScan(cfg.camera)


def make_segmenter(cfg: Config, mode: str):
    s = cfg.segmentation
    if mode == "tiles":
        return TileSegmenter(s.tile_lines)
    return PeriodicSegmenter(s.nominal_period(cfg.camera.line_rate_hz), s.window_factor,
                             s.track_gain, s.min_contrast, s.profile_sign)


def capture(cfg: Config, sim: bool, mode: str, seconds: float | None) -> None:
    seg = make_segmenter(cfg, mode)
    writer = AsyncWriter(cfg.storage).start()
    cut = lost = 0
    t0 = last_report = time.monotonic()
    try:
        with make_source(cfg, sim) as src:
            blocks = src.blocks()
            try:
                for block in blocks:
                    lost += block.lost_frames
                    for win in seg.push(block):
                        writer.submit(win)
                        cut += 1
                    now = time.monotonic()
                    if now - last_report >= 10:
                        last_report = now
                        log.info("cut %d, written %d, dropped %d, lost frames %d, period %.1f",
                                 cut, writer.written, writer.dropped, lost,
                                 getattr(seg, "period", 0.0))
                    if seconds is not None and now - t0 >= seconds:
                        break
            finally:
                blocks.close()  # stop grabbing before the camera is closed
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        writer.stop()
        log.info("done: cut %d, written %d, dropped %d, failed %d, lost frames %d",
                 cut, writer.written, writer.dropped, writer.failed, lost)


def calibrate(cfg: Config, sim: bool, seconds: float) -> None:
    nominal = cfg.segmentation.nominal_period(cfg.camera.line_rate_hz)
    profiles = []
    t0 = time.monotonic()
    with make_source(cfg, sim) as src:
        blocks = src.blocks()
        try:
            for block in blocks:
                profiles.append(line_profile(block.lines, cfg.segmentation.profile_sign))
                if time.monotonic() - t0 >= seconds:
                    break
        finally:
            blocks.close()
    profile = np.concatenate(profiles)
    if len(profile) < 4 * nominal:
        raise SystemExit(f"only {len(profile)} lines — grab longer (need ≥ {4 * nominal:.0f})")
    period = estimate_period(profile, 0.5 * nominal, 1.5 * nominal)
    bpm = cfg.camera.line_rate_hz * 60 / period
    print(f"lines grabbed     : {len(profile)}")
    print(f"nominal period    : {nominal:.1f} lines")
    print(f"measured period   : {period:.1f} lines  ({100 * (period / nominal - 1):+.1f}%)")
    print(f"implied line speed: {bpm:.1f} birds/min at {cfg.camera.line_rate_hz:.0f} lines/s")
    print(f"profile contrast  : {np.ptp(profile):.1f} grey levels")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m acquisition")
    ap.add_argument("command", choices=["capture", "calibrate", "view"])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--sim", action="store_true", help="use the simulator instead of the camera")
    ap.add_argument("--mode", choices=["birds", "tiles"], default=None,
                    help="default: from config (periodic → birds)")
    ap.add_argument("--seconds", type=float, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    if args.command == "calibrate":
        calibrate(cfg, args.sim, args.seconds or 10.0)
    elif args.command == "view":
        from pathlib import Path
        from .viewer import run as run_viewer
        run_viewer(cfg, Path(args.config), args.sim, args.seconds)
    else:
        mode = args.mode or ("tiles" if cfg.segmentation.method == "tiles" else "birds")
        capture(cfg, args.sim, mode, args.seconds)


if __name__ == "__main__":
    main()
