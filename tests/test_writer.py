import csv
import time
from dataclasses import replace

import cv2
import numpy as np

from acquisition.config import load_config
from acquisition.segmenter import BirdWindow
from acquisition.writer import LOG_NAME, AsyncWriter


def storage(tmp_path, **kw):
    return replace(load_config().storage, data_dir=tmp_path, **kw)


def window(seq, n_lines=120, width=64):
    lines = np.full((n_lines, width, 3), 100, dtype=np.uint8)
    return BirdWindow(seq, "bird", seq * n_lines, lines, time.time())


def test_writes_upright_images_and_daily_log(tmp_path):
    w = AsyncWriter(storage(tmp_path)).start()
    for i in range(3):
        assert w.submit(window(i))
    w.stop()
    assert (w.written, w.dropped, w.failed) == (3, 0, 0)

    (day,) = tmp_path.iterdir()
    rows = list(csv.DictReader((day / LOG_NAME).open()))
    assert [r["seq"] for r in rows] == ["0", "1", "2"]
    img = cv2.imread(str(day / rows[0]["file"]))
    assert img.shape == (64, 120, 3)  # rotated: sensor line vertical, time horizontal


def test_full_queue_drops_instead_of_blocking(tmp_path):
    w = AsyncWriter(storage(tmp_path, queue_size=1))  # not started: nothing drains
    assert w.submit(window(0))
    assert not w.submit(window(1))
    assert w.dropped == 1
