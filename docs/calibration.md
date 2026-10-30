# Camera calibration

Procedure for setting up the Basler racer 2 S on the Milouf line, following the
integrator's calibration video. The UI's **Calibration** tab walks through the same
steps (`python -m ui`); pylon Viewer works too.

## How often

| Step | When | Redo when |
|---|---|---|
| 1. Network (IP) | Installation | PC or NIC replaced |
| 2. Connect + test grab | Installation | — |
| 3. Aperture | Installation | Lighting changed |
| 4. Flat-field (PRNU) | Installation — stored in the camera | Lights moved/replaced, aperture changed, camera moved relative to the lights, vertical streaks appear |
| 5. Focus + lock screw | Installation | Camera bumped, working distance changed |
| 6. Calibration board (mm per pixel) | Installation | Working distance changed |
| Quick check | **Start of every shift** (~1 min) | — |

Steps 4 → 5 must stay in that order: flat-field is taken **out of focus**, then focus
is restored.

## 1. Network

Camera on a static IP in the site subnet **192.168.2.x** (not too low, not too high —
avoid .1 and .255), set with pylon IP Configurator. The PC's camera NIC must be in the
same subnet. Put the address in `config/camera.yaml` → `camera.ip`.

On a NIC without jumbo frames keep `packet_size_bytes: 1500`; with jumbo frames enabled
use 8192 (the racer 2 S maximum).

## 2. Connect and test grab

Belt running. Settings for the check:

- Image Format Control: **ReverseX on** (`reverse_x: true`), Width 4096, Height 400
- Acquisition Control: line rate and exposure until the image looks normal

Grab continuously and confirm the moving belt is visible. This only proves the
camera, cable and NIC work.

## 3. Aperture (lower ring)

Range f/4–f/16. Work at **f/5.6–f/8**:

- too open (f/4): shallow depth of field — parts of a swinging bird drop out of focus
- too closed: needs very long exposure, which the line rate does not allow
  (`exposure_us ≤ 1e6 / line_rate_hz`, 333 µs at 3000 Hz)

The current rig is at f/5.6. Don't touch the aperture after flat-field — changing it
invalidates step 4.

## 4. Flat-field correction (PRNU)

PRNU = photo-response non-uniformity. Each of the 4096 pixels, the lens (vignetting)
and the light bars respond a little differently along the line, so a uniform white
target comes out brighter in the middle and darker at the edges. PRNU makes the
camera learn that profile and divide it out. Without it every image has a brightness
gradient along the bird that a model can mistake for a defect.

You need: a smooth white sheet (no creases, no marks) or an official grey/white
target.

1. **Defocus.** Loosen the focus screw and turn the upper (focus) ring all the way to
   infinity. Check with the stripe chart: stripes visible → still in focus; stripes
   gone → good. Defocus keeps paper texture out of the correction.
2. Belt **stopped**. White sheet under the camera, lit as evenly as possible. Lower
   the camera/lift as far as possible (lift to **0** in the technician app) so the
   light on the sheet is uniform.
3. Stream and set exposure so the white reads **R, G, B ≈ 180–200** — bright but not
   clipped (on the reference rig: ~270 µs).
4. Image Processing Control: **Color Space = Off**, **Light Source Preset = Off**.
5. Shading Correction Selector = **PRNU**, mode = **User**, press **Execute**. Takes
   a few seconds; the correction is stored in the camera.
6. Verify the white is now flat along the whole line (UI: the column-profile plot is
   flat; ±3 grey levels is fine).
7. **Verify it survives a power cycle.** Power the camera off and on and check step 6
   again. If the correction is gone, save to a user set and make it the default
   (UI: *Save to camera*; pylon Viewer: User Set Control → UserSet1 → Save, Default =
   UserSet1).

## 5. Focus (upper ring)

1. Stripe (focus) chart under the camera, stripes **across** the sensor line (so the
   line crosses many stripes).
2. Raise the lift to working height **33**.
3. Raise exposure if the chart is dark (~800 µs is fine for a static chart).
4. Stream, one hand on the focus ring, turn until the stripes are sharpest
   (UI: maximise the *sharpness* number — go past the peak and come back).
5. **Lock the screw.** Don't touch it again.

## 6. Calibration board

Scan the official calibration board (not the focus chart) and save the image. It
gives mm per pixel along the sensor and along the belt. Manual calculation for now.

Expected at 450 mm with the 12 mm lens: ~350 mm field along the bird → ~0.085 mm per
pixel; a 2 mm defect ≈ 24 pixels.

## Quick check — start of every shift

1. White sheet under the camera: profile flat, no vertical streaks. Streaks → redo
   step 4.
2. Stripe chart: sharpness at the value recorded after step 5. Lower → camera moved,
   redo step 5.
3. Empty belt running: no horizontal banding. Banding = lights flickering (they must
   be DC; the Mean Well RSP-200 is). Not fixable in software.

## Production exposure

After calibration, with birds on the line at 3000 Hz, pin exposure to ~330 µs (the
ceiling) and judge with the live histogram:

| Metric | Target |
|---|---|
| `mean` | 100–150 |
| `clip hi` (pixels at 255) | < 1% |
| `clip lo` (pixels at 0) | < 5% |

Too bright → close the aperture one stop *and redo PRNU*; too dark → more light.
Prefer fixing brightness with the lights over the aperture, since an aperture change
means redoing step 4.

## Line-scan gotchas

- **A static scene shows one slice repeated.** With nothing moving, every line is the
  same slice of the world. Exposure and focus can be set this way; motion artifacts,
  free-run drift and lighting flicker over time can only be checked with the belt
  running.
- **The sensor sees one thin line.** A hand waved in front of the camera only shows
  if it crosses that line.
