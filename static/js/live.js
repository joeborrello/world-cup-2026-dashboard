/* Site-wide live ticker. Polls /api/live for in-play matches and shows their
 * live score in a ticker at the top of every page; also enriches any on-page
 * match cards (data-match) with the live score.
 *
 * The free football-data tier has no live minute, so we don't show one. The only
 * clock label is "HT", which comes straight from the API's PAUSED state. */
(function () {
  const url = window.WC_LIVE;
  const ticker = document.getElementById('liveTicker');
  if (!url || !ticker) return;

  let live = [];

  // only authoritative state labels — no estimated minute
  function stateLabel(state) { return state === 'paused' ? 'HT' : ''; }

  function flag(code, name) {
    if (!code) return '';
    const n = (name || '').replace(/"/g, '&quot;');
    return `<img class="flag-img" src="https://flagcdn.com/${code}.svg" alt="${n}" width="22" height="16"> `;
  }

  function render() {
    if (!live.length) { ticker.hidden = true; ticker.innerHTML = ''; updateCards(); return; }
    ticker.hidden = false;
    ticker.innerHTML = '<span class="lt-label"><span class="lt-dot"></span>LIVE</span>' +
      live.map(m => {
        const label = stateLabel(m.state);
        return `<span class="lt-match"><span class="lt-teams">` +
          `${flag(m.team1_code, m.team1)}${m.team1} <b class="lt-score">${m.score1}–${m.score2}</b> ` +
          `${flag(m.team2_code, m.team2)}${m.team2}</span>` +
          (label ? `<span class="lt-min">${label}</span>` : '') +
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
        const label = stateLabel(m.state);
        const badge = document.createElement('span');
        badge.className = 'mc-live';
        badge.innerHTML = `<span class="lt-dot"></span>LIVE${label ? ' ' + label : ''}`;
        meta.appendChild(badge);
      }
    });
  }

  function poll() {
    fetch(url).then(r => r.json()).then(d => { live = d.matches || []; render(); }).catch(() => {});
  }

  poll();
  setInterval(poll, 45000);                   // refresh scores / HT state
})();
