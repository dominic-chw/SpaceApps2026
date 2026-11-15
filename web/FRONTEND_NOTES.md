# Planetary Twin frontend

This folder is the **only** change to the original SpaceApps2026 repository. The interface follows `docs/plan.md`, especially §§2–3, 6, 8, 15, and never writes to `pipeline/`, `output/`, `weights/`, `tests/`, `models/`, `notebooks/`, or shared files.

## Run (no venv and no package installation)

From the repository root:

```powershell
py -3.11 -m http.server 8000
```

Then open **http://localhost:8000/web/**. Any Python 3 installation works: `py -m http.server 8000`. Alternatively, serve the root folder using VS Code Live Server. Internet is needed only for the optional Google font; everything else is vanilla HTML, CSS, and JS.

## Contract

Frontend reads, without modifying:

- `output/grid/<band>.json`: uses the exact `cells[].{cell_id,lat,lon,mars,twins}` shape, including `fake`.
- `output/showcase.json`: optional richer showcase results (one of `sites[]`/`cells[]` or a single matching site; `bands[band]`/`site[band]`/`site.band`). The plan describes this less rigidly than the grid format. Confirm the concrete structure with Dominic if the display doesn't detect it.
- `output/validation.json`: shows recall@10, recall@20, random baseline, and pair count without making up metrics.
- `sites/showcase.csv`, `research/earth_campaigns.csv`: optional quick-jumps and sourced field campaign references.
- `output/earth_library.json`: not yet used for rescoring. The plan requires the same normalized weighted-Gower formula as the backend, but the reference implementation and JSON shape were not in the uploaded repository. The advanced UI intentionally does **not** claim to re-score.

Coordinates use ±70° latitude with east-positive longitudes, 2° grid snap matching `pipeline/common/coords.py`. Deep (>3 m) is marked experimental. Only backend-generated scores are shown. Synthetic data carries a conspicuous banner. Missing files display an honest empty state instead of invented predictions.

The Mars explorer is now a **rotatable, zoomable, clickable orthographic globe** rendered with vanilla canvas JavaScript (`mars-globe.js`). Drag rotates, wheel zooms, click selects Mars coordinates, and keyboard arrows adjust the selected coordinate. The globe terrain is **procedural illustration, not orbital imagery**, and is not used for the matching model. The Earth diagram is an equirectangular coordinate plot, not a live basemap. Map tiles can be connected later without changing the output contract. Weather is labelled unavailable until `functions/` provides the Meteomatics proxy.

## Frontend files

- `index.html`: layout and accessible controls.
- `styles.css`: responsive design.
- `app.js`: input events, grid JSON lookup, output rendering, CSV export, and optional source loading.

## Checks before PR

Run `pytest tests/` if installed and review `git diff --name-only`: only `web/` files should appear. Use branch `owner/task` and open a PR to `main` as stated in `CLAUDE.md`.


## About page
The top navigation opens `web/about.html` as a separate page. Return to the Explorer using the Explorer tab or the back link. No backend files or data contracts were modified.
