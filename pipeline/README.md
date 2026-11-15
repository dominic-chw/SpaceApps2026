# pipeline/

Contract and formats: `docs/plan.md` section 3 (fixed). Methods: section 10.

Run order (`python -m pipeline.run_all`, matcher from `config.yaml`):
1. `mars_features/` extract + grid (uses `physics/heat_model.py`)
2. `earth_library/` regions + layers, then `earth_ml/` gap-filling
3. `matching/` normalize -> match -> explain
4. `validation/evaluate.py` -> `output/validation.json`
5. `deep/` is optional; never required.

Data lives in `data/` (gitignored, Drive). Outputs go to `output/`.
