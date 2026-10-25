"""Cut the continuous line-scan stream into one image per bird.

Birds overlap on the shackle line, so there is no background gap to cut on. Instead each
bird is a fixed window centred on a predicted position one period after the previous one.
With a free-running line rate the real period drifts from the nominal one (line speed is
never exact), and a 0.5% error alone slips a whole bird every 200 birds — so every cut is
corrected from the per-line brightness profile: bird bodies are brightest at their centre
(lights fall off behind the birds), so the profile peak near the prediction is the measured
centre. A small fraction of the error also adjusts the period (a 2nd-order tracking loop).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .camera import Block


@dataclass
class BirdWindow:
    seq: int
    kind: str  # "bird" | "tile"
    start_line: int
    lines: np.ndarray  # (window, width, 3); missing lines (lost frames) are black
    timestamp: float
    contrast: float = 0.0
    error_lines: float = 0.0  # measured - predicted centre (0 when not tracked)
    period: float = 0.0


def line_profile(lines: np.ndarray, sign: int = 1) -> np.ndarray:
    """Mean brightness per line (0..255); inverted when birds are darker than background."""
    m = lines[:, ::8, :].mean(axis=(1, 2))
    return m if sign > 0 else 255.0 - m


class _LineBuffer:
    """Recent blocks + their profiles; cuts any [start, end) range, zero-filling gaps."""

    def __init__(self):
        self._items: deque[tuple[Block, np.ndarray]] = deque()

    def append(self, block: Block, profile: np.ndarray) -> None:
        self._items.append((block, profile))

    @property
    def end(self) -> int:
        if not self._items:
            return 0
        b = self._items[-1][0]
        return b.start_line + b.lines.shape[0]

    def lost_frames(self, start: int, end: int) -> int:
        return sum(b.lost_frames for b, _ in self._items if start < b.start_line < end)

    def slice(self, start: int, end: int) -> np.ndarray:
        first = self._items[0][0].lines
        out = np.zeros((end - start, first.shape[1], first.shape[2]), dtype=first.dtype)
        for b, _ in self._items:
            self._copy(out, b.lines, b.start_line, start, end)
        return out

    def profile(self, start: int, end: int) -> np.ndarray:
        out = np.zeros(end - start)
        for b, p in self._items:
            self._copy(out, p, b.start_line, start, end)
        return out

    @staticmethod
    def _copy(out, src, src_start, start, end):
        lo, hi = max(start, src_start), min(end, src_start + src.shape[0])
        if lo < hi:
            out[lo - start:hi - start] = src[lo - src_start:hi - src_start]

    def drop_before(self, line: int) -> None:
        while self._items:
            b = self._items[0][0]
            if b.start_line + b.lines.shape[0] > line:
                break
            self._items.popleft()


class PeriodicSegmenter:
    """One window per bird, `window_factor` periods long, centred on the tracked bird centre."""

    def __init__(
        self,
        nominal_period: float,
        window_factor: float = 1.5,
        track_gain: float = 0.3,
        min_contrast: float = 2.0,
        profile_sign: int = 1,
        period_tolerance: float = 0.1,
    ):
        self.period = float(nominal_period)
        self._nominal = float(nominal_period)
        self._tol = period_tolerance
        self.gain = track_gain
        self.min_contrast = min_contrast
        self.sign = profile_sign
        self.window = int(round(nominal_period * window_factor))
        self._half = self.window // 2
        self._search = int(nominal_period / 3)
        self._smooth = max(1, int(nominal_period / 8))
        self._margin = self._half + self._search + self._smooth
        self._next_center = float(self._margin)
        self._seq = 0
        self._buf = _LineBuffer()

    def push(self, block: Block) -> list[BirdWindow]:
        self._buf.append(block, line_profile(block.lines, self.sign))
        out = []
        while self._buf.end >= int(round(self._next_center)) + self._margin:
            out.append(self._cut())
        return out

    def _measure(self, predicted: int) -> tuple[float | None, float]:
        lo = predicted - self._search - self._smooth
        p = self._buf.profile(lo, predicted + self._search + self._smooth + 1)
        k = 2 * self._smooth + 1
        sm = np.convolve(p, np.ones(k) / k, mode="valid")  # sm[i] ↔ line lo + smooth + i
        contrast = float(sm.max() - sm.min())
        if contrast < self.min_contrast:
            return None, contrast  # empty shackles / no light: keep the prediction
        top = np.flatnonzero(sm >= sm.max() - 0.1 * contrast)  # centre of a flat peak
        return lo + self._smooth + float(top.mean()), contrast

    def _cut(self) -> BirdWindow:
        predicted = self._next_center
        center, error, contrast = predicted, 0.0, 0.0
        if self.gain > 0:
            measured, contrast = self._measure(int(round(predicted)))
            if measured is not None:
                error = measured - predicted
                center = predicted + self.gain * error
                p = self.period + 0.1 * self.gain * error
                self.period = float(np.clip(p, self._nominal * (1 - self._tol),
                                            self._nominal * (1 + self._tol)))
        start = int(round(center)) - self._half
        win = BirdWindow(
            seq=self._seq,
            kind="bird",
            start_line=start,
            lines=self._buf.slice(start, start + self.window),
            timestamp=time.time(),
            contrast=contrast,
            error_lines=error,
            period=self.period,
        )
        self._seq += 1
        self._next_center = center + self.period
        self._buf.drop_before(int(self._next_center) - self._margin - 1)
        return win


class TileSegmenter:
    """Fixed consecutive chunks, no tracking — for bench tests and looking at raw stream."""

    def __init__(self, tile_lines: int):
        self.tile = tile_lines
        self._seq = 0
        self._buf = _LineBuffer()

    def push(self, block: Block) -> list[BirdWindow]:
        self._buf.append(block, np.zeros(block.lines.shape[0]))
        out = []
        while self._buf.end >= (self._seq + 1) * self.tile:
            start = self._seq * self.tile
            out.append(BirdWindow(self._seq, "tile", start,
                                  self._buf.slice(start, start + self.tile), time.time()))
            self._seq += 1
            self._buf.drop_before(self._seq * self.tile)
        return out


def estimate_period(profile: np.ndarray, min_period: float, max_period: float) -> float:
    """Dominant repeat length of a brightness profile (autocorrelation peak)."""
    x = profile - profile.mean()
    n = len(x)
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    lo, hi = int(min_period), min(int(max_period) + 1, n - 1)
    return float(lo + np.argmax(ac[lo:hi]))
