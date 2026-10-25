# Defect definitions (draft — to be agreed with the veterinary services)

Each defect needs: a definition, what counts as positive, size/severity threshold,
borderline cases, and the recommended action. Fill the TBDs with the vets before labelling
starts — labels made against a moving definition are wasted.

| # | Defect | Hebrew | Visible from one side? | Recommended action | Threshold |
|---|---|---|---|---|---|
| 1 | Wing fracture | שבר כנף | Only the camera-facing wing | TBD (partial?) | TBD |
| 2 | Bruise / haemorrhage | שטף דם | Yes | TBD | Min. area TBD; fresh vs. old? |
| 3 | Skin defect | פגם עור | Yes | TBD | Min. length/area TBD |
| 4 | Fecal contamination | זיהום צואתי | Yes, if visible in RGB | TBD | Any visible? |

## Open questions per defect

### 1. Wing fracture
- Visual cues: abnormal angle, dangling wing, bruise around the break?
- Does a fracture on the hidden side count as a miss in evaluation? (It can't be seen.)

### 2. Bruise / haemorrhage
- Minimum size to flag. Does colour (fresh red vs. old green/yellow) change the action?
- Location matters (breast vs. leg) for partial condemnation?

### 3. Skin defect
- Which sub-types: scratches, tears, scabs, dermatitis? One class or several?

### 4. Fecal contamination
- RGB can only catch visible contamination. Agree up-front that this class is
  exploratory in phase 1.

## Labelling standard

- Unit: one image per bird. Box (or polygon) per defect instance + bird-level label.
- **Negatives matter**: label a sample of clean birds too, not only defects.
- Borderline cases get their own tag (`uncertain`), not forced into yes/no.
- Target: ≥500 positive examples per defect.
