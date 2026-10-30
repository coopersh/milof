from pathlib import Path

import yaml

from acquisition.config import save_values

CONFIG = Path(__file__).resolve().parent.parent / "config"


def test_defects_yaml_has_four_unique_ids():
    defects = yaml.safe_load((CONFIG / "defects.yaml").read_text(encoding="utf-8"))["defects"]
    ids = [d["id"] for d in defects]
    assert len(ids) == 4
    assert len(set(ids)) == len(ids)


def test_camera_yaml_loads():
    cfg = yaml.safe_load((CONFIG / "camera.yaml").read_text(encoding="utf-8"))
    assert cfg["camera"]["trigger"] in ("encoder", "free_run")


def test_save_values_rewrites_values_and_keeps_comments(tmp_path):
    path = tmp_path / "camera.yaml"
    path.write_text(
        "camera:\n"
        "  line_rate_hz: 3000      # lines per second\n"
        "  exposure_us: null       # null = keep camera value\n"
        "  reverse_x: false\n"
        "\n"
        "storage:\n"
        "  rotate: true\n",
        encoding="utf-8",
    )
    save_values(path, {"camera": {"line_rate_hz": 2500.0, "exposure_us": 330.0,
                                  "packet_size_bytes": 1500},
                       "storage": {"rotate": False}})
    text = path.read_text(encoding="utf-8")
    assert "  line_rate_hz: 2500.0      # lines per second\n" in text
    assert "  exposure_us: 330.0       # null = keep camera value\n" in text
    assert "  packet_size_bytes: 1500\n\nstorage:" in text  # appended inside its own section
    raw = yaml.safe_load(text)
    assert raw["camera"]["line_rate_hz"] == 2500.0
    assert raw["camera"]["reverse_x"] is False
    assert raw["storage"]["rotate"] is False
