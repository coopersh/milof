# Line & acquisition requirements

## Known

| Item | Value |
|---|---|
| Line speed | 156 birds/min (2.6 /s, ~385 ms per bird) |
| View | One side only |
| Camera | Basler racer 2 S r2L4096-14gc (4k colour line-scan, GigE) — see `hardware_setup.md` |

## To measure on site

| Item | Why it matters | Value |
|---|---|---|
| Shackle pitch | Gives conveyor speed (pitch × 2.6/s) → required line rate | TBD (6" = 152 mm is common) |
| Encoder on the chain? | Line trigger must follow conveyor speed, or images distort | TBD |
| Camera-to-bird distance | Lens choice, field of view | TBD |
| Bird height in view | Sensor width / pixels across | TBD |
| Space for lighting | Line light + diffuser/polariser against wet-skin glare | TBD |
| Wash-down / IP rating | Housing requirements | TBD |

## Back-of-envelope (assuming 6" pitch)

- Conveyor speed ≈ 0.152 m × 2.6 /s ≈ **0.40 m/s**.
- At 0.25 mm/pixel along travel → **~1,600 lines/s** (well within line-scan limits).
- ~616 lines per bird.
- Raw stream ≈ 10 MB/s; cut + compressed ≈ 200–400 KB/bird ≈ 30–50 GB per 8 h shift.

## Data collection reality

~70k birds per shift, but at 0.5% prevalence finding 500 examples of one defect needs
~100k images. To avoid labelling blind:
- **Inspector button**: timestamp each condemnation → jump straight to candidate images.
- **Pre-filter**: after a first labelling round, a weak model ranks images for the labeller.
