/* Device-local "Today" grouping. The page is rendered with the whole schedule
 * (each match card carries its UTC kickoff via .time[data-utc]); here we group
 * the cards by the VIEWER's local calendar day and show the one that is "today"
 * on their device — falling back to the next upcoming match day, then the most
 * recent past one. So a late West-Coast game stays under "Today" for a US viewer
 * while showing as the next day for someone in, say, London. */
(function () {
  const grid = document.getElementById('matchGrid');
  if (!grid) return;

  const pad = n => String(n).padStart(2, '0');
  const keyOf = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const human = key => {
    const [y, m, d] = key.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined,
      { weekday: 'short', month: 'short', day: 'numeric' });
  };

  const byDay = {};
  grid.querySelectorAll('.match').forEach(card => {
    const t = card.querySelector('.time');
    const utc = t && t.getAttribute('data-utc');
    const d = utc ? new Date(utc) : null;
    if (!d || isNaN(d)) { card.hidden = true; return; }
    const k = keyOf(d);
    card.dataset.day = k;
    (byDay[k] = byDay[k] || []).push(card);
  });

  const days = Object.keys(byDay).sort();
  const todayKey = keyOf(new Date());
  const target = byDay[todayKey] ? todayKey
    : days.find(k => k > todayKey) || days.filter(k => k < todayKey).pop() || null;

  const title = document.getElementById('todayTitle');
  const sub = document.getElementById('todaySub');
  const nfin = window.WC_NFIN;
  const played = nfin != null ? `${nfin} of 104 matches played so far` : '';

  if (!target) {
    document.getElementById('noMatches').hidden = false;
    if (title) title.textContent = 'No matches';
    if (sub) sub.textContent = played;
    return;
  }

  grid.querySelectorAll('.match').forEach(card => {
    card.hidden = card.dataset.day !== target;
  });

  if (target === todayKey) {
    if (title) title.textContent = "Today's matches";
    if (sub) sub.textContent = `${human(target)} · ${played}`;
  } else {
    const tense = target > todayKey ? 'No games today — showing' : 'Showing';
    if (title) title.textContent = 'Next match day';
    if (sub) sub.textContent = `${tense} ${human(target)}.`;
  }
  grid.hidden = false;
})();
