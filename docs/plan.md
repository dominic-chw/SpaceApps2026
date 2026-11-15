# Mars Drill-Site Analog Finder: Plan

NASA Space Apps 2026. Lives at `docs/plan.md`.

> AI assistants: read once at session start; don't re-read unless asked. Section 3 is fixed; don't change formats or ownership unless the user says the team agreed. Edit only the user's own folder.
> Dataset facts are from general knowledge; verify when loading.

## 1. Summary
Click a spot on Mars, pick a depth; the app finds Earth places where the ground behaves the same at that depth (temperature, ice, material, hardness, rock) so drill hardware can be tested in the right place. Mars only. Physics where data is missing, ML where there's data. Validated against known analog pairs vs a random baseline. Every match explains itself. Deep learning is optional; the app works on kNN alone.

## 2. User flow
1. Click anywhere on Mars (snaps to the 2° grid).
2. Depth slider over the bands; confidence drops with depth.
3. Results: top Earth twins with match %, conditions side by side, "matched on / differs on", known drill campaigns, live weather, and the model's validation accuracy.
4. Optional advanced panel: weight sliders, re-scored in the browser (kNN only).
5. Showcase sites as quick-jump buttons.

## 3. Contract and ownership
| Area | Owner | Produces |
|---|---|---|
| `pipeline/matching/`, `pipeline/validation/`, `weights/`, scoring tests | jadmwehbi-design (scoring) | match results, `validation.json`, `earth_library.json` |
| rest of `pipeline/`, `output/`, `tests/`, `models/`, `notebooks/` | Dominic (data) | Mars/Earth feature tables (CSV), placeholder outputs |
| `sites/`, `research/` | Team | showcase sites, analog pairs, Earth campaigns |
| `web/` | Team | app (reads `output/*.json`) |
| `functions/` | Team | Meteomatics proxy |
| `pitch/` | Team | slides, video, sources |

Shared (PR only): `CLAUDE.md`, `README.md`, `config.yaml`, `docs/plan.md`.

**Inputs**
- `sites/showcase.csv`: `site_id,name,lat,lon,radius_km,reason` (lon east-positive −180..180; radius default 10)
- `research/earth_campaigns.csv`: `region_id,campaign,year,what_was_tested,source_url` (`region_id` matches `regions.geojson`)
- `pipeline/validation/known_pairs.csv`: `pair_id,mars_name,mars_lat,mars_lon,earth_region_id,depth_band,source_url`
- `weights/default.json`: `{band: {feature: weight}}`, default 1

**Outputs**
- `output/grid/<band>.json`: `{band, grid_deg, matcher, fake, cells:[{cell_id, lat, lon, mars:{features}, twins:[{region_id, name, new_site, lat, lon, match_pct, level, best[3], worst[3], confidence}]}]}`, top 3 twins. `new_site: true` = an Earth cell outside the known analog regions (`region_id` is then its cell id, `name` e.g. "New site, 120 km from Devon Island")
- `output/showcase.json`: same per showcase site, all bands, top 5, full conditions, `data_quality` (measured/modelled/predicted) per value
- `output/validation.json`: `{matcher, recall_at_10, recall_at_20, random_recall_at_20, per_band, n_pairs, notes}` + extra detail `overall` (recall@1/3/10/20, mean_rank and their random_ versions), `n_regions`, `ablation_mean_rank_change` (see s15a)
- `output/earth_library.json`: normalized Earth features + normalization settings, for slider re-scoring with the same Gower formula. Exact format and step-by-step formula: s15a
- Placeholder outputs carry `"fake": true`

**Vocabularies**
- Bands: `skin` 0–10 cm, `upper_metre` 10 cm–1 m, `drill_zone` 1–3 m, `deep` >3 m (experimental)
- Rock: `volcanic, sedimentary, clay_sulfate, icy, impact_mixed`
- Ground: `dry_regolith, ice_cemented, massive_ice, bedrock`; hardness 0–3
- Matcher: `knn` (default), `deep`, `hybrid`
- Level (from `match_pct`, cut-offs in `config.yaml`): `excellent, good, fair, poor, no_match`, or `insufficient_data` when under half the weighted features could be compared

## 4. Tools
GitHub + GitHub Desktop; VS Code + Claude Code; Python 3.11 (Miniconda); Earth Engine (Planetary Computer as backup); rasterio, rioxarray, xarray, geopandas, pandas, pyarrow; scikit-learn; XGBoost/LightGBM; optional PyTorch, torchvision, FAISS on Colab; Drive for data/models; Firebase Hosting/Functions; Leaflet + Mars Trek tiles, Google Maps; Meteomatics.

