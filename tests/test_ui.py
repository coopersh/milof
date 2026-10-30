"""UI service and API against the simulator (no camera, no browser)."""
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from acquisition.config import load_config
from ui.server import create_app
from ui.service import CameraService, UserError

CONFIG = Path(__file__).resolve().parent.parent / "config" / "camera.yaml"


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture
def service(tmp_path):
    config_path = tmp_path / "camera.yaml"
    shutil.copy(CONFIG, config_path)
    cfg = load_config(config_path)
    cfg.storage.data_dir = tmp_path / "data"
    svc = CameraService(cfg, config_path, sim=True, realtime=False).start()
    wait_for(lambda: svc.blocks >= 20)
    yield svc
    svc.shutdown()


def test_preview_and_stats(service):
    img = cv2.imdecode(np.frombuffer(service.preview_jpeg(400), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (400, 400, 3)  # rotated strip, square pixels restored
    s = service.stats()
    assert 0 < s["mean"] < 255
    assert len(s["hist"]) == 64
    assert len(s["profile"]["r"]) == 512


def test_capture_writes_one_image_per_cut(service, tmp_path):
    service.start_capture("birds")
    wait_for(lambda: service.status()["capture"]["written"] >= 3)
    summary = service.stop_capture()
    assert summary["dropped"] == 0 and summary["failed"] == 0
    assert summary["written"] == summary["cut"]
    assert len(list((tmp_path / "data").glob("*/images/*.jpg"))) == summary["written"]
    assert service.recent() and service.recent_jpeg(service.recent()[-1]["seq"])


def test_capture_blocks_geometry_changes(service):
    service.start_capture("birds")
    try:
        with pytest.raises(UserError):
            service.set_camera({"lines_per_frame": 128})
        service.set_camera({"exposure_us": 200})  # exposure may change mid-run
    finally:
        service.stop_capture()


def test_prnu_flattens_white(service):
    service.set_sim_scene("white")
    time.sleep(0.5)
    assert service.stats()["flat"]["spread"] > 20  # lens/light fall-off before correction
    service.run_prnu()
    wait_for(lambda: service.stats()["flat"]["spread"] < 3)
    assert service.calibration_info()["log"][-1]["action"] == "prnu"


def test_api_rejects_out_of_range_values(service):
    client = TestClient(create_app(service))
    r = client.post("/api/settings/camera", json={"line_rate_hz": 99999})
    assert r.status_code == 400
    assert "line_rate_hz" in r.json()["detail"]
    assert client.get("/api/status").json()["source"] == "sim"


def test_warns_when_exposure_caps_line_rate(service):
    rate = service.cfg.camera.line_rate_hz
    service.set_camera({"exposure_us": 0.9e6 / rate})
    assert service.status()["warning"] is None
    service.set_camera({"exposure_us": 1.3e6 / rate})
    assert "µs" in service.status()["warning"]


def test_save_config_keeps_comments_and_backs_up(service):
    service.set_camera({"exposure_us": 300})
    result = service.save_config()
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "exposure_us: 300.0" in text
    assert "# null = keep camera value" in text
    assert Path(result["backup"]).exists()
