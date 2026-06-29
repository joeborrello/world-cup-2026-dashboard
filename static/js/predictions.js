/* Predictions page: render the Monte-Carlo odds (title + group qualification) and
 * drive the MiroFish-inspired AI pundit panel. */
(function () {
  const flag = (code, name) => code
    ? `<img class="flag-img" src="https://flagcdn.com/${code}.svg" alt="${name || ''}" width="22" height="16"> ` : '';
  // Odds are always shown to one decimal place of precision, matching the
  // GitHub Pages landing strip (both read the droplet's Monte-Carlo odds). See JOE-12.
  const pct = x => (x * 100).toFixed(1) + '%';

  fetch(window.WC.predUrl).then(r => r.json()).then(render);

  function render(d) {
    document.getElementById('predMeta').textContent =
      `${d.sims.toLocaleString()} simulations · ${d.n_finished} matches played so far`;

    const teams = Object.entries(d.teams).map(([t, v]) => ({ team: t, ...v }));

    // title odds (top 16 by champion %)
    const top = teams.slice().sort((a, b) => b.champion - a.champion).slice(0, 16);
    document.getElementById('titleTable').innerHTML =
      '<thead><tr><th>Team</th><th>Grp</th><th>Adv</th><th>R16</th><th>QF</th>' +
      '<th>SF</th><th>Final</th><th>Champion</th></tr></thead><tbody>' +
      top.map(v => `<tr><td class="ot-team">${flag(v.code, v.team)}${v.team}</td>` +
        `<td>${v.group}</td><td>${pct(v.advance)}</td><td>${pct(v.r16)}</td>` +
        `<td>${pct(v.qf)}</td><td>${pct(v.sf)}</td><td>${pct(v.final)}</td>` +
        `<td class="ot-win"><span class="ot-bar" style="width:${Math.round(v.champion * 100)}%"></span>` +
        `<span class="ot-val">${pct(v.champion)}</span></td></tr>`).join('') + '</tbody>';

    // group qualification cards
    const groups = {};
    teams.forEach(v => { (groups[v.group] = groups[v.group] || []).push(v); });
    document.getElementById('groupOdds').innerHTML = Object.keys(groups).sort().map(g => {
      const rows = groups[g].sort((a, b) => b.advance - a.advance).map(v =>
        `<div class="grow"><span class="gt">${flag(v.code, v.team)}${v.team}</span>` +
        `<span class="gbar"><i style="width:${Math.round(v.advance * 100)}%"></i></span>` +
        `<span class="gp">${pct(v.advance)}</span></div>`).join('');
      return `<div class="gcard"><h3>Group ${g}</h3>${rows}` +
        `<div class="gnote">% chance to reach the Round of 32</div></div>`;
    }).join('');

    renderForm(teams);

    // pundit scope options
    document.getElementById('punditScope').innerHTML =
      '<option value="knockout">Title race</option>' +
      Object.keys(groups).sort().map(g => `<option value="group:${g}">Group ${g}</option>`).join('');
  }

  // ── form tracker: biggest dynamic-Elo movers vs the pre-tournament prior ────
  function renderForm(teams) {
    const el = document.getElementById('formTrack');
    if (!el) return;
    const m = teams
      .map(v => ({ team: v.team, code: v.code, elo: v.elo, delta: (v.elo || 0) - (v.elo_prior || 0) }))
      .filter(v => Math.abs(v.delta) >= 1);
    if (!m.length) {
      el.innerHTML = '<p class="subtle">Ratings are still at their pre-tournament priors — ' +
        'check back once more matches are played.</p>';
      return;
    }
    const up = m.filter(v => v.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, 6);
    const down = m.filter(v => v.delta < 0).sort((a, b) => a.delta - b.delta).slice(0, 6);
    const row = v => `<div class="ft-row"><span class="ft-t">${flag(v.code, v.team)}${v.team}</span>` +
      `<span class="ft-d ${v.delta > 0 ? 'up' : 'down'}">${v.delta > 0 ? '▲' : '▼'} ` +
      `${Math.abs(Math.round(v.delta))}</span><span class="ft-e">${Math.round(v.elo)}</span></div>`;
    el.innerHTML =
      `<div class="ft-col"><h3>Rising</h3>${up.map(row).join('') || '<p class="subtle">—</p>'}</div>` +
      `<div class="ft-col"><h3>Falling</h3>${down.map(row).join('') || '<p class="subtle">—</p>'}</div>`;
  }

  // ── AI pundit panel ─────────────────────────────────────────────────────────
  const out = document.getElementById('punditOut');
  const budgetEl = document.getElementById('punditBudget');

  function renderBudget(b) {
    if (!b || b.enabled === false) { budgetEl.textContent = ''; return; }
    const overDay = b.day_used >= b.day_cap, overMonth = b.month_spent >= b.month_cap;
    const cls = (overDay || overMonth) ? ' over' : '';
    const tip = `Pundits stop at ${b.day_cap}/day · $${b.month_cap.toFixed(2)}/mo ` +
      `(${b.reserve_pct}% of the ${b.day_max}/day · $${b.month_budget.toFixed(2)}/mo total kept open). Cache hits are free.`;
    budgetEl.innerHTML = `<span class="pb${cls}" title="${tip}">` +
      `${b.day_used}/${b.day_cap} today · $${b.month_spent.toFixed(2)} / $${b.month_cap.toFixed(2)} this month ` +
      `· ${b.reserve_pct}% reserved</span>`;
  }
  fetch(window.WC.budgetUrl).then(r => r.json()).then(renderBudget).catch(() => {});

  document.getElementById('punditGo').addEventListener('click', () => {
    const scope = document.getElementById('punditScope').value;
    out.innerHTML = '<p class="subtle pundit-wait">The panel is deliberating…</p>';
    fetch(window.WC.punditUrl + '?scope=' + encodeURIComponent(scope))
      .then(r => r.json()).then(p => {
        if (p.budget) renderBudget(Object.assign({ enabled: true }, p.budget));
        if (!p.available) {
          const cls = p.limited ? 'pundit-na limited' : 'pundit-na';
          out.innerHTML = `<p class="${cls}">${p.message || 'Panel unavailable.'}</p>`;
          return;
        }
        const cards = (p.pundits || []).map(pu =>
          `<div class="pcard"><div class="pname">${pu.name}` +
          `${pu.lean ? ` · <span class="plean">${pu.lean}</span>` : ''}</div>` +
          `<div class="ptake">${pu.take || ''}</div></div>`).join('');
        out.innerHTML =
          `<div class="pundit-title">${p.title || ''}${p.cached ? ' <span class="pcache">cached</span>' : ''}</div>` +
          `<div class="pcards">${cards}</div>` +
          (p.consensus ? `<div class="pconsensus"><b>Consensus${p.lean ? ` — ${p.lean}` : ''}:</b> ${p.consensus}</div>` : '');
      })
      .catch(() => { out.innerHTML = '<p class="pundit-na">Could not reach the panel.</p>'; });
  });
})();
