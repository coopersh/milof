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
- Camera: **Basler line-scan** (model TBD). The 2D image is built line by line as the shackle
  line moves past, so line triggering must follow conveyor speed (encoder) — otherwise speed
  changes stretch/compress the image.
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

## Architecture (phase 1)

```
Basler line-scan ──> acquisition/  (grab lines, cut stream into one image per bird,
                                    async save + daily CSV log)
                     labeling/     (export to labelling tool, import labels)
                     models/       (train / evaluate per-defect detectors)
                     review/       (run models on saved data, retrospective report)
config/  camera + defect category definitions
docs/    defect definitions, line requirements
```

## Design rules

- **Never block the capture path.** At 385 ms/bird a stall loses birds. Disk writes, uploads,
  and inference run on separate threads/processes fed by queues.
- Captured images never go in git (`data/` is ignored).
- One image + one log row per bird; daily folders `data/YYYYMMDD/`.
- Inspector input (button press on condemnation) is the ground-truth signal — log it with a
  timestamp so it can be matched to the bird image.

## Testing

```bash
pytest tests/
```
