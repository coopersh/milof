# Milof — System Design

Vision-based decision support for veterinary inspection on the Milouf poultry slaughter line.

Status: **proposed design for phase 1**, with the phase 2 path it must not block.
Related: `hardware_setup.md` (rig, optics, wiring), `defects.md` (defect definitions),
`line_requirements.md` (site measurements).

---

## 1. Goals and non-goals

**Goals**
- Image every carcass on the line and keep the raw data intact.
- Phase 1: produce one image per bird, label ≥500 examples per defect, train first models and
  show a **retrospective** demo on real line data.
- Phase 2: detect the defects in real time and show the inspector a per-bird recommendation.

**Non-goals**
- **No automatic condemnation.** The system recommends; the veterinary inspector decides.
- No carcass identification (flock, supplier) in phase 1. A defect only has to be attributed
  to *a* bird.
- No real-time inference in phase 1.

## 2. Constraints

| Constraint | Value | Consequence |
|---|---|---|
| Line speed | 156 birds/min ≈ 385 ms/bird | Capture must never stall; all heavy work is off the capture path |
| View | One side of the carcass | Defects on the hidden side are out of scope |
| Birds overlap on the line | No background gap between birds | Birds cannot be separated by simple gaps (see §6) |
| Camera | Basler racer 2 S r2L4096-14gc, line-scan, GigE | The image is a continuous strip, not frames |
| Line trigger | Free-run (no encoder yet) | Line speed drift stretches/compresses birds slightly |
| Environment | Wet, cold, wash-down | Industrial PC/enclosure, IP67 lights, locked lens |

## 3. Key design decisions

1. **Capture and segmentation are separated.** The capture stage records the continuous strip
   as-is. Cutting it into birds happens afterwards, on the stored data. Capture then has one
   simple job that cannot go wrong in a way that loses data, and segmentation can be improved
   and re-run on the same recordings.
2. **The global line index is the master clock.** Every event is stored against the line
   index of the current recording session: image tiles, hook pulses, inspector presses and
   detections. Line index ↔ wall-clock time is a linear map (session start time + line rate).
3. **Birds are located by their hooks.** Every bird hangs from a shackle. A "hook passed"
   signal, from a photo-sensor or from detecting the shackle in the image, gives each bird's
   exact position in the strip. Image-based brightness tracking is the fallback when neither
   is available.
4. **Labels live in strip coordinates.** Labels drawn on a bird image are stored mapped back
   to (line index, pixel row). They then survive any later re-cut of the birds.
5. **Detection runs on the strip, not on bird crops.** In phase 2 the model runs as a sliding
   window over the continuous strip, and each detection is assigned to a bird through the
   hooks. Bird crops exist for labelling and per-bird reporting.

## 4. System overview

```
                    PHASE 1 (now)                                   PHASE 2 (later)

  ┌───────────── ON THE LINE (online) ─────────────┐
  │ racer 2 S ──► Capture ──► Strip tiles (JPEG)   │        Strip ──► Sliding-window
  │ hook sensor ─────────────► hooks.csv           │                  detector (GPU)
  │ inspector button ────────► inspector.csv       │                      │
  └────────────────────────┬───────────────────────┘                assign to hook
                           │ (files on disk)                              │
  ┌────────── OFFLINE (same PC or office) ─────────┐               per-bird result
  │ Bird segmentation ──► bird images + birds.csv  │                      │
  │ Labelling export/import ──► labels (strip coords)                Inspector display
  │ Training / evaluation ──► models               │                (recommendation)
  │ Retrospective review ──► report                │
  └────────────────────────────────────────────────┘
```

## 5. Online: capture

### 5.1 Responsibilities
- Grab line blocks from the camera.
- Write the strip to disk as **contiguous, non-overlapping tiles**, e.g. 2,000 lines each,
  as JPEG. Offline tools stitch neighbouring tiles, so the tiles do not need to overlap.
- Record hook pulses and inspector presses with their line index.
- Log lost frames and dropped writes. Never block.

### 5.2 Threads

```
grab thread ──► tile assembler ──► queue ──► writer thread (orient, JPEG, disk, CSV)
                    │
camera I/O line ────┴─► hook pulse ──► hooks.csv           (line index of each pulse)
button / key ──────────► inspector.csv                     (wall time → line index)
```

- The grab thread only copies lines into the current tile and hands full tiles to a bounded
  queue.
- When the queue is full, the tile is **dropped and counted**. A gap in the strip is better
  than a stalled camera, which loses data for every bird behind it.
- Lost GigE frames advance the line index, so line index = time is preserved. The missing
  lines are black.

### 5.3 Hook signal (recommended)
- Photo-sensor at the shackle rail, wired to a camera input line. The camera stamps the pulse
  onto the line where it happened, giving a hook position accurate to one line (~0.13 mm).
- Alternative without hardware: if the shackles are inside the field of view, detect them in
  the image offline (metal, fixed shape, fixed height).
- The hook signal is also the long-term path to carcass identification: hook number → line
  log → farm data.

### 5.4 Inspector ground truth
- A button (or key) pressed when the inspector condemns a bird. It is stored as wall-clock
  time and converted to a line index with the session map, minus a calibrated reaction/
  position delay.
- It is used to find defect candidates quickly for labelling, and later to measure agreement
  between model and inspector.

## 6. Offline: bird segmentation

Input: a session's tiles + `hooks.csv` (if any). Output: one image per bird + `birds.csv`.

