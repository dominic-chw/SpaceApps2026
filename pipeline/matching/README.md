# pipeline/matching/ (scoring)

Plain maths, no AI model: normalize -> weighted Gower distance -> nearest Earth places -> match % + level -> explanation + confidence.

| File | What it does |
|---|---|
| `features.yaml` | **The feature list.** Edit this when variables change. Also the confidence settings. |
| `normalize.py` | Puts every feature on a 0-1 comparable scale. |
| `match.py` | Distance, top twins (200 km apart), match %, level. Entry point: `match_band()`. |
| `explain.py` | best[3] / worst[3] features and the confidence label. |
| `earth_library.py` | Writes `output/earth_library.json` for the web sliders (format + browser formula: plan s15a; `tests/test_earth_library.py` proves the formula matches Python). |
| `../../weights/default.json` | How much each feature counts per depth band (placeholders for now). |

## Feature tables (handoff from the data side)
The data side writes one LONG table per planet (`pipeline/common/schema.py`):
`data/features/{mars,earth}_cells.parquet` (CSV with the same columns also works), columns
`planet, cell_id, lat, lon, band, feature, value_num, value_cat, data_quality`,
one row per place x band x feature. `load_tables.py` reshapes it into one wide table per band.

- **`feature`** values = the `column` names in `features.yaml` (right now `temp_mean, frozen_fraction, temp_swing, ice, ground_class, hardness, rock, slope_deg, roughness_m, dryness`).
- Numbers in `value_num`; words (`rock`, `ground_class`) in `value_cat`. `ice` may be 0/1 (shown as no/yes).
- **`data_quality`** = `measured`, `modelled` or `predicted` (drives confidence).
- **Earth:** a cell whose `cell_id` is a region in `regions.geojson` (e.g. `dry_valleys`) is a known
  analog and gets its name. Every other cell is a "new site", labelled e.g. "New site, 120 km from Devon Island".
- Unknown value = leave the row out (or empty). It is skipped and confidence goes down.
- `lon` east-positive, -180..180. Temperatures in kelvin.
- `frozen_fraction` = share of the year (0-1) the ground at that band's depth is below 0 °C.
  Earth: count months below 0 °C in ERA5-Land soil temperature at that depth, divide by 12.
  Mars: from the heat model (close to 1 almost everywhere).
- `rock` and `ground_class` must use the fixed words from plan s3. Translate dataset labels first (lookup CSVs).

A column the code expects but the table lacks is treated as all-missing, so partial tables still run.

## Run it
```
python -m pipeline.run_all            # score existing feature tables -> output/*.json
python -m pipeline.run_all --build    # rebuild the feature tables first (data side)
python -m pytest tests/
```
