# CLAUDE.md
- Session start: read `docs/plan.md` once; don't re-read unless asked. Ask which folder the user owns; edit only that folder.
- Project: Mars Drill-Site Analog Finder. Click Mars + pick depth → matching Earth drill-test sites. Mars only. Default matcher `knn`; deep is optional, never required.
- Ownership: jadmwehbi-design (scoring) `pipeline/matching/ pipeline/validation/ weights/ tests/test_matching*.py tests/test_validation*.py`; Dominic (data acquisition, Mars and Earth) `data_sources.yaml pipeline/data_fetch/ pipeline/earth_library/ pipeline/earth_ml/ data/` plus `output/ tests/ models/ notebooks/`; coworker `pipeline/mars_features/ pipeline/physics/` (read from `data/mars/`); team `sites/ research/ web/ functions/ pitch/`. Shared (PR only): `CLAUDE.md README.md config.yaml docs/plan.md`.
- Contract: plan.md section 3. Don't change formats, column names or vocabularies.
- Bands `skin upper_metre drill_zone deep`; rock `volcanic sedimentary clay_sulfate icy impact_mixed`; ground `dry_regolith ice_cemented massive_ice bedrock`; hardness 0–3.
- Rules: never commit `data/ models/ .env`; logic in `.py`; clear notebooks; `pytest tests/` before PRs; branch `owner/task`, PRs to `main`.
- Replies: short; show only changed lines, not whole files.
