/* All-games map: every venue at once, each drawn as a ring split into one slice
 * per match it hosts, colored by stage. A legend + stage filter let you see when
 * and where the tournament happens. Times render in the device's local zone. */
(function () {
  // Stage buckets (ordered roughly by date) → colors.
  const STAGES = [
    { key: 'group', label: 'Group stage', color: '#0b6e4f' },
    { key: 'Round of 32', label: 'Round of 32', color: '#2f6fb0' },
    { key: 'Round of 16', label: 'Round of 16', color: '#7a3fb0' },
    { key: 'Quarter-final', label: 'Quarter-finals', color: '#e08a1e' },
    { key: 'Semi-final', label: 'Semi-finals', color: '#d0432f' },
    { key: 'final', label: 'Final & 3rd place', color: '#e3b23c' },
  ];
  const COLOR = {};
  STAGES.forEach(s => COLOR[s.key] = s.color);

  function bucket(m) {
    if (m.group) return 'group';
    if (m.round === 'Final' || m.round === 'Match for third place') return 'final';
    return m.round;  // 'Round of 32' / 'Round of 16' / 'Quarter-final' / 'Semi-final'
  }

  const map = L.map('map', { scrollWheelZoom: true }).setView([39, -96], 3.6);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  const active = new Set(STAGES.map(s => s.key));  // which stages are shown
  let byVenue = {};   // ground -> {venue, matches:[]}

  function badgeIcon(matches) {
    // visible matches only, in chronological order
    const ms = matches.filter(m => active.has(bucket(m)))
                      .sort((a, b) => (a.utc_datetime || '').localeCompare(b.utc_datetime || ''));
    const n = ms.length;
    const size = Math.round(26 + Math.min(n, 9) * 2.2);
    let bg;
    if (n === 0) {
      bg = '#d8e0db';
    } else if (n === 1) {
      bg = COLOR[bucket(ms[0])];
    } else {
      const slice = 360 / n;
      const stops = ms.map((m, i) =>
        `${COLOR[bucket(m)]} ${(i * slice).toFixed(1)}deg ${((i + 1) * slice).toFixed(1)}deg`);
      bg = `conic-gradient(${stops.join(',')})`;
    }
    const dim = n === 0 ? ' dim' : '';
    const html = `<div class="venue-badge${dim}" style="width:${size}px;height:${size}px;background:${bg}">` +
                 `<span class="venue-count">${n}</span></div>`;
    return L.divIcon({ html, className: 'venue-badge-wrap', iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
  }

  function popupHtml(entry) {
    const v = entry.venue;
    let html = `<strong>${v.stadium}</strong><br><span class="pop-city">${v.city}, ${v.country}</span>` +
               `<div class="pop-count">${entry.matches.length} matches</div><hr>`;
    entry.matches.slice().sort((a, b) => (a.utc_datetime || '').localeCompare(b.utc_datetime || ''))
      .forEach(m => {
        const c = COLOR[bucket(m)];
        const tag = m.group ? `Grp ${m.group}` : m.round;
        const sc = m.status === 'finished' ? ` <b>${m.score1}–${m.score2}</b>` : '';
        const dim = active.has(bucket(m)) ? '' : ' style="opacity:.4"';
        html += `<div class="pop-match"${dim}><span class="pop-swatch" style="background:${c}"></span>` +
          `<span class="pop-when">${WCTime.datetime(m.utc_datetime)}</span><br>` +
          `<span class="pop-tag">${tag}</span> ${wcFlag(m.team1_code, m.team1)}${m.team1} v ` +
          `${wcFlag(m.team2_code, m.team2)}${m.team2}${sc}</div>`;
      });
    return html;
  }

  let markers = [];
  function render() {
    markers.forEach(m => map.removeLayer(m));
    markers = [];
    const bounds = [];
    Object.values(byVenue).forEach(entry => {
      const v = entry.venue;
      if (v.lat == null) return;
      const marker = L.marker([v.lat, v.lng], { icon: badgeIcon(entry.matches) }).addTo(map);
      marker.bindPopup(popupHtml(entry), { maxHeight: 320, minWidth: 240 });
      markers.push(marker);
      bounds.push([v.lat, v.lng]);
    });
    if (bounds.length && !map._fitted) { map.fitBounds(bounds, { padding: [40, 40] }); map._fitted = true; }
  }

  function buildLegend() {
    const tzNote = WCTime.tz ? ` · times in ${WCTime.tz}` : '';
    document.getElementById('schedLegend').innerHTML =
      STAGES.map(s => `<span class="leg-item"><i class="leg-dot" style="background:${s.color}"></i>${s.label}</span>`).join('') +
      `<span class="leg-note">Ring slice = one match${tzNote}</span>`;

    const ul = document.getElementById('stageFilter');
    ul.innerHTML = STAGES.map(s =>
      `<li><label><input type="checkbox" data-stage="${s.key}" checked>` +
      `<i class="leg-dot" style="background:${s.color}"></i>${s.label} ` +
      `<span class="stage-n" data-stage-n="${s.key}"></span></label></li>`).join('');
    ul.addEventListener('change', e => {
      const k = e.target.getAttribute('data-stage');
      if (!k) return;
      if (e.target.checked) active.add(k); else active.delete(k);
      render();
    });
  }

  Promise.all([
    fetch(window.WC.venuesUrl).then(r => r.json()),
    fetch(window.WC.matchesUrl).then(r => r.json())
  ]).then(([venues, matches]) => {
    const vmap = {};
    venues.forEach(v => vmap[v.ground] = v);
    const counts = {};
    matches.forEach(m => {
      const g = m.ground;
      if (!byVenue[g]) byVenue[g] = { venue: vmap[g] || { stadium: g, city: '', country: '', lat: m.lat, lng: m.lng }, matches: [] };
      byVenue[g].matches.push(m);
      const b = bucket(m); counts[b] = (counts[b] || 0) + 1;
    });
    buildLegend();
    Object.entries(counts).forEach(([k, n]) => {
      const el = document.querySelector(`[data-stage-n="${k}"]`);
      if (el) el.textContent = `(${n})`;
    });
    render();
  });
})();
