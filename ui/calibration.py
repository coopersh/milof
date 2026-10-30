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
    return steps


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