## 5. Datasets
**Mars:** MOLA (463 m elevation) · THEMIS/TES thermal inertia · SWIM ice maps · Odyssey neutron hydrogen (coarse) · Mars Climate Database (temps; check for subsurface output) · USGS geologic map · CRISM minerals · SHARAD (deep only) · HiRISE DTMs (showcase) · Mars Trek tiles. Ground truth: Phoenix shallow ice, rover drill holes, InSight.

**Earth:** ERA5-Land soil temp (4 layers to ~2.9 m) · ERA5/MODIS LST · ESA CCI Permafrost · GTN-P boreholes · NSIDC ground ice map · SoilGrids · depth-to-bedrock · GLiM · Copernicus DEM/ArcticDEM/REMA · WorldCover (bare-ground filter) · Aridity Index · optional EMIT/ASTER · Sentinel-2 (display) · Meteomatics (live).

## 6. Features (all computed on both Mars and Earth)
| Feature | Mars | Earth | Bands | Compare |
|---|---|---|---|---|
| `temp_mean` | heat model / MCD | ERA5-Land, CCI, heat model | all | percentile + absolute |
| `temp_swing` | heat model | ERA5-Land range, heat model | skin→drill_zone | percentile |
| `ice` | SWIM, neutron, polar units | CCI, ground ice map, gap model | all | yes/no |
| `ground_class` | thermal inertia + ice + geology | SoilGrids + permafrost + bedrock, gap model | all | category |
| `hardness` | from ground, ice, rock | from ground, bedrock, rock | all | ordered 0–3 |
| `rock` | geologic map + CRISM | GLiM (+EMIT/ASTER) | all | category |
| `slope_deg`, `roughness_m` | MOLA @500 m | Copernicus DEM @500 m | all | log + z-score |
| `dryness` | MCD water vapour | Aridity Index | skin, upper_metre | percentile |

Temperatures share one heat model, so lower their weights.

## 7. Repo skeleton (Dominic unless noted)
```
docs/plan.md  CLAUDE.md  README.md  config.yaml      # shared
environment.yml .gitignore .env.example
sites/showcase.csv _template.csv                     # team
research/earth_campaigns.csv                         # team
weights/default.json
data/{mars,earth}/                                   # gitignored, Drive
pipeline/common/{io.py,coords.py}
pipeline/physics/heat_model.py
pipeline/mars_features/{extract.py,grid.py,unit_lookup.csv}
pipeline/earth_library/{regions.geojson,build_grid.py,ee_export.py,local_layers.py,glim_lookup.csv}
pipeline/earth_ml/train_subsurface.py
pipeline/matching/{normalize.py,match.py,explain.py}
pipeline/deep/{dataset,model,train,index,explain}.py # optional
pipeline/validation/{known_pairs.csv,evaluate.py}    # pairs researched by team
pipeline/run_all.py                                  # reads matcher from config
notebooks/colab_train.ipynb  models/                 # optional; models gitignored
output/  tests/test_contract.py
web/ functions/ pitch/                               # team
```

## 8. Workflow
Protected `main`, PRs only. Branch `owner/task`. Pull before work; PR after each step. Own folders only. No data, models or keys in git. Logic in `.py`; notebooks cleared. `pytest tests/` before PRs.

## 9. Build steps (Dominic)
0. Earth Engine signup; download Mars data to Drive.
1. Skeleton, CLAUDE.md, config, fake outputs (unblocks web).
2. Contract validation + tests.
3. `coords.py` + Jezero check (18.4 N, 77.5 E).
4. Heat model (check vs MCD subsurface, Phoenix).
5. Mars features per band.
6. Mars 2° grid (±70°).
7. Earth library: GLOBAL grid at `earth_grid_deg` (0.5°, fallback 1°), bare/sparse ground only (WorldCover), lat ±85. Cells inside `regions.geojson` get that region's `region_id` + `name` (known analogs); all other cells are candidate new sites. EE export, local layers.
8. Earth gap-filling model.
9. Normalization.
10. kNN matching, explanations, `run_all.py`, real outputs.
11. Validation → `validation.json`.
12. Optional: deep/hybrid matcher.
13. Freeze ~5 h before deadline.

