import numpy as np
import pytest

from acquisition.camera import Block, SimulatedLineScan
from acquisition.segmenter import (PeriodicSegmenter, TileSegmenter, estimate_period,
                                   line_profile)

PERIOD = 300.0


def indexed_block(start, n, width=4):
    """Lines whose pixels encode their own global line index (ch0 low byte, ch1 high byte)."""
    idx = np.arange(start, start + n)
    lines = np.zeros((n, width, 3), dtype=np.uint8)
    lines[:, :, 0] = (idx % 256)[:, None]
    lines[:, :, 1] = (idx // 256 % 256)[:, None]
    return Block(start, lines)


def decode(lines):
    return lines[:, 0, 0].astype(int) + 256 * lines[:, 0, 1].astype(int)


def feed(seg, blocks):
    out = []
    for b in blocks:
        out.extend(seg.push(b))
    return out


def blocks_of(sizes):
    start = 0
    for n in sizes:
        yield indexed_block(start, n)
        start += n


def test_tiles_are_contiguous_across_uneven_blocks():
    wins = feed(TileSegmenter(500), blocks_of([123, 77, 400, 256, 256, 1000, 37]))
    assert [w.start_line for w in wins] == [0, 500, 1000, 1500]  # 2149 lines → 4 full tiles
    for w in wins:
        np.testing.assert_array_equal(decode(w.lines), np.arange(w.start_line, w.start_line + 500))


def test_fixed_period_without_tracking():
    seg = PeriodicSegmenter(PERIOD, window_factor=1.5, track_gain=0.0)
    wins = feed(seg, blocks_of([256] * 40))
    starts = [w.start_line for w in wins]
    assert np.all(np.diff(starts) == PERIOD)
    for w in wins:
        assert w.lines.shape[0] == 450
        np.testing.assert_array_equal(decode(w.lines), np.arange(w.start_line, w.start_line + 450))


def test_lost_frame_leaves_black_gap_without_crashing():
    blocks = list(blocks_of([256] * 20))
    del blocks[5]  # frame lost on the wire; line indices of later blocks keep real time
    wins = feed(TileSegmenter(1000), blocks)
    gap = wins[1].lines[256 * 5 - 1000:256 * 6 - 1000]
    assert not gap.any()


def match_birds(wins, sim):
    """Index of the true bird nearest each window centre, and the offset to it."""
    ks, offsets = [], []
    for w in wins:
        c = w.start_line + w.lines.shape[0] / 2
        k = int(round((c - sim.phase) / sim.true_period - 0.5))
        ks.append(k)
        offsets.append(c - sim.bird_center(k))
    return np.array(ks), np.array(offsets)


@pytest.mark.parametrize("speed_error", [-0.03, 0.0, 0.03])
def test_tracking_follows_line_speed_drift(speed_error):
    sim = SimulatedLineScan(width=64, period=PERIOD, bird_factor=1.25,
                            speed_error=speed_error, phase=80)
    seg = PeriodicSegmenter(PERIOD, window_factor=1.5, track_gain=0.3)
    wins = feed(seg, sim.blocks(total_lines=int(PERIOD * 300)))
    ks, offsets = match_birds(wins, sim)
    # exactly one window per bird, none skipped or doubled
    assert np.all(np.diff(ks) == 1)
    # after settling, each bird sits near the window centre
    assert np.abs(offsets[20:]).max() < 0.1 * PERIOD
    assert seg.period == pytest.approx(sim.true_period, rel=0.01)


def test_without_tracking_drift_slips_birds():
    sim = SimulatedLineScan(width=64, period=PERIOD, speed_error=0.03)
    seg = PeriodicSegmenter(PERIOD, track_gain=0.0)
    ks, _ = match_birds(feed(seg, sim.blocks(total_lines=int(PERIOD * 300))), sim)
    assert not np.all(np.diff(ks) == 1)


def test_estimate_period_on_overlapping_birds():
    sim = SimulatedLineScan(width=64, period=PERIOD, speed_error=0.02)
    profile = np.concatenate([line_profile(b.lines) for b in sim.blocks(int(PERIOD * 30))])
    assert estimate_period(profile, 0.5 * PERIOD, 1.5 * PERIOD) == pytest.approx(
        sim.true_period, rel=0.01)
