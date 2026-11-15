/* Interactive Mars globe. Purely illustrative terrain: not NASA imagery or geological evidence.
   No dependencies and no changes to the matching / pipeline contracts. */
(() => {
  'use strict';
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
    const w = 1024, h = 512, px = new Uint8ClampedArray(w * h * 4);
    const basins = [
      [55, -13, 18, -0.55], [-45, 10, 15, -0.45], [135, 3, 21, -0.56],
      [-110, -37, 21, -0.33], [170, -25, 13, -0.34], [87, 25, 11, 0.45],
      [-120, 22, 17, 0.28], [35, 45, 13, 0.28]
    ];
    for (let y = 0; y < h; y++) {
      const lat = 90 - y / (h - 1) * 180;
      for (let x = 0; x < w; x++) {
        const lon = x / w * 360 - 180;
        let v = noise(x / 256, y / 256, 4) * .50 + noise(x / 75, y / 75, 14) * .28 + noise(x / 22, y / 22, 47) * .14 + noise(x / 5, y / 5, 205) * .08;
        for (const [blon, blat, radius, strength] of basins) {
          let dlon = Math.abs(lon - blon); dlon = Math.min(dlon, 360 - dlon);
          const d2 = Math.pow(dlon * Math.cos(lat * RAD), 2) + Math.pow(lat - blat, 2);
          v += strength * Math.exp(-d2 / (radius * radius));
        }
        const crater = noise(x / 10, y / 10, 104) - .5;
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
  const surface = texture();
  function create(canvas, onSelect) {
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return null;
    const stage = canvas.parentElement;
    const s = { lon: 77.5, lat: 18.4, targetLon: 77.5, targetLat: 18.4, zoom: 1, width: 720, height: 425, dragging: null, scheduled: false };
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
      const marker = project(s.targetLat, s.targetLon);
      if (marker) {
        ctx.strokeStyle = '#ffffff';ctx.fillStyle = '#ffc7a1';ctx.lineWidth = 1.5;
        ctx.beginPath();ctx.arc(marker.x,marker.y,11,0,Math.PI*2);ctx.stroke();
        ctx.beginPath();ctx.arc(marker.x,marker.y,3,0,Math.PI*2);ctx.fill();
        ctx.beginPath();ctx.moveTo(marker.x-18,marker.y);ctx.lineTo(marker.x-7,marker.y);ctx.moveTo(marker.x+7,marker.y);ctx.lineTo(marker.x+18,marker.y);ctx.moveTo(marker.x,marker.y-18);ctx.lineTo(marker.x,marker.y-7);ctx.moveTo(marker.x,marker.y+7);ctx.lineTo(marker.x,marker.y+18);ctx.stroke();
      }
    }
    function schedule() { if (!s.scheduled) { s.scheduled = true; requestAnimationFrame(frame); } }
    function resize() {
      const rect = canvas.getBoundingClientRect();
      s.width = Math.max(320, Math.round(rect.width)); s.height = Math.max(240, Math.round(rect.height));
      // Fixed 1x backing store preserves smooth pointer interaction even on high-DPI screens.
      canvas.width = s.width; canvas.height = s.height; schedule();
    }
    function target(lat, lon, focus = true) {
      s.targetLat = clamp(lat, -70, 70); s.targetLon = wrap(lon);
      if (focus && !project(s.targetLat, s.targetLon)) { s.lat = s.targetLat; s.lon = s.targetLon; }
      schedule();
    }
    function coords(e) { const rect = canvas.getBoundingClientRect(); return { x: (e.clientX - rect.left) * s.width / rect.width, y: (e.clientY - rect.top) * s.height / rect.height }; }
    canvas.addEventListener('pointerdown', e => { if (e.button !== 0) return; const p = coords(e); s.dragging = { x: p.x, y: p.y, initialX: p.x, initialY: p.y, moved: false }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => {
      if (!s.dragging) return;
      const p = coords(e), dx = p.x - s.dragging.x, dy = p.y - s.dragging.y;
      if (Math.hypot(p.x - s.dragging.initialX, p.y - s.dragging.initialY) > 5) s.dragging.moved = true;
      if (s.dragging.moved) { const r = orbit().r; s.lon = wrap(s.lon - dx * 105 / r); s.lat = clamp(s.lat + dy * 95 / r, -85, 85); schedule(); }
      s.dragging.x = p.x; s.dragging.y = p.y;
    });
    canvas.addEventListener('pointerup', e => {if (!s.dragging) return; const moved = s.dragging.moved; s.dragging = null; if (!moved) { const p = coords(e), hit = point(p.x, p.y); if (hit) onSelect(clamp(hit.lat,-70,70),hit.lon); }});
    canvas.addEventListener('pointercancel', () => {s.dragging=null;});
    canvas.addEventListener('wheel', e => {e.preventDefault(); s.zoom = clamp(s.zoom * (e.deltaY < 0 ? 1.1 : .9), .75, 1.22); schedule();},{passive:false});
    if ('ResizeObserver' in window) new ResizeObserver(resize).observe(stage); else window.addEventListener('resize', resize);
    resize();
    return { setTarget: target, centerOnTarget() {s.lat=s.targetLat;s.lon=s.targetLon;schedule();} };
  }
  window.MarsGlobe = { create };
})();
