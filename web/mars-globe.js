/* Interactive Mars globe. Purely illustrative terrain: not NASA imagery or geological evidence.
   No dependencies and no changes to the matching / pipeline contracts. */
(() => {
  'use strict';
  const BASE = (document.currentScript && document.currentScript.src) || location.href;
  const RAD = Math.PI / 180, DEG = 180 / Math.PI;
  const clamp = (n, a, b) => Math.min(b, Math.max(a, n));
  const wrap = d => ((d + 180) % 360 + 360) % 360 - 180;
  const hash = (x, y) => { let n = (Math.imul(x, 374761393) + Math.imul(y, 668265263)) | 0; n = Math.imul(n ^ (n >>> 13), 1274126177); return ((n ^ (n >>> 16)) >>> 0) / 4294967295; };
  const smooth = t => t * t * (3 - 2 * t);
  function noise(x, y, period) {
    const ix = Math.floor(x), iy = Math.floor(y), fx = smooth(x - ix), fy = smooth(y - iy);
    const h = (a, b) => hash(((a % period) + period) % period, b);
    const a = h(ix, iy) * (1 - fx) + h(ix + 1, iy) * fx;
    const b = h(ix, iy + 1) * (1 - fx) + h(ix + 1, iy + 1) * fx;
    return a * (1 - fy) + b * fy;
  }
  function texture() {
    const w = 384, h = 192, px = new Uint8ClampedArray(w * h * 4);
    const basins = [
      [55, -13, 18, -0.55], [-45, 10, 15, -0.45], [135, 3, 21, -0.56],
      [-110, -37, 21, -0.33], [170, -25, 13, -0.34], [87, 25, 11, 0.45],
      [-120, 22, 17, 0.28], [35, 45, 13, 0.28]
    ];
    for (let y = 0; y < h; y++) {
      const lat = 90 - y / (h - 1) * 180;
      for (let x = 0; x < w; x++) {
        const lon = x / w * 360 - 180;
        let v = noise(x / 96, y / 96, 4) * .50 + noise(x / 28, y / 28, 14) * .28 + noise(x / 8, y / 8, 47) * .14 + noise(x / 2, y / 2, 205) * .08;
        for (const [blon, blat, radius, strength] of basins) {
          let dlon = Math.abs(lon - blon); dlon = Math.min(dlon, 360 - dlon);
          const dl = dlon * Math.cos(lat * RAD), db = lat - blat;
          const d2 = dl * dl + db * db;
          v += strength * Math.exp(-d2 / (radius * radius));
        }
        const crater = noise(x / 4, y / 4, 104) - .5;
        v += crater * .07;
        const polar = clamp((Math.abs(lat) - 73) / 17, 0, 1);
        const pale = Math.pow(polar, 1.9) * .82;
        const light = clamp(v * 1.2, 0, 1);
        const i = (y * w + x) * 4;
        px[i]     = 88 + light * 120 + pale * 35;
        px[i + 1] = 39 + light * 80  + pale * 89;
        px[i + 2] = 29 + light * 55  + pale * 107;
        px[i + 3] = 255;
      }
    }
    return { w, h, px };
  }
  let surface = texture();
  const redraws = [];
  // Real imagery: NASA Viking MDIM 2.1 colour mosaic (NASA Ames / USGS), via NASA Solar System Treks.
  const photo = new Image(); photo.src = new URL('mars.jpg', BASE).href;
  photo.onload = () => { const c = document.createElement('canvas'); c.width = photo.width; c.height = photo.height; const x = c.getContext('2d'); x.drawImage(photo, 0, 0); surface = { w: c.width, h: c.height, px: x.getImageData(0, 0, c.width, c.height).data }; redraws.forEach(f => f()); };
  function create(canvas, onSelect) {
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return null;
    const stage = canvas.parentElement;
    const s = { lon: 77.5, lat: 18.4, targetLon: 77.5, targetLat: 18.4, zoom: 1, width: 720, height: 425, dragging: null, scheduled: false, hover: null, pulse: 0, zoomAt: 0 };
    const rand = (seed => () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; })(23);
    const stars = Array.from({ length: 140 }, () => ({ x: rand(), y: rand(), r: .4 + rand() * 1.1, a: .16 + rand() * .46 }));
    const orbit = () => ({ cx: s.width / 2, cy: s.height / 2, r: Math.min(s.width * .34, s.height * .435) * s.zoom });
    function point(x, y) {
      const { cx, cy, r } = orbit();
      const u = (x - cx) / r, v = (cy - y) / r;
      const rho = Math.hypot(u, v);
      if (rho > 1) return null;
      const c = Math.asin(Math.min(1, rho)), sinC = Math.sin(c), cosC = Math.cos(c);
      const phi0 = s.lat * RAD, sin0 = Math.sin(phi0), cos0 = Math.cos(phi0);
      const lat = Math.asin(clamp(cosC * sin0 + (rho ? v * sinC * cos0 / rho : 0), -1, 1));
      const lon = s.lon * RAD + Math.atan2(u * sinC, rho * cos0 * cosC - v * sin0 * sinC);
      return { lat: lat * DEG, lon: wrap(lon * DEG) };
    }
    function project(lat, lon) {
      const a = lat * RAD, d = wrap(lon - s.lon) * RAD, b = s.lat * RAD;
      const z = Math.sin(a) * Math.sin(b) + Math.cos(a) * Math.cos(b) * Math.cos(d);
      if (z <= 0) return null;
      const { cx, cy, r } = orbit();
      return { x: cx + r * Math.cos(a) * Math.sin(d), y: cy - r * (Math.sin(a) * Math.cos(b) - Math.cos(a) * Math.cos(d) * Math.sin(b)) };
    }
    function frame() {
      s.scheduled = false;
      const { width: w, height: h } = s;
      const back = ctx.createLinearGradient(0, 0, w, h);
      back.addColorStop(0, '#070c18'); back.addColorStop(.55, '#141526'); back.addColorStop(1, '#090d1b');
      ctx.fillStyle = back; ctx.fillRect(0, 0, w, h);
      for (const star of stars) { ctx.globalAlpha = star.a; ctx.fillStyle = '#e0e5ff'; ctx.beginPath(); ctx.arc(star.x * w, star.y * h, star.r, 0, Math.PI * 2); ctx.fill(); }
      ctx.globalAlpha = 1;
      const { cx, cy, r } = orbit();
      const glow = ctx.createRadialGradient(cx, cy, r * .63, cx, cy, r * 1.45);
      glow.addColorStop(0, '#a850271e'); glow.addColorStop(.6, '#d8722930'); glow.addColorStop(1, '#d8722900');
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(cx, cy, r * 1.45, 0, Math.PI * 2); ctx.fill();
      // Render the orthographic sphere using inverse projection, with a fixed upper-left light source.
      const x0 = Math.max(0, Math.floor(cx - r - 1)), y0 = Math.max(0, Math.floor(cy - r - 1));
      const x1 = Math.min(w, Math.ceil(cx + r + 1)), y1 = Math.min(h, Math.ceil(cy + r + 1));
      const bw = x1 - x0, bh = y1 - y0, image = ctx.createImageData(bw, bh);
      const phi0 = s.lat * RAD, sin0 = Math.sin(phi0), cos0 = Math.cos(phi0), lon0 = s.lon * RAD;
      for (let py = 0; py < bh; py++) {
        const v = (cy - (y0 + py + .5)) / r;
        for (let px = 0; px < bw; px++) {
          const u = ((x0 + px + .5) - cx) / r, d2 = u * u + v * v;
          if (d2 > 1) continue;
          const z = Math.sqrt(1 - d2);
          const lat = Math.asin(clamp(v * cos0 + z * sin0, -1, 1));
          const lon = lon0 + Math.atan2(u, z * cos0 - v * sin0);
          const tx = ((lon / (2 * Math.PI) + .5) % 1 + 1) % 1;
          const sx = Math.min(surface.w - 1, Math.floor(tx * surface.w));
          const sy = clamp(Math.floor((.5 - lat / Math.PI) * surface.h), 0, surface.h - 1);
          const si = (sy * surface.w + sx) * 4, di = (py * bw + px) * 4;
          const normalLight = Math.max(0, u * -.47 + v * .33 + z * .81);
          const illum = .23 + normalLight * .88;
          const rim = Math.pow(1 - z, 4) * .14;
          image.data[di]     = surface.px[si] * illum + rim * 235;
          image.data[di + 1] = surface.px[si + 1] * illum + rim * 99;
          image.data[di + 2] = surface.px[si + 2] * illum + rim * 55;
          image.data[di + 3] = 255;
        }
      }
      // putImageData ignores transparency in alpha:false canvases; mask the disk via an offscreen canvas.
      if (!s.disk || s.disk.width !== bw || s.disk.height !== bh) {
        s.disk = document.createElement('canvas'); s.disk.width = bw; s.disk.height = bh;
      }
      const disk = s.disk.getContext('2d'); disk.putImageData(image, 0, 0);
      ctx.drawImage(s.disk, x0, y0);
      ctx.strokeStyle = '#ef9d6d66'; ctx.lineWidth = 1.1;
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      // Visible globe graticule, kept subtle to avoid obscuring terrain.
      ctx.lineWidth = .6; ctx.strokeStyle = '#f5bc9760';
      const line = points => { let active = false; ctx.beginPath(); for (const [lat, lon] of points) { const p = project(lat, lon); if (!p) {active = false;continue;} if (!active) ctx.moveTo(p.x, p.y);else ctx.lineTo(p.x, p.y);active = true; } ctx.stroke(); };
      for (let lat = -60; lat <= 60; lat += 30) line(Array.from({length:181}, (_, i) => [lat, i * 2 - 180]));
      for (let lon = -180; lon < 180; lon += 30) line(Array.from({length:89}, (_, i) => [i * 2 - 88, lon]));
      // --- Visual feedback layer (presentation only) ---
      const now = performance.now();
      if (s.dragging && s.dragging.moved) {
        const ring = ctx.createRadialGradient(cx, cy, r * .96, cx, cy, r * 1.08);
        ring.addColorStop(0, '#ff7a5200'); ring.addColorStop(.5, '#ff7a5255'); ring.addColorStop(1, '#ff7a5200');
        ctx.fillStyle = ring; ctx.beginPath(); ctx.arc(cx, cy, r * 1.08, 0, Math.PI * 2); ctx.fill();
      }
      if (s.hover && !s.dragging) {
        const hp = project(s.hover.lat, s.hover.lon);
        if (hp) {
          ctx.strokeStyle = '#ffd3b8aa'; ctx.lineWidth = 1; ctx.setLineDash([3, 4]);
          ctx.beginPath(); ctx.arc(hp.x, hp.y, 16, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
          ctx.fillStyle = '#ffd3b8'; ctx.beginPath(); ctx.arc(hp.x, hp.y, 2, 0, Math.PI * 2); ctx.fill();
          const out = Math.abs(s.hover.lat) > 70;
          const txt = out ? `${Math.abs(s.hover.lat).toFixed(1)}° ${s.hover.lat >= 0 ? 'N' : 'S'} · SELECTS ${s.hover.lat >= 0 ? '70° N' : '70° S'} (DATA LIMIT)` : `${Math.abs(s.hover.lat).toFixed(1)}° ${s.hover.lat >= 0 ? 'N' : 'S'} · ${Math.abs(s.hover.lon).toFixed(1)}° ${s.hover.lon >= 0 ? 'E' : 'W'}`;
          ctx.font = '500 11px "JetBrains Mono", monospace';
          const tw = ctx.measureText(txt).width + 18, bx = Math.min(w - tw - 8, hp.x + 22), by = Math.max(8, hp.y - 34);
          ctx.fillStyle = 'rgba(10,12,20,.82)'; ctx.strokeStyle = '#ff7a5288'; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.roundRect(bx, by, tw, 24, 12); ctx.fill(); ctx.stroke();
          ctx.fillStyle = '#ffe6d6'; ctx.fillText(txt, bx + 9, by + 16);
        }
      }
      let animating = false;
      const pt = (now - s.pulse) / 700;
      const marker = project(s.targetLat, s.targetLon);
      if (marker && pt < 1) {
        animating = true;
        for (const k of [0, .25]) { const t = clamp(pt - k, 0, 1); ctx.strokeStyle = `rgba(255,140,100,${(1 - t) * .9})`; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(marker.x, marker.y, 10 + t * 46, 0, Math.PI * 2); ctx.stroke(); }
      }
      const zt = (now - s.zoomAt) / 1100;
      if (zt < 1) {
        animating = true;
        ctx.globalAlpha = zt < .7 ? 1 : (1 - zt) / .3;
        const label = `ZOOM ${s.zoom.toFixed(2)}×`, bw2 = 150, bx2 = w / 2 - bw2 / 2, by2 = h - 84;
        ctx.fillStyle = 'rgba(10,12,20,.8)'; ctx.beginPath(); ctx.roundRect(bx2, by2, bw2, 26, 13); ctx.fill();
        const f = (s.zoom - .75) / (3.5 - .75);
        ctx.fillStyle = '#ffffff22'; ctx.fillRect(bx2 + 82, by2 + 12, 56, 2);
        ctx.fillStyle = '#ff7a52'; ctx.fillRect(bx2 + 82, by2 + 12, 56 * f, 2);
        ctx.font = '500 10px "JetBrains Mono", monospace'; ctx.fillStyle = '#ffe6d6'; ctx.fillText(label, bx2 + 12, by2 + 17);
        ctx.globalAlpha = 1;
      }
      if (animating) schedule();
      if (marker) {
        ctx.strokeStyle = '#ffffff';ctx.fillStyle = '#ffc7a1';ctx.lineWidth = 1.5;
        ctx.beginPath();ctx.arc(marker.x,marker.y,11,0,Math.PI*2);ctx.stroke();
        ctx.beginPath();ctx.arc(marker.x,marker.y,3,0,Math.PI*2);ctx.fill();
        ctx.beginPath();ctx.moveTo(marker.x-18,marker.y);ctx.lineTo(marker.x-7,marker.y);ctx.moveTo(marker.x+7,marker.y);ctx.lineTo(marker.x+18,marker.y);ctx.moveTo(marker.x,marker.y-18);ctx.lineTo(marker.x,marker.y-7);ctx.moveTo(marker.x,marker.y+7);ctx.lineTo(marker.x,marker.y+18);ctx.stroke();
      }
    }
    function setHover(h) { s.hover = h; stage.classList.toggle('is-over-globe', !!h); schedule(); }
    function schedule() { if (!s.scheduled) { s.scheduled = true; requestAnimationFrame(frame); } }
    function resize() {
      const rect = canvas.getBoundingClientRect();
      s.width = Math.max(320, Math.round(rect.width)); s.height = Math.max(240, Math.round(rect.height));
      // Fixed 1x backing store preserves smooth pointer interaction even on high-DPI screens.
      canvas.width = s.width; canvas.height = s.height; schedule();
    }
    function target(lat, lon, focus = true) {
      s.targetLat = clamp(lat, -70, 70); s.targetLon = wrap(lon); s.pulse = performance.now();
      if (focus && !project(s.targetLat, s.targetLon)) { s.lat = s.targetLat; s.lon = s.targetLon; }
      schedule();
    }
    function coords(e) { const rect = canvas.getBoundingClientRect(); return { x: (e.clientX - rect.left) * s.width / rect.width, y: (e.clientY - rect.top) * s.height / rect.height }; }
    canvas.addEventListener('pointerdown', e => { if (e.button !== 0) return; const p = coords(e); stage.classList.add('is-dragging'); s.dragging = { x: p.x, y: p.y, initialX: p.x, initialY: p.y, moved: false }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => {
      if (!s.dragging) { const q = coords(e); setHover(point(q.x, q.y)); return; }
      const p = coords(e), dx = p.x - s.dragging.x, dy = p.y - s.dragging.y;
      if (Math.hypot(p.x - s.dragging.initialX, p.y - s.dragging.initialY) > 5) s.dragging.moved = true;
      if (s.dragging.moved) { const r = orbit().r; s.lon = wrap(s.lon - dx * 105 / r); s.lat = clamp(s.lat + dy * 95 / r, -85, 85); schedule(); }
      s.dragging.x = p.x; s.dragging.y = p.y;
    });
    canvas.addEventListener('pointerup', e => {if (!s.dragging) return; const moved = s.dragging.moved; s.dragging = null; stage.classList.remove('is-dragging'); if (!moved) { s.pulse = performance.now(); const p = coords(e), hit = point(p.x, p.y); if (hit) onSelect(clamp(hit.lat,-70,70),hit.lon); }});
    canvas.addEventListener('pointercancel', () => {s.dragging=null;stage.classList.remove('is-dragging');});
    canvas.addEventListener('pointerleave', () => { if (!s.dragging) setHover(null); });
    const ZMIN = .75, ZMAX = 3.5;
    s.zoomTo = s.zoom;
    function zoomStep() { const d = s.zoomTo - s.zoom; if (Math.abs(d) < .002) s.zoom = s.zoomTo; else { s.zoom += d * .22; requestAnimationFrame(zoomStep); } s.zoomAt = performance.now(); schedule(); }
    function zoomBy(f) { const run = Math.abs(s.zoomTo - s.zoom) < .002; s.zoomTo = clamp(s.zoomTo * f, ZMIN, ZMAX); if (run) zoomStep(); }
    canvas.addEventListener('wheel', e => { e.preventDefault(); const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1); zoomBy(Math.exp(-dy * .0018)); }, { passive: false });
    canvas.addEventListener('dblclick', e => { e.preventDefault(); const p = coords(e), hit = point(p.x, p.y); if (hit) { s.lat = clamp(hit.lat, -85, 85); s.lon = hit.lon; } zoomBy(1.8); });
    const zc = document.createElement('div'); zc.className = 'globe-zoom';
    zc.innerHTML = '<button type="button" aria-label="Zoom in">+</button><button type="button" aria-label="Zoom out">−</button><button type="button" aria-label="Reset zoom">⟲</button>';
    const [zi, zo, zr] = zc.querySelectorAll('button');
    zi.onclick = () => zoomBy(1.5); zo.onclick = () => zoomBy(1 / 1.5); zr.onclick = () => { s.zoomTo = s.zoom; s.zoomTo = 1; zoomStep(); };
    stage.appendChild(zc);
    if ('ResizeObserver' in window) new ResizeObserver(resize).observe(stage); else window.addEventListener('resize', resize);
    redraws.push(schedule);
    resize();
    return { setTarget: target, centerOnTarget() {s.lat=s.targetLat;s.lon=s.targetLon;schedule();} };
  }
  window.MarsGlobe = { create };
})();
