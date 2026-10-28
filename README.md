# milof

Vision-based decision support for veterinary inspection on the Milouf poultry slaughter line.
A Basler line-scan camera images every carcass; AI flags visible defects (wing fractures,
bruises, skin defects, fecal contamination) and recommends an action. **The inspector decides.**

Status: **phase 1 — feasibility** (data collection, labelling, retrospective demo).

- `docs/defects.md` — defect definitions (to agree with the vets)
- `docs/line_requirements.md` — site measurements still needed
- `docs/SYSTEM_DESIGN.md` — system design (capture → offline segmentation → labelling → models)
- `CLAUDE.md` — architecture and design rules

```bash
pip install -e ".[dev]"          # add ,camera for pypylon
pytest tests/
```
