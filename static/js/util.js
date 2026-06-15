/* Shared helpers — render every match time in the VIEWER's device timezone.
 * Times are stored server-side as UTC ISO strings (utc_datetime); the browser
 * converts them to whatever timezone the device is in. */
window.WCTime = (function () {
  function tzAbbr() {
    try {
      const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' })
        .formatToParts(new Date());
      const p = parts.find(x => x.type === 'timeZoneName');
      return p ? p.value : '';
    } catch (e) { return ''; }
  }
  const TZ = tzAbbr();

  function _d(utc) { const d = new Date(utc); return isNaN(d) ? null : d; }

  function time(utc) {
    const d = _d(utc); if (!d) return 'TBD';
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  function date(utc) {
    const d = _d(utc); if (!d) return '';
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }
  function datetime(utc) {
    const d = _d(utc); if (!d) return 'TBD';
    return d.toLocaleString(undefined,
      { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  // Rewrite any element carrying data-utc into device-local text.
  //   data-fmt = time | date | datetime   (default: time)
  //   data-tz  present  -> append the device tz abbreviation
  function process(root) {
    (root || document).querySelectorAll('[data-utc]').forEach(el => {
      const u = el.getAttribute('data-utc');
      if (!u) return;
      const f = el.getAttribute('data-fmt') || 'time';
      let txt = f === 'datetime' ? datetime(u) : f === 'date' ? date(u) : time(u);
      if (el.hasAttribute('data-tz') && txt !== 'TBD' && txt !== '') txt += ' ' + TZ;
      el.textContent = txt;
    });
  }

  document.addEventListener('DOMContentLoaded', () => process(document));
  return { tz: TZ, time, date, datetime, process };
})();

// flag image (matches the server-side flags.flag() output) for JS-built markup
window.wcFlag = function (code, name) {
  if (!code) return '';
  const n = (name || '').replace(/"/g, '&quot;');
  return `<img class="flag-img" src="https://flagcdn.com/${code}.svg" alt="${n}" ` +
         `title="${n}" loading="lazy" width="22" height="16"> `;
};
