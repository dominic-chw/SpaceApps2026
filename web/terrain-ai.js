/* AI terrain briefing: sends selected Mars coordinates + a crop of the Viking mosaic to /api/describe-terrain. */
(() => {
  'use strict';
  const BASE = (document.currentScript && document.currentScript.src) || location.href;
  const API = new URL('/api/describe-terrain', location.href).href;
  let mosaic = null;
  const loadMosaic = () => mosaic || (mosaic = new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = new URL('mars.jpg', BASE).href; }));
  async function crop(lat, lon) {
    const img = await loadMosaic(), ppd = img.width / 360, span = 20, size = 512;
    const c = document.createElement('canvas'); c.width = c.height = size; const x = c.getContext('2d');
    const sx = (lon + 180 - span / 2) * ppd, sy = (90 - lat - span / 2) * ppd, sw = span * ppd, k = size / sw;
    x.fillStyle = '#000'; x.fillRect(0, 0, size, size); x.imageSmoothingQuality = 'high';
    for (const off of [-img.width, 0, img.width]) x.drawImage(img, 0, 0, img.width, img.height, (off - sx) * k, -sy * k, img.width * k, img.height * k);
    x.strokeStyle = '#ffffff'; x.lineWidth = 2; x.beginPath(); x.arc(size / 2, size / 2, 10, 0, 7); x.stroke();
    return c.toDataURL('image/jpeg', .85);
  }
  function init() {
    const btn = document.getElementById('ai-describe'), out = document.getElementById('ai-output'), prev = document.getElementById('ai-preview'), meta = document.getElementById('ai-meta');
    if (!btn || btn.dataset.ready) return; btn.dataset.ready = '1';
    let ctrl = null;
    btn.addEventListener('click', async () => {
      if (ctrl) { ctrl.abort(); return; }
      const lat = Number(document.getElementById('latitude').value), lon = Number(document.getElementById('longitude').value);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) { out.textContent = 'Enter valid coordinates first.'; return; }
      ctrl = new AbortController(); btn.classList.add('busy'); btn.firstElementChild.textContent = 'STOP';
      out.className = 'ai-output loading'; out.textContent = 'Reading the Viking mosaic and analysing terrain…';
      meta.textContent = `${Math.abs(lat).toFixed(1)}° ${lat >= 0 ? 'N' : 'S'} · ${Math.abs(lon).toFixed(1)}° ${lon >= 0 ? 'E' : 'W'}`;
      try {
        const image = await crop(lat, lon); prev.src = image; prev.hidden = false;
        const r = await fetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lat, lon, image }), signal: ctrl.signal });
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || 'The AI service is unavailable.'); }
        const reader = r.body.getReader(), dec = new TextDecoder(); let text = '';
        out.className = 'ai-output';
        for (;;) { const { done, value } = await reader.read(); if (done) break; text += dec.decode(value, { stream: true });
          const i = text.indexOf('\u0000ERROR:'); out.textContent = i >= 0 ? text.slice(0, i) : text;
          if (i >= 0) throw new Error(text.slice(i + 7)); }
        if (!text.trim()) out.textContent = 'No description was returned.';
      } catch (e) {
        if (e.name === 'AbortError') { out.textContent += out.textContent ? '\n\n[Stopped]' : 'Stopped.'; }
        else { out.className = 'ai-output error'; out.textContent = (out.textContent && !out.classList.contains('loading') ? out.textContent + '\n\n' : '') + (location.protocol === 'file:' || /Failed to fetch/.test(e.message) ? 'AI briefing needs the hosted app (the server endpoint is not available here).' : e.message); }
      } finally { ctrl = null; btn.classList.remove('busy'); btn.firstElementChild.textContent = 'DESCRIBE TERRAIN WITH AI'; }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
