/* Site-wide live ticker. Polls /api/live for in-play matches and shows their
 * live score in a ticker at the top of every page; also enriches any on-page
 * match cards (data-match) with the live score.
 *
 * The minute of play is resolved server-side at the time of each check (JOE-17):
 * football-data's own minute when available, otherwise estimated from kickoff.
 * It is a snapshot — "as of the last check" — so we label it with the check time
 * rather than pretending to tick a live clock in the browser. PAUSED -> "HT". */
(function () {
  const url = window.WC_LIVE;
  const ticker = document.getElementById('liveTicker');
  if (!url || !ticker) return;

  let live = [];
  let checkedAt = null;

  // Minute-of-play label as of the most recent check. PAUSED already arrives as
  // "HT" from the server; otherwise show whatever snapshot minute it resolved.
  function minuteLabel(m) {
    if (m.state === 'paused') return 'HT';
    return m.minute || '';
  }

  // "as of HH:MM" tooltip so a stale snapshot minute can't be mistaken for a
  // live-ticking clock.
  function checkedTitle() {
    if (!checkedAt) return '';
    const d = new Date(checkedAt);
    if (isNaN(d)) return '';
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `Minute as of last check, ${hh}:${mm}`;
  }

  function flag(code, name) {
    if (!code) return '';
    const n = (name || '').replace(/"/g, '&quot;');
    return `<img class="flag-img" src="https://flagcdn.com/${code}.svg" alt="${n}" width="22" height="16"> `;
  }

  function render() {
    if (!live.length) { ticker.hidden = true; ticker.innerHTML = ''; updateCards(); return; }
    ticker.hidden = false;
    const title = checkedTitle();
    ticker.innerHTML = '<span class="lt-label"><span class="lt-dot"></span>LIVE</span>' +
      live.map(m => {
        const label = minuteLabel(m);
        return `<span class="lt-match"><span class="lt-teams">` +
          `${flag(m.team1_code, m.team1)}${m.team1} <b class="lt-score">${m.score1}–${m.score2}</b> ` +
          `${flag(m.team2_code, m.team2)}${m.team2}</span>` +
          (label ? `<span class="lt-min"${title ? ` title="${title}"` : ''}>${label}</span>` : '') +
          `<span class="lt-tag">${m.tag || ''}</span></span>`;
      }).join('');
    updateCards();
  }

  // reflect live state on any match cards present on the page (e.g. the Today page)
  function updateCards() {
    document.querySelectorAll('.match[data-match]').forEach(card => {
      card.classList.remove('live');
      const b = card.querySelector('.mc-live'); if (b) b.remove();
      const t = card.querySelector('.time'); if (t) t.style.display = '';
    });
    live.forEach(m => {
      const card = document.querySelector(`.match[data-match="${m.num}"]`);
      if (!card) return;
      card.classList.add('live');
      const s1 = card.querySelector('[data-score="1"]'), s2 = card.querySelector('[data-score="2"]');
      if (s1) s1.textContent = m.score1;
      if (s2) s2.textContent = m.score2;
      const t = card.querySelector('.time'); if (t) t.style.display = 'none';
      const meta = card.querySelector('.match-meta');
      if (meta) {
        const label = minuteLabel(m);
        const badge = document.createElement('span');
        badge.className = 'mc-live';
        const title = checkedTitle();
        if (title) badge.title = title;
        badge.innerHTML = `<span class="lt-dot"></span>LIVE${label ? ' ' + label : ''}`;
        meta.appendChild(badge);
      }
    });
  }

  function poll() {
    fetch(url).then(r => r.json()).then(d => {
      live = d.matches || [];
      checkedAt = d.checked_at || null;
      render();
    }).catch(() => {});
  }

  poll();
  setInterval(poll, 45000);                   // refresh scores / minute / HT state
})();
