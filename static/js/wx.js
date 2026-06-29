/* Shared kickoff-weather formatting for the maps.
 *
 * Payloads come from /api/weather (see weather.py). Each match maps to
 * {temp_f, precip_prob, precip_in, humidity, dewpoint_f, wind_mph, desc, emoji,
 *  kind, available}. `kind` is forecast | current | historical, so the UI can
 * label whether a reading is predicted or the actual observed conditions at
 * kickoff. The chosen temperature unit (°F/°C) is persisted per device and shared
 * across views via localStorage. */
window.WCWx = (function () {
  const KIND_LABEL = { forecast: 'Forecast', current: 'Today', historical: 'Actual' };
  let unit = localStorage.getItem('wcUnit') === 'C' ? 'C' : 'F';
  const listeners = [];

  function cvt(f) { return unit === 'C' ? (f - 32) * 5 / 9 : f; }
  function tShort(f) { return f == null ? '' : Math.round(cvt(f)) + '°'; }
  function tFull(f) { return f == null ? '' : Math.round(cvt(f)) + '°' + unit; }

  function setUnit(u) {
    u = u === 'C' ? 'C' : 'F';
    if (u === unit) return;
    unit = u;
    localStorage.setItem('wcUnit', unit);
    listeners.forEach(fn => { try { fn(unit); } catch (e) { /* ignore */ } });
  }
  function onChange(fn) { listeners.push(fn); }

  // compact pin chip: emoji + temperature (+ precip chance)
  function chip(w) {
    if (!w || w.available === false) return '';
    const p = w.precip_prob != null ? ` ${w.precip_prob}%` : '';
    return `<span class="wx">${w.emoji || ''}${tShort(w.temp_f)}${p}</span>`;
  }
  // full detail line for popups / list rows
  function line(w) {
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

  return {
    get unit() { return unit; },
    setUnit, onChange, chip, line, tShort, tFull, cvt, KIND_LABEL,
  };
})();