**Known Earth analog regions** (tags on the global grid, used for validation, names and drill campaigns; matching searches the whole grid): Dry Valleys (University Valley), Devon Island/Haughton, Axel Heiberg, Svalbard, Greenland margin, high + coastal Atacama, Iceland highlands, Hawaii, Lanzarote, Hanksville UT, Death Valley, Mojave, Rio Tinto, Lake Salda, Pilbara, Namib, Tibetan Plateau, Lava Beds CA, Craters of the Moon ID.

## 10. Methods
**Heat model (required).** Surface daily/yearly swings decay exponentially with depth; skin depth from thermal diffusivity (via thermal inertia) and cycle length. Mars daily swing fades in cm, yearly (~687 days) reaches ~1 m+; deeper ≈ yearly mean. Gives `temp_mean`/`temp_swing` per band. Assumes uniform ground.

**Earth gap-filling (required, supervised).** XGBoost/LightGBM predicts `ice`, `ground_class` and missing bedrock depth from surface temps, ERA5-Land, elevation, slope, latitude, GLiM, SoilGrids, aridity. Labels: CCI, ground ice map, boreholes. Spatial block CV (hold out regions). Earth only; outputs marked `predicted`.

**kNN matcher (required, default).** Same features for Mars and Earth cells → climate features as within-planet percentiles, terrain log + z-score (Earth stats), categories match/mismatch → weighted Gower distance (handles mixed types, skips missing) → nearest neighbours with 200 km spacing → match % via exponential (typical good ≈ 75) → best/worst features by share of distance; confidence lowered for missing, modelled, predicted or deep values.

**Deep matcher (optional).** `matcher: deep|hybrid`; if `models/` is empty, fall back to kNN with a warning. 64×64 patches at 500 m, ~10 layers from the features. ResNet-18 (10-channel input) → 128-d embedding. SimCLR contrastive + DANN planet-adversarial (gradient reversal). 20–50k patches, half Mars (±70°), half Earth bare ground; stride 16 px; exclude 50 km around validation sites; drop >20% missing; first run ~10k. FAISS search; hybrid = 0.6 kNN + 0.4 deep. Explain by channel occlusion. ~1–2 h per Colab run. Ship whichever wins validation; if not validated ~8 h before deadline, ship kNN.

## 11. Validation
Pairs (team confirms sources): Phoenix→Dry Valleys; Arcadia→Devon Island; Jezero→Lake Salda; Gale→Hanksville; Meridiani→Rio Tinto; hyper-arid plains→Atacama; Tharsis plains→Hawaii/Iceland.
Recall@10/20 overall and per band; random baseline (1,000 runs); kNN vs deep vs hybrid; ablation by feature group; leave-one-out or reasoned weights. Physics checks: heat model vs MCD/Phoenix; gap model on held-out regions. `validation.json` is shown in the app as accuracy.

## 12. Risks → fallback
- Earth Engine slow/blocked → Planetary Computer + downloaded ERA5-Land
- MCD temps hard to get → thermal inertia + latitude + heat model
- Coordinate bugs → Jezero check; plot before matching
- Thin Earth subsurface data → ERA5-Land + gap model (labelled predicted)
- Coarse Mars subsurface → regional context, lower confidence
- Big rasters → coarser global versions
- Rock maps don't align → 5 broad categories
- Twins cluster in one region → spacing rule, varied regions
- Grid files too big → one per band, top 3, short keys, coarser grid
- Web blocked → fake outputs from step 1
- Deep fails → it's optional; ship kNN
- Time → steps 1–11 are the project; freeze 5 h early

## 13. Timeline
Sat first hour: steps 0–1 / team sets up, starts research. Sat evening: 2–7 / web on fake data, research CSVs. Sat night: 8–10, real outputs / web switches. Overnight: exports or deep training. Sun morning: 11 (+12 if on track) / weather, sliders, pitch. Deadline −5 h: freeze / video, slides. Deadline −1 h: submit.

---
## 14. Showcase sites and research (team)
_TBD_

## 15. Website (team: Archit, Waleed)
> AI agents working on `web/`: this section is everything you need from the backend. Don't edit `pipeline/`; ask Jad (scoring) or Dominic (data) for changes.

