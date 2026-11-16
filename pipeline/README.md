# pipeline/

The contract and formats are in `docs/plan.md` section 3. Only add keys or files there; never rename or remove them. The methods are in section 10.

## Rebuild everything from raw data

```bash
# 1. Earth layers from Earth Engine (needs EE_PROJECT in .env), direct fetch, no Drive
python -m pipeline.earth_library.ee_export --fetch-grid --grid-deg 0.5 --min-bare 0   # all land cells, ~20 min
python -m pipeline.earth_library.ee_export --fetch-antarctica                         # ice-free Antarctica
python -m pipeline.earth_library.ee_export --fetch-modis-lc                           # glacier / barren fractions
python -m pipeline.data_fetch.fetch --only pelletier_dtb glim                          # bedrock depth, rock map
#    ESA CCI Permafrost PFR/ALT/GTD 2021 -> data/earth/cci_permafrost/ (data_sources.yaml: cci_permafrost)

# 2. Earth layer table + permafrost model + subsurface ensemble
#    (writes output/ml_report.json, models/permafrost.joblib, models/profile_ensemble.pt; ensemble needs torch)
python -m pipeline.earth_library.build_layers

# 3. Feature tables, matching, learned weights, validation -> output/*.json
python -m pipeline.run_all --build --learn

# Optional: re-run the subsurface-net benchmarks (~25 min, CPU) -> output/ml_report.json profile_nn / profile_ensemble
python -m pipeline.earth_ml.profile_nn            # add --ensemble-only to skip the single-net benchmark
```

## Run order (`python -m pipeline.run_all`; the matcher comes from `config.yaml`)
1. `mars_features/`: extract features and build the grid (uses `physics/heat_model.py`).
2. `earth_library/`:
   - Build the layers. Ground ice is **ESA CCI** inside 25–85 N and the **`earth_ml/permafrost.py`** classifier elsewhere.
   - Then `earth_ml/train_subsurface.py` fills the ground class wherever it is still unknown.
   - Ground temperature at 0–10 m comes from **ESA CCI** inside its coverage and the **`earth_ml/profile_nn.py`** ensemble elsewhere. Where the ensemble is extrapolating, the value is left blank. `build_grid.py` uses it as `temp_mean` for `upper_metre`, `drill_zone` and `deep`, and falls back to the heat model where it is blank.
3. `matching/`: normalize, then match (weighted Gower kNN with the weights from `config.yaml paths.weights`), then explain.
4. `validation/`:
   - `evaluate.py` writes `output/validation.json`.
   - `learn_weights.py` learns the weights and adds the `learned_weights` block (with `--learn`).
5. `deep/` is optional and never required.

## The ML models

| | Permafrost classifier (`earth_ml/permafrost.py`) | Learned match weights (`validation/learn_weights.py`) |
|---|---|---|
| Predicts | ground ice yes/no per 0.5° Earth cell | one weight multiplier per feature |
| Labels | ESA CCI Permafrost 2021 (PFR ≥ 0.5 ice, < 0.1 no ice) | 12 published Mars–Earth analog pairs |
| Honest test | 10° spatial block CV and leave-region-out (Alaska, Tibet, Svalbard, Siberia) | leave-one-Mars-site-out |
| Baselines | majority class, latitude-only logistic, temperature rule | hand-set weights and random ranking |
| Output | `output/ml_report.json`, plus `ice` / `data_quality` in the Earth table | `weights/learned.json`, `output/validation.json.learned_weights` |

**Subsurface network (`earth_ml/profile_nn.py`, PyTorch, optional)**
- **What it is:** a multi-task MLP that predicts P(ice), active-layer thickness, and mean annual ground temperature at 0, 1, 2, 5 and 10 m. It uses surface data only, with the same inputs as the permafrost classifier except that latitude enters as |lat|.
- **Physics-informed loss:** penalises profiles that kink below 2 m, have a gradient no real ground has, or have ice above 0 °C.
- **Labels:** ESA CCI GTD/ALT/PFR 2021.
- **Benchmarks** (spatial CV and leave-region-out): the same network without the physics term, gradient boosting per depth, and the heat model.
- **Production:** a 5-model deep ensemble (the spread is the models' agreement). It flags a cell as **extrapolating** when the Mahalanobis distance in its hidden layer exceeds the 99% level of held-out calibration blocks. Flagged cells fall back to the heat model; this catches hot deserts, which have no CCI labels.
- **Outputs:** `output/ml_report.json` (`profile_nn`, `profile_ensemble`, `subsurface_production`) and `output/earth_subsurface.json`, which feeds the app's "Underground temperature" row.
- **Tests:** `tests/test_profile_nn.py`.

Guards against leakage:
- `perm_prob`, `ice_label`, `ground_label` and the CCI layers are never predictors.
- `tests/test_permafrost.py` checks this.
- `tests/test_learn_weights.py` checks that a held-out site is never in its training fold.

Data lives in `data/` (gitignored; sources in `data_sources.yaml`). Outputs go to `output/`.
