# Hardware setup

Rig BOM, wiring, optics and physics. For the on-site tuning procedure (which knob to
turn in what order, with the viewer), see [calibration.md](calibration.md).

## Bill of materials (rig as built)

| Qty | Item | Role |
|---|---|---|
| 1 | Basler racer 2 S **r2L4096-14gc** | 4k colour line-scan camera, GigE |
| 1 | Heat sink for ace 2 / racer 2 S | Camera cooling + mounting |
| 1 | Power-I/O cable, M8 6-pin / open, 5 m | Camera power (12–24 VDC) and I/O (trigger / encoder / strobe) |
| 1 | GigE cable, Cat 6, RJ45 screw-lock / RJ45, 5 m | Camera data |
| 2 | Light Bar 10×500, IP67, white | Line illumination (500 mm long ≈ bird height) |
| 2 | Foil bracket set + collimated diffuser foil, Bar 10×500 | Even, directed light on the line |
| 2 | M8 cable 2 m, tilted | Light bar power |
| 1 | Mean Well RSP-200 | 24 VDC supply for the light bars |
| 1 | Basler lens C11-0824-12M (8.5 mm) | Spare lens option — same FOV at 293 mm |
| 1 | Basler lens C11-1220-12M (12 mm) | **Initial setup lens** — 450 mm to bird |
| — | Aluminium profiles (4×400, 2×340, 4×900, 1×550, 4×2500, 6×620 mm) | Frame |

**Not in the BOM: an encoder.** See "Line trigger" below.

## Wiring

```
220 V ──> Mean Well RSP-200 (24 VDC) ──M8──> Light bar ×2

220 V ──> PoE injector
            ├── P+DATA OUT ──GigE Cat6 (screw-lock end at camera)──> camera RJ45
            └── DATA IN ─────Cat6──────────────────────────────────> PC, dedicated NIC

camera M8 6-pin ──Power-I/O cable (open wires)──> encoder / photo-sensor (trigger in)
```

- Power the camera **either** by PoE (injector) **or** by 12–24 VDC on the Power-I/O
  cable — not both.
- The supplied GigE cable is 5 m: the PC (or a switch) must be within 5 m, or replace it
  with a longer industrial Cat6 (GigE max 100 m).
- Pinout of the M8 6-pin: see the racer 2 S manual before connecting the open wires.

## PC network setup

1. Dedicated Intel NIC (i210 / i350) for the camera only.
2. Static IP on that NIC, e.g. `192.168.10.1/24`; Jumbo frames (MTU 9000).
3. Install Basler **pylon**; in **pylon IP Configurator** give the camera a persistent
   IP, e.g. `192.168.10.2`.
4. Open **pylon Viewer**, confirm the model reads `r2L4096-14gc`, grab test lines.

## Optics — initial setup (as in the CAD drawing)

Top view, scan line vertical (out of the drawing), birds move across it:

```
            camera + 12 mm lens
                   │
                   │ 450 mm  (lens front → bird)
                   │
   light bar ┐     │     ┌ light bar      ← lights at the ends of the two arms,
              ╲ 15°│15° ╱                   ~40 mm either side of the camera axis
         150 mm╲   │   ╱
                ╲  │  ╱
                 ╲ │ ╱
  ───────────────── ● ─────────────────  bird surface (scan line)
```

| Item | Value |
|---|---|
| Lens | **12 mm** (C11-1220-12M) |
| Lens front → bird | **450 mm** |
| Light bars → bird | **150 mm** (their nominal working distance) |
| Light tilt | **15°** inward, converging on the camera axis at the bird |
| Light offset from camera axis | 150 × tan 15° ≈ **40 mm** each side |
| Field of view (along bird height) | ~14.3 mm × 450 / 12 ≈ **~535 mm** |
| Resolution | ~**0.13 mm/px** |

Sensor: 4096 px × 3.5 µm ≈ 14.3 mm line. The 8.5 mm lens (design WD 293 mm) gives the
same FOV closer in; it is the spare option if space is short.

Mount the camera so the **sensor line is vertical** (along the hanging bird).

### Depth tolerance — the real constraint

Birds are 3D and swing on the shackles. Two things limit how far the bird surface can
move toward/away from the camera:

**Focus** (at 450 mm, circle of confusion ≈ 2 px = 7 µm):

| Aperture | In focus |
|---|---|
| f/2 | ±17 mm |
| f/4 | ±40 mm |
| f/5.6 | ±55 mm |

**Light stripe.** With collimated diffusers each bar throws a narrow stripe that hits the
scan line only at the 150 mm convergence plane. A surface Δ mm off that plane sees each
stripe shifted by Δ × tan 15° ≈ 0.27 Δ — e.g. ±40 mm depth → ~11 mm off the scan line,
so the image darkens. Check this on the bench: move a target ±20/40/60 mm and watch the
brightness.

Start at **f/4**; every stop down halves the light, and at ~3,000 lines/s the exposure
per line is at most ~330 µs. A guide rail that stops birds swinging toward/away from the
camera helps more than any lens or light setting.

**Glare.** Lights only ~15° off the camera axis means wet skin facing the camera
reflects them straight back (bright streaks). If it saturates, add a polariser pair
(film on the bars, polariser on the lens) or increase the tilt.

After focusing at 450 mm, lock the lens rings with their set screws.

## Line rate and data volume (assuming 6" shackle pitch, 156 birds/min)

- Conveyor ≈ 0.40 m/s. Square pixels at 0.13 mm → **~3,000 lines/s**
  (camera max 14 kHz).
- ~1,150 lines per shackle pitch → one bird image ≈ 4096 × 1,150 px.
- Raw RGB ≈ 37 MB/s (fits GigE, ~115 MB/s usable). JPEG ≈ 1–1.5 MB/bird →
  **~70–100 GB per 8 h shift**. Plan a 4 TB data SSD.

## Line trigger

A line-scan image is only geometrically correct if lines are triggered by distance
travelled, not time.

- **Preferred:** shaft encoder on the shackle-line drive, wired to the camera I/O
  (racer 2 S supports encoder input). Must be bought and fitted.
- **Phase-1 fallback:** free-run at a fixed line rate. Works if the line runs at a
  steady speed; speed changes stretch/compress birds. Acceptable for data collection,
  not for measuring defect size. The segmenter tracks the bird period from the image
  itself, so slow drift does not lose birds.
- **Cheap upgrade:** a photo-sensor on the shackles wired to a camera input gives an
  exact "bird here" mark per shackle — better bird cutting than image-based tracking
  when birds overlap heavily.

## Open items

- Shackle pitch (sets line rate and lines per bird).
- Can a guide rail limit bird swing in front of the camera?
- Encoder: available / can be fitted?
- PC location relative to the camera (cable length).
