/* Planetary Twin explorer. Reads the pipeline outputs in output/ (docs/plan.md §3, §15a); never changes them. */
(() => {
  'use strict';

  const ROOT = new URL('../', window.location.href);
  const BAND_KEYS = ['skin', 'upper_metre', 'drill_zone', 'deep'];
  const BANDS = {
    skin:        {name: 'Skin', range: '0–10 cm', hint: 'Surface layer, 0–10 cm.'},
    upper_metre: {name: 'Upper metre', range: '10 cm–1 m', hint: 'Shallow ground, 10 cm–1 m.'},
    drill_zone:  {name: 'Drill zone', range: '1–3 m', hint: 'Typical drill target, 1–3 m.'},
    deep:        {name: 'Deep', range: '>3 m', hint: 'Below 3 m. Experimental: less subsurface data, lower confidence.'}
  };
  const FALLBACK_LABELS = {
    temp_mean_rank: 'Coldness', temp_mean: 'Mean temperature', frozen_fraction: 'Frozen share of the year',
    temp_swing: 'Temperature swing', ice: 'Ground ice', ground_class: 'Ground type', hardness: 'Hardness',
    rock: 'Rock type', slope_deg: 'Slope', roughness_m: 'Surface roughness', dryness: 'Dryness'
  };
  // Hover text for "Matched on" / "Differs on": what the feature is, where it comes from, how it is compared.
  const FEATURE_INFO = {
    temp_mean_rank: 'Mean ground temperature at this depth, ranked as a percentile within its own planet (Mars is 60–100 K colder, so absolute kelvin would never match). From a 1-D heat-conduction model: surface daily and yearly temperature waves decay as e^(−z/δ), with skin depth δ = √(κP/π) set by thermal inertia (TES on Mars; ERA5-Land / SoilGrids on Earth). Compared as |Δ percentile|.',
    frozen_fraction: 'Share of the year this layer stays below 273 K, from the same heat model (mean plus the damped yearly wave). Compared as an absolute difference in fraction of the year (0–1).',
    temp_swing: 'Amplitude of the temperature cycle at this depth. The daily wave dies out within centimetres; the yearly wave (687 sols on Mars) reaches about a metre. Percentile within each planet, compared as |Δ percentile|.',
    ice: 'Ground ice present in this layer (yes/no). Mars: Odyssey neutron hydrogen (water-equivalent hydrogen ≥ 25%) plus an ice-table depth that deepens away from the poles. Earth: ESA CCI permafrost, NSIDC ground-ice map, a cold-ground rule (< 271 K) and the gap-filling model; ice only below the ~0.5 m active layer. Same or different: distance 0 or 1.',
    ground_class: 'Ground type: dry regolith, ice-cemented, massive ice or bedrock. Mars: thermal inertia (≥ 700 SI → bedrock) and ice fraction (≥ 70% → massive ice). Earth: permafrost and ground-ice maps, gaps filled by a gradient-boosted model (SoilGrids, ERA5-Land, terrain as predictors). Categorical: distance 0 or 1.',
    hardness: 'How hard the layer is to drill, on a 0–3 scale derived from ground type and rock (dry regolith 0, ice-bearing 2, bedrock 2–3 by rock type). Compared as |Δ| / 3.',
    rock: 'Broad rock class: volcanic, sedimentary, clay/sulfate, icy or impact-mixed. Mars: USGS geologic map SIM 3292 units. Earth: GLiM global lithology. Categorical: distance 0 or 1.',
    slope_deg: 'Mean terrain slope at 500 m resolution (MOLA 463 m on Mars, Copernicus DEM on Earth). Compared after ln(1 + slope) and a z-score on Earth statistics, so 1° vs 2° counts more than 20° vs 21°.',
    roughness_m: 'Standard deviation of elevation in a 3 × 3 window of 500 m pixels: a proxy for boulders, ridges and rough ground. Compared after ln(1 + x) and a z-score on Earth statistics.',
    dryness: 'How dry the surface environment is. Mars: water-vapour column (Mars Climate Database). Earth: Global Aridity Index. Percentile within each planet. Used for the two shallow layers only.'
  };
  const LEVEL_TEXT = {excellent: 'Excellent', good: 'Good', fair: 'Fair', poor: 'Poor', no_match: 'No match', insufficient_data: 'Not enough data'};
  const GROUND_TEXT = {dry_regolith: 'Dry regolith', ice_cemented: 'Ice-cemented ground', massive_ice: 'Massive ice', bedrock: 'Bedrock'};
  const SNAP_SHOWCASE_DEG = 0.06;
  const MARS_TILES = 'https://astro.arcgis.com/arcgis/rest/services/OnMars/{layer}/MapServer/tile/{z}/{y}/{x}';
  const EARTH_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

  const $ = id => document.getElementById(id);
  const state = {
    lat: 18.4, lon: 77.5, band: 'drill_zone', picked: false,   // picked: no search until the user chooses a spot
    cell: null, nearest: null, column: {}, gridDeg: 2, twins: [], baseTwins: [], hiddenIcy: [], candidates: [], selected: 0, site: null, highlight: '',
    grids: {}, showcase: null, library: null, validation: null, mlReport: null, subsurface: null, campaigns: [], customWeights: null, token: 0,
    weightMode: null,   // 'hand' | 'learned' | 'custom' (weights panel)
    mode: 'forward',
    rev: {query: null, place: null, near: [], snapKm: 0, basis: 'surface', band: 'skin', grid: null, top: [], pct: null, selected: 0, token: 0, pending: null}
  };
  let globe = null, marsMap = null, earthMap = null, marsLayer = null, earthLayer = null, searchTimer = 0, revMap = null, revLayer = null;

  /* ---------- helpers ---------- */
  const wrapLon = lon => ((lon + 180) % 360 + 360) % 360 - 180;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v)) ? Number(v) : null;
  const deg = (v, pos, neg, d = 1) => `${Math.abs(v).toFixed(d)}°${v >= 0 ? pos : neg}`;
  const coordText = (lat, lon, d = 1) => `${deg(Number(lat), 'N', 'S', d)} ${deg(Number(lon), 'E', 'W', d)}`;
  const pctText = v => { const n = num(v); return n === null ? null : n.toFixed(1).replace(/\.0$/, ''); };
  const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  const safeUrl = s => { try { const u = new URL(String(s)); return ['https:', 'http:'].includes(u.protocol) ? u.href : null; } catch { return null; } };
  const featureLabel = name => state.library?.bands?.[state.band]?.features?.find(f => f.name === name)?.label || FALLBACK_LABELS[name] || String(name).replaceAll('_', ' ');
  const levelFor = pct => { const l = state.library?.levels || {}; return pct >= (l.excellent ?? 85) ? 'excellent' : pct >= (l.good ?? 70) ? 'good' : pct >= (l.fair ?? 50) ? 'fair' : pct >= (l.poor ?? 30) ? 'poor' : 'no_match'; };
  const km = (a, b) => 12742 * Math.asin(Math.min(1, Math.sqrt(Math.sin((b.lat - a.lat) * Math.PI / 360) ** 2 + Math.cos(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) * Math.sin(wrapLon(b.lon - a.lon) * Math.PI / 360) ** 2)));

  const pool = () => state.twins.concat(state.candidates);
  const current = () => pool()[state.selected];

  function notice(message, kind = 'warning') {
    const n = $('notice');
    n.hidden = !message; n.className = `notice ${kind}`; n.textContent = message || '';
  }

  async function request(path, asText = false) {
    const res = await fetch(new URL(path, ROOT), {cache: 'no-cache'});
    if (!res.ok) throw new Error(`${path} returned HTTP ${res.status}`);
    return asText ? res.text() : res.json();
  }
  const optional = (path, asText = false) => request(path, asText).catch(() => null);

  function gridFor(band) {
    if (!state.grids[band]) state.grids[band] = request(`output/grid/${band}.json`).catch(err => { delete state.grids[band]; throw err; });
    return state.grids[band];
  }

  function angular(aLat, aLon, bLat, bLon) {
    const r = Math.PI / 180, dLat = (bLat - aLat) * r, dLon = wrapLon(bLon - aLon) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(aLat * r) * Math.cos(bLat * r) * Math.sin(dLon / 2) ** 2;
    return 2 * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function nearestCell(grid, lat, lon) {
    let best = null, bestD = Infinity;
    for (const c of grid?.cells || []) {
      const cl = num(c.lat), co = num(c.lon); if (cl === null || co === null) continue;
      const d = angular(lat, lon, cl, co); if (d < bestD) { bestD = d; best = c; }
    }
    return best;
  }

  function csvRows(text) {
    if (!text) return [];
    const rows = [], line = []; let field = '', quoted = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === '"') { if (quoted && text[i + 1] === '"') { field += '"'; i++; } else quoted = !quoted; }
      else if (ch === ',' && !quoted) { line.push(field); field = ''; }
      else if ((ch === '\n' || ch === '\r') && !quoted) { if (ch === '\r' && text[i + 1] === '\n') i++; line.push(field); field = ''; if (line.some(x => x.trim())) rows.push(line.splice(0)); else line.length = 0; }
      else field += ch;
    }
    line.push(field); if (line.some(x => x.trim())) rows.push(line);
    const keys = (rows.shift() || []).map(k => k.trim());
    return rows.map(r => Object.fromEntries(keys.map((k, i) => [k, (r[i] || '').trim()])));
  }

  /* ---------- Earth values from earth_library.json (normalized; decoded back where possible) ---------- */
  // Every other land cell (output/earth_lookup.json): the reverse search can use the clicked cell itself
  // (a city, farmland...). Never offered as a Mars twin. Loaded on the first reverse search.
  let lookupPromise = null;
  function loadLookup() {
    if (!lookupPromise) lookupPromise = optional('output/earth_lookup.json').then(d => {
      if (!d?.ids) return null;
      d.index = new Map(d.ids.map((id, i) => [id, i]));
      return (state.lookup = d);
    });
    return lookupPromise;
  }
  function placeFor(band, id) {
    const p = state.library?.bands?.[band]?.places?.find(x => x.id === id);
    if (p) return p;
    const d = state.lookup, i = d?.index?.get(id);
    if (i === undefined || !d.bands?.[band]) return null;
    return {id, name: '', new_site: true, lookup: true, lat: d.lat[i], lon: d.lon[i], v: d.bands[band].v[i]};
  }
  function nearestLookup(lat, lon) {
    const d = state.lookup; if (!d) return null;
    let best = null;
    for (let i = 0; i < d.ids.length; i++) {
      if (Math.abs(d.lat[i] - lat) > 2) continue;
      const dist = km({lat, lon}, {lat: d.lat[i], lon: d.lon[i]});
      if (!best || dist < best.d) best = {d: dist, p: placeFor('skin', d.ids[i])};
    }
    return best;
  }

  function earthProfile(placeId, band) {
    const lib = state.library?.bands?.[band];
    const place = placeFor(band, placeId);
    if (!place) return null;
    const out = {};
    lib.features.forEach((f, i) => {
      const v = place.v[i]; if (v === null || v === undefined) return;
      if (f.compare === 'same' && f.codes) out[f.name] = Object.keys(f.codes).find(k => f.codes[k] === v) ?? null;
      else if (f.compare === 'steps' || f.compare === 'absolute') out[f.name] = v;   // stored as real values
    });
    return out;
  }
  // Earth place has permanent ground ice somewhere in its column (permafrost, ice sheet margin, snowfields).
  const icyCache = new Map();
  function earthIsIcy(placeId) {
    if (icyCache.has(placeId)) return icyCache.get(placeId);
    const v = BAND_KEYS.some(b => { const e = earthProfile(placeId, b); return e && (e.ice === 'yes' || e.ground_class === 'ice_cemented' || e.ground_class === 'massive_ice'); });
    icyCache.set(placeId, v);
    return v;
  }
  function marsIsIcy() {
    return Object.values(state.column).some(c => c?.mars && (c.mars.ice === 'yes' || c.mars.ice === true || ['ice_cemented', 'massive_ice'].includes(c.mars.ground_class)));
  }

  /* Rule: an Earth site with permanent ground ice cannot be the twin of a Mars spot with no ground ice at any depth.
     Such twins are removed and the list is refilled from the Earth library with the same scoring formula. */
  function libraryWithout(band, exclude) {
    const lib = state.library?.bands?.[band];
    return lib ? {...lib, places: lib.places.filter(p => !exclude(p.id))} : null;
  }
  function applyIceRule(twins, weights) {
    state.hiddenIcy = [];
    if (!state.library || marsIsIcy()) return twins;
    const keep = [], n = Math.max(3, twins.length);
    for (const t of twins) (earthIsIcy(t.region_id) ? state.hiddenIcy : keep).push(t);
    if (!state.hiddenIcy.length) return twins;
    const lib = libraryWithout(state.band, earthIsIcy);
    if (lib && window.TwinRescore && state.cell?.mars) {
      try {
        const conf = twins[0]?.confidence;
        const extra = window.TwinRescore.scorePlaces(state.cell.mars, lib, state.library, weights || lib.weights);
        const spacing = Number(state.library.spacing_km ?? 200);
        for (const m of extra) {
          if (keep.length >= n) break;
          if (keep.some(k => k.region_id === m.region_id || km(k, m) < spacing)) continue;
          const {_dist, ...rest} = m;
          keep.push({...rest, confidence: conf});
        }
      } catch { /* keep what we have */ }
    }
    return keep.sort((a, b) => (num(b.match_pct) ?? -1) - (num(a.match_pct) ?? -1));
  }

  /* Untested candidates: Earth places outside the known analog regions (new_site), shown even when they
     fall a little below the cut-off of the main list (within UNTESTED_MARGIN points of its lowest match). */
  const UNTESTED_MARGIN = 8;
  function findCandidates() {
    state.candidates = [];
    const lib = state.library?.bands?.[state.band];
    if (!lib || !window.TwinRescore || !state.cell?.mars || !lib.places.some(p => p.new_site)) return;
    const shown = state.twins;
    if (shown.some(isNew)) return;   // only offer extra untested sites when every top site is a known (tested) one
    const pcts = shown.map(t => num(t.match_pct)).filter(x => x !== null);
    const floor = (pcts.length ? Math.min(...pcts) : 0) - UNTESTED_MARGIN;
    const dry = !marsIsIcy();
    const sub = {...lib, places: lib.places.filter(p => p.new_site && !(dry && earthIsIcy(p.id)) && !shown.some(t => t.region_id === p.id))};
    const spacing = Number(state.library.spacing_km ?? 200);
    try {
      for (const m of window.TwinRescore.scorePlaces(state.cell.mars, sub, state.library, state.customWeights || lib.weights)) {
        if (m.match_pct < floor || state.candidates.length >= 3) break;
        if ([...shown, ...state.candidates].some(k => km(k, m) < spacing)) continue;
        const {_dist, ...rest} = m;
        state.candidates.push({...rest, confidence: null, untested: true});
      }
    } catch { state.candidates = []; }
  }

  function renderUntested() {
    const box = $('untested'), n0 = state.twins.length;
    box.hidden = !state.candidates.length;
    if (!state.candidates.length) { box.innerHTML = ''; return; }
    box.innerHTML = `<span class="u-label" tabindex="0">Also worth a look: no field tests yet<span class="tip" role="tooltip"><strong>Sites nobody has field-tested yet</strong><span>Earth places outside the ${esc(KNOWN_REGION_COUNT())} known analog regions, where NASA, ESA and university teams have run field or drill tests. These are new candidates: their match is within ${UNTESTED_MARGIN} points of the list above. Scored in your browser with the pipeline formula; no confidence is computed for them.</span></span></span>
      ${state.candidates.map((c, k) => `<button type="button" class="u-chip" data-i="${n0 + k}" aria-pressed="${n0 + k === state.selected}"><b>${esc(pctText(c.match_pct))}%</b>${nameSpan(c)}</button>`).join('')}`;
    box.querySelectorAll('[data-i]').forEach(el => el.addEventListener('click', () => selectTwin(Number(el.dataset.i))));
  }
  const KNOWN_REGION_COUNT = () => (state.library?.bands?.[state.band]?.places || []).filter(p => !p.new_site).length || 'known';

  /* ---------- drilling-target highlight on the globe (drill zone, 1–3 m) ---------- */
  const HIGHLIGHTS = {
    ice: {
      legend: '<i style="background:#78beff"></i>Massive ice <i style="background:#b4dcff"></i>Ice-cemented ground',
      note: 'Ground ice at this depth: Odyssey neutron hydrogen plus the ice-table model.',
      color: c => c.ground_class === 'massive_ice' ? [120, 190, 255, 220] : c.ground_class === 'ice_cemented' || c.ice === 'yes' ? [180, 220, 255, 170] : null
    },
    sedimentary: {
      legend: '<i style="background:#f0c070"></i>Sedimentary, flat <i style="background:#c9a060"></i>Sedimentary',
      note: 'Water-laid units (USGS geologic map): the best bet for hydrated minerals such as clays and sulfates.',
      color: c => c.rock === 'sedimentary' ? (num(c.slope_deg) !== null && c.slope_deg < 1 ? [240, 192, 112, 220] : [201, 160, 96, 170]) : null
    },
    volcanic: {
      legend: '<i style="background:#ff7a55"></i>Volcanic',
      note: 'Volcanic units (USGS geologic map): basaltic bedrock and lava plains.',
      color: c => c.rock === 'volcanic' ? [255, 122, 85, 190] : null
    },
    easy: {
      legend: '<i style="background:#8fdc8f"></i>Dry, soft, flat and smooth',
      note: 'Dry regolith, hardness 0, slope under 1° and roughness below the Mars median: easiest to land on and drill.',
      color: (c, ctx) => c.ground_class === 'dry_regolith' && Number(c.hardness) === 0 && num(c.slope_deg) !== null && c.slope_deg < 1 && num(c.roughness_m) !== null && c.roughness_m < ctx.roughMedian ? [143, 220, 143, 200] : null
    },
    difficulty: {
      legend: '<span class="ramp-wrap"><span class="ramp difficulty"></span><span class="ramp-ends"><span>Easy</span><span>Hard</span></span></span>',
      note: 'Drilling difficulty at this depth: hardness (0–3), +1 for ice-cemented ground or massive ice, +0.5 on slopes over 5°.',
      color: c => {
        const h = num(c.hardness); if (h === null) return null;
        const score = Math.min(4, h + (['ice_cemented', 'massive_ice'].includes(c.ground_class) ? 1 : 0) + (num(c.slope_deg) > 5 ? .5 : 0)) / 4;
        const stops = [[95, 200, 120], [230, 205, 80], [235, 130, 60], [205, 55, 55]];
        const x = score * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(x)), f = x - i;
        return [...stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f)), 200];
      }
    }
  };
  async function setHighlight(kind) {
    state.highlight = kind;
    if (state.cell) writeHash();
    $('highlight').querySelectorAll('[data-hl]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.hl === kind)));
    const legend = $('globe-legend'), band = state.band;
    $('highlight-label').textContent = `Highlight drilling targets on the globe (${BANDS[band].range})`;
    if (state.mode !== 'forward') return;
    if (!kind || !HIGHLIGHTS[kind]) { globe?.setOverlay(null); legend.hidden = true; return; }
    legend.hidden = false; legend.innerHTML = '<span>Loading…</span>';
    try {
      const g = await gridFor(band);
      if (state.highlight !== kind || state.band !== band || state.mode !== 'forward') return;
      const gd = num(g.grid_deg) || 2, w = Math.round(360 / gd), h = Math.round(180 / gd);
      const rough = g.cells.map(c => num(c.mars?.roughness_m)).filter(x => x !== null).sort((a, b) => a - b);
      const ctx = {roughMedian: rough[Math.floor(rough.length / 2)] ?? Infinity};
      const px = new Uint8ClampedArray(w * h * 4);
      let n = 0;
      for (const c of g.cells) {
        const col = HIGHLIGHTS[kind].color(c.mars || {}, ctx); if (!col) continue;
        const r = Math.min(h - 1, Math.max(0, Math.floor((90 - Number(c.lat)) / gd))), q = Math.min(w - 1, Math.max(0, Math.floor((wrapLon(Number(c.lon)) + 180) / gd)));
        px.set(col, (r * w + q) * 4); n++;
      }
      globe?.setOverlay({w, h, px});
      const share = Math.round(100 * n / g.cells.length);
      legend.innerHTML = `<div class="gl-keys">${HIGHLIGHTS[kind].legend}</div><p>${esc(HIGHLIGHTS[kind].note)} ${n.toLocaleString()} cells (${share}% of the map).</p>`;
    } catch (e) { legend.innerHTML = `<span>Could not load the ${esc(BANDS[band].name.toLowerCase())} grid.</span>`; }
  }

  /* Readable names for untested sites (OpenStreetMap Nominatim reverse geocoding, max 1 request/s, cached). */
  const geo = new Map();
  let geoQueue = Promise.resolve();
  const isNew = t => t && (t.new_site || t.untested);
  const siteName = t => (isNew(t) && geo.get(t.region_id)) || t?.name || t?.region_id || 'Earth site';
  const nameSpan = t => `<span data-name-id="${esc(t.region_id)}">${esc(siteName(t))}</span>`;
  function geocode(list) {
    for (const t of list) {
      if (!isNew(t) || geo.has(t.region_id) || num(t.lat) === null) continue;   // only unnamed (not yet field-tested) sites
      geo.set(t.region_id, null);
      geoQueue = geoQueue.then(() => fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=6&accept-language=en&lat=${t.lat}&lon=${t.lon}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          const a = d?.address || {};
          const label = [a.state || a.region || a.province || a.county, a.country].filter(Boolean).join(', ');
          if (!label) return;
          geo.set(t.region_id, `Near ${label}`);
          document.querySelectorAll(`[data-name-id="${CSS.escape(t.region_id)}"]`).forEach(el => { el.textContent = `Near ${label}`; });
          if (current()?.region_id === t.region_id) $('earth-name').textContent = `Earth · Near ${label}`;
        })
        .catch(() => {})
        .then(() => new Promise(r => setTimeout(r, 1100))));
    }
  }

  /* ---------- maps ---------- */
  function initMaps() {
    if (!window.L) { notice('Map library failed to load; satellite views need an internet connection.', 'error'); return; }
    marsMap = L.map('mars-map', {crs: L.CRS.EPSG4326, minZoom: 2, maxZoom: 14, zoomSnap: 0.5, worldCopyJump: true});
    marsMap.attributionControl.setPrefix(false);
    // Esri OnMars services use a geographic 512 px tiling with 2×1 tiles at level 0, hence zoomOffset −1.
    const marsOpts = {tileSize: 512, zoomOffset: -1, minZoom: 2, maxZoom: 14};
    L.tileLayer(MARS_TILES.replace('{layer}', 'MDIM'), {...marsOpts, maxNativeZoom: 10}).addTo(marsMap);
    L.tileLayer(MARS_TILES.replace('{layer}', 'CTX1'), {...marsOpts, maxNativeZoom: 13, attribution: 'CTX: NASA/JPL/MSSS/Murray Lab · Viking: NASA/USGS · Esri'}).addTo(marsMap);
    marsMap.setView([state.lat, state.lon], 7);
    marsLayer = L.layerGroup().addTo(marsMap);

    earthMap = L.map('earth-map', {minZoom: 2, maxZoom: 18, zoomSnap: 0.5, worldCopyJump: true});
    earthMap.attributionControl.setPrefix(false);
    L.tileLayer(EARTH_TILES, {maxNativeZoom: 18, attribution: 'Esri, Maxar, Earthstar Geographics'}).addTo(earthMap);
    earthMap.setView([20, 0], 2);
    earthLayer = L.layerGroup().addTo(earthMap);

    const ro = new ResizeObserver(() => { marsMap.invalidateSize(); earthMap.invalidateSize(); });
    ro.observe($('mars-map')); ro.observe($('earth-map'));
  }

  function renderMars() {
    const c = state.nearest;
    $('mars-caption').textContent = c
      ? `${coordText(state.lat, state.lon)} · ${state.site ? state.site.name : `${state.gridDeg}° cell at ${coordText(c.lat, c.lon, 0)}`}`
      : coordText(state.lat, state.lon);
    if (!marsMap) return;
    marsLayer.clearLayers();
    if (c && !state.site) {
      const h = state.gridDeg / 2, la = Number(c.lat), lo = Number(c.lon);
      const bounds = [[la - h, lo - h], [la + h, lo + h]];
      L.rectangle(bounds, {color: '#ffffff', weight: 1, opacity: .7, fill: false, dashArray: '4 4', interactive: false}).addTo(marsLayer);
      marsMap.fitBounds(bounds, {padding: [24, 24], animate: false});
    } else {
      marsMap.setView([state.lat, state.lon], 8, {animate: false});
    }
    L.circleMarker([state.lat, state.lon], {radius: 6, color: '#ffffff', weight: 2, fillColor: '#c8714c', fillOpacity: 1, interactive: false}).addTo(marsLayer);
  }

  function renderEarth() {
    const t = current();
    if (!t) { $('earth-name').textContent = 'Earth'; $('earth-caption').textContent = '—'; earthLayer?.clearLayers(); return; }
    const p = pctText(t.match_pct);
    $('earth-name').textContent = `Earth · ${siteName(t)}`;
    $('earth-caption').textContent = `${coordText(t.lat, t.lon)}${t.level !== 'insufficient_data' && p ? ` · ${p}% match` : ''}`;
    if (!earthMap) return;
    earthLayer.clearLayers();
    pool().forEach((tw, i) => {
      const la = num(tw.lat), lo = num(tw.lon); if (la === null || lo === null) return;
      const cand = i >= state.twins.length;
      const icon = L.divIcon({className: '', html: `<div class="pin${cand ? ' cand' : ''}${i === state.selected ? ' on' : ''}">${cand ? 'U' : i + 1}</div>`, iconSize: [22, 22], iconAnchor: [11, 11]});
      L.marker([la, lo], {icon, title: siteName(tw), zIndexOffset: i === state.selected ? 1000 : 0})
        .on('click', () => selectTwin(i)).addTo(earthLayer);
    });
    earthMap.setView([Number(t.lat), Number(t.lon)], 9, {animate: false});
  }

  /* ---------- results ---------- */
  function setSub(text) { $('result-sub').textContent = text; }

  function renderTwins() {
    const list = $('twins');
    $('export-button').disabled = !state.twins.length;
    $('brief-button').disabled = !state.twins.length;
    if (!state.twins.length) { list.innerHTML = ''; return; }
    list.style.setProperty('--n', state.twins.length);
    list.innerHTML = state.twins.map((t, i) => {
      const insufficient = t.level === 'insufficient_data';
      const p = pctText(t.match_pct);
      const conf = String(t.confidence || '').toLowerCase();
      return `<li><button type="button" class="twin" data-i="${i}" aria-pressed="${i === state.selected}">
        <span class="top"><span class="pct">${insufficient || p === null ? '—' : `${p}<small>%</small>`}</span><span class="rank">#${i + 1}</span></span>
        <span class="name" title="${esc(t.name || t.region_id)}">${nameSpan(t)}</span>
        <span class="meta"><span class="level ${esc(t.level)}">${esc(LEVEL_TEXT[t.level] || t.level || '')}</span>
          ${t.new_site ? '<span class="tag new" title="No recorded field or drill tests here (NASA, ESA or university campaigns). A candidate site worth a first visit.">No field tests yet</span>' : '<span class="tag known" title="A known Mars analog region where field or drill tests have been run.">Field-tested analog</span>'}
          ${conf === 'low' ? '<span class="tag low">Low confidence</span>' : ''}</span>
      </button></li>`;
    }).join('');
    list.querySelectorAll('[data-i]').forEach(el => el.addEventListener('click', () => selectTwin(Number(el.dataset.i))));
  }

  function renderWhy() {
    const t = current();
    if (!t) { $('why').innerHTML = ''; return; }
    const chips = (arr, cls = '') => (arr || []).length
      ? `<div class="chips">${arr.map(v => { const name = typeof v === 'object' ? v.feature ?? v.name : v;
          return `<span class="chip ${cls}" tabindex="0">${esc(featureLabel(name))}${featureTip(t, name)}</span>`; }).join('')}</div>`
      : '<span class="muted">—</span>';
    const conf = t.confidence ? cap(String(t.confidence)) : '—';
    const camps = state.campaigns.filter(c => c.region_id && c.region_id === t.region_id);
    const hidden = state.hiddenIcy.length
      ? `<p class="why-note">Left out ${state.hiddenIcy.map(h => esc(siteName(h))).join(', ')}: ${state.hiddenIcy.length === 1 ? 'it has' : 'they have'} permanent ground ice or snow cover, and this spot on Mars has no ground ice.</p>`
      : '';
    $('why').innerHTML = `
      <div><h3>Matched on</h3>${chips(t.best)}</div>
      <div><h3>Differs on</h3>${chips(t.worst, 'diff')}</div>
      <div class="conf"><h3>Confidence</h3>${esc(conf)}</div>
      ${hidden}
      ${undergroundHtml(t)}
      ${camps.length ? `<div class="campaigns"><h3>Drill and field campaigns here</h3>${camps.map(c => {
        const u = safeUrl(c.source_url);
        return `<p>${esc(c.campaign)}${c.year ? ` (${esc(c.year)})` : ''}${c.what_was_tested ? ` · ${esc(c.what_was_tested)}` : ''}${u ? ` · <a href="${esc(u)}" target="_blank" rel="noopener noreferrer">source</a>` : ''}</p>`;
      }).join('')}</div>` : ''}`;
  }

  // Underground temperature for an Earth twin (output/earth_subsurface.json, pipeline/earth_ml/profile_nn.py).
  function undergroundHtml(t) {
    const sub = state.subsurface, rec = sub?.places?.[t.region_id];
    if (!rec) return '';
    const SRC = {
      cci: 'ESA CCI Permafrost (satellite-driven ground model).',
      nn: `Predicted by our physics-informed neural network, a 5-model ensemble${num(rec.sd) !== null ? `; the models agree to within ±${num(rec.sd).toFixed(1)} °C` : ''}. ESA's map does not cover this place.`,
    };
    SRC.extrapolated = 'Heat-model estimate (yearly surface mean at every depth).';
    const warn = rec.src === 'extrapolated'
      ? `<p class="ug-warn" role="note"><b>Neural net not used: it is extrapolating.</b> This ground is unlike anything it was trained on (its training data is cold northern ground), so we fall back to the heat model.</p>`
      : '';
    const cells = (sub.depths_m || []).map((d, i) => {
      const v = num(rec.t?.[i]);
      return `<div class="ug-cell${v !== null && v < 0 ? ' frozen' : ''}"><span>${d === 0 ? 'Surface' : `${d} m`}</span><b>${v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)} °C`}</b></div>`;
    }).join('');
    return `<div class="underground"><h3>Underground temperature (yearly mean)</h3>
      <div class="ug-row">${cells}</div><p class="ug-src">${SRC[rec.src] || ''}</p>${warn}</div>`;
  }

  const ord = n => `${n}${(n % 100 >= 11 && n % 100 <= 13) ? 'th' : ({1: 'st', 2: 'nd', 3: 'rd'}[n % 10] || 'th')}`;
  function showNorm(f, x, raw) {
    if (x === null || x === undefined) return '—';
    switch (f.compare) {
      case 'percentile': return `${ord(Math.round(x * 100))} percentile${f.name === 'temp_mean_rank' && num(raw) !== null ? ` (${(num(raw) - 273.15).toFixed(0)} °C)` : ''}`;
      case 'same': return cap(String(Object.keys(f.codes || {}).find(k => f.codes[k] === x) ?? x).replaceAll('_', ' '));
      case 'steps': return `${x} of ${f.max ?? 3}`;
      case 'absolute': return f.name === 'frozen_fraction' ? `${Math.round(x * 100)}% of year` : String(+Number(x).toFixed(2));
      case 'log_z': return `z = ${x >= 0 ? '+' : ''}${Number(x).toFixed(2)}${num(raw) !== null ? ` (${(+num(raw).toFixed(1))}${f.unit ? ' ' + f.unit : ''})` : ''}`;
      default: return String(x);
    }
  }
  function featureTip(t, name) {
    const info = FEATURE_INFO[name];
    const lib = state.library?.bands?.[state.band];
    const i = lib?.features?.findIndex(f => f.name === name) ?? -1;
    let vals = '';
    if (lib && i >= 0 && window.TwinRescore && state.cell?.mars) {
      const f = lib.features[i];
      const raw = state.cell.mars[name === 'temp_mean_rank' ? 'temp_mean' : name];
      const m = window.TwinRescore.normalized(state.cell.mars, f);
      const e = lib.places.find(p => p.id === t.region_id)?.v?.[i] ?? null;
      let d = null;
      if (m !== null && e !== null) {
        const sc = f.compare === 'steps' ? Number(f.max) - Number(f.min) : (f.compare === 'percentile' || f.compare === 'same') ? 1 : Number(f.scale) || 1;
        d = f.compare === 'same' ? (m === e ? 0 : 1) : Math.min(1, Math.abs(e - m) / sc);
      }
      const w = Number((state.customWeights || lib.weights || {})[name] ?? 1);
      vals = `<span class="tip-vals"><span>Mars</span><b>${esc(showNorm(f, m, raw))}</b><span>Earth</span><b>${esc(showNorm(f, e))}</b>
        <span>Distance</span><b>${d === null ? '—' : d.toFixed(2)} <em>(0 same, 1 opposite) · weight ${w.toFixed(1)}</em></b></span>`;
    }
    if (!info && !vals) return '';
    return `<span class="tip" role="tooltip"><strong>${esc(featureLabel(name))}</strong>${info ? `<span>${esc(info)}</span>` : ''}${vals}</span>`;
  }

  /* ---------- ground profile: side view of Mars vs Earth down through the four depth bands ---------- */
  function stratumAttrs(v) {
    if (!v) return '';
    return `data-ground="${esc(v.ground_class || 'unknown')}" data-rock="${esc(v.rock || 'unknown')}"`;
  }
  function stratumText(v, other, side) {
    if (!v) return `<span class="st-label muted">${side === 'earth' ? 'No profile for this site' : 'Loading…'}</span>`;
    const diff = k => other && v[k] != null && other[k] != null && String(v[k]) !== String(other[k]) ? ' class="d"' : '';
    const bits = [];
    if (v.rock) bits.push(`<span${diff('rock')}>${esc(cap(String(v.rock).replaceAll('_', ' ')))}</span>`);
    if (v.hardness != null) bits.push(`<span${diff('hardness')}>hardness ${esc(v.hardness)}/3</span>`);
    if (v.ice != null) bits.push(`<span${diff('ice')}>${v.ice === 'yes' ? 'ice' : 'no ice'}</span>`);
    const extra = [];
    if (num(v.temp_mean) !== null) extra.push(`${(num(v.temp_mean) - 273.15).toFixed(0)} °C`);
    if (num(v.frozen_fraction) !== null) extra.push(`frozen ${Math.round(num(v.frozen_fraction) * 100)}% of year`);
    return `<span class="st-label"><strong${diff('ground_class')}>${esc(GROUND_TEXT[v.ground_class] || cap(String(v.ground_class || '—')))}</strong>
      <small>${bits.join(' · ')}</small>${extra.length ? `<small>${extra.join(' · ')}</small>` : ''}</span>`;
  }

  function bandMatch(t, band) {
    if (!t) return null;
    if (band === state.band && !state.customWeights) return num(t.match_pct);
    const mars = state.column[band]?.mars, lib = state.library?.bands?.[band];
    if (!mars || !lib || !window.TwinRescore) return null;
    // Same weights as the cards: your sliders for this depth, the chosen preset for the other depths.
    const w = band === state.band && state.customWeights ? state.customWeights
      : presetWeights(lib, state.weightMode === 'learned' ? 'learned' : 'hand') || lib.weights;
    try {
      const all = window.TwinRescore.scorePlaces(mars, {...lib, places: lib.places.filter(p => p.id === t.region_id)}, state.library, w);
      return all.length ? all[0].match_pct : null;
    } catch { return null; }
  }

  function renderProfile() {
    const box = $('profile'), t = current();
    if (!state.cell || !t) { box.innerHTML = '<p class="muted">Run a search to compare the ground column.</p>'; return; }
    $('profile-sub').innerHTML = `${esc(state.site ? state.site.name : coordText(state.lat, state.lon))} vs ${nameSpan(t)}. Click a layer to switch depth.`;
    const sel = BAND_KEYS.indexOf(state.band);
    const marsSurf = state.column[BAND_KEYS.find(b => state.column[b]?.mars)]?.mars || {};
    const surf = [num(marsSurf.slope_deg) !== null ? `slope ${num(marsSurf.slope_deg).toFixed(1)}°` : '', num(marsSurf.roughness_m) !== null ? `roughness ${num(marsSurf.roughness_m).toFixed(1)} m` : ''].filter(Boolean).join(' · ');
    const rows = BAND_KEYS.map((b, i) => {
      const m = state.column[b]?.mars || null;
      const e = earthProfile(t.region_id, b);
      const pct = bandMatch(t, b);
      const cls = `${i === sel ? ' sel' : ''}${i <= sel ? ' drilled' : ''}`;
      return `<div class="p-row${cls}" data-band="${b}" role="button" tabindex="0" aria-pressed="${i === sel}" aria-label="${esc(BANDS[b].range)}">
        <div class="p-depth"><strong>${esc(BANDS[b].range)}</strong><span>${esc(BANDS[b].name)}</span></div>
        <div class="stratum" ${stratumAttrs(m)}>${stratumText(m, e, 'mars')}</div>
        <div class="p-mid">${pct === null ? '<span class="muted">—</span>' : `<strong>${pctText(pct)}%</strong><span class="level ${levelFor(pct)}">${LEVEL_TEXT[levelFor(pct)]}</span>`}</div>
        <div class="stratum" ${stratumAttrs(e)}>${stratumText(e, m, 'earth')}</div>
      </div>`;
    }).join('');
    box.innerHTML = `<div class="p-head"><div></div><div><strong>Mars</strong>${surf ? `<span>${esc(surf)}</span>` : ''}</div><div>Match</div><div><strong>Earth</strong>${nameSpan(t)}</div></div>
      <div class="p-surface"><div></div><div class="ground-line mars"></div><div></div><div class="ground-line earth"></div></div>
      ${rows}
      <div class="p-legend">
        <span><i data-ground="dry_regolith" data-rock="sedimentary"></i>Dry regolith</span>
        <span><i data-ground="ice_cemented" data-rock="sedimentary"></i>Ice-cemented</span>
        <span><i data-ground="massive_ice"></i>Massive ice</span>
        <span><i data-ground="bedrock" data-rock="volcanic"></i>Bedrock</span>
        <span class="d-key">Text in orange differs between the planets</span>
      </div>`;
    box.querySelectorAll('.p-row').forEach(r => {
      const go = () => { if (r.dataset.band !== state.band) { setBand(r.dataset.band); search(); } };
      r.addEventListener('click', go);
      r.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
    });
  }

  /* ---------- "How accurate is this?": matching, learned weights, ground-ice model ---------- */
  const pctOf = x => num(x) === null ? '—' : `${(num(x) * 100).toFixed(0)}%`;
  const rankOf = x => num(x) === null ? '—' : num(x).toFixed(0);
  function bestCells(rows, key, lowerIsBetter = false) {
    const vals = rows.map(r => num(r[key])).filter(x => x !== null);
    const best = vals.length ? (lowerIsBetter ? Math.min(...vals) : Math.max(...vals)) : null;
    return r => num(r[key]) !== null && num(r[key]) === best ? ' class="best"' : '';
  }
  function renderValidation() {
    const v = state.validation, ml = state.mlReport?.permafrost_model;
    const real = v && !v.fake;
    if (!real && !ml) { $('validation-card').hidden = true; return; }
    $('validation-card').hidden = false;
    const findings = [], details = [];
    const regionsWith = (key, metric) => Object.values(ml?.leave_region_out || {}).filter(r => num(r.model?.[metric]) !== null && num(r[key]?.[metric]) !== null);
    const meanOf = (rs, key, metric) => rs.length ? rs.reduce((a, r) => a + num(r[key][metric]), 0) / rs.length : null;

    // 1. Ground ice: gradient boosting vs neural net vs the best rule (held-out regions, balanced accuracy).
    if (ml) {
      const rs = regionsWith('temperature_rule', 'balanced_accuracy');
      const gbm = meanOf(rs, 'model', 'balanced_accuracy'), rule = meanOf(rs, 'temperature_rule', 'balanced_accuracy');
      const nnRs = ml.nn ? regionsWith('nn', 'balanced_accuracy') : [];
      const nnv = ml.nn ? meanOf(nnRs, 'nn', 'balanced_accuracy') : null;
      const names = rs.length ? Object.entries(ml.leave_region_out).filter(([, r]) => rs.includes(r)).map(([n]) => n).join(', ') : '';
      findings.push(`<article class="finding">
        <span class="finding-kicker">Ground-ice model</span>
        <h3>${!ml.nn || nnv === null || gbm === null ? 'Gradient boosting beats the physical rule on regions it never saw'
          : Math.abs(nnv - gbm) < 0.02 ? 'A neural network and gradient boosting perform the same, and both beat the physical rule'
          : `The ${nnv > gbm ? 'neural network' : 'gradient-boosted model'} is slightly ahead (${Math.max(nnv, gbm).toFixed(2)} vs ${Math.min(nnv, gbm).toFixed(2)}), and both beat the physical rule`}</h3>
        <div class="finding-bars">
          ${[['Gradient boosting', gbm, 'gbm'], ...(ml.nn ? [['Neural net (PyTorch)', nnv, 'nn']] : []), ['Temperature rule', rule, 'rule']].map(([n, x, cls]) =>
            `<div class="fbar ${cls}"><span>${n}</span><i style="--w:${x === null ? 0 : Math.max(0, (x - 0.5) / 0.5) * 100}%"></i><b>${x === null ? '—' : x.toFixed(2)}</b></div>`).join('')}
        </div>
        <p>Balanced accuracy for "is there ground ice?", averaged over regions held out of training (${esc(names)}). 0.5 is a coin flip; with ${rs.length} regions, small gaps between the two models are within noise. The models predict ice where ESA's permafrost map stops, such as Antarctica's Dry Valleys.</p>
      </article>`);
    }
    // 1b. Underground temperature: physics-informed neural net vs plain net vs gradient boosting vs heat model.
    const pn = state.mlReport?.profile_nn, pe = state.mlReport?.profile_ensemble;
    const PN_COLS = [['physics_informed_nn', 'Physics-informed NN', 'nn'], ['plain_nn', 'Same NN, no physics', 'plain'],
      ['gbm_per_depth', 'Gradient boosting', 'gbm'], ['heat_model', 'Heat model', 'rule']];
    const pnRegions = Object.entries(pn?.leave_region_out || {}).filter(([, r]) => r.physics_informed_nn);
    const pnMean = (key, metric) => {
      const xs = pnRegions.map(([, r]) => num(r[key]?.[metric])).filter(x => x !== null);
      return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
    };
    if (pn && pnRegions.length) {
      const vals = PN_COLS.map(([k, n, cls]) => [n, pnMean(k, 'rmse_10m'), cls]);
      const worst = Math.max(...vals.map(v => v[1] ?? 0)) || 1;
      const nnv = vals[0][1], heat = vals[3][1];
      const vio = [num(pn.spatial_block_cv?.physics_informed_nn?.physics_violation_rate), num(pn.spatial_block_cv?.plain_nn?.physics_violation_rate)];
      const ens = Object.values(pe?.leave_region_out || {}).filter(r => num(r.rmse_flagged) !== null && num(r.rmse_not_flagged) !== null);
      const ratio = ens.length ? ens.reduce((a, r) => a + num(r.rmse_flagged) / num(r.rmse_not_flagged), 0) / ens.length : null;
      findings.push(`<article class="finding">
        <span class="finding-kicker">Underground temperature</span>
        <h3>${(() => {
          const gbmv = vals[2][1];
          const lead = nnv && heat ? `Learned models predict ground temperature 10 m down about ${(heat / Math.min(nnv, gbmv ?? nnv)).toFixed(1)}× closer than the heat model` : 'Learned models beat the heat model 10 m down';
          if (gbmv === null || nnv === null) return lead;
          if (Math.abs(gbmv - nnv) < 0.05) return `${lead}; the physics-informed neural net matches gradient boosting and keeps profiles physical`;
          return gbmv < nnv
            ? `${lead}. Gradient boosting is slightly more accurate; the physics-informed neural net keeps profiles physical and knows when it is guessing`
            : `${lead}. The physics-informed neural net is the most accurate`;
        })()}</h3>
        <div class="finding-bars">
          ${vals.map(([n, x, cls]) => `<div class="fbar ${cls}"><span>${n}</span><i style="--w:${x === null ? 0 : (x / worst) * 100}%"></i><b>${x === null ? '—' : x.toFixed(1) + '°'}</b></div>`).join('')}
        </div>
        <p>Error at 10 m (°C, lower is better), averaged over regions held out of training (${esc(pnRegions.map(([n]) => n).join(', '))}). ${vio.every(x => x !== null) ? `The physics term in its loss cuts physically impossible profiles from ${pctOf(vio[1])} to ${pctOf(vio[0])}.` : ''} ${ratio ? `A 5-model ensemble flags places unlike anything it trained on; there its error is ${ratio.toFixed(1)}× higher, so we fall back to the heat model.` : ''}</p>
      </article>`);
    }
    // 2. Matching finds known analogs far better than chance.
    if (real) {
      const o = v.overall || {};
      findings.push(`<article class="finding">
        <span class="finding-kicker">Matching</span>
        <h3>Known analogs land in the top 10 ${num(o.random_recall_at_10) > 0 ? `${Math.round(num(o.recall_at_10) / num(o.random_recall_at_10))}× more often than chance` : 'well above chance'}</h3>
        <div class="finding-stat"><strong>${pctOf(o.recall_at_10)}</strong><span>of ${esc(v.n_pairs)} published Mars–Earth pairs in the top 10 of ${esc((v.n_regions ?? 0).toLocaleString())} Earth places. Random: ${pctOf(o.random_recall_at_10)}.</span></div>
      </article>`);
    }
    // 3. Learned weights: honest negative result.
    const lw = v?.learned_weights;
    if (lw?.loo) {
      findings.push(`<article class="finding">
        <span class="finding-kicker">Learned weights</span>
        <h3>${lw.adopted ? 'Weights learned from known pairs beat the hand-tuned ones' : 'Learning the weights from 12 pairs did not beat hand-tuning'}</h3>
        <div class="finding-stat"><strong>${rankOf(lw.loo.learned.mean_rank)} <small>vs</small> ${rankOf(lw.loo.hand_set.mean_rank)}</strong><span>mean rank of the known site, learned vs hand-tuned, on Mars sites held out of training. Random: ${rankOf(lw.loo.hand_set.random_mean_rank)}. ${lw.adopted ? '' : 'Hand-tuned weights stay the default.'}</span></div>
      </article>`);
    }

    // ---------- the data behind the findings ----------
    if (ml) {
      const regions = Object.entries(ml.leave_region_out || {}).filter(([, r]) => r.model);
      const cols = [['model', 'Gradient boosting'], ...(ml.nn ? [['nn', 'Neural net (PyTorch)']] : []), ['model_unguarded', 'Gradient boosting, no physics guard'],
        ['temperature_rule', 'Temperature rule'], ['latitude_logistic', 'Latitude only'], ['majority', 'Majority class']];
      const metric = state.mlMetric || 'balanced_accuracy';
      const row = (label, block) => {
        const vals = cols.map(([k]) => num(block[k]?.[metric]));
        const known = vals.filter(x => x !== null);
        const best = known.length ? Math.max(...known) : null;
        return `<tr><th>${esc(label)}${block.model?.n ? ` <small>${block.model.n.toLocaleString()} cells</small>` : ''}</th>${vals.map(x => `<td${x !== null && x === best ? ' class="best"' : ''}>${x === null ? '—' : x.toFixed(2)}</td>`).join('')}</tr>`;
      };
      const METRICS = {balanced_accuracy: 'Balanced accuracy', f1: 'F1', roc_auc: 'ROC-AUC'};
      const conf = ml.calibration?.conformal || {};
      const top = (ml.permutation_importance || []).slice(0, 3).map(r => esc(featureLabelAny(r.feature))).join(', ');
      details.push(`<section class="acc wide">
        <div class="acc-head"><h3>Ground-ice model, by held-out test</h3>
          <div class="segmented metric-switch" role="radiogroup" aria-label="Metric">${Object.entries(METRICS).map(([k, n]) => `<button type="button" role="radio" data-metric="${k}" aria-checked="${k === metric}">${n}</button>`).join('')}</div></div>
        <div class="table-scroll"><table class="acc-table"><thead><tr><th>Held out</th>${cols.map(([, n]) => `<th>${n}</th>`).join('')}</tr></thead><tbody>
          ${row('10° spatial blocks', ml.spatial_block_cv || {})}
          ${regions.map(([name, r]) => row(name, r)).join('')}
        </tbody></table></div>
        <p class="validation-note">Labels: ESA CCI Permafrost (${esc((ml.n_labelled ?? 0).toLocaleString())} cells, 25–85° N). Inputs: soil and surface temperatures, terrain, aridity, soil texture, rock type; never the permafrost map itself. Best value per row in bold; "—" means the region has only one class. Most informative inputs: ${top || '—'}. The physics guard sets ice to "no" where even the 1–3 m soil never freezes; without it the model leaned towards ice in hot deserts it had never seen. ${conf.empirical_coverage !== undefined ? `90% conformal prediction sets covered ${pctOf(conf.empirical_coverage)} of cells in held-out blocks${num(conf.empirical_coverage) < 0.9 ? ', so the model is overconfident when the region changes' : ''}.` : ''}</p>
      </section>`);
    }
    if (pn && pnRegions.length) {
      const cell = (block, k, m, fmt) => { const x = num(block?.[k]?.[m]); return x === null ? '—' : fmt(x); };
      const pr = (label, block) => {
        const xs = PN_COLS.map(([k]) => num(block?.[k]?.rmse_10m));
        const known = xs.filter(x => x !== null), best = known.length ? Math.min(...known) : null;
        return `<tr><th>${esc(label)}</th>${xs.map(x => `<td${x !== null && x === best ? ' class="best"' : ''}>${x === null ? '—' : x.toFixed(2)}</td>`).join('')}
          <td>${cell(block, 'physics_informed_nn', 'physics_violation_rate', pctOf)} / ${cell(block, 'plain_nn', 'physics_violation_rate', pctOf)}</td></tr>`;
      };
      const fx = x => num(x) === null ? '—' : num(x).toFixed(2);
      const ensRows = Object.entries(pe?.leave_region_out || {});
      details.push(`<section class="acc wide">
        <h3>Underground temperature at 10 m, by held-out test (error in °C)</h3>
        <div class="table-scroll"><table class="acc-table"><thead><tr><th>Held out</th>${PN_COLS.map(([, n]) => `<th>${n}</th>`).join('')}<th>Impossible profiles, physics NN / plain NN</th></tr></thead><tbody>
          ${pr('10° spatial blocks', pn.spatial_block_cv)}
          ${pnRegions.map(([n, r]) => pr(n, r)).join('')}
        </tbody></table></div>
        ${ensRows.length ? `<div class="table-scroll"><table class="acc-table"><thead><tr><th>Ensemble, held out</th><th>Flagged "extrapolating"</th><th>Error, flagged</th><th>Error, not flagged</th><th>Truth within ±2 spread</th></tr></thead><tbody>
          ${ensRows.map(([n, r]) => `<tr><th>${esc(n)}</th><td>${pctOf(r.share_flagged)}</td><td>${fx(r.rmse_flagged)}</td><td>${fx(r.rmse_not_flagged)}</td><td>${pctOf(r.within_2_std)}</td></tr>`).join('')}
        </tbody></table></div>` : ''}
        <p class="validation-note">Labels: ESA CCI Permafrost ground temperature at 0, 1, 2, 5 and 10 m (${esc((pn.n_cells ?? 0).toLocaleString())} cells, 25–85° N). One PyTorch network predicts ground ice, thaw depth and the whole temperature profile from surface data only. Its loss also penalises profiles that break heat conduction (kinks, or gradients no real ground has). The heat model assumes the temperature at depth equals the yearly mean. Best value per row in bold.</p>
      </section>`);
    }
    if (lw?.loo) {
      const rows = [
        {name: 'Hand-tuned', ...lw.loo.hand_set},
        {name: 'Learned', ...lw.loo.learned},
        {name: 'Random', recall_at_1: lw.loo.hand_set.random_recall_at_1, recall_at_3: lw.loo.hand_set.random_recall_at_3,
          recall_at_10: lw.loo.hand_set.random_recall_at_10, mean_rank: lw.loo.hand_set.random_mean_rank}
      ];
      const b1 = bestCells(rows, 'recall_at_1'), b3 = bestCells(rows, 'recall_at_3'), b10 = bestCells(rows, 'recall_at_10'), bm = bestCells(rows, 'mean_rank', true);
      details.push(`<section class="acc">
        <h3>Weights, leave-one-Mars-site-out</h3>
        <div class="table-scroll"><table class="acc-table"><thead><tr><th></th><th>Top 1</th><th>Top 3</th><th>Top 10</th><th>Mean rank</th></tr></thead><tbody>
          ${rows.map(r => `<tr><th>${r.name}</th><td${b1(r)}>${pctOf(r.recall_at_1)}</td><td${b3(r)}>${pctOf(r.recall_at_3)}</td><td${b10(r)}>${pctOf(r.recall_at_10)}</td><td${bm(r)}>${rankOf(r.mean_rank)}</td></tr>`).join('')}
        </tbody></table></div>
        <p class="validation-note">One multiplier per feature, learned on ${esc(lw.n_groups - 1)} Mars sites and tested on the one left out, for each of ${esc(lw.n_groups)} sites (${esc(lw.n_pairs)} pairs).</p>
      </section>`);
    }
    if (real) {
      const o = v.overall || {};
      details.push(`<section class="acc">
        <h3>Matching, all ${esc(v.n_pairs)} known pairs</h3>
        <div class="table-scroll"><table class="acc-table"><thead><tr><th></th><th>Top 1</th><th>Top 10</th><th>Top 20</th><th>Mean rank</th></tr></thead><tbody>
          <tr><th>Planetary Twin</th><td class="best">${pctOf(o.recall_at_1)}</td><td class="best">${pctOf(o.recall_at_10)}</td><td class="best">${pctOf(o.recall_at_20)}</td><td class="best">${rankOf(o.mean_rank)}</td></tr>
          <tr><th>Random</th><td>${pctOf(o.random_recall_at_1)}</td><td>${pctOf(o.random_recall_at_10)}</td><td>${pctOf(o.random_recall_at_20)}</td><td>${rankOf(o.random_mean_rank)}</td></tr>
        </tbody></table></div>
        <p class="validation-note">Each pair's Earth site (e.g. the Dry Valleys for Phoenix) is ranked among ${esc((v.n_regions ?? 0).toLocaleString())} Earth places kept 200 km apart.</p>
      </section>`);
    }
    $('validation').innerHTML = `<p class="acc-why"><strong>Why machine learning?</strong> Satellite maps of what lies underground (permafrost, ground temperature) stop at 25° N, but several of the best Mars analogs, such as Antarctica's Dry Valleys and the high Andes, are further south. The models below learn from the places those maps cover and predict the rest. Each is compared with the simple rule it replaces, on whole regions it never trained on.</p>
      <div class="findings">${findings.join('')}</div>
      ${details.length ? `<h3 class="data-title">The data behind these findings</h3><div class="acc-grid">${details.join('')}</div>` : ''}`;
    $('validation').querySelectorAll('[data-metric]').forEach(b => b.addEventListener('click', () => { state.mlMetric = b.dataset.metric; renderValidation(); }));
  }
  const ML_LABELS = {
    soil_temperature_level_1_mean: 'Soil temperature 0–7 cm', soil_temperature_level_2_mean: 'Soil temperature 7–28 cm',
    soil_temperature_level_3_mean: 'Soil temperature 28–100 cm', soil_temperature_level_4_mean: 'Soil temperature 1–2.9 m',
    skin_temperature_mean: 'Surface temperature', soil_temperature_level_1_range: 'Topsoil yearly range',
    soil_temperature_level_4_range: 'Deep-soil yearly range', skin_temperature_range: 'Surface yearly range',
    lst_day_k: 'Daytime land temperature', lst_night_k: 'Night-time land temperature', elevation_m: 'Elevation',
    slope_deg: 'Slope', roughness_m: 'Roughness', aridity: 'Aridity', sand_0_2m: 'Sand content', clay_0_2m: 'Clay content',
    bdod_0_2m: 'Soil density', lat: 'Latitude', glim_class: 'Rock type'
  };
  const featureLabelAny = n => ML_LABELS[n] || FALLBACK_LABELS[n] || String(n).replaceAll('_', ' ');

  /* Weight presets: hand-tuned (weights/default.json) or learned from the known pairs (weights/learned.json). */
  const baseMode = () => state.library?.weights_in_use === 'learned.json' ? 'learned' : 'hand';
  function presetWeights(lib, mode) {
    const hand = lib.hand_weights || lib.weights;
    if (mode !== 'learned') return hand;
    if (!lib.learned_weights) return null;
    // Only ratios between weights matter (distance = sum(w*d) / sum(w)), so scale the learned set to the same
    // total as the hand-tuned one: identical rankings and match %, but values that fit the sliders.
    const sum = o => Object.values(o).reduce((a, x) => a + Math.max(0, Number(x) || 0), 0);
    const k = sum(hand) / (sum(lib.learned_weights) || 1);
    return Object.fromEntries(Object.entries(lib.learned_weights).map(([f, w]) => [f, Number(w) * k]));
  }
  const sliderMax = lib => Math.max(4, Math.ceil(Math.max(...['hand', 'learned'].flatMap(m => Object.values(presetWeights(lib, m) || {}).map(Number)))));
  function renderWeights() {
    const panel = $('weights-panel'), lib = state.library?.bands?.[state.band];
    if (!lib || !Array.isArray(lib.features) || !window.TwinRescore) { $('weights-card').hidden = true; return; }
    $('weights-card').hidden = false;
    const mode = state.weightMode || baseMode();
    const w = state.customWeights || presetWeights(lib, mode) || lib.weights || {};
    const lw = state.validation?.learned_weights?.loo;
    const hasLearned = !!lib.learned_weights;
    const note = !hasLearned ? 'Learned weights are not available for this build.'
      : mode === 'learned' ? `Learned from the known analog pairs (scaled to the same total as hand-tuned; only the ratios matter). Tested on sites they never saw: mean rank ${rankOf(lw?.learned?.mean_rank)} vs ${rankOf(lw?.hand_set?.mean_rank)} hand-tuned (random ${rankOf(lw?.hand_set?.random_mean_rank)}).`
      : mode === 'custom' ? 'Your own weights. Pick a preset to go back.'
      : 'Set by hand from the physics: ice, ground type and hardness weigh most below the surface.';
    panel.innerHTML = `<div class="weights-head">
        <div class="segmented wmode" role="radiogroup" aria-label="Weight preset">
          <button type="button" role="radio" data-wmode="hand" aria-checked="${mode === 'hand'}">Hand-tuned</button>
          <button type="button" role="radio" data-wmode="learned" aria-checked="${mode === 'learned'}" ${hasLearned ? '' : 'disabled'}>Learned</button>
        </div>
        <p class="hint">${esc(note)}</p>
      </div>
      <div class="weights">${lib.features.map(f => {
        const val = Number(w[f.name] ?? 1);
        return `<label class="weight"><span>${esc(f.label || featureLabel(f.name))}</span><output>${val.toFixed(1)}</output><input type="range" min="0" max="${sliderMax(lib)}" step="0.1" value="${val}" data-w="${esc(f.name)}"></label>`;
      }).join('')}</div>
      <div class="weight-actions"><span>Re-ranks all ${(lib.places?.length ?? 0).toLocaleString()} Earth places in your browser with the same formula as the pipeline.</span></div>`;
    let timer = 0;
    panel.querySelectorAll('[data-w]').forEach(el => el.addEventListener('input', () => {
      el.previousElementSibling.textContent = Number(el.value).toFixed(1);
      if (state.weightMode !== 'custom') {
        state.weightMode = 'custom';
        panel.querySelectorAll('[data-wmode]').forEach(b => b.setAttribute('aria-checked', 'false'));
        panel.querySelector('.weights-head .hint').textContent = 'Your own weights. Pick a preset to go back.';
      }
      clearTimeout(timer); timer = setTimeout(() => applyWeights(), 120);
    }));
    panel.querySelectorAll('[data-wmode]').forEach(b => b.addEventListener('click', () => setWeightMode(b.dataset.wmode)));
  }
  function setWeightMode(mode) {
    const lib = state.library?.bands?.[state.band];
    if (!lib) return;
    state.weightMode = mode;
    if (mode === baseMode()) {   // the pipeline's own result (keeps its confidence values)
      state.customWeights = null; state.twins = applyIceRule(state.baseTwins); state.selected = 0;
      renderWeights(); renderAll(); return;
    }
    const w = presetWeights(lib, mode);
    if (!w) return;
    renderWeights(); applyWeights(w);
  }

  function applyWeights(preset) {
    const lib = state.library?.bands?.[state.band];
    if (!state.cell?.mars || !lib) return;
    const w = preset || Object.fromEntries([...$('weights-panel').querySelectorAll('[data-w]')].map(el => [el.dataset.w, Number(el.value)]));
    try {
      const n = Math.max(3, state.baseTwins.length);
      const scored = window.TwinRescore.score(state.cell.mars, lib, state.library, w, n + 6);
      const conf = state.baseTwins[0]?.confidence;
      state.twins = applyIceRule(scored.map(t => ({...t, confidence: conf})), w).slice(0, n);
      state.customWeights = w; state.selected = 0; notice(''); renderAll();
    } catch (e) { notice(e.message, 'error'); }
  }

  function renderAll() {
    findCandidates();
    geocode(pool());
    renderTwins(); renderUntested(); renderWhy(); renderEarth(); renderProfile();
    const band = BANDS[state.band];
    $('result-title').textContent = state.site ? `Earth analogs for ${state.site.name}` : `Earth analogs for ${coordText(state.lat, state.lon)}`;
    const wl = state.customWeights ? ({learned: ' · learned weights', hand: ' · hand-tuned weights'}[state.weightMode] || ' · custom weights') : '';
    if (state.twins.length) setSub(`${band.name}, ${band.range} below the surface${wl}`);
  }

  function selectTwin(i) {
    state.selected = i;
    document.querySelectorAll('#twins [data-i], #untested [data-i]').forEach(el => el.setAttribute('aria-pressed', String(Number(el.dataset.i) === i)));
    renderWhy(); renderEarth(); renderProfile();
  }

  /* ---------- search ---------- */
  function showcaseAt(lat, lon) {
    return (state.showcase?.sites || []).find(s => Math.abs(Number(s.lat) - lat) < SNAP_SHOWCASE_DEG && Math.abs(wrapLon(Number(s.lon) - lon)) < SNAP_SHOWCASE_DEG) || null;
  }

  async function search() {
    const token = ++state.token;
    const lat = state.lat, lon = state.lon, band = state.band;
    notice('');
    if (!state.grids[band] || !state.cell) $('twins').innerHTML = '<li><div class="twin skeleton">Loading Mars data…</div></li>';
    $('find-button').disabled = true;
    try {
      const grid = await gridFor(band);
      if (token !== state.token) return;
      const nearest = nearestCell(grid, lat, lon);
      if (!nearest) throw new Error(`no Mars cells in output/grid/${band}.json`);
      const site = showcaseAt(lat, lon);
      const entry = site?.bands?.[band];
      state.site = entry && Array.isArray(entry.twins) ? site : null;
      state.gridDeg = num(grid.grid_deg) || 2;
      state.nearest = nearest;
      state.cell = state.site ? entry : nearest;

      // Mars column through all depths (showcase has it; for grid cells load the other band files).
      state.column = {};
      if (state.site) { for (const b of BAND_KEYS) state.column[b] = state.site.bands[b] || null; }
      else {
        state.column[band] = nearest;
        if (band !== 'deep') {   // the ice rule needs the deepest band
          const deep = await gridFor('deep').catch(() => null);
          if (token !== state.token) return;
          state.column.deep = deep ? nearestCell(deep, lat, lon) : null;
        }
      }

      state.baseTwins = Array.isArray(state.cell.twins) ? state.cell.twins : [];
      state.customWeights = null; state.selected = 0;
      state.twins = applyIceRule(state.baseTwins);
      if (state.weightMode === 'custom') state.weightMode = null;
      const keepPreset = state.weightMode && state.weightMode !== baseMode() && state.library?.bands?.[band];
      $('showcase').value = state.site ? state.site.site_id : '';
      writeHash();
      renderMars(); renderWeights(); renderAll();
      if (keepPreset) setWeightMode(state.weightMode);
      if (!state.twins.length) setSub('No Earth analogs found for this Mars cell.');

      if (!state.site) {
        for (const b of BAND_KEYS) {
          if (state.column[b] !== undefined) continue;
          gridFor(b).then(g => { if (token === state.token) { state.column[b] = nearestCell(g, lat, lon); renderProfile(); } }).catch(() => {});
        }
      }
    } catch (e) {
      if (token !== state.token) return;
      state.cell = null; state.twins = []; state.baseTwins = []; state.column = {};
      renderAll(); renderMars();
      notice(`Could not load match results (${e.message}). Serve the repository root, e.g. py -m http.server 8000, and open /web/.`, 'error');
    } finally {
      if (token === state.token) $('find-button').disabled = false;
    }
  }
  const searchSoon = () => { clearTimeout(searchTimer); searchTimer = setTimeout(search, 200); };

  function setCoords(lat, lon, {fields = true} = {}) {
    state.lat = Math.min(70, Math.max(-70, lat));
    state.lon = wrapLon(lon);
    state.picked = true;
    if (fields) { $('latitude').value = state.lat.toFixed(1); $('longitude').value = state.lon.toFixed(1); }
    globe?.setTarget(state.lat, state.lon);
  }

  function setBand(band) {
    state.band = band;
    $('depth').querySelectorAll('[data-band]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.band === band)));
    $('depth-hint').textContent = BANDS[band].hint;
    if (globe && state.highlight && state.mode === 'forward') setHighlight(state.highlight);
  }

  function exportCsv() {
    if (!state.twins.length) return;
    const cols = ['rank', 'region_id', 'name', 'new_site', 'lat', 'lon', 'match_pct', 'level', 'confidence', 'matched_on', 'differs_on'];
    const rows = state.twins.map((t, i) => [i + 1, t.region_id, t.name, t.new_site, t.lat, t.lon, t.match_pct, t.level, t.confidence, (t.best || []).join('; '), (t.worst || []).join('; ')]);
    const csv = [cols, ...rows].map(r => r.map(v => `"${String(v ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
    const a = Object.assign(document.createElement('a'), {href: url, download: `mars-analogs-${state.band}-${state.lat.toFixed(1)}_${state.lon.toFixed(1)}.csv`});
    a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ---------- Earth → Mars: reverse search ---------- */
  // Above-ground geology: what a geologist standing there would see. Subsurface ice and hardness are switched off.
  const SURFACE_WEIGHTS = {rock: 2, ground_class: 1, slope_deg: 1, roughness_m: 1, dryness: 1, temp_mean_rank: .5, temp_swing: .5};
  const BASIS = {
    surface: {band: 'skin', name: 'surface geology', hint: 'Above-ground geology: rock type, exposed bedrock vs loose regolith, slope, roughness, dryness and surface temperature. Buried ice and hardness are ignored.'},
    upper_metre: {band: 'upper_metre', name: '10 cm–1 m underground', hint: 'Ground 10 cm–1 m down, every feature with the default weights.'},
    drill_zone: {band: 'drill_zone', name: '1–3 m underground', hint: 'The 1–3 m drill zone: ice, ground type and hardness weigh most.'},
    deep: {band: 'deep', name: 'deeper than 3 m', hint: 'Below 3 m. Experimental: less subsurface data, lower confidence.'}
  };
  const PRESETS = [
    ['Reykjavík', 64.15, -21.94], ['Atacama Desert', -24.5, -69.25], ['Death Valley', 36.46, -116.87],
    ['Hawaiʻi Volcanoes', 19.42, -155.29], ['Antarctic Dry Valleys', -77.5, 162.0], ['Toronto', 43.65, -79.38]
  ];
  // Approximate centres of well-known Mars regions (east-positive longitude), used only to label results.
  const MARS_PLACES = [
    ['Olympus Mons', 18.6, -133.8], ['Tharsis Montes', 1.0, -112.0], ['Valles Marineris', -13.9, -59.2], ['Hellas Planitia', -42.4, 70.5],
    ['Argyre Planitia', -49.7, -43.4], ['Utopia Planitia', 46.7, 117.5], ['Arcadia Planitia', 47.2, -175.6], ['Amazonis Planitia', 24.8, -164.0],
    ['Acidalia Planitia', 49.8, -20.6], ['Chryse Planitia', 28.4, -40.0], ['Elysium Mons', 25.0, 147.2], ['Elysium Planitia', 3.0, 154.7],
    ['Isidis Planitia', 12.9, 87.0], ['Syrtis Major', 8.4, 69.5], ['Arabia Terra', 21.3, 6.0], ['Meridiani Planum', -0.2, -6.0],
    ['Terra Sirenum', -39.7, -150.0], ['Terra Cimmeria', -32.7, 145.5], ['Noachis Terra', -45.0, -10.0], ['Tempe Terra', 39.7, -71.0],
    ['Lunae Planum', 10.0, -67.0], ['Daedalia Planum', -18.0, -127.0], ['Tyrrhena Terra', -14.0, 90.0], ['Promethei Terra', -64.0, 97.0],
    ['Xanthe Terra', 2.0, -45.0], ['Aeolis Mensae', -2.5, 140.0], ['Vastitas Borealis', 66.0, 30.0], ['Alba Mons', 40.5, -109.6]
  ];
  const marsKm = (a, b) => angular(Number(a.lat), Number(a.lon), Number(b.lat), Number(b.lon)) * 3389.5;
  function marsName(c) {
    for (const s of state.showcase?.sites || []) if (num(s.lat) !== null && marsKm(c, s) < 150) return s.name;
    let best = null, bd = Infinity;
    for (const [n, la, lo] of MARS_PLACES) { const d = marsKm(c, {lat: la, lon: lo}); if (d < bd) { bd = d; best = n; } }
    return bd < 900 ? `Near ${best}` : null;
  }

  /* OpenStreetMap Nominatim: shared queue, max 1 request per second (their usage policy). */
  function osm(path) {
    const run = geoQueue.then(() => fetch(`https://nominatim.openstreetmap.org/${path}`).then(r => r.ok ? r.json() : null).catch(() => null));
    geoQueue = run.then(() => new Promise(r => setTimeout(r, 1100)));
    return run;
  }
  const placeCache = new Map();
  function placeLookup(q) {
    const key = q.trim().toLowerCase();
    if (key.length < 3) return Promise.resolve([]);
    if (!placeCache.has(key)) placeCache.set(key, osm(`search?format=jsonv2&limit=5&accept-language=en&q=${encodeURIComponent(q.trim())}`)
      .then(r => (r || []).map(x => ({label: String(x.display_name || ''), short: x.name || String(x.display_name || '').split(',')[0], lat: Number(x.lat), lon: Number(x.lon)}))
        .filter(x => Number.isFinite(x.lat) && Number.isFinite(x.lon))));
    return placeCache.get(key);
  }

  function nearestPlaces(lat, lon, n = 1) {
    const places = state.library?.bands?.skin?.places || [], here = {lat, lon};
    return places.filter(p => num(p.lat) !== null && num(p.lon) !== null).map(p => ({p, d: km(here, p)})).sort((a, b) => a.d - b.d).slice(0, n);
  }
  const revNotice = (msg, kind = 'warning') => { const n = $('rev-notice'); n.hidden = !msg; n.className = `notice ${kind}`; n.textContent = msg || ''; };

  function pickEarth(lat, lon, label) {
    if (!state.library) { setMode('reverse'); revNotice('The Earth library (output/earth_library.json) is still loading or missing.', 'error'); return; }
    lat = Math.max(-90, Math.min(90, lat)); lon = wrapLon(lon);
    const near = nearestPlaces(lat, lon, 6);
    if (!near.length) return;
    const r = state.rev;
    r.query = {lat, lon, label: label || null};
    r.near = near; r.place = near[0].p; r.snapKm = near[0].d;
    // Prefer the clicked land cell itself when it is closer than the nearest open ground.
    const q0 = r.query;
    loadLookup().then(() => {
      if (r.query !== q0) return;
      const own = nearestLookup(lat, lon);
      if (own && own.d < near[0].d) { r.near = [own, ...near.slice(0, 5)]; r.place = own.p; r.snapKm = own.d; reverseSearch(); }
    });
    if (!label) {
      const q = r.query;
      osm(`reverse?format=jsonv2&zoom=8&accept-language=en&lat=${lat}&lon=${lon}`).then(d => {
        const a = d?.address || {};
        const l = [a.city || a.town || a.village || a.county || a.state_district || a.state, a.country].filter(Boolean).join(', ');
        if (l && r.query === q) { q.label = l; renderReverse(); }
      });
    }
    setMode('reverse');
    reverseSearch();
  }
  function pickPlace(id) {
    const p = (state.library?.bands?.skin?.places || []).find(x => x.id === id); if (!p) return;
    const r = state.rev; r.place = p; r.snapKm = r.query ? km(r.query, p) : 0;
    reverseSearch();
  }

  async function reverseSearch() {
    const r = state.rev, token = ++r.token;
    if (!r.place) { renderReverse(); return; }
    const band = BASIS[r.basis].band;
    revNotice('');
    $('rev-twins').innerHTML = '<li class="empty">Scoring all 12,600 Mars cells…</li>';
    try {
      const grid = await gridFor(band);
      if (token !== r.token) return;
      const lib = state.library.bands[band];
      const place = placeFor(band, r.place.id);
      if (!place) throw new Error(`no ${band} values for this Earth place`);
      const weights = r.basis === 'surface' ? Object.fromEntries(lib.features.map(f => [f.name, SURFACE_WEIGHTS[f.name] ?? 0])) : lib.weights;
      const res = window.TwinRescore.scoreMars(place, lib, state.library, grid, weights, {topN: 5, spacingKm: 300});
      Object.assign(r, {band, grid, weights, top: res.top, pct: res.pct, selected: 0});
      if (!res.top.length) revNotice('Not enough data at this Earth place to compare with Mars. Try a nearby cell or another depth.');
      writeHash(); renderReverse(); paintReverseGlobe(true);
    } catch (e) {
      if (token !== r.token) return;
      r.top = []; r.pct = null; renderReverse(); paintReverseGlobe();
      revNotice(`Could not score Mars (${e.message}). Serve the repository root and open /web/.`, 'error');
    }
  }

  function paintReverseGlobe(focus = false) {
    const r = state.rev, legend = $('rev-legend');
    if (state.mode !== 'reverse') return;
    if (!r.pct || !r.grid) { globe?.setOverlay(null); legend.hidden = true; return; }
    const g = r.grid, gd = num(g.grid_deg) || 2, w = Math.round(360 / gd), h = Math.round(180 / gd);
    const px = new Uint8ClampedArray(w * h * 4);
    let good = 0;
    g.cells.forEach((c, i) => {
      const p = r.pct[i]; if (!(p >= 30)) return;
      if (p >= (state.library?.levels?.good ?? 70)) good++;
      const t = Math.min(1, (p - 30) / 60);
      const row = Math.min(h - 1, Math.max(0, Math.floor((90 - Number(c.lat)) / gd))), col = Math.min(w - 1, Math.max(0, Math.floor((wrapLon(Number(c.lon)) + 180) / gd)));
      px.set([Math.round(40 + 60 * t), Math.round(140 + 100 * t), Math.round(200 + 55 * t), Math.round(40 + 200 * t * t)], (row * w + col) * 4);
    });
    globe?.setOverlay({w, h, px});
    const sel = r.top[r.selected];
    if (sel) { globe?.setTarget(sel.lat, sel.lon); if (focus) globe?.centerOnTarget(); }
    legend.hidden = false;
    legend.innerHTML = `<div class="gl-keys"><span class="ramp-wrap"><span class="ramp similarity"></span><span class="ramp-ends"><span>30%</span><span>90%+ match</span></span></span></div>
      <p>Every Mars cell scored against this Earth place. ${good ? `${good.toLocaleString()} cells (${(100 * good / g.cells.length).toFixed(good * 100 < g.cells.length ? 1 : 0)}% of the map) are a good match or better.` : 'No cell is a good match: this place has no close twin on Mars.'}</p>`;
  }

  function initRevMap() {
    if (revMap || !window.L) return;
    revMap = L.map('rev-earth-map', {minZoom: 1, maxZoom: 18, zoomSnap: 0.5, worldCopyJump: true});
    revMap.attributionControl.setPrefix(false);
    L.tileLayer(EARTH_TILES, {maxNativeZoom: 18, attribution: 'Esri, Maxar, Earthstar Geographics'}).addTo(revMap);
    revMap.setView([25, 0], 1.5);
    revLayer = L.layerGroup().addTo(revMap);
    revMap.on('click', e => pickEarth(e.latlng.lat, e.latlng.lng, null));
    new ResizeObserver(() => revMap.invalidateSize()).observe($('rev-earth-map'));
  }

  // log_z values in earth_library.json can be turned back into real units: x = e^(z·σ + μ) − 1.
  function earthRaw(place, band, name) {
    const lib = state.library?.bands?.[band], i = lib?.features?.findIndex(f => f.name === name) ?? -1;
    const v = i >= 0 ? place?.v?.[i] : null, f = lib?.features?.[i];
    if (v === null || v === undefined || !f) return null;
    if (f.compare === 'log_z') return Math.expm1(v * Number(f.earth_log_std) + Number(f.earth_log_mean));
    if (f.compare === 'same') return Object.keys(f.codes || {}).find(k => f.codes[k] === v) ?? null;
    return v;
  }

  function revTip(c, name) {
    const lib = state.library?.bands?.[state.rev.band], i = lib?.features?.findIndex(f => f.name === name) ?? -1;
    const info = FEATURE_INFO[name];
    let vals = '';
    if (i >= 0) {
      const f = lib.features[i], place = placeFor(state.rev.band, state.rev.place?.id);
      const m = window.TwinRescore.normalized(c.mars, f), e = place?.v?.[i] ?? null;
      vals = `<span class="tip-vals"><span>Earth</span><b>${esc(showNorm(f, e))}</b><span>Mars</span><b>${esc(showNorm(f, m, c.mars?.[name === 'temp_mean_rank' ? 'temp_mean' : name]))}</b>
        <span>Weight</span><b>${Number(state.rev.weights?.[name] ?? 0).toFixed(1)}</b></span>`;
    }
    return `<span class="tip" role="tooltip"><strong>${esc(featureLabel(name))}</strong>${info ? `<span>${esc(info)}</span>` : ''}${vals}</span>`;
  }

  function renderReverse() {
    const r = state.rev, q = r.query, p = r.place;
    $('rev-basis').querySelectorAll('[data-basis]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.basis === r.basis)));
    $('rev-basis-hint').textContent = BASIS[r.basis].hint;
    if (!q || !p) {
      $('rev-twins').innerHTML = '<li class="empty">No place chosen yet. Search for a city or region, pick one of the examples, or click the Earth map.</li>';
      $('rev-why').innerHTML = ''; $('rev-place').hidden = true;
      $('rev-earth-name').textContent = 'Or click the map'; $('rev-earth-caption').textContent = '';
      return;
    }
    const where = q.label || coordText(q.lat, q.lon);
    $('rev-title').textContent = `Where on Mars is like ${where}?`;
    const far = r.snapKm > 150;
    $('rev-sub').textContent = p.lookup
      ? `Compared on ${BASIS[r.basis].name}, using the ground of the surrounding 55 km map cell.`
      : `Compared on ${BASIS[r.basis].name}, using open ground ${r.snapKm < 1 ? 'at this spot' : `${Math.round(r.snapKm).toLocaleString()} km away`}.${far ? ' Only bare or sparsely vegetated ground (where drill tests happen) is in the Earth data, so cities and forests use the nearest open terrain.' : ''}`;

    // Earth map (left column)
    $('rev-earth-name').textContent = siteName({...p, region_id: p.id});
    $('rev-earth-caption').textContent = coordText(p.lat, p.lon);
    geocode([{...p, region_id: p.id}]);
    if (revMap) {
      revLayer.clearLayers();
      const pts = [[q.lat, q.lon], [Number(p.lat), Number(p.lon)]];
      r.near.slice(1).forEach(({p: o}) => L.circleMarker([o.lat, o.lon], {radius: 5, color: '#ffffff', weight: 1, fillColor: '#8f969d', fillOpacity: .9})
        .bindTooltip('Nearby open ground · click to use').on('click', ev => { L.DomEvent.stopPropagation(ev); pickPlace(o.id); }).addTo(revLayer));
      if (r.snapKm >= 1) {
        L.polyline(pts, {color: '#ffffff', weight: 1.5, dashArray: '4 4', opacity: .8, interactive: false}).addTo(revLayer);
        L.circleMarker(pts[0], {radius: 6, color: '#ffffff', weight: 2, fillColor: '#4a90d9', fillOpacity: 1}).bindTooltip(esc(where)).addTo(revLayer);
      }
      L.circleMarker(pts[1], {radius: 8, color: '#ffffff', weight: 2, fillColor: '#c8714c', fillOpacity: 1}).bindTooltip('Ground used for the comparison').addTo(revLayer);
      if (r.snapKm >= 1) revMap.fitBounds(pts, {padding: [30, 30], maxZoom: 7, animate: false});
      else revMap.setView(pts[1], 6, {animate: false});
    }

    // What the Earth ground is like (left column, compact)
    const e0 = earthProfile(p.id, 'skin') || {};
    const deep = earthProfile(p.id, 'drill_zone') || {};
    const slope = earthRaw(p, 'skin', 'slope_deg');
    const surface = [e0.rock ? cap(String(e0.rock).replaceAll('_', ' ')) : '', GROUND_TEXT[e0.ground_class] || '', slope !== null ? `${slope.toFixed(1)}° slope` : ''].filter(Boolean).join(' · ');
    const below = [GROUND_TEXT[deep.ground_class] || '', deep.ice === 'yes' ? 'ground ice' : deep.ice === 'no' ? 'no ice' : ''].filter(Boolean).join(' · ');
    $('rev-place').hidden = false;
    $('rev-place').innerHTML = `<div class="rp-head"><span class="label">Ground used</span>${p.lookup ? '<span class="tag" title="Vegetated or built-up ground: compared on its own soil, rock and climate, but not a drill-test candidate.">Not open ground</span>' : p.new_site ? '<span class="tag new" title="No recorded field or drill tests here (NASA, ESA or university campaigns).">No field tests yet</span>' : '<span class="tag known" title="A known Mars analog region where field or drill tests have been run.">Field-tested analog</span>'}</div>
      <dl class="rp-facts"><div><dt>Surface</dt><dd>${esc(surface || '—')}</dd></div><div><dt>1–3 m down</dt><dd>${esc(below || '—')}</dd></div></dl>
      ${r.near.length > 1 ? `<details class="rp-near"><summary>Use other open ground nearby</summary><div>${r.near.slice(1).filter(({p: o}) => !o.lookup).map(({p: o}) =>
        `<button type="button" class="link" data-place="${esc(o.id)}">${esc(Math.round(km(q, o)).toLocaleString())} km · ${esc(o.new_site ? coordText(o.lat, o.lon, 1) : o.name)}</button>`).join('')}</div></details>` : ''}`;
    $('rev-place').querySelectorAll('[data-place]').forEach(b => b.addEventListener('click', () => pickPlace(b.dataset.place)));

    // Mars results (right column)
    const list = $('rev-twins');
    list.innerHTML = r.top.length ? r.top.map((c, i) => `<li><button type="button" class="rev-item" data-i="${i}" aria-pressed="${i === r.selected}">
        <span class="ri-rank">${i + 1}</span>
        <span class="ri-main"><strong>${esc(marsName(c) || coordText(c.lat, c.lon, 0))}</strong><small>${marsName(c) ? esc(coordText(c.lat, c.lon, 0)) : 'No named region nearby'}</small></span>
        <span class="ri-score"><strong>${pctText(c.match_pct)}%</strong><span class="level ${esc(c.level)}">${esc(LEVEL_TEXT[c.level] || c.level)}</span></span>
      </button></li>`).join('') : '<li class="empty">Scoring Mars…</li>';
    list.querySelectorAll('[data-i]').forEach(el => el.addEventListener('click', () => { r.selected = Number(el.dataset.i); renderReverse(); paintReverseGlobe(true); }));
    renderReverseWhy();
  }

  function renderReverseWhy() {
    const r = state.rev, c = r.top[r.selected];
    if (!c) { $('rev-why').innerHTML = ''; return; }
    const chips = (arr, cls = '') => (arr || []).length
      ? `<div class="chips">${arr.map(n => `<span class="chip ${cls}" tabindex="0">${esc(featureLabel(n))}${revTip(c, n)}</span>`).join('')}</div>`
      : '<span class="muted">—</span>';
    const m = c.mars || {};
    const facts = [m.rock ? cap(String(m.rock).replaceAll('_', ' ')) : '', GROUND_TEXT[m.ground_class] || '', num(m.temp_mean) !== null ? `${(num(m.temp_mean) - 273.15).toFixed(0)} °C mean` : '', m.ice === 'yes' ? 'ground ice' : ''].filter(Boolean).join(' · ');
    $('rev-why').innerHTML = `
      <div class="why-title"><strong>#${r.selected + 1} ${esc(marsName(c) || 'Mars ' + coordText(c.lat, c.lon, 0))}</strong><span class="muted">${esc(coordText(c.lat, c.lon, 1))} · ${pctText(c.match_pct)}% match</span></div>
      <div><h3>Matched on</h3>${chips(c.best)}</div>
      <div><h3>Differs on</h3>${chips(c.worst, 'diff')}</div>
      <div class="conf"><h3>This Mars cell</h3>${esc(facts || '—')}</div>
      <div class="rev-go"><button type="button" class="btn" id="rev-open">Find Earth drill sites for this Mars spot →</button></div>`;
    $('rev-open').addEventListener('click', () => openForward(c));
  }

  function pickMarsInReverse(lat, lon) {
    const r = state.rev;
    if (!r.top.length) return;
    let best = 0, bd = Infinity;
    r.top.forEach((c, i) => { const d = marsKm({lat, lon}, c); if (d < bd) { bd = d; best = i; } });
    r.selected = best; renderReverse(); paintReverseGlobe(true);
  }

  function openForward(c) {
    if (state.rev.basis !== 'surface') setBand(state.rev.band);
    setMode('forward');
    setCoords(c.lat, c.lon); globe?.centerOnTarget(); search();
    window.scrollTo({top: 0, behavior: 'smooth'});
  }

  function setMode(mode) {
    state.mode = mode;
    document.body.classList.toggle('mode-reverse', mode === 'reverse');
    document.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.mode === mode)));
    const stage = $('mars-stage');
    if (mode === 'reverse') { $('rev-globe-slot').prepend(stage); $('globe-hint').textContent = 'Match % on every Mars cell · drag to rotate'; }
    else { $('side').prepend(stage); $('globe-hint').textContent = 'Click to pick a site · drag to rotate'; }
    if (mode === 'reverse') {
      initRevMap();
      requestAnimationFrame(() => revMap?.invalidateSize());
      renderReverse(); paintReverseGlobe();
    } else {
      $('rev-legend').hidden = true;
      globe?.setOverlay(null);
      if (state.highlight) setHighlight(state.highlight);
      if (state.picked) globe?.setTarget(state.lat, state.lon);
      requestAnimationFrame(() => { marsMap?.invalidateSize(); earthMap?.invalidateSize(); });
    }
    writeHash();
  }

  function installReverse() {
    document.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => { if (b.dataset.mode !== state.mode) setMode(b.dataset.mode); }));
    $('rev-basis').addEventListener('click', ev => {
      const b = ev.target.closest('[data-basis]'); if (!b || b.dataset.basis === state.rev.basis) return;
      state.rev.basis = b.dataset.basis; renderReverse(); reverseSearch();
    });
    $('presets').innerHTML = `Examples: ${PRESETS.map(([n], i) => `<button type="button" class="link" data-preset="${i}">${esc(n)}</button>`).join(', ')}`;
    $('presets').addEventListener('click', ev => {
      const b = ev.target.closest('[data-preset]'); if (!b) return;
      const [n, la, lo] = PRESETS[Number(b.dataset.preset)];
      $('place-q').value = n; hideSuggest(); pickEarth(la, lo, n);
    });

    const input = $('place-q'), box = $('place-suggest');
    let items = [], active = -1, timer = 0, ask = 0;
    const show = list => {
      items = list; active = -1;
      box.hidden = !list.length;
      box.innerHTML = list.map((x, i) => `<li role="option" data-i="${i}" aria-selected="false"><strong>${esc(x.short)}</strong><span>${esc(x.label)}</span></li>`).join('');
    };
    const choose = x => { if (!x) return; input.value = x.short; hideSuggest(); pickEarth(x.lat, x.lon, x.short); };
    function hideSuggest() { box.hidden = true; items = []; active = -1; }
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value, my = ++ask;
      if (q.trim().length < 3) { hideSuggest(); return; }
      timer = setTimeout(() => placeLookup(q).then(list => { if (my === ask && document.activeElement === input) show(list); }), 450);
    });
    input.addEventListener('keydown', ev => {
      if (box.hidden || !items.length) return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        active = (active + (ev.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        box.querySelectorAll('li').forEach((li, i) => li.setAttribute('aria-selected', String(i === active)));
      } else if (ev.key === 'Escape') hideSuggest();
    });
    input.addEventListener('blur', () => setTimeout(hideSuggest, 150));
    box.addEventListener('mousedown', ev => { const li = ev.target.closest('[data-i]'); if (li) { ev.preventDefault(); choose(items[Number(li.dataset.i)]); } });
    $('place-form').addEventListener('submit', async ev => {
      ev.preventDefault();
      if (items.length) { choose(items[Math.max(0, active)]); return; }
      const q = input.value.trim();
      if (q.length < 3) { setMode('reverse'); revNotice('Type at least 3 letters of a city or region.'); return; }
      const my = ++ask;
      setMode('reverse'); revNotice(`Looking up “${q}”…`, 'info');
      const list = await placeLookup(q);
      if (my !== ask) return;
      if (!list.length) { revNotice(`No place called “${q}” found. Try a bigger city or region, or click the Earth map.`, 'error'); return; }
      revNotice(''); choose(list[0]);
    });
  }

  /* ---------- mission brief (print / save as PDF) ---------- */
  async function missionBrief() {
    if (!state.cell || !state.twins.length) return;
    const btn = $('brief-button'); btn.disabled = true;
    try {
      const lat = state.lat, lon = state.lon, column = {};
      for (const b of BAND_KEYS) column[b] = state.column[b] || (state.site ? null : nearestCell(await gridFor(b).catch(() => null), lat, lon));
      const t = current() || state.twins[0];
      const title = state.site ? state.site.name : `Mars ${coordText(lat, lon)}`;
      const lbl = n => esc(featureLabel(n));
      const temp = m => num(m?.temp_mean) !== null ? `${(num(m.temp_mean) - 273.15).toFixed(0)} °C` : '—';
      const frozen = m => num(m?.frozen_fraction) !== null ? `${Math.round(num(m.frozen_fraction) * 100)}%` : '—';
      const col = (m, k) => m?.[k] == null ? '—' : k === 'ground_class' ? esc(GROUND_TEXT[m[k]] || m[k]) : esc(cap(String(m[k]).replaceAll('_', ' ')));
      const marsRows = BAND_KEYS.map(b => { const m = column[b]?.mars;
        return `<tr${b === state.band ? ' class="sel"' : ''}><th>${esc(BANDS[b].range)}</th><td>${col(m, 'ground_class')}</td><td>${col(m, 'rock')}</td><td>${col(m, 'ice')}</td><td>${m?.hardness ?? '—'}</td><td>${temp(m)}</td><td>${frozen(m)}</td></tr>`; }).join('');
      const earthRows = BAND_KEYS.map(b => { const e = earthProfile(t.region_id, b);
        return `<tr${b === state.band ? ' class="sel"' : ''}><th>${esc(BANDS[b].range)}</th><td>${col(e, 'ground_class')}</td><td>${col(e, 'rock')}</td><td>${col(e, 'ice')}</td><td>${e?.hardness ?? '—'}</td><td>${frozen(e)}</td><td>${bandMatch(t, b) === null ? '—' : `${pctText(bandMatch(t, b))}%`}</td></tr>`; }).join('');
      const twinRows = state.twins.map((tw, i) => `<tr${tw === t ? ' class="sel"' : ''}><td>${i + 1}</td><td>${esc(siteName(tw))}${tw.new_site ? ' <em>no field tests yet</em>' : ''}</td><td>${esc(coordText(tw.lat, tw.lon))}</td>
        <td>${tw.level === 'insufficient_data' ? '—' : `${esc(pctText(tw.match_pct))}%`}</td><td>${esc(LEVEL_TEXT[tw.level] || tw.level)}</td><td>${esc(cap(String(tw.confidence || '—')))}</td>
        <td>${(tw.best || []).map(lbl).join(', ') || '—'}</td><td>${(tw.worst || []).map(lbl).join(', ') || '—'}</td></tr>`).join('');
      const camps = state.campaigns.filter(c => state.twins.some(tw => tw.region_id === c.region_id));
      const v = state.validation && !state.validation.fake ? state.validation.overall || {} : null;
      const pctOf = x => num(x) === null ? '—' : `${(num(x) * 100).toFixed(0)}%`;
      $('brief').innerHTML = `
        <header><p class="b-kicker">Planetary Twin · Field-test mission brief</p><h1>${esc(title)}</h1>
          <p>${esc(coordText(lat, lon, 2))} · target depth ${esc(BANDS[state.band].range)} (${esc(BANDS[state.band].name)}) · generated ${esc(new Date().toISOString().slice(0, 10))}</p></header>
        <section><h2>Recommended Earth test site</h2>
          <p class="b-lead"><strong>${esc(siteName(t))}</strong>, ${esc(coordText(t.lat, t.lon, 2))}: ${t.level === 'insufficient_data' ? 'not enough data for a score' : `${esc(pctText(t.match_pct))}% match (${esc(LEVEL_TEXT[t.level] || t.level)})`}${t.new_site ? ', a site with no recorded field tests yet' : ''}.</p>
          <p>Matched on ${(t.best || []).map(lbl).join(', ') || '—'}. Differs on ${(t.worst || []).map(lbl).join(', ') || '—'}.</p></section>
        <section><h2>Ground column</h2><div class="b-cols">
          <div><h3>Mars</h3><table><thead><tr><th>Depth</th><th>Ground</th><th>Rock</th><th>Ice</th><th>Hardness</th><th>Mean T</th><th>Frozen</th></tr></thead><tbody>${marsRows}</tbody></table></div>
          <div><h3>Earth · ${esc(siteName(t))}</h3><table><thead><tr><th>Depth</th><th>Ground</th><th>Rock</th><th>Ice</th><th>Hardness</th><th>Frozen</th><th>Match</th></tr></thead><tbody>${earthRows}</tbody></table></div>
        </div></section>
        <section><h2>All candidate sites</h2><table><thead><tr><th>#</th><th>Site</th><th>Location</th><th>Match</th><th>Level</th><th>Confidence</th><th>Matched on</th><th>Differs on</th></tr></thead><tbody>${twinRows}</tbody></table></section>
        ${camps.length ? `<section><h2>Previous field and drill campaigns</h2><ul>${camps.map(c => `<li>${esc(c.campaign)}${c.year ? ` (${esc(c.year)})` : ''}${c.what_was_tested ? `: ${esc(c.what_was_tested)}` : ''}</li>`).join('')}</ul></section>` : ''}
        ${v ? `<section><h2>Model accuracy</h2><p>On ${esc(state.validation.n_pairs)} published Mars–Earth analog pairs, the known analog is in the top 3 for ${pctOf(v.recall_at_3)} and the top 20 for ${pctOf(v.recall_at_20)} of pairs (random: ${pctOf(v.random_recall_at_3)} and ${pctOf(v.random_recall_at_20)}) among ${esc(state.validation.n_regions)} Earth places.</p></section>` : ''}
        <footer><p>Scores are a weighted Gower distance over temperature, ice, ground type, hardness, rock, terrain and dryness (weighted kNN). They are research leads for field testing, not drilling-safety certification.</p>
          <p>Open this view: ${esc(location.href)}</p></footer>`;
      window.print();
    } finally { btn.disabled = !state.twins.length; }
  }

  /* ---------- startup ---------- */
  function writeHash() {
    const q = state.rev.query;
    const hash = state.mode === 'reverse'
      ? `mode=reverse&basis=${state.rev.basis}${q ? `&elat=${q.lat.toFixed(2)}&elon=${q.lon.toFixed(2)}` : ''}`
      : `${state.picked ? `lat=${state.lat.toFixed(1)}&lon=${state.lon.toFixed(1)}&` : ''}band=${state.band}${state.highlight ? `&hl=${state.highlight}` : ''}`;
    history.replaceState(null, '', `#${hash}`);
  }
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    const lat = num(h.get('lat')), lon = num(h.get('lon'));
    if (lat !== null && lon !== null && Math.abs(lat) <= 70 && Math.abs(lon) <= 180) { state.lat = lat; state.lon = lon; state.picked = true; }
    if (BANDS[h.get('band')]) state.band = h.get('band');
    if (HIGHLIGHTS[h.get('hl')]) state.highlight = h.get('hl');
    if (h.get('mode') === 'reverse') state.mode = 'reverse';
    if (BASIS[h.get('basis')]) state.rev.basis = h.get('basis');
    const elat = num(h.get('elat')), elon = num(h.get('elon'));
    if (elat !== null && elon !== null && Math.abs(elat) <= 90 && Math.abs(elon) <= 180) state.rev.pending = {lat: elat, lon: elon};
    if (h.get('q')) { state.mode = 'reverse'; state.rev.pendingQuery = h.get('q').slice(0, 120); }
  }

  function install() {
    const wantAccuracy = location.hash === '#accuracy';
    if (wantAccuracy) history.replaceState(null, '', location.pathname);
    $('nav-accuracy').addEventListener('click', ev => {
      ev.preventDefault();
      if (!$('validation-card').hidden) $('validation-card').scrollIntoView({behavior: 'smooth', block: 'start'});
    });
    readHash();
    setBand(state.band);
    initMaps();

    if (window.MarsGlobe?.create) {
      globe = window.MarsGlobe.create($('mars-canvas'), (lat, lon) => {
        if (state.mode === 'reverse') { pickMarsInReverse(lat, lon); return; }
        setCoords(lat, lon); search();
      });
      $('mars-canvas').addEventListener('keydown', ev => {
        const step = {ArrowLeft: [0, -2], ArrowRight: [0, 2], ArrowUp: [2, 0], ArrowDown: [-2, 0]}[ev.key];
        if (!step) return;
        ev.preventDefault(); setCoords(state.lat + step[0], state.lon + step[1]); searchSoon();
      });
    }

    $('controls').addEventListener('submit', ev => {
      ev.preventDefault();
      const lat = Number($('latitude').value), lon = Number($('longitude').value);
      if (!$('latitude').value || !$('longitude').value || !(Math.abs(lat) <= 70) || !(Math.abs(lon) <= 180)) {
        notice('Latitude must be between −70° and 70°, longitude between −180° and 180°.', 'error'); return;
      }
      setCoords(lat, lon, {fields: false}); globe?.centerOnTarget(); search();
    });
    $('depth').addEventListener('click', ev => {
      const b = ev.target.closest('[data-band]'); if (!b || b.dataset.band === state.band) return;
      setBand(b.dataset.band); if (state.picked) search();
    });
    $('showcase').addEventListener('change', ev => {
      const site = (state.showcase?.sites || []).find(s => s.site_id === ev.target.value);
      if (!site) return;
      setCoords(Number(site.lat), Number(site.lon)); globe?.centerOnTarget(); search();
    });
    $('export-button').addEventListener('click', exportCsv);
    $('brief-button').addEventListener('click', missionBrief);
    installReverse();
    $('highlight').addEventListener('click', ev => { const b = ev.target.closest('[data-hl]'); if (b) setHighlight(b.dataset.hl); });

    if (location.protocol === 'file:') {
      notice('Open this page through a local web server from the repository root (py -m http.server 8000, then /web/).', 'error');
      return;
    }
    if (state.picked) setCoords(state.lat, state.lon);   // only a shared link (#lat=..&lon=..) opens on a spot
    // Library first: the ice rule and the ground profile both need it.
    Promise.all([optional('output/showcase.json'), optional('output/earth_library.json'), optional('output/validation.json'),
      optional('research/earth_campaigns.csv', true), optional('output/ml_report.json'),
      optional('output/earth_subsurface.json')])
      .then(([showcase, library, validation, campaigns, mlReport, subsurface]) => {
        state.showcase = showcase; state.library = library; state.validation = validation; state.campaigns = csvRows(campaigns);
        state.mlReport = mlReport; state.subsurface = subsurface;
        for (const s of showcase?.sites || []) {
          if (!s.site_id || num(s.lat) === null || num(s.lon) === null) continue;
          $('showcase').append(new Option(s.name || s.site_id, s.site_id));
        }
        renderValidation();
        setMode(state.mode);
        if (wantAccuracy) requestAnimationFrame(() => $('validation-card').scrollIntoView({block: 'start'}));
        if (state.mode === 'reverse') {
          const p = state.rev.pending, q = state.rev.pendingQuery;
          state.rev.pending = state.rev.pendingQuery = null;
          if (q) { $('place-q').value = q; $('place-form').requestSubmit(); }
          else if (p) pickEarth(p.lat, p.lon, null);
        } else {
          if (state.picked) search();
          if (state.highlight) setHighlight(state.highlight);
        }
      });
  }

  install();
})();