| Method | When | How |
|---|---|---|
| `hook_sensor` | Photo-sensor fitted | Bird centre = hook line + fixed offset (calibrated once) |
| `hook_image` | Shackles visible in the image | Detect shackles in the strip, then as above |
| `brightness` | Neither | Predicted centre one period apart, corrected to the brightness peak (birds are lit, background is dark); tracks speed drift. Implemented in `acquisition/segmenter.py` |

- Each bird image is a window of ~1.5 bird-periods around the centre, so the whole bird is
  inside and overlapping neighbours appear at the edges.
- `birds.csv` records: bird id, session, centre line, start/end line, method, confidence
  (e.g. profile contrast, distance from predicted position).
- Segmentation is deterministic and **re-runnable**. A better method regenerates bird images,
  and labels (in strip coordinates) re-attach automatically.

## 7. Labelling

- Tool: CVAT or Label Studio (self-hosted).
- Unit: one bird image. Bounding box or polygon per defect instance, classes from
  `config/defects.yaml`, plus an `uncertain` tag for borderline cases.
- Candidate selection, so the labeller is not searching blind (defect prevalence is ~1%):
  1. birds near inspector presses;
  2. after the first round, a weak model ranks unlabelled birds by defect score.
- A random sample of clean birds is labelled too, as negatives.
- Import converts boxes to strip coordinates (session, line range, pixel-row range).

## 8. Models

- Detector per defect class (one multi-class detector to start), trained on bird images or
  strip windows cut from labelled regions.
- Evaluation is **per bird**: did we flag a bird the labeller marked, per class? Also
  detection-level metrics.
- Splits by **day/session**, never by random image, to avoid leakage between neighbouring
  birds.
- Fecal contamination is exploratory in RGB and is not held to the same bar.

## 9. Phase 2: real-time path (design constraint now, built later)

```
strip lines ──► sliding window (≥ 1 bird length, stride ≈ ½ bird)
            ──► detector on GPU ──► detections in strip coords
            ──► merge duplicates across overlapping windows
            ──► assign each detection to nearest hook ──► per-bird result
            ──► inspector display: bird #, defect, recommended action
```

- Latency per bird ≈ window length (~0.6 s) + inference. The result reaches the display while
  the bird is still upstream of the inspector, given the camera is placed far enough ahead.
- Same coordinate system, segmentation logic and labels as phase 1. Nothing in phase 1 is
  thrown away.
- Inference runs in its own process. Capture stays identical.

## 10. Data layout

```
data/
  YYYYMMDD/
    session_HHMMSS/                 # one per capture run; line index restarts at 0
      session.json                  # start time, line rate, camera + config snapshot
      tiles/strip_00000000.jpg      # name = first line index of the tile
      tiles.csv                     # tile, start_line, n_lines, wall_time, lost_frames
      hooks.csv                     # line index of each hook pulse
      inspector.csv                 # wall_time, line (estimated), operator
      capture_events.csv            # lost frames, dropped tiles, errors
    derived/
      birds_<method>_<version>/     # re-generated by offline segmentation
        images/bird_<session>_<id>.jpg
        birds.csv
labels/                             # strip-coordinate labels, versioned
models/
```

## 11. Throughput and storage

| Item | Value |
|---|---|
| Line rate | ~3,000 lines/s (square ~0.13 mm pixels at ~0.40 m/s) |
| Raw stream | 4096 × 3 B × 3,000 ≈ 37 MB/s (GigE usable ~115 MB/s) |
| Tiles | 2,000 lines, JPEG q≈92–95 ≈ 2–3 MB each, ~1.5 tiles/s |
| Per 8 h shift | ~100–130 GB strip (+ derived bird images if kept) |
| Disk | 4 TB NVMe data drive; move older sessions to an off-line archive |

Bird images are derived data and can be regenerated. Only the strip and the logs are
precious.

## 12. Health and monitoring

Logged per session and shown in the capture console:
- lost GigE frames, dropped tiles (writer queue full), disk free space;
- mean strip brightness (lights failed / lens covered);
- hook pulse rate vs expected (sensor dirty / line stopped);
- measured bird period vs nominal (line speed changed, or wrong config).

## 13. Mapping to the current code

| Component | Status |
|---|---|
| `acquisition/camera.py` — racer 2 S grab + simulator | Done (untested on hardware) |
| `acquisition/writer.py` — async writer, non-blocking | Done |
| `acquisition/segmenter.py` — tiles + brightness tracking | Done; brightness tracking moves to the offline stage |
| Capture writes strip tiles + session files (§5, §10) | **To do**: make tiles the capture default, add `session.json` / `tiles.csv` |
| Hook pulse logging | To do, once the sensor (or shackle visibility) is decided |
| Inspector button | To do |
| Offline segmentation CLI over stored tiles (§6) | To do; reuses `PeriodicSegmenter` |
| Labelling export/import (§7) | To do |
| Models, review (§8) | Later in phase 1 |

## 14. Open decisions

| # | Question | Affects |
|---|---|---|
| 1 | Are the shackles visible in the field of view? | Photo-sensor vs image-based hook detection |
| 2 | Shackle pitch (assumed 6" = 152 mm) | Line rate, bird period |
| 3 | Encoder fittable on the line drive? | Geometric accuracy (defect size) |
| 4 | Labelling tool: CVAT or Label Studio | Export/import format |
| 5 | Where does offline processing run (line PC vs office/cloud)? | Data transfer, GPU |
| 6 | Camera distance upstream of the inspector | Phase 2 latency budget |
