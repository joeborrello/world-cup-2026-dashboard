/* All-games map: every venue at once, each drawn as a ring split into one slice
 * per match it hosts, colored by stage. A stage-chip bar above the map selects
 * one or more stages to visualize where they're played: clicking a chip while
 * everything is shown isolates that stage; further clicks add/remove stages.
 * The selection round-trips through ?stages= so a filtered view is shareable.
 * Times render in the device's local zone. */
(function () {
  // Stage buckets (ordered roughly by date) → colors. `slug` is the short,
  // URL-safe token used by the ?stages= query parameter.
  const STAGES = [
    { key: 'group', slug: 'group', label: 'Group stage', color: '#0b6e4f' },
    { key: 'Round of 32', slug: 'r32', label: 'Round of 32', color: '#2f6fb0' },
    { key: 'Round of 16', slug: 'r16', label: 'Round of 16', color: '#7a3fb0' },
    { key: 'Quarter-final', slug: 'qf', label: 'Quarter-finals', color: '#e08a1e' },
    { key: 'Semi-final', slug: 'sf', label: 'Semi-finals', color: '#d0432f' },
    { key: 'final', slug: 'final', label: 'Final & 3rd place', color: '#e3b23c' },
  ];
  const COLOR = {}, KEY_BY_SLUG = {};
  STAGES.forEach(s => { COLOR[s.key] = s.color; KEY_BY_SLUG[s.slug] = s.key; });

  function bucket(m) {
    if (m.group) return 'group';
    if (m.round === 'Final' || m.round === 'Match for third place') return 'final';
    return m.round;  // 'Round of 32' / 'Round of 16' / 'Quarter-final' / 'Semi-final'
  }

  const map = L.map('map', { scrollWheelZoom: true }).setView([39, -96], 3.6);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  // Which stages are selected. All-selected doubles as "no filter".
  const active = new Set(STAGES.map(s => s.key));
  let byVenue = {};   // ground -> {venue, matches:[]}
  const counts = {};  // stage key -> total matches

  // Deep link: ?stages=sf,final preselects stages (unknown tokens ignored).
  (function initFromUrl() {
    const q = new URLSearchParams(location.search).get('stages');
    if (!q) return;
    const keys = q.split(',').map(t => KEY_BY_SLUG[t.trim()]).filter(Boolean);
    if (keys.length) { active.clear(); keys.forEach(k => active.add(k)); }
  })();

  const allOn = () => active.size === STAGES.length;

  function syncUrl() {
    const url = new URL(location);
    if (allOn()) url.searchParams.delete('stages');
    else url.searchParams.set('stages',
      STAGES.filter(s => active.has(s.key)).map(s => s.slug).join(','));
    history.replaceState(null, '', url);
  }

  function selectAll() { STAGES.forEach(s => active.add(s.key)); }

  function onChipClick(key) {
    if (allOn()) {
      // Nothing filtered yet: first click isolates that stage.
      active.clear();
      active.add(key);
    } else if (active.has(key)) {
      active.delete(key);
      if (!active.size) selectAll();  // deselecting the last stage = back to all
    } else {
      active.add(key);
    }
    syncUrl();
    updateChips();
    render();
  }

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

  function buildChips() {
    const bar = document.getElementById('stageChips');
    bar.innerHTML = STAGES.map(s =>
      `<button type="button" class="stage-chip" data-stage="${s.key}" aria-pressed="true">` +
      `<i class="leg-dot" style="background:${s.color}"></i>${s.label}` +
      `<span class="stage-n">${counts[s.key] || 0}</span></button>`).join('') +
      `<button type="button" class="stage-chip stage-chip-all" id="stageAll">Show all stages</button>`;
    bar.addEventListener('click', e => {
      const btn = e.target.closest('button.stage-chip');
      if (!btn) return;
      if (btn.id === 'stageAll') {
        selectAll();
        syncUrl();
        updateChips();
        render();
        return;
      }
      onChipClick(btn.getAttribute('data-stage'));
    });
  }

  function updateChips() {
    document.querySelectorAll('#stageChips .stage-chip[data-stage]').forEach(btn => {
      const on = active.has(btn.getAttribute('data-stage'));
      btn.classList.toggle('off', !on);
      btn.setAttribute('aria-pressed', String(on));
    });
    const allBtn = document.getElementById('stageAll');
    if (allBtn) allBtn.hidden = allOn();
    updateSummary();
  }

  function updateSummary() {
    const el = document.getElementById('stageSummary');
    const tzNote = WCTime.tz ? ` · times in ${WCTime.tz}` : '';
    const nVenues = Object.keys(byVenue).length;
    if (allOn()) {
      el.textContent = `Every stage shown — each ring slice is one match${tzNote}. ` +
        'Click a stage above to see where it plays out.';
      return;
    }
    let nMatches = 0;
    const grounds = new Set();
    Object.entries(byVenue).forEach(([g, entry]) => entry.matches.forEach(m => {
      if (active.has(bucket(m))) { nMatches++; grounds.add(g); }
    }));
    const names = STAGES.filter(s => active.has(s.key)).map(s => s.label).join(' + ');
    el.textContent = `${names}: ${nMatches} ${nMatches === 1 ? 'match' : 'matches'} ` +
      `at ${grounds.size} of ${nVenues} venues${tzNote}.`;
  }

  Promise.all([
    fetch(window.WC.venuesUrl).then(r => r.json()),
    fetch(window.WC.matchesUrl).then(r => r.json())
  ]).then(([venues, matches]) => {
    const vmap = {};
    venues.forEach(v => vmap[v.ground] = v);
    matches.forEach(m => {
      const g = m.ground;
      if (!byVenue[g]) byVenue[g] = { venue: vmap[g] || { stadium: g, city: '', country: '', lat: m.lat, lng: m.lng }, matches: [] };
      byVenue[g].matches.push(m);
      const b = bucket(m); counts[b] = (counts[b] || 0) + 1;
    });
    buildChips();
    updateChips();
    render();
  });
})();
