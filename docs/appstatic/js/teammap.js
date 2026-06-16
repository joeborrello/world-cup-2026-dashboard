/* Follow-a-team map: pick teams from the checklist; their matches are plotted as
 * flag pins colored by date (cool = early, warm = late), and each team's matches are
 * joined chronologically by a route line that follows the same date gradient with
 * directional arrows. Knockout matches appear for a team once its slot resolves.
 * Match details live in the click popup. */
(function () {
  const START = window.WC.start, END = window.WC.end;

  // ── date → color (continuous cool→warm gradient across the tournament) ──────
  const t0 = Date.parse(START + 'T00:00:00Z');
  const t1 = Date.parse(END + 'T23:59:59Z');
  function frac(dateStr) {
    const t = Date.parse((dateStr || START) + 'T12:00:00Z');
    return Math.max(0, Math.min(1, (t - t0) / (t1 - t0)));
  }
  function hueColor(f) { return `hsl(${Math.round(210 - 210 * f)}, 75%, 47%)`; }  // blue→red
  function dateColor(dateStr) { return hueColor(frac(dateStr)); }

  // ── map ─────────────────────────────────────────────────────────────────────
  const map = L.map('map', { scrollWheelZoom: true }).setView([39, -96], 3.6);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);
  const layer = L.layerGroup().addTo(map);

  let teams = [], matches = [];
  const selected = new Set();   // team display names being followed
  const listEl = document.getElementById('teamList');
  const countEl = document.getElementById('tmCount');
  const legendEl = document.getElementById('lineLegend');

  function esc(s) { return (s || '').replace(/"/g, '&quot;'); }
  function flagImg(code, name) {
    if (!code) return '<span class="fp-tbd">?</span>';
    return `<img class="fp-flag" src="https://flagcdn.com/${code}.svg" alt="${esc(name)}" title="${esc(name)}">`;
  }

  // matches a team plays (display name match), with known coordinates, chronological
  function teamMatches(name) {
    return matches
      .filter(m => (m.team1 === name || m.team2 === name) && m.lat != null)
      .sort((a, b) => (a.utc_datetime || '').localeCompare(b.utc_datetime || ''));
  }

  function popupHtml(group) {
    const v = group[0];
    let html = `<strong>${v.stadium}</strong><br><span class="pop-city">${v.city}, ${v.country}</span><hr>`;
    group.slice().sort((a, b) => (a.utc_datetime || '').localeCompare(b.utc_datetime || ''))
      .forEach(m => {
        const c = dateColor(m.date);
        const tag = m.group ? `Grp ${m.group}` : m.round;
        const sc = m.status === 'finished' ? ` <b>${m.score1}–${m.score2}</b>` : '';
        html += `<div class="pop-match"><span class="pop-swatch" style="background:${c}"></span>` +
          `<span class="pop-when">${WCTime.datetime(m.utc_datetime)}</span><br>` +
          `<span class="pop-tag">${tag}</span> ${flagImg(m.team1_code, m.team1)}${m.team1} v ` +
          `${flagImg(m.team2_code, m.team2)}${m.team2}${sc}</div>`;
      });
    return html;
  }

  // ── route line: gradient-colored sub-segments + a directional arrow per leg ──
  function lerp(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]; }

  function addArrow(a, b, color) {
    // pixel angle is invariant to zoom in Web Mercator (uniform scaling), so we
    // can compute it once; the marker stays anchored at the leg's midpoint.
    const pa = map.latLngToLayerPoint(a), pb = map.latLngToLayerPoint(b);
    const ang = Math.atan2(pb.y - pa.y, pb.x - pa.x) * 180 / Math.PI;
    const html = `<div class="rt-arrow" style="transform:rotate(${ang}deg)">` +
      `<svg viewBox="0 0 12 12" width="15" height="15"><path d="M2 2 L10 6 L2 10 Z" ` +
      `fill="${color}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/></svg></div>`;
    L.marker(lerp(a, b, 0.5), {
      icon: L.divIcon({ html, className: 'rt-arrow-wrap', iconSize: [15, 15], iconAnchor: [7.5, 7.5] }),
      interactive: false, keyboard: false,
    }).addTo(layer);
  }

  function drawRoute(ms) {
    const STEPS = 8;
    for (let i = 0; i < ms.length - 1; i++) {
      const a = [ms[i].lat, ms[i].lng], b = [ms[i + 1].lat, ms[i + 1].lng];
      if (a[0] === b[0] && a[1] === b[1]) continue;   // same venue twice — no leg
      const fA = frac(ms[i].date), fB = frac(ms[i + 1].date);
      for (let s = 0; s < STEPS; s++) {
        const seg = [lerp(a, b, s / STEPS), lerp(a, b, (s + 1) / STEPS)];
        const f = fA + (fB - fA) * ((s + 0.5) / STEPS);
        L.polyline(seg, { color: hueColor(f), weight: 3.5, opacity: 0.9, lineCap: 'round' }).addTo(layer);
      }
      addArrow(a, b, hueColor((fA + fB) / 2));
    }
  }

  // ── render selected teams onto the map ──────────────────────────────────────
  function render() {
    layer.clearLayers();

    // route lines first (drawn beneath the pins), one per followed team
    selected.forEach(name => drawRoute(teamMatches(name)));

    // pins: one per venue, each match shown as a date-colored band (deduped by num)
    const seen = new Set(), byVenue = {};
    selected.forEach(name => teamMatches(name).forEach(m => {
      if (seen.has(m.num)) return;
      seen.add(m.num);
      (byVenue[m.ground] = byVenue[m.ground] || []).push(m);
    }));

    const bounds = [];
    Object.values(byVenue).forEach(group => {
      const v = group[0];
      group.sort((a, b) => (a.utc_datetime || '').localeCompare(b.utc_datetime || ''));
      const rows = group.map(m =>
        `<div class="fp-row tp-band" style="background:${dateColor(m.date)}">` +
        `${flagImg(m.team1_code, m.team1)}<span class="fp-v">v</span>${flagImg(m.team2_code, m.team2)}</div>`
      ).join('');
      const lastC = dateColor(group[group.length - 1].date);
      const w = 92, h = group.length * 23 + 13;
      const icon = L.divIcon({
        html: `<div class="fp-pin tp-pin">${rows}<i class="fp-stem" style="border-top-color:${lastC}"></i></div>`,
        className: 'fp-wrap', iconSize: [w, h], iconAnchor: [w / 2, h],
      });
      L.marker([v.lat, v.lng], { icon }).addTo(layer).bindPopup(popupHtml(group), { maxHeight: 320, minWidth: 230 });
      bounds.push([v.lat, v.lng]);
    });

    if (bounds.length) map.fitBounds(bounds, { padding: [60, 60], maxZoom: 6 });
  }

  // ── team checklist (grouped A–L, filterable) ────────────────────────────────
  function buildList(filter) {
    const f = (filter || '').toLowerCase();
    const byGroup = {};
    teams.forEach(t => { (byGroup[t.group] = byGroup[t.group] || []).push(t); });
    const html = Object.keys(byGroup).sort().map(letter => {
      const items = byGroup[letter].filter(t => t.name.toLowerCase().includes(f));
      if (!items.length) return '';
      return `<div class="tg"><div class="tg-h">Group ${letter}</div>` +
        items.map(t => {
          const on = selected.has(t.name);
          return `<label class="tm-item${on ? ' on' : ''}">` +
            `<input type="checkbox" data-team="${esc(t.name)}"${on ? ' checked' : ''}>` +
            `${flagImg(t.code, t.name)}<span class="tm-name">${t.name}</span></label>`;
        }).join('') + `</div>`;
    }).join('');
    listEl.innerHTML = html || '<p class="subtle tm-empty">No teams match.</p>';
  }

  function updateCount() { countEl.textContent = selected.size ? `(${selected.size})` : ''; }

  function buildLegend() {
    if (!selected.size) { legendEl.innerHTML = ''; return; }
    legendEl.innerHTML = '<span class="ll-label">Following:</span>' +
      [...selected].map(name => {
        const t = teams.find(x => x.name === name) || {};
        return `<span class="ll-item">${flagImg(t.code, name)}${name}</span>`;
      }).join('');
  }

  function buildDateLegend() {
    const fmt = d => new Date(d + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    const stops = [];
    for (let i = 0; i <= 10; i++) stops.push(hueColor(i / 10));
    document.getElementById('dateLegend').innerHTML =
      `<span class="dl-label">${fmt(START)}</span>` +
      `<span class="dl-bar" style="background:linear-gradient(to right, ${stops.join(',')})"></span>` +
      `<span class="dl-label">${fmt(END)}</span>` +
      `<span class="dl-note">pin &amp; route = match date</span>`;
  }

  // ── events ──────────────────────────────────────────────────────────────────
  listEl.addEventListener('change', e => {
    const cb = e.target.closest('input[data-team]');
    if (!cb) return;
    const name = cb.getAttribute('data-team');
    if (cb.checked) selected.add(name); else selected.delete(name);
    cb.closest('.tm-item').classList.toggle('on', cb.checked);
    updateCount(); buildLegend(); render();
  });

  document.getElementById('teamSearch').addEventListener('input', e => buildList(e.target.value));
  document.getElementById('teamClear').addEventListener('click', () => {
    selected.clear();
    buildList(document.getElementById('teamSearch').value);
    updateCount(); buildLegend(); render();
  });

  // ── boot ────────────────────────────────────────────────────────────────────
  Promise.all([
    fetch(window.WC.teamsUrl).then(r => r.json()),
    fetch(window.WC.matchesUrl).then(r => r.json())
  ]).then(([ts, ms]) => {
    teams = ts; matches = ms;
    buildDateLegend(); buildList(''); updateCount();
  });
})();
