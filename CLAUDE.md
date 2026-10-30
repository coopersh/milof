# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What the system does

Vision-based decision support for veterinary inspection on the **Milouf (מילועוף)** poultry
slaughter line. Today several inspectors check each carcass visually as it passes; this is
costly, subjective (large inter-inspector variance) and poorly documented. Milof images every
carcass, flags visible defects, and shows the inspector a recommendation.

**The inspector decides.** The system only recommends (full/partial condemnation or referral
for further inspection). Never build a path that condemns a carcass automatically —
veterinary regulation requires the human decision.

## Line facts (measured / stated by the site)

- **156 birds/min** ≈ 2.6 birds/s ≈ **385 ms per bird**.
- **One side only** is visible to the camera.
- Camera: **Basler racer 2 S r2L4096-14gc** — 4k colour line-scan, GigE (PoE), 12 mm or
  8.5 mm lens, two 500 mm IP67 light bars. The 2D image is built line by line as the shackle
  line moves past, so line triggering should follow conveyor speed (encoder) — otherwise speed
  changes stretch/compress the image. No encoder is fitted yet (free-run for now).
- Rig BOM, wiring, optics: `docs/hardware_setup.md`.
- Shackle pitch, camera distance, encoder availability: **TBD** — see `docs/line_requirements.md`.

## Target defects (phase 1)

1. Wing fractures (שברי כנפיים)
2. Bruises / haemorrhages (שטפי דם)
3. Skin defects (פגמי עור)
4. Fecal contamination (זיהום צואתי) — hardest in RGB; do not promise parity with the others.

Definitions, thresholds and borderline cases live in `docs/defects.md` and are agreed with the
veterinary services. Code must read categories from `config/defects.yaml`, never hard-code them.

## Phases

| Phase | Deliverable | Real-time? |
|---|---|---|
| 1 — Feasibility | Acquisition rig, ≥500 labelled examples per defect, first models, **retrospective** demo on real line data | No |
| 2 — Real-time | On-line detection of the ~4 defects + distribution / basic reports | Yes |
| 3 — Full system | All metrics, real-time alerts, trend reports, integration | Yes |

Phase 1 does **not** need carcass identification: it is enough to show a defect was found on
*a* bird. Each bird is a separate image cut from the continuous line-scan stream.

**Birds overlap on the line** — there is no background gap between them. Segmentation
(`acquisition/segmenter.py`) therefore cuts a fixed window (1.5 periods) around a predicted
centre one bird-period apart, and corrects each centre from the per-line brightness
profile so the cut follows line-speed drift (free-run has no encoder; a 0.5% speed error
would otherwise slip a bird every 200). Neighbours appear at the window edges by design.

## Architecture (phase 1)

```
Basler line-scan ──> acquisition/  (grab lines, cut stream into one image per bird,
                                    async save + daily CSV log)
                     labeling/     (export to labelling tool, import labels)
                     models/       (train / evaluate per-defect detectors)
                     review/       (run models on saved data, retrospective report)
                     ui/           (station web UI: live view, settings, capture, calibration wizard)
config/  camera + defect category definitions
docs/    defect definitions, line requirements, calibration procedure
```

## Design rules

- **Never block the capture path.** At 385 ms/bird a stall loses birds. Disk writes, uploads,
  and inference run on separate threads/processes fed by queues.
- Captured images never go in git (`data/` is ignored).
- One image + one log row per bird; daily folders `data/YYYYMMDD/`.
- Inspector input (button press on condemnation) is the ground-truth signal — log it with a
  timestamp so it can be matched to the bird image.

## Running acquisition

```bash
python -m acquisition calibrate --sim          # measured bird period vs config
python -m acquisition capture --sim --seconds 20
python -m acquisition capture                  # real camera (needs pypylon: pip install -e ".[camera]")
python -m acquisition capture --mode tiles     # bench: fixed chunks, no bird cutting
```

Output: `data/YYYYMMDD/images/*.jpg` + `data/YYYYMMDD/capture_log.csv` (one row per image:
start line, profile contrast, tracking error, current period).

Threads: capture loop (grab → segment) on the main thread; `AsyncWriter` thread does
rotation, JPEG encoding and disk I/O. `submit()` never blocks — a full queue drops and
counts the image.

## Station UI

```bash
python -m ui --sim             # simulator, http://127.0.0.1:8000
python -m ui                   # real camera (pip install -e ".[camera,ui]")
```

One "camera" thread in `ui/service.py` owns the camera (grab, preview ring, segmenter,
writer); HTTP handlers reach it only through `CameraService.call()`, and build previews and
stats from copies — so a slow request never stalls the grab loop. The calibration wizard
(`ui/calibration.py`) finds shading-correction / user-set nodes by name pattern and shows
them before executing; procedure in `docs/calibration.md`. Settings save back to
`config/camera.yaml` with comments kept (`acquisition.config.save_values`), backup under
`data/config_backups/`. No auth: bind to 127.0.0.1 unless on the isolated camera network.

## Testing

```bash
pytest tests/
```

Tests run on `SimulatedLineScan` (overlapping synthetic birds, configurable speed error) —
no camera or pypylon needed.
