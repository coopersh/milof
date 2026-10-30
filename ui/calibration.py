"""Flat-field (PRNU) and user-set operations for the calibration wizard.

GenICam node names for shading correction differ between Basler families and firmware
(e.g. `ShadingCorrectionSelector` vs `BslShadingCorrection...`), so the nodes are found
by name pattern and shown to the operator before anything is executed. All functions take
the camera object and must run on the camera thread.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Preferred verbs for the command that builds the shading correction, best first.
_CREATE_VERBS = ("create", "execute", "collect", "generate", "start", "acquire")
_NOT_CREATE = ("load", "save", "reset", "clear", "default", "abort", "stop")


@dataclass
class Nodes:
    color_space: str | None = None
    light_preset: str | None = None
    shading_selector: str | None = None
    shading_mode: str | None = None
    shading_create: str | None = None
    shading_create_status: str | None = None  # Active while the correction is being built
    shading_create_result: str | None = None  # NoError / ErrorRange / ... once done
    user_set_selector: str | None = None
    user_set_save: str | None = None
    user_set_default: str | None = None
    shading_commands: list[str] = field(default_factory=list)  # every candidate, for display

    def missing_for_prnu(self) -> list[str]:
        need = ("shading_selector", "shading_mode", "shading_create")
        return [k for k in need if getattr(self, k) is None]

    def missing_for_user_set(self) -> list[str]:
        need = ("user_set_selector", "user_set_save", "user_set_default")
        return [k for k in need if getattr(self, k) is None]


def discover(cam) -> Nodes:
    kinds = cam.node_kinds()

    def find(kind: str, *parts: str) -> str | None:
        hits = [n for n, k in kinds.items()
                if k == kind and all(p.lower() in n.lower() for p in parts)]
        return min(hits, key=len) if hits else None  # shortest = least specialised

    shading_cmds = sorted(n for n, k in kinds.items()
                          if k == "command" and "shading" in n.lower())
    create = None
    for verb in _CREATE_VERBS:
        hits = [n for n in shading_cmds if verb in n.lower()
                and not any(x in n.lower() for x in _NOT_CREATE)]
        if hits:
            create = min(hits, key=len)
            break
    return Nodes(
        color_space=find("enum", "ColorSpace"),
        light_preset=find("enum", "LightSource", "Preset"),
        shading_selector=find("enum", "ShadingCorrection", "Selector"),
        shading_mode=find("enum", "ShadingCorrection", "Mode"),
        shading_create=create,
        shading_create_status=find("enum", "ShadingCorrection", "CreateStatus"),
        shading_create_result=find("enum", "ShadingCorrection", "CreateResult"),
        user_set_selector=find("enum", "UserSetSelector"),
        user_set_save=find("command", "UserSetSave"),
        user_set_default=find("enum", "UserSetDefault"),
        shading_commands=shading_cmds,
    )


def _option(cam, node: str, wanted: str) -> str:
    """The enum entry of `node` matching `wanted` case-insensitively."""
    options = cam.enum_options(node)
    for o in options:
        if o.lower() == wanted.lower():
            return o
    raise ValueError(f"{node} has no option {wanted!r} (options: {', '.join(options)})")


def run_prnu(cam, nodes: Nodes) -> list[str]:
    """Color space and light preset off, selector PRNU, mode User, then create.

    The camera must be looking at an evenly lit white target, defocused, belt stopped.
    Returns the steps applied, for the calibration log.
    """
    missing = nodes.missing_for_prnu()
    if missing:
        raise RuntimeError(f"camera nodes not found: {', '.join(missing)}")
    steps = []
    for node in (nodes.color_space, nodes.light_preset):
        if node is not None:
            cam.set(node, _option(cam, node, "Off"))
            steps.append(f"{node}=Off")
    cam.set(nodes.shading_selector, _option(cam, nodes.shading_selector, "PRNU"))
    steps.append(f"{nodes.shading_selector}=PRNU")
    cam.set(nodes.shading_mode, _option(cam, nodes.shading_mode, "User"))
    steps.append(f"{nodes.shading_mode}=User")
    cam.execute(nodes.shading_create, wait_s=60.0)
    steps.append(f"{nodes.shading_create}()")
    _wait_for_create(cam, nodes, timeout_s=60.0)
    if nodes.shading_create_result is not None:
        result = str(cam.get(nodes.shading_create_result))
        steps.append(f"{nodes.shading_create_result}={result}")
        if result.lower() != "noerror":
            raise RuntimeError(f"shading correction failed: {nodes.shading_create_result}={result}")
    return steps


def _wait_for_create(cam, nodes: Nodes, timeout_s: float) -> None:
    """IsDone on the command can return before the camera finishes; poll the status node."""
    if nodes.shading_create_status is None:
        return
    deadline = time.monotonic() + timeout_s
    while True:
        status = str(cam.get(nodes.shading_create_status))
        if status.lower() != "active":
            if status.lower().startswith("error"):  # e.g. ErrorReverseX
                raise RuntimeError(
                    f"shading correction failed: {nodes.shading_create_status}={status}")
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"{nodes.shading_create_status} still Active after {timeout_s:.0f} s")
        time.sleep(0.2)


def save_user_set(cam, nodes: Nodes, user_set: str = "UserSet1") -> list[str]:
    """Save the current camera settings to `user_set` and load it at power-up."""
    missing = nodes.missing_for_user_set()
    if missing:
        raise RuntimeError(f"camera nodes not found: {', '.join(missing)}")
    target = _option(cam, nodes.user_set_selector, user_set)
    cam.set(nodes.user_set_selector, target)
    cam.execute(nodes.user_set_save, wait_s=30.0)
    cam.set(nodes.user_set_default, _option(cam, nodes.user_set_default, user_set))
    return [f"{nodes.user_set_selector}={target}", f"{nodes.user_set_save}()",
            f"{nodes.user_set_default}={target}"]


def read_back(cam, node: str | None) -> str | None:
    """A node's current value as text, or None if it is absent or unreadable."""
    if node is None:
        return None
    try:
        return str(cam.get(node))
    except Exception:  # noqa: BLE001 — a missing value is reported, not raised
        return None


