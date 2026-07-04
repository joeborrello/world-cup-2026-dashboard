/* What-if page: send a free-form scenario question to /api/scenarios and draw
 * the returned scenario tree as a MiroFish-style branch map (root question at
 * the top, branches fanning out below, CSS connector lines). */
(function () {
  const out = document.getElementById('wiOut');
  const budgetEl = document.getElementById('wiBudget');
  const qEl = document.getElementById('wiQuestion');
  const goBtn = document.getElementById('wiGo');

  // Everything rendered here is either user-typed or LLM-generated — escape it all.
  const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  function renderBudget(b) {
    if (!b || b.enabled === false) { budgetEl.textContent = ''; return; }
    const over = b.day_used >= b.day_cap || b.month_spent >= b.month_cap;
    const tip = `Shared with the pundit panel: stops at ${b.day_cap}/day · ` +
      `$${b.month_cap.toFixed(2)}/mo. Repeating a question is free (cached).`;
    budgetEl.innerHTML = `<span class="pb${over ? ' over' : ''}" title="${esc(tip)}">` +
      `${b.day_used}/${b.day_cap} today · $${b.month_spent.toFixed(2)} / ` +
      `$${b.month_cap.toFixed(2)} this month</span>`;
  }
  fetch(window.WC.budgetUrl).then(r => r.json()).then(renderBudget).catch(() => {});

  // One node card: title, probability badge (bar width = probability), summary, impact.
  function nodeCard(n) {
    const p = (n.probability == null) ? '' :
      `<span class="wi-prob"><span class="wi-bar">` +
      `<i style="width:${Math.round(n.probability * 100)}%"></i></span>` +
      `<b>${(n.probability * 100).toFixed(0)}%</b></span>`;
    return `<div class="wi-node">` +
      `<div class="wi-node-head">${esc(n.title)}${p}</div>` +
      (n.summary ? `<div class="wi-node-sum">${esc(n.summary)}</div>` : '') +
      (n.impact ? `<div class="wi-node-impact">${esc(n.impact)}</div>` : '') +
      `</div>`;
  }

  function branch(n) {
    const kids = (n.children && n.children.length)
      ? `<ul>${n.children.map(branch).join('')}</ul>` : '';
    return `<li>${nodeCard(n)}${kids}</li>`;
  }

  function renderMap(d) {
    const cached = d.cached ? ' <span class="pcache">cached</span>' : '';
    const tree = (d.scenarios && d.scenarios.length)
      ? `<div class="wi-map"><ul class="wi-tree"><li>` +
        `<div class="wi-node wi-root"><div class="wi-node-head">${esc(d.question)}</div>` +
        (d.reading ? `<div class="wi-node-sum">${esc(d.reading)}</div>` : '') + `</div>` +
        `<ul>${d.scenarios.map(branch).join('')}</ul></li></ul></div>`
      : (d.reading ? `<p class="wi-reading">${esc(d.reading)}</p>` : '');
    out.innerHTML =
      `<div class="pundit-title">Scenario map${cached}</div>` + tree +
      (d.bottom_line ? `<div class="pconsensus"><b>Bottom line:</b> ${esc(d.bottom_line)}</div>` : '');
  }

  function ask() {
    const question = qEl.value.trim();
    if (!question) { qEl.focus(); return; }
    goBtn.disabled = true;
    out.innerHTML = '<p class="subtle pundit-wait">Mapping the branches…</p>';
    fetch(window.WC.askUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    })
      .then(r => r.json())
      .then(d => {
        if (d.budget) renderBudget(Object.assign({ enabled: true }, d.budget));
        if (!d.available) {
          const cls = d.limited ? 'pundit-na limited' : 'pundit-na';
          out.innerHTML = `<p class="${cls}">${esc(d.message || 'Scenario mapper unavailable.')}</p>`;
          return;
        }
        renderMap(d);
      })
      .catch(() => { out.innerHTML = '<p class="pundit-na">Could not reach the scenario mapper.</p>'; })
      .finally(() => { goBtn.disabled = false; });
  }

  goBtn.addEventListener('click', ask);
  qEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
  });
  document.getElementById('wiExamples').addEventListener('click', e => {
    const chip = e.target.closest('.wi-chip');
    if (chip) { qEl.value = chip.textContent; ask(); }
  });
})();
