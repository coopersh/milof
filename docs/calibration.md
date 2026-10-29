# Camera calibration

Field SOP for setting the Basler racer 2 S on the Milouf line, in the order the
decisions should be made. Each knob depends on the ones before it; skipping a step
means re-tuning the ones after.

Use `python -m acquisition view` on-site. The viewer shows a rolling strip of the last
~340 ms of lines, the mean/min/max of those pixels, the fraction clipped at 0 and 255,
and a luminance histogram. Press `w` to write the current knobs back to
`config/camera.yaml`.

## 1. Line rate — geometry, not brightness

Line rate = how often the sensor reads one line of 4096 pixels. It sets the vertical
pixel size at the bird plane:

```
line_rate_hz = line_speed_m_per_s / target_pixel_size_m
```

At 156 birds/min and a 6-inch (152 mm) shackle pitch the belt runs at ~0.4 m/s. For
~0.13 mm pixels (square, matching the horizontal pitch 350 mm / 4096 ≈ 0.085 mm — close
enough):

```
line_rate_hz ≈ 0.4 / 0.00013 ≈ 3000 Hz
```

This is the default in `config/camera.yaml`. Only change it if the belt speed changes
or you deliberately want a different vertical resolution.

## 2. Exposure — pin it to the ceiling the line rate allows

Exposure is capped by the line rate:

```
exposure_us_max = 1_000_000 / line_rate_hz   # 333 µs at 3000 Hz
```

Set exposure at that ceiling. Anything higher is silently clamped by the camera. Don't
lower exposure to control brightness — that throws away light. Brightness is for the
aperture and light bars.

## 3. Aperture — the one knob you turn to control brightness

Start at **f/5.6**. Watch the viewer's `clip hi` and `mean`:

| Observation | Action |
|---|---|
| `clip hi > 1%` (information lost in bright regions) | Close one stop (e.g. f/5.6 → f/8) |
| `mean < 80` (too dark, no headroom in shadows) | Open one stop (e.g. f/5.6 → f/4) |
| `clip hi < 1%` and `mean` 100–150 | Done |

Keep aperture in **f/4–f/5.6**. Beyond f/4 depth of field is too shallow for the ±5 cm
variation in bird position; past f/8 you lose too much light and have to compensate
with impractically bright lamps.

## 4. Lighting — must be flicker-free at the line rate

Must be DC or LED with PWM frequency **well above** the line rate (> 50 kHz is safe).

**Diagnostic:** aim the viewer at an evenly-lit static surface. Horizontal dark bands
in the rolling strip = light source is pulsing on and off between lines. This is NOT a
camera problem and NOT fixable in software. Fix the lighting.

The Mean Well RSP-200 driving the Basler Light Bars is DC — this is a solved problem
on this rig. If someone swaps the driver, re-check for banding.

## 5. Framing and focus — in that order

**Framing** (where the sensor line falls on the scene): the 4096-pixel sensor should
cover the bird's full height, head to feet, with a 10–20% margin on each side. At
450 mm working distance with the 12 mm lens this is a ~35 cm vertical field.

**Focus:** always set with the aperture **wide open** so the depth of field is as
shallow as possible — that way the sharpest point is obvious. Focus on the bird plane,
not the background. Then stop down to the working aperture (f/5.6).

## 6. Target histogram

| Metric | Target |
|---|---|
| `mean` | 100–150 (middle of range) |
| `clip hi` | < 1% (no saturated pixels — would lose detail in the brightest birds) |
| `clip lo` | < 5% (a few deep-shadow pixels are fine) |
| shape | peak somewhere in the middle, long tails trimmed |

A left-piled histogram means underexposure (open aperture or more light). A
right-piled histogram means overexposure (close aperture or dim the lights). A
double-peak is usually fine — it reflects bird vs background.

## 7. Field calibration order

1. Mount the camera at the right distance and angle: bird centred vertically in the
   sensor line's field, horizontal axis perpendicular to the shackle track.
2. Power up the Light Bars.
3. Open aperture wide, focus on the bird plane, stop down to f/5.6.
4. Open the viewer. Hang a real bird (or a cardboard proxy of similar size and
   reflectance) in the sensor line.
5. Adjust aperture (or light bar brightness, if dimmable) until the histogram target
   is met.
6. Press `w` to save `exposure_us` and `line_rate_hz` back to `config/camera.yaml`.
7. Repeat the check at shift start/end and when ambient lighting changes.

## 8. Line-scan gotchas worth remembering

- **Static-scene tuning is partial.** The viewer with a motionless scene shows the
  same vertical slice stacked 1024 times. You can set exposure right, but you cannot
  catch motion artifacts, encoder-free drift, or lighting flicker across time without
  the line actually running. Finish calibration during a real run, even a short one.

- **The sensor sees one thin vertical slice.** Waving a hand in front of the camera
  usually does nothing — the hand must physically cross the slice to register. The
  viewer makes this disorienting; it goes away as soon as the belt moves.

- **Resolution budget.** At 4096 pixels across ~35 cm of bird height, 1 pixel ≈
  0.085 mm. A 2 mm defect is ~24 pixels — detectable. A 0.2 mm hair-fine mark is 2–3
  pixels — on the edge, don't promise it to the vet.
