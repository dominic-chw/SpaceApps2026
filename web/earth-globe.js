/* Interactive Earth globe. Mirrors the Earth analog pins rendered by app.js in #earth-map
   and flies to the selected one. Read-only: selection still goes through app.js pins. */
(() => {
  'use strict';
  const BASE = (document.currentScript && document.currentScript.src) || location.href;
  const RAD = Math.PI / 180, DEG = 180 / Math.PI;
  const clamp = (n, a, b) => Math.min(b, Math.max(a, n));
  const wrap = d => ((d + 180) % 360 + 360) % 360 - 180;
  const ease = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  // Reverse geocoding (OpenStreetMap Nominatim, free, no key). Cached; one request per new location.
  const placeCache = new Map();
  function lookupPlace(lat, lon) {
    const key = lat.toFixed(2) + ',' + lon.toFixed(2);
    if (!placeCache.has(key)) placeCache.set(key, fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lon}&zoom=10&accept-language=en`)
      .then(r => r.ok ? r.json() : null).then(d => {
        const a = d && d.address; if (!a) return { city: null, region: null, country: null };
        return { city: a.city || a.town || a.village || a.hamlet || a.municipality || a.county || null, region: a.state || a.region || null, country: a.country || null };
      }).catch(() => null));
    return placeCache.get(key);
  }
  const esc = t => String(t ?? '').replace(/[<>&"]/g, '');
  function init() {
    const canvas = document.getElementById('earth-canvas'), map = document.getElementById('earth-map');
    if (!canvas || !map || canvas.dataset.ready) return; canvas.dataset.ready = '1';
    const stage = canvas.parentElement, hud = document.getElementById('earth-hud');
    const place = document.createElement('div'); place.className = 'earth-place'; place.setAttribute('aria-live', 'polite'); stage.appendChild(place);
    function showPlace(p) {
      place.classList.remove('show'); if (!p) return;
      place.innerHTML = '<span class="place-k">LOCATING…</span>'; place.classList.add('show');
      lookupPlace(p.lat, p.lon).then(info => { if (s.lastSel !== p) return;
        if (!info) { place.innerHTML = '<span class="place-k">LOCATION</span><strong>Place name unavailable</strong>'; return; }
        if (!info.country) { place.innerHTML = '<span class="place-k">LOCATION</span><strong>Open ocean / no country</strong>'; return; }
        place.innerHTML = `<span class="place-k">CITY</span><strong>${esc(info.city || '—')}</strong><span class="place-k">${info.region ? 'REGION' : ''}</span><em>${esc(info.region || '')}</em><span class="place-k">COUNTRY</span><strong>${esc(info.country)}</strong>`; });
    }
    const ctx = canvas.getContext('2d', { alpha: false });
    const s = { lat: 20, lon: -30, zoom: 1, w: 600, h: 420, pins: [], sel: -1, fly: null, drag: null, hover: -1, idle: true, visible: true, lastSel: null, pulse: 0 };
    let tex = null;
    const img = new Image(); img.src = new URL('earth.jpg', BASE).href;
    img.onload = () => { const c = document.createElement('canvas'); c.width = img.width; c.height = img.height; const x = c.getContext('2d'); x.drawImage(img, 0, 0); tex = { w: c.width, h: c.height, px: x.getImageData(0, 0, c.width, c.height).data }; };
    const rand = (seed => () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; })(7);
    const stars = Array.from({ length: 120 }, () => ({ x: rand(), y: rand(), r: .3 + rand(), a: .1 + rand() * .45 }));
    const orbit = () => ({ cx: s.w / 2, cy: s.h / 2, r: Math.min(s.w * .36, s.h * .42) * s.zoom });
    function project(lat, lon) {
      const a = lat * RAD, d = wrap(lon - s.lon) * RAD, b = s.lat * RAD;
      const z = Math.sin(a) * Math.sin(b) + Math.cos(a) * Math.cos(b) * Math.cos(d);
      if (z <= 0) return null; const { cx, cy, r } = orbit();
      return { x: cx + r * Math.cos(a) * Math.sin(d), y: cy - r * (Math.sin(a) * Math.cos(b) - Math.cos(a) * Math.cos(d) * Math.sin(b)), z };
    }
    function readPins() {
      const pins = [...map.querySelectorAll('[data-map-index]')].map(g => {
        const c = g.querySelector('circle'); const cx = +c.getAttribute('cx'), cy = +c.getAttribute('cy');
        return { i: +g.dataset.mapIndex, lon: cx / 720 * 360 - 180, lat: 90 - cy / 330 * 180, sel: c.getAttribute('r') === '12', name: (g.querySelector('title')?.textContent || '').split(' — ')[0], el: g };
      });
      s.pins = pins; const sel = pins.find(p => p.sel);
      stage.classList.toggle('has-pins', pins.length > 0);
      if (sel && (!s.lastSel || s.lastSel.lat !== sel.lat || s.lastSel.lon !== sel.lon)) { s.lastSel = sel; flyTo(sel); showPlace(sel); }
      if (!sel) { if (s.lastSel) showPlace(null); s.lastSel = null; s.idle = true; if (hud) hud.innerHTML = '<span class="hud-dot"></span> AWAITING MARS TARGET'; }
      loop();
    }
    function flyTo(p) {
      s.idle = false;
      s.fly = { t0: performance.now(), dur: 1800, lat0: s.lat, lon0: s.lon, z0: s.zoom, lat1: clamp(p.lat, -75, 75), dlon: wrap(p.lon - s.lon), z1: 1.55 };
      stage.classList.remove('locked'); stage.classList.add('flying');
      if (hud) hud.innerHTML = `<span class="hud-dot"></span> MAPPING MARS → EARTH · <strong>${p.name.replace(/[<>&]/g, '')}</strong>`;
    }
    function frame(now) {
      s.raf = 0;
      if (s.fly) {
        const t = clamp((now - s.fly.t0) / s.fly.dur, 0, 1), e = ease(t), f = s.fly;
        s.lat = f.lat0 + (f.lat1 - f.lat0) * e; s.lon = wrap(f.lon0 + f.dlon * e);
        s.zoom = f.z0 + (f.z1 - f.z0) * e - Math.sin(Math.PI * t) * .32;
        if (t >= 1) { s.fly = null; s.pulse = now; stage.classList.remove('flying'); stage.classList.add('locked');
          const sel = s.pins.find(p => p.sel); if (hud && sel) hud.innerHTML = `<span class="hud-dot ok"></span> LOCKED · <strong>${sel.name.replace(/[<>&]/g, '')}</strong> · ${Math.abs(sel.lat).toFixed(1)}°${sel.lat >= 0 ? 'N' : 'S'} ${Math.abs(sel.lon).toFixed(1)}°${sel.lon >= 0 ? 'E' : 'W'}`; }
      } else if (s.idle && !s.drag) s.lon = wrap(s.lon + .12);
      const { w, h } = s;
      const back = ctx.createLinearGradient(0, 0, w, h); back.addColorStop(0, '#05070e'); back.addColorStop(1, '#0a1220');
      ctx.fillStyle = back; ctx.fillRect(0, 0, w, h);
      for (const st of stars) { ctx.globalAlpha = st.a; ctx.fillStyle = '#dfe8ff'; ctx.beginPath(); ctx.arc(st.x * w, st.y * h, st.r, 0, 7); ctx.fill(); }
      ctx.globalAlpha = 1;
      const { cx, cy, r } = orbit();
      const glow = ctx.createRadialGradient(cx, cy, r * .9, cx, cy, r * 1.35);
      glow.addColorStop(0, '#5fa8ff55'); glow.addColorStop(.4, '#3b7bff22'); glow.addColorStop(1, '#3b7bff00');
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(cx, cy, r * 1.35, 0, 7); ctx.fill();
      if (tex) {
        const x0 = Math.max(0, Math.floor(cx - r - 1)), y0 = Math.max(0, Math.floor(cy - r - 1));
        const x1 = Math.min(w, Math.ceil(cx + r + 1)), y1 = Math.min(h, Math.ceil(cy + r + 1));
        const bw = x1 - x0, bh = y1 - y0; if (bw > 0 && bh > 0) {
        const image = ctx.createImageData(bw, bh), d = image.data;
        const sin0 = Math.sin(s.lat * RAD), cos0 = Math.cos(s.lat * RAD), lon0 = s.lon * RAD;
        for (let py = 0; py < bh; py++) { const v = (cy - (y0 + py + .5)) / r;
          for (let px = 0; px < bw; px++) { const u = ((x0 + px + .5) - cx) / r, d2 = u * u + v * v; const di = (py * bw + px) * 4;
            if (d2 > 1) { d[di + 3] = 0; continue; }
            const z = Math.sqrt(1 - d2);
            const lat = Math.asin(clamp(v * cos0 + z * sin0, -1, 1)), lon = lon0 + Math.atan2(u, z * cos0 - v * sin0);
            const tx = ((lon / (2 * Math.PI) + .5) % 1 + 1) % 1;
            const si = (clamp(Math.floor((.5 - lat / Math.PI) * tex.h), 0, tex.h - 1) * tex.w + Math.min(tex.w - 1, Math.floor(tx * tex.w))) * 4;
            const L = .28 + Math.max(0, u * -.45 + v * .35 + z * .82) * .9, rim = Math.pow(1 - z, 3) * .55;
            d[di] = tex.px[si] * L + rim * 90; d[di + 1] = tex.px[si + 1] * L + rim * 160; d[di + 2] = tex.px[si + 2] * L + rim * 255; d[di + 3] = 255; } }
        if (!s.disk || s.disk.width !== bw || s.disk.height !== bh) { s.disk = document.createElement('canvas'); s.disk.width = bw; s.disk.height = bh; }
        s.disk.getContext('2d').putImageData(image, 0, 0); ctx.drawImage(s.disk, x0, y0); }
      } else { ctx.fillStyle = '#0f2a4a'; ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.fill(); }
      ctx.strokeStyle = '#8cc4ff55'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.stroke();
      if (s.drag && s.drag.moved) { ctx.strokeStyle = '#8cc4ff99'; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(cx, cy, r + 4, 0, 7); ctx.stroke(); }
      let anim = !!s.fly || s.idle;
      for (const p of s.pins) { const q = project(p.lat, p.lon); if (!q) continue; p.x = q.x; p.y = q.y;
        const big = p.sel, hov = p.i === s.hover;
        ctx.globalAlpha = .4 + q.z * .6;
        ctx.fillStyle = big ? '#ff7a52' : hov ? '#ffd3b8' : '#ffb38a'; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(q.x, q.y, big ? 7 : hov ? 6 : 4.5, 0, 7); ctx.fill(); ctx.stroke();
        if (big) { const t = ((now - s.pulse) % 1800) / 1800; anim = true; ctx.strokeStyle = `rgba(255,122,82,${1 - t})`; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(q.x, q.y, 8 + t * 30, 0, 7); ctx.stroke();
          if (!s.fly) { ctx.strokeStyle = '#ffffffcc'; ctx.lineWidth = 1; for (const [a, b] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) { ctx.beginPath(); ctx.moveTo(q.x + a * 12, q.y + b * 12); ctx.lineTo(q.x + a * 22, q.y + b * 22); ctx.stroke(); } } }
        if (hov || big) { ctx.font = '500 11px "JetBrains Mono", monospace'; const txt = `${p.i + 1} · ${p.name}`; const tw = ctx.measureText(txt).width + 16;
          ctx.fillStyle = 'rgba(8,12,22,.85)'; ctx.beginPath(); ctx.roundRect(q.x + 16, q.y - 30, tw, 22, 11); ctx.fill(); ctx.fillStyle = '#eef4ff'; ctx.fillText(txt, q.x + 24, q.y - 15); }
        ctx.globalAlpha = 1; }
      if (anim && s.visible) loop();
    }
    function loop() { if (!s.raf) s.raf = requestAnimationFrame(frame); }
    function resize() { const b = canvas.getBoundingClientRect(); s.w = Math.max(300, Math.round(b.width)); s.h = Math.max(240, Math.round(b.height)); canvas.width = s.w; canvas.height = s.h; loop(); }
    const pos = e => { const b = canvas.getBoundingClientRect(); return { x: (e.clientX - b.left) * s.w / b.width, y: (e.clientY - b.top) * s.h / b.height }; };
    const hit = p => s.pins.findIndex(q => q.x != null && project(q.lat, q.lon) && Math.hypot(q.x - p.x, q.y - p.y) < 12);
    canvas.addEventListener('pointerdown', e => { const p = pos(e); s.drag = { ...p, sx: p.x, sy: p.y, moved: false }; s.fly = null; stage.classList.add('is-dragging'); canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => { const p = pos(e);
      if (!s.drag) { const i = hit(p); const hv = i >= 0 ? s.pins[i].i : -1; if (hv !== s.hover) { s.hover = hv; stage.classList.toggle('is-over-pin', hv >= 0); loop(); } return; }
      if (Math.hypot(p.x - s.drag.sx, p.y - s.drag.sy) > 5) { s.drag.moved = true; s.idle = false; }
      if (s.drag.moved) { const r = orbit().r; s.lon = wrap(s.lon - (p.x - s.drag.x) * 105 / r); s.lat = clamp(s.lat + (p.y - s.drag.y) * 95 / r, -85, 85); loop(); }
      s.drag.x = p.x; s.drag.y = p.y; });
    canvas.addEventListener('pointerup', e => { const d = s.drag; s.drag = null; stage.classList.remove('is-dragging'); loop();
      if (d && !d.moved) { const i = hit(pos(e)); if (i >= 0) s.pins[i].el.dispatchEvent(new MouseEvent('click', { bubbles: true })); } });
    canvas.addEventListener('pointerleave', () => { if (s.hover !== -1) { s.hover = -1; stage.classList.remove('is-over-pin'); loop(); } });
    canvas.addEventListener('wheel', e => { e.preventDefault(); const dy = e.deltaY * (e.deltaMode === 1 ? 16 : 1); s.zoom = clamp(s.zoom * Math.exp(-dy * .0015), .7, 2.6); loop(); }, { passive: false });
    if ('IntersectionObserver' in window) new IntersectionObserver(([en]) => { s.visible = en.isIntersecting; if (s.visible) loop(); }).observe(stage);
    new ResizeObserver(resize).observe(stage); resize();
    new MutationObserver(readPins).observe(map, { childList: true, subtree: true, attributes: true });
    readPins();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
