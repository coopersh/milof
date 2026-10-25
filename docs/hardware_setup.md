# Hardware setup

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
| 1 | Basler lens C11-0824-12M (8.5 mm) | Lens option — short working distance |
| 1 | Basler lens C11-1220-12M (12 mm) | Lens option — longer working distance (currently mounted) |
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

## Optics

Sensor: 4096 px × 3.5 µm ≈ 14.3 mm line. Field of view ≈ 14.3 mm × distance / focal length.
Working distances from the rig design (engineer):

| Item | Working distance | FOV | Resolution |
|---|---|---|---|
| Lens 8.5 mm | 293 mm | ~493 mm | ~0.12 mm/px |
| **Lens 12 mm** (mounted) | **414 mm** | ~494 mm | ~0.12 mm/px |
| Light bars (collimated diffuser) | 150 mm nominal; **tilted 15° in the rig to converge at ~450 mm from the lens** (not shown in the CAD model) | | |

Both lenses were sized for the same ~0.5 m FOV, so magnification and depth of field are the
same — the choice is about space: 12 mm keeps the camera further from splash and matches the
light convergence point, so it is the intended lens.

Mount the camera so the **sensor line is vertical** (along the hanging bird); the bird
moves horizontally through it. Place the bird's near surface at ~414–450 mm (between lens
focus and light convergence) and focus at ~430 mm.

### Depth of field — the real constraint

Birds are 3D and swing on the shackles. Approximate depth of field at ~420 mm
(circle of confusion ≈ 2 px = 7 µm):

| Aperture | In focus |
|---|---|
| f/2 | ±16 mm |
| f/4 | ±33 mm |
| f/5.6 | ±47 mm |
| f/8 | ±70 mm |

f/2 is too shallow for a carcass. Start at **f/4–f/5.6**; every stop down halves the
light, and at ~3,300 lines/s the exposure per line is at most ~300 µs — check image
brightness with both light bars at full power. A guide rail that stops birds swinging
toward/away from the camera helps more than any lens setting.

After focusing at the real working distance, lock the lens rings with their set screws.

## Line rate and data volume (assuming 6" shackle pitch, 156 birds/min)

- Conveyor ≈ 0.40 m/s. Square pixels at 0.12 mm → **~3,300 lines/s**
  (camera max 14 kHz).
- ~1,250 lines per shackle pitch → one bird image ≈ 4096 × 1,250 px.
- Raw RGB ≈ 40 MB/s (fits GigE, ~115 MB/s usable). JPEG ≈ 1–1.5 MB/bird →
  **~70–100 GB per 8 h shift**. Plan a 4 TB data SSD.

## Line trigger

A line-scan image is only geometrically correct if lines are triggered by distance
travelled, not time.

- **Preferred:** shaft encoder on the shackle-line drive, wired to the camera I/O
  (racer 2 S supports encoder input). Must be bought and fitted.
- **Phase-1 fallback:** free-run at a fixed line rate. Works if the line runs at a
  steady speed; speed changes stretch/compress birds. Acceptable for data collection,
  not for measuring defect size.

## Open items

- Shackle pitch (sets line rate and lines per bird).
- Can a guide rail limit bird swing in front of the camera?
- Encoder: available / can be fitted?
- PC location relative to the camera (cable length).
