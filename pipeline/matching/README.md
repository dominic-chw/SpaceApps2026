# pipeline/matching/ (scoring)

Plain maths, no AI model: normalize -> weighted Gower distance -> nearest Earth places -> match % + level -> explanation + confidence.

| File | What it does |
|---|---|
| `features.yaml` | **The feature list.** Edit this when variables change. Also the confidence settings. |
| `normalize.py` | Puts every feature on a 0-1 comparable scale. |
| `match.py` | Distance, top twins (200 km apart), match %, level. Entry point: `match_band()`. |
| `explain.py` | best[3] / worst[3] features and the confidence label. |
| `../../weights/default.json` | How much each feature counts per depth band (placeholders for now). |

## Feature tables (handoff from the data side)
One CSV per planet per band, e.g. `mars_upper_metre.csv`, `earth_upper_metre.csv`.

- **Mars columns:** `cell_id, lat, lon`, then the feature columns.
- **Earth columns:** `region_id, name, lat, lon`, then the feature columns.
- **Feature columns:** the `column` names in `features.yaml` (right now `temp_mean, frozen_fraction, temp_swing, ice, ground_class, hardness, rock, slope_deg, roughness_m, dryness`).
- **Quality (optional, recommended):** `<column>_quality` = `measured`, `modelled` or `predicted`.
- Leave a cell **empty** when the value is unknown. It is skipped and confidence goes down.
- `lon` east-positive, -180..180. Temperatures in kelvin. `ice` = yes/no.
- `frozen_fraction` = share of the year (0-1) the ground at that band's depth is below 0 °C.
  Earth: count months below 0 °C in ERA5-Land soil temperature at that depth, divide by 12.
  Mars: from the heat model (close to 1 almost everywhere).
- `rock` and `ground_class` must use the fixed words from plan s3. Translate dataset labels first (lookup CSVs).

A column the code expects but the table lacks is treated as all-missing, so partial tables still run.

## Try it
```
python -m pytest tests/test_matching.py
```
