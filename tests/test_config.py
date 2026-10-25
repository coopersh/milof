from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config"


def test_defects_yaml_has_four_unique_ids():
    defects = yaml.safe_load((CONFIG / "defects.yaml").read_text())["defects"]
    ids = [d["id"] for d in defects]
    assert len(ids) == 4
    assert len(set(ids)) == len(ids)


def test_camera_yaml_loads():
    cfg = yaml.safe_load((CONFIG / "camera.yaml").read_text())
    assert cfg["camera"]["trigger"] in ("encoder", "free_run")
