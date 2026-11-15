# Planetary Twin frontend

Static site; reads `output/*.json` (docs/plan.md §3, §15a) and never writes to it.

## Run
From the repo root: `py -m http.server 8000`, open http://localhost:8000/web/. Internet is needed for Leaflet (cdnjs), the Inter font and satellite tiles.

## Files
- `index.html`, `styles.css`, `app.js`: explorer. Click the globe, change depth or pick a landing site and results load immediately. State is in the URL hash (`#lat=18.4&lon=77.5&band=drill_zone`), so views can be shared.
- `mars-globe.js` + `mars.jpg`: picker globe (Viking MDIM 2.1 texture).
- `rescore.js`: in-browser kNN re-scoring for the weight sliders (same formula as `pipeline/matching/match.py`, §15a).
- `about.html`: project summary.

## Satellite views
- Mars: Esri OnMars `CTX1` (Murray Lab CTX mosaic, ~5 m/px) over `MDIM` (Viking colour), geographic 512 px tiles → Leaflet `EPSG4326`, `tileSize 512`, `zoomOffset -1`.
- Earth: Esri World Imagery.

## Data handling
- Grid lookups use the nearest cell in `output/grid/<band>.json`; coordinates within 0.06° of a showcase site use `output/showcase.json` (top 5 + data quality).
- `validation.json` is only shown when `fake` is false; the placeholder file is hidden rather than shown with a warning.
- `research/earth_campaigns.csv` is optional; when present, campaigns show under the selected Earth site.

## Ground profile
Side view of Mars vs the selected Earth site through all four bands (ground type pattern, rock colour, ice, hardness, frozen share). Mars values come from each band's grid file (loaded in the background) or showcase.json; Earth values are decoded from earth_library.json. The per-depth match % for non-selected bands is computed with `TwinRescore.scorePlaces` (same formula as the pipeline). Clicking a layer switches depth.

## Ice rule (frontend)
If the Mars cell has no ground ice at any loaded depth, Earth sites with permanent ground ice at any depth are dropped from the twins and the list is refilled from earth_library.json with the same formula. The UI names the sites it left out. This belongs in pipeline/matching long term.

## Untested sites, tooltips, globe highlight
- Untested = `new_site` Earth places (outside the named analog regions). Cards tag them; when every card is a known (tested) site, a row below the cards lists up to 3 untested ones within 8 points of the lowest card (browser-scored, ice rule applied). Names come from OpenStreetMap Nominatim reverse geocoding (1 request/s, cached).
- "Matched on / Differs on" chips show a tooltip: feature definition, data source, comparison method, and this pair's Mars/Earth values, distance and weight.
- Globe highlight (`#...&hl=ice|sedimentary|volcanic|easy`, default off) colours drill-zone grid cells via `MarsGlobe.setOverlay`.