# --- checks on what a calibration step produced: [{"ok": bool, "msg": str}, ...] ---
# They only advise the operator; nothing is blocked on them.

FLAT_MAX = 6.0  # grey levels of spread along the sensor on the white sheet


def check(ok: bool, msg: str) -> dict:
    return {"ok": bool(ok), "msg": msg}


def check_prnu(nodes: Nodes, mode: str | None, before: dict | None, after: dict | None) -> list[dict]:
    out = []
    if nodes.shading_mode is not None:
        out.append(check(mode is not None and mode.lower() == "user",
                         f"camera reports {nodes.shading_mode}={mode or 'unreadable'}"))
    if after is None:
        out.append(check(False, "no flatness measurement after PRNU"))
    else:
        was = f"{before['spread']:.1f} → " if before else ""
        out.append(check(after["spread"] <= FLAT_MAX,
                         f"spread along the sensor {was}{after['spread']:.1f} grey levels "
                         f"(at most {FLAT_MAX:.0f})"))
    return out


def check_user_set(nodes: Nodes, default: str | None, user_set: str = "UserSet1") -> list[dict]:
    return [check(default is not None and default.lower() == user_set.lower(),
                  f"power-up default {nodes.user_set_default}={default or 'unreadable'}")]


def check_board(gray) -> list[dict]:
    """`gray`: the saved board image as (lines along the motion, sensor pixels), uint8."""
    g = gray.astype(np.float32)
    mean = float(g.mean())
    sat = float((gray >= 255).mean() * 100)
    out = [check(40 <= mean <= 230 and sat < 1.0,
                 f"brightness: mean {mean:.0f}, saturated {sat:.1f}%"
                 + ("" if 40 <= mean <= 230 else " — too dark" if mean < 40 else " — too bright"))]
    # a board on the belt shows as lines with texture across the sensor
    texture = g.std(axis=1)
    thr = max(8.0, 0.5 * float(np.percentile(texture, 95)))
    rows = np.flatnonzero(texture > thr)
    if len(rows) < 0.05 * len(g):
        out.append(check(False, "no board found in the image — click as the board reaches the camera"))
        return out
    edge = max(2, int(0.02 * len(g)))
    cut = rows[0] < edge or rows[-1] >= len(g) - edge
    out.append(check(not cut, "board fits in the image" if not cut else
                     "board touches the start or end of the image — it may be cut off; try again"))
    # a board that did not move gives identical lines: no detail along the motion axis
    along = float(np.abs(np.diff(g[rows], axis=0)).mean())
    across = float(np.abs(np.diff(g[rows], axis=1)).mean())
    moving = along >= 0.1 * max(across, 1e-6)
    out.append(check(moving, "board moved under the camera" if moving else
                     "every line is the same — the board must move under the camera"))
    return out


class CalibrationLog:
    """Append-only JSON-lines record of calibration actions and the measurements at the time."""

    def __init__(self, path: Path):
        self.path = path

    def append(self, action: str, **details) -> dict:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, **details}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]

    def last(self, action: str) -> dict | None:
        for e in reversed(self.entries(limit=1000)):
            if e["action"] == action:
                return e
        return None