### 15a. Data the web app reads (all static JSON in `output/`)
Build against the placeholder files first (they carry `"fake": true`; show a small "demo data" tag while it's true). The shapes below don't change when the real data arrives.

**`output/grid/<band>.json`**: one file per band (`skin`, `upper_metre`, `drill_zone`, `deep`). On a map click, snap to the nearest `cells[]` entry (2° grid, lat ±70, lon −180..180 east-positive).
```
{band, grid_deg, matcher, fake,
 cells: [{cell_id, lat, lon,
          mars: {temp_mean: 205.1, frozen_fraction: 1.0, ice: "yes", rock: "volcanic", ...},  // raw values, null = unknown
          twins: [{region_id, name, new_site, lat, lon, match_pct, level, best:[...], worst:[...], confidence}]}]}
```
**`output/showcase.json`**: same twin shape, for the quick-jump showcase sites, all bands, top 5.

**Twin fields: how to show them**
| Field | Meaning | Display suggestion |
|---|---|---|
| `name` | Known analog ("Devon Island") or a generated label ("New site, 120 km from Devon Island") | Title of the result card |
| `new_site` | `true` = outside all known analog regions: a candidate site nobody has tested | **"New site" badge.** This is our headline feature; make it visible |
| `match_pct` | 0–100, relative: a typical best match ≈ 75 | Number on the card |
| `level` | `excellent` ≥85, `good` ≥70, `fair` ≥50, `poor` ≥30, `no_match` <30, `insufficient_data` (under half the features could be compared) | Coloured pill. `insufficient_data` = grey, "Not enough data", hide the % |
| `best` / `worst` | Up to 3 feature names each | "Matched on" / "Differs on" lists, using the feature `label` from `earth_library.json` |
| `confidence` | `high` / `medium` / `low` (lower for missing, modelled, predicted or deep data) | Small tag; "low" in amber |
| `lat`, `lon` | Earth location | Marker on the Earth map (Leaflet / Google Maps) |

Twins are already sorted best first and at least 200 km apart.

**`output/validation.json`**: model accuracy, shown in an "How accurate is this?" panel.
- Headline: `overall.recall_at_3` ("found the known analog in its top 3 for X% of known pairs") vs `overall.random_recall_at_3` (chance). Also `overall.mean_rank` vs `overall.random_mean_rank` out of `n_regions` Earth places.
- `per_band` has the same numbers per depth band; `ablation_mean_rank_change` = how much worse it gets without each feature (bigger = more important). Good for a small bar chart.
- Plan fields `recall_at_10`, `recall_at_20`, `random_recall_at_20`, `n_pairs`, `notes` are also present.

**`output/earth_library.json`**: for the advanced weight sliders (re-score in the browser, kNN only).
```
{fake, levels: {excellent:85, good:70, fair:50, poor:30}, min_coverage: 0.5, spacing_km: 200,
 bands: {<band>: {
   features: [{name, label, unit, compare, scale, min, max, codes, mars_quantiles,
               earth_log_mean, earth_log_std}],     // only the keys that compare method needs
   weights: {<feature>: weight},                    // default slider positions (0 = off)
   steepness: 0.12,                                 // match % curve
   places: [{id, name, new_site, lat, lon, v: [..]}] // Earth values, already normalized, same order as features; null = missing
 }}}
```
Re-scoring one clicked Mars cell (same maths as `pipeline/matching/match.py`):
1. Normalize each raw Mars value `x` from the grid file by its feature's `compare`:
   `percentile` → position of `x` in `mars_quantiles` (101 values, 0..1, interpolate) · `absolute`, `steps` → `x` · `log_z` → `(ln(1+max(x,0)) − earth_log_mean) / earth_log_std` · `same` → `codes[x]` (lower-case; yes/no for ice).
2. For each Earth place and feature: `d = |v − m| / s` capped at 1, where `s` = `scale` (absolute, log_z), `max − min` (steps) or 1 (percentile). For `same`: `d = 0` if equal, else 1. Skip if either value is null.
3. `distance = Σ(w·d) / Σ(w)` over the features that weren't skipped (w = slider weight). `coverage = Σ(w of non-skipped) / Σ(all w)`.
4. Sort by distance; take the top 3, skipping any within `spacing_km` of one already picked (haversine).
5. `match_pct = 100 · exp(−distance / steepness)`; `level` from `levels`; if `coverage < min_coverage` → `insufficient_data`.
6. "Matched on" = up to 3 features with the smallest `d` (and `d < 0.5`); "differs on" = up to 3 with the largest `w·d` (and `d > 0`).
Keep the pipeline's `confidence` from the grid file; the browser doesn't recompute it.

### 15b. UI design
_TBD (Archit, Waleed)_

## 16. Weather backend (team)
_TBD_

## 17. Pitch (team)
_TBD_
