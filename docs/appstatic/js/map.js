/* Daily match map: a Leaflet map + day slider over the tournament calendar.
 * Each match shows its kickoff weather (forecast / today / historical). When the
 * selected day is *today*, live overlays are available: radar (RainViewer),
 * isobars/pressure (OpenWeatherMap, if a key is configured) and weather
 * advisories (NWS + Environment Canada). */
(function () {
  const TODAY = new Date().toISOString().slice(0, 10);

  const map = L.map('map', { scrollWheelZoom: true }).setView([37.8, -96], 3.4);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  let days = [];        // [{date, count}]
  let venues = {};      // ground -> venue
  let markers = [];     // active match markers
  let idx = 0;

  const slider = document.getElementById('daySlider');
  const dayLabel = document.getElementById('dayLabel');
  const panelDate = document.getElementById('panelDate');
  const matchList = document.getElementById('matchList');

  function fmtDate(iso) {
    return new Date(iso + 'T12:00:00').toLocaleDateString(undefined,
      { weekday: 'short', month: 'short', day: 'numeric' });
  }
  function clearMarkers() { markers.forEach(m => map.removeLayer(m)); markers = []; }

  // ── temperature unit (device preference, persisted) ─────────────────────────
  let unit = localStorage.getItem('wcUnit') === 'C' ? 'C' : 'F';
  function cvt(f) { return unit === 'C' ? (f - 32) * 5 / 9 : f; }
  function tShort(f) { return f == null ? '' : Math.round(cvt(f)) + '°'; }
  function tFull(f) { return f == null ? '' : Math.round(cvt(f)) + '°' + unit; }

  // ── weather formatting ──────────────────────────────────────────────────────
  const KIND_LABEL = { forecast: 'Forecast', current: 'Today', historical: 'Actual' };

  function wxChip(w) {
    if (!w || w.available === false) return '';
    const p = w.precip_prob != null ? ` ${w.precip_prob}%` : '';
    return `<span class="wx">${w.emoji || ''}${tShort(w.temp_f)}${p}</span>`;
  }
  function wxLine(w) {
    if (!w) return '';
    if (w.available === false)
      return `<div class="ml-wx wx-na">⏳ Forecast not yet available (more than 16 days out)</div>`;
    const parts = [];
    if (w.temp_f != null) parts.push(tFull(w.temp_f));
    if (w.humidity != null) parts.push(`${w.humidity}% humidity`);
    if (w.dewpoint_f != null) parts.push(`${tShort(w.dewpoint_f)} dew pt`);
    if (w.precip_prob != null) parts.push(`${w.precip_prob}% precip`);
    else if (w.precip_in) parts.push(`${w.precip_in}″ rain`);
    if (w.wind_mph != null) parts.push(`${Math.round(w.wind_mph)} mph wind`);
    return `<div class="ml-wx">${w.emoji || ''} <b>${w.desc || ''}</b> · ${parts.join(' · ')} ` +
      `<span class="wx-kind">${KIND_LABEL[w.kind] || ''}</span></div>`;
  }

  // ── pins ────────────────────────────────────────────────────────────────────
  function pinFlag(code, name) {
    if (!code) return '<span class="fp-tbd">?</span>';
    const n = (name || '').replace(/"/g, '&quot;');
    return `<img class="fp-flag" src="https://flagcdn.com/${code}.svg" alt="${n}" title="${n}">`;
  }
  function flagPin(matches, wx) {
    const rows = matches.map(m =>
      `<div class="fp-row">${pinFlag(m.team1_code, m.team1)}` +
      `<span class="fp-v">v</span>${pinFlag(m.team2_code, m.team2)}${wxChip(wx[m.num])}</div>`).join('');
    const w = 104, h = matches.length * 21 + 14;
    return L.divIcon({
      html: `<div class="fp-pin">${rows}<i class="fp-stem"></i></div>`,
      className: 'fp-wrap', iconSize: [w, h], iconAnchor: [w / 2, h],
    });
  }
  function popupHtml(matches, wx) {
    const v = matches[0];
    let html = `<strong>${v.stadium}</strong><br><span class="pop-city">${v.city}, ${v.country}</span><hr>`;
    matches.forEach(m => {
      const sc = m.status === 'finished' ? ` <b>${m.score1}–${m.score2}</b>` : '';
      const tag = m.group ? `Grp ${m.group}` : m.round;
      html += `<div class="pop-match"><span class="pop-tag">${tag}</span> ${WCTime.time(m.utc_datetime)} ` +
        `${wcFlag(m.team1_code, m.team1)}${m.team1} v ${wcFlag(m.team2_code, m.team2)}${m.team2}${sc}` +
        wxLine(wx[m.num]) + `</div>`;
    });
    return html;
  }

  // ── render one day ──────────────────────────────────────────────────────────
  let lastMatches = [], lastWx = {};

  function renderDay() {
    const day = days[idx];
    if (!day) return;
    slider.value = idx;
    dayLabel.textContent = fmtDate(day.date) + ` · ${day.count} match${day.count > 1 ? 'es' : ''}`;
    panelDate.textContent = fmtDate(day.date);
    matchList.innerHTML = '<li class="loading">Loading…</li>';
    clearMarkers();
    updateLiveLayers(day.date);

    Promise.all([
      fetch(window.WC.matchesUrl + '?date=' + day.date).then(r => r.json()),
      fetch(window.WC.weatherUrl + '?date=' + day.date).then(r => r.json()).catch(() => ({})),
    ]).then(([matches, wx]) => {
      lastMatches = matches; lastWx = wx;
      draw(true);
    });
  }

  // draw the cached day; `fit` re-centers the map (skipped on unit re-render)
  function draw(fit) {
    clearMarkers();
    const matches = lastMatches, wx = lastWx;
    const byVenue = {};
    matches.forEach(m => { (byVenue[m.ground] = byVenue[m.ground] || []).push(m); });

    const bounds = [];
    Object.values(byVenue).forEach(ms => {
      const v = ms[0];
      if (v.lat == null) return;
      const marker = L.marker([v.lat, v.lng], { icon: flagPin(ms, wx) }).addTo(map);
      marker.bindPopup(popupHtml(ms, wx));
      markers.push(marker);
      bounds.push([v.lat, v.lng]);
    });
    if (fit && bounds.length) map.fitBounds(bounds, { padding: [50, 50], maxZoom: 6 });

    matchList.innerHTML = '';
    if (!matches.length) { matchList.innerHTML = '<li class="empty">No matches.</li>'; return; }
    matches.forEach(m => {
      const li = document.createElement('li');
      const sc = m.status === 'finished' ? `<span class="ml-score">${m.score1}–${m.score2}</span>` : '';
      const tag = m.group ? `Group ${m.group}` : m.round;
      li.innerHTML = `<div class="ml-top"><span class="ml-tag">${tag}</span>` +
        `<span class="ml-time">${WCTime.time(m.utc_datetime)} ${WCTime.tz}</span></div>` +
        `<div class="ml-teams">${wcFlag(m.team1_code, m.team1)}${m.team1} <em>v</em> ` +
        `${wcFlag(m.team2_code, m.team2)}${m.team2} ${sc}</div>` +
        `<div class="ml-venue">📍 ${m.stadium}, ${m.city}</div>` + wxLine(wx[m.num]);
      li.addEventListener('click', () => {
        const mk = markers.find(x => {
          const ll = x.getLatLng();
          return Math.abs(ll.lat - m.lat) < 1e-6 && Math.abs(ll.lng - m.lng) < 1e-6;
        });
        if (mk) { map.setView(mk.getLatLng(), 6); mk.openPopup(); }
      });
      matchList.appendChild(li);
    });
  }

  // ── temperature heat-map legend (matches OWM's temp palette, 0–30°C) ─────────
  const TEMP_ANCHORS_C = [0, 10, 20, 30];   // evenly spaced -> align with the CSS bar
  function renderTempLegend() {
    const cap = document.getElementById('tempCap');
    const ticks = document.getElementById('tempTicks');
    if (cap) cap.textContent = `Air temp (°${unit})`;
    if (ticks) ticks.innerHTML = TEMP_ANCHORS_C.map((c, i) => {
      const val = unit === 'C' ? c : Math.round(c * 9 / 5 + 32);
      return `<span>${val}°${i === TEMP_ANCHORS_C.length - 1 ? '+' : ''}</span>`;
    }).join('');
  }

  // ── unit toggle ─────────────────────────────────────────────────────────────
  const unitToggle = document.getElementById('unitToggle');
  unitToggle.querySelectorAll('button').forEach(b => {
    b.classList.toggle('active', b.dataset.unit === unit);
    b.addEventListener('click', () => {
      if (b.dataset.unit === unit) return;
      unit = b.dataset.unit;
      localStorage.setItem('wcUnit', unit);
      unitToggle.querySelectorAll('button').forEach(x => x.classList.toggle('active', x.dataset.unit === unit));
      renderTempLegend();   // keep the heat-map scale in the chosen unit
      draw(false);          // re-render temps in place, keep current view
    });
  });

  // ── live overlays (today only): radar, isobars, advisories ──────────────────
  const panel = document.getElementById('liveLayers');
  const cbRadar = document.getElementById('layRadar');
  const cbTemp = document.getElementById('layTemp');
  const cbIsobars = document.getElementById('layIsobars');
  const cbAdvis = document.getElementById('layAdvis');
  const hint = document.getElementById('liveHint');
  const tempLegend = document.getElementById('tempLegend');
  let radarLayer = null, tempLayer = null, isobarLayer = null, advisLayer = null;
  let rvHost = null, rvPath = null, advisData = null;

  // the OWM-backed layers (temp heat map + isobars) need an OpenWeatherMap key
  if (!window.WC.owmKey) {
    [cbTemp, cbIsobars].forEach(cb => {
      cb.disabled = true;
      cb.parentElement.classList.add('disabled');
      cb.parentElement.title = 'Add an OpenWeatherMap key to enable this layer';
    });
  }

  function removeOverlays() {
    [radarLayer, tempLayer, isobarLayer, advisLayer].forEach(l => l && map.removeLayer(l));
    radarLayer = tempLayer = isobarLayer = advisLayer = null;
    cbRadar.checked = cbTemp.checked = cbIsobars.checked = cbAdvis.checked = false;
    tempLegend.hidden = true;
  }

  function updateLiveLayers(date) {
    if (date === TODAY) {
      panel.hidden = false;
      hint.textContent = window.WC.owmKey
        ? '(live — today only)' : '(live — today only · temp map & isobars need an OWM key)';
    } else {
      removeOverlays();
      panel.hidden = true;
    }
  }

  cbRadar.addEventListener('change', () => {
    if (!cbRadar.checked) { if (radarLayer) map.removeLayer(radarLayer); radarLayer = null; return; }
    const add = () => {
      if (!rvPath) return;
      radarLayer = L.tileLayer(`${rvHost}${rvPath}/256/{z}/{x}/{y}/4/1_1.png`,
        { opacity: 0.6, zIndex: 300, attribution: 'Radar © RainViewer' }).addTo(map);
    };
    if (rvPath) return add();
    fetch('https://api.rainviewer.com/public/weather-maps.json').then(r => r.json()).then(d => {
      rvHost = d.host; const past = d.radar.past; rvPath = past[past.length - 1].path; add();
    }).catch(() => {});
  });

  cbTemp.addEventListener('change', () => {
    if (!cbTemp.checked) {
      if (tempLayer) map.removeLayer(tempLayer);
      tempLayer = null; tempLegend.hidden = true; return;
    }
    tempLayer = L.tileLayer(
      `https://tile.openweathermap.org/map/temp_new/{z}/{x}/{y}.png?appid=${window.WC.owmKey}`,
      { opacity: 0.55, zIndex: 200, attribution: '© OpenWeatherMap' }).addTo(map);
    renderTempLegend();
    tempLegend.hidden = false;
  });

  cbIsobars.addEventListener('change', () => {
    if (!cbIsobars.checked) { if (isobarLayer) map.removeLayer(isobarLayer); isobarLayer = null; return; }
    isobarLayer = L.tileLayer(
      `https://tile.openweathermap.org/map/pressure_new/{z}/{x}/{y}.png?appid=${window.WC.owmKey}`,
      { opacity: 0.7, zIndex: 250, attribution: '© OpenWeatherMap' }).addTo(map);
  });

  cbAdvis.addEventListener('change', () => {
    if (!cbAdvis.checked) { if (advisLayer) map.removeLayer(advisLayer); advisLayer = null; return; }
    const add = () => {
      advisLayer = L.geoJSON(advisData, {
        style: f => ({ color: f.properties.color, weight: 2, fillColor: f.properties.color, fillOpacity: 0.25 }),
        pointToLayer: (f, ll) => L.circleMarker(ll,
          { radius: 9, color: '#fff', weight: 2, fillColor: f.properties.color, fillOpacity: 0.9 }),
        onEachFeature: (f, layer) => layer.bindPopup(advisPopup(f.properties)),
      }).addTo(map);
      const n = (advisData.features || []).length;
      hint.textContent = n ? `${n} active advisory${n > 1 ? 'ies' : ''} near venues` : 'No active advisories near venues';
    };
    if (advisData) return add();
    fetch(window.WC.alertsUrl).then(r => r.json()).then(d => { advisData = d; add(); }).catch(() => {});
  });

  function advisPopup(p) {
    const exp = p.expires ? `<div class="adv-exp">Until ${WCTime.datetime(p.expires)}</div>` : '';
    const desc = p.description ? `<div class="adv-desc">${p.description.replace(/\n/g, ' ')}</div>` : '';
    return `<div class="adv"><span class="adv-sev" style="background:${p.color}">${p.severity}</span>` +
      `<strong>${p.event || 'Advisory'}</strong><div class="adv-head">${p.headline || ''}</div>` +
      exp + desc + `<div class="adv-src">${p.country} · near ${p.venue}</div></div>`;
  }

  // ── controls ────────────────────────────────────────────────────────────────
  function go(delta) { idx = Math.max(0, Math.min(days.length - 1, idx + delta)); renderDay(); }
  document.getElementById('prevDay').addEventListener('click', () => go(-1));
  document.getElementById('nextDay').addEventListener('click', () => go(1));
  slider.addEventListener('input', () => { idx = +slider.value; renderDay(); });

  // ── boot ────────────────────────────────────────────────────────────────────
  Promise.all([
    fetch(window.WC.venuesUrl).then(r => r.json()),
    fetch(window.WC.daysUrl).then(r => r.json())
  ]).then(([vs, ds]) => {
    vs.forEach(v => venues[v.ground] = v);
    days = ds;
    slider.max = Math.max(0, days.length - 1);
    let start = days.findIndex(d => d.date >= TODAY);
    if (start < 0) start = days.length - 1;
    idx = start;
    renderDay();
  });
})();
