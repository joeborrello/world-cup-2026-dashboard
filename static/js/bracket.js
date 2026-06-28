/* Graphical bracket: draws SVG connector lines between match boxes (and from the
 * group rail into the Round of 32) and provides zoom + drag-to-pan.
 *
 * Link geometry is computed in the inner element's UNSCALED coordinate space
 * (offsetLeft/offsetTop), so it only needs to be recomputed on layout changes —
 * the CSS transform that zooms the inner element scales the SVG along with it. */
(function () {
  const viewport = document.getElementById('bviewport');
  const inner = document.getElementById('binner');
  const svg = document.getElementById('blinks');
  const SVGNS = 'http://www.w3.org/2000/svg';

  function pos(el) {
    // accumulate offsets up the offsetParent chain to the inner container, so the
    // result is in inner-local coordinates whether or not columns are positioned
    let x = 0, y = 0, node = el;
    while (node && node !== inner) { x += node.offsetLeft; y += node.offsetTop; node = node.offsetParent; }
    return { left: x, right: x + el.offsetWidth, midY: y + el.offsetHeight / 2,
             cx: x + el.offsetWidth / 2 };
  }

  // connect the facing edges of two boxes, so links flow inward from both halves
  function connect(a, b, cls) {
    const x1 = a.cx < b.cx ? a.right : a.left;
    const x2 = a.cx < b.cx ? b.left : b.right;
    const midX = x1 + (x2 - x1) / 2;
    const p = document.createElementNS(SVGNS, 'path');
    p.setAttribute('d', `M ${x1} ${a.midY} H ${midX} V ${b.midY} H ${x2}`);
    p.setAttribute('class', cls);
    svg.appendChild(p);
  }

  function drawLinks() {
    // size the SVG to the full (unscaled) content box
    const w = inner.scrollWidth, h = inner.scrollHeight;
    svg.setAttribute('width', w);
    svg.setAttribute('height', h);
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    inner.querySelectorAll('.bmatch').forEach(box => {
      const tgt = pos(box);
      const side = box.dataset.side || 'l';
      // links from previous-round matches (W##/L##)
      (box.dataset.src || '').split(',').filter(Boolean).forEach(num => {
        const src = document.getElementById('m' + num);
        if (!src) return;
        connect(pos(src), tgt, box.dataset.loss ? 'lnk loss' : 'lnk win');
      });
      // links from the same-side group rail into the Round of 32
      (box.dataset.groups || '').split(',').filter(Boolean).forEach(letter => {
        const grp = document.getElementById('grp-' + side + '-' + letter);
        if (!grp) return;
        connect(pos(grp), tgt, 'lnk feed');
      });
    });
  }

  // ── zoom + pan ────────────────────────────────────────────────────────────
  let scale = 0.5, tx = 0, ty = 0;
  const range = document.getElementById('zoomRange');

  function apply() {
    inner.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    range.value = Math.round(scale * 100);
  }
  function clampScale(s) { return Math.max(0.3, Math.min(1.4, s)); }

  function setScale(s, cx, cy) {
    // keep the point under (cx,cy) — viewport coords — roughly fixed while zooming
    const rect = viewport.getBoundingClientRect();
    const px = (cx - rect.left - tx) / scale;
    const py = (cy - rect.top - ty) / scale;
    scale = clampScale(s);
    tx = cx - rect.left - px * scale;
    ty = cy - rect.top - py * scale;
    apply();
  }

  function fit() {
    const pad = 8;
    const sw = (viewport.clientWidth - pad * 2) / inner.scrollWidth;
    const sh = (viewport.clientHeight - pad * 2) / inner.scrollHeight;
    scale = clampScale(Math.min(sw, sh));
    // center horizontally within the viewport
    tx = Math.max(pad, (viewport.clientWidth - inner.scrollWidth * scale) / 2);
    ty = pad;
    apply();
  }

  document.getElementById('zoomIn').addEventListener('click',
    () => setScale(scale + 0.15, viewport.clientWidth / 2 + viewport.getBoundingClientRect().left,
                   viewport.clientHeight / 2 + viewport.getBoundingClientRect().top));
  document.getElementById('zoomOut').addEventListener('click',
    () => setScale(scale - 0.15, viewport.clientWidth / 2 + viewport.getBoundingClientRect().left,
                   viewport.clientHeight / 2 + viewport.getBoundingClientRect().top));
  document.getElementById('zoomFit').addEventListener('click', fit);
  range.addEventListener('input', () => {
    const r = viewport.getBoundingClientRect();
    setScale(+range.value / 100, r.left + viewport.clientWidth / 2, r.top + viewport.clientHeight / 2);
  });

  viewport.addEventListener('wheel', e => {
    if (e.ctrlKey) return;            // let pinch-zoom gestures through
    e.preventDefault();
    setScale(scale + (e.deltaY < 0 ? 0.08 : -0.08), e.clientX, e.clientY);
  }, { passive: false });

  // drag to pan
  let dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
  viewport.addEventListener('pointerdown', e => {
    if (e.target.closest('a, button, input')) return;
    // while projecting, a press on a projected box is a pick, not a pan — so the
    // whole box stays a reliable click target (pan elsewhere: rails, headers, gaps)
    if (projected && e.target.closest('.bmatch.has-pred')) return;
    dragging = true; sx = e.clientX; sy = e.clientY; ox = tx; oy = ty;
    viewport.setPointerCapture(e.pointerId);
    viewport.classList.add('grabbing');
  });
  viewport.addEventListener('pointermove', e => {
    if (!dragging) return;
    tx = ox + (e.clientX - sx); ty = oy + (e.clientY - sy);
    apply();
  });
  function endDrag() { dragging = false; viewport.classList.remove('grabbing'); }
  viewport.addEventListener('pointerup', endDrag);
  viewport.addEventListener('pointercancel', endDrag);

  // ── projected bracket overlay + interactive manipulation ────────────────────
  // In "Projected" mode the user can click a projected team to FORCE it to win
  // (advance) that match; we send those picks as overrides so the engine
  // re-resolves every downstream slot around them. `overrides` is {matchNum: team}.
  const predToggle = document.getElementById('predToggle');
  const depthSel = document.getElementById('depthSel');
  const resetBtn = document.getElementById('resetPicks');
  const pickHint = document.getElementById('pickHint');
  const pickStatus = document.getElementById('pickStatus');
  // a floating copy of the status pinned inside the viewport, so the result of a
  // click is visible right where the user is looking even when the page-top
  // status line is scrolled out of view (the "I clicked and nothing happened" report)
  const pickToast = document.getElementById('pickToast');
  let projected = false;
  let overrides = {};
  let toastTimer;

  // surface what the projection is doing so a failed/empty fetch is never silent
  function setStatus(msg, kind) {
    if (pickStatus) {
      pickStatus.textContent = msg || '';
      pickStatus.hidden = !msg;
      pickStatus.className = 'pick-status' + (kind ? ' ' + kind : '');
    }
    if (pickToast) {
      pickToast.textContent = msg || '';
      pickToast.hidden = !msg;
      pickToast.className = 'pick-toast' + (kind ? ' ' + kind : '');
      // let "applied" confirmations linger then fade; keep loading/errors up
      clearTimeout(toastTimer);
      if (msg && kind === 'ok') {
        toastTimer = setTimeout(() => { pickToast.hidden = true; }, 6000);
      }
    }
  }

  // snapshot the team currently shown in every projected slot, keyed by
  // match-number + side, so a re-projection can tell which slots actually moved
  function snapshotSlots() {
    const snap = {};
    inner.querySelectorAll('.bmatch').forEach(box => {
      box.querySelectorAll('.bm-side').forEach((side, i) => {
        if (side.dataset.team) snap[box.id + ':' + i] = side.dataset.team;
      });
    });
    return snap;
  }

  function clearProjection() {
    inner.querySelectorAll('.bm-side.predicted').forEach(side => {
      if (side.dataset.orig !== undefined) side.innerHTML = side.dataset.orig;
      side.classList.remove('predicted', 'locked');
      delete side.dataset.team;
      delete side.dataset.match;
    });
    inner.querySelectorAll('.bmatch.has-pred, .bmatch.forced')
      .forEach(b => b.classList.remove('has-pred', 'forced'));
  }

  // briefly flash a set of boxes so a re-projection is never silent — the user
  // sees exactly which slots their pick moved (plus the box they clicked)
  function flashBoxes(ids) {
    ids.forEach(id => {
      const box = document.getElementById(id);
      if (!box) return;
      box.classList.remove('flash');
      void box.offsetWidth;          // restart the CSS animation
      box.classList.add('flash');
    });
  }

  function updateResetBtn() {
    if (!resetBtn) return;
    const n = Object.keys(overrides).length;
    resetBtn.disabled = !projected || n === 0;
    resetBtn.textContent = n ? `Reset picks (${n})` : 'Reset picks';
  }

  function applyProjection(clickedNum) {
    if (!(window.WC && window.WC.bracketPredUrl)) return;
    const params = new URLSearchParams({ depth: depthSel.value });
    if (Object.keys(overrides).length) params.set('overrides', JSON.stringify(overrides));
    setStatus('Projecting the rest of the bracket…', 'loading');
    const prev = snapshotSlots();         // remember teams before re-projecting
    fetch(window.WC.bracketPredUrl + '?' + params.toString())
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(d => {
        // reconcile to what the engine actually applied (drops stale/invalid picks)
        overrides = Object.assign({}, d.overrides || {});
        clearProjection();
        let filled = 0;
        const moved = new Set();          // boxes whose projected team changed
        Object.entries(d.slots).forEach(([num, e]) => {
          const box = document.getElementById('m' + num);
          if (!box) return;
          const sides = box.querySelectorAll('.bm-side');
          [['team1', sides[0], 0], ['team2', sides[1], 1]].forEach(([k, side, i]) => {
            const slot = e[k];
            if (!slot || !side || !side.classList.contains('tbd')) return; // keep real teams
            if (side.dataset.orig === undefined) side.dataset.orig = side.innerHTML;
            side.classList.add('predicted');
            side.dataset.team = slot.team;
            side.dataset.match = num;
            const locked = overrides[num] === slot.team;
            side.classList.toggle('locked', locked);
            // affordance: every projected team is clickable to steer the bracket
            side.title = locked
              ? `${slot.team} is your pick to advance — click to undo`
              : `Click to make ${slot.team} advance from match #${num}`;
            const conf = Math.round(slot.conf * 100);
            const flag = slot.code
              ? `<img class="flag-img" src="https://flagcdn.com/${slot.code}.svg" width="22" height="16"> ` : '';
            side.innerHTML = `${flag}<span class="name">${slot.team}</span>` +
              `<span class="conf" title="model confidence">${conf}%</span>` +
              (locked ? '<span class="lock" title="your pick to advance">✓</span>' : '');
            // a projected team that differs from what was here before has "moved"
            if (prev['m' + num + ':' + i] && prev['m' + num + ':' + i] !== slot.team) {
              moved.add('m' + num);
            }
            filled++;
          });
          box.classList.add('has-pred');
          if (overrides[num]) box.classList.add('forced');   // box-level pick marker
        });
        updateResetBtn();
        // always flash the box the user clicked (so a pick is never silent, even
        // when forcing the already-favoured team leaves the bracket unchanged)
        // plus every downstream box whose projected team actually moved.
        const flash = new Set(moved);
        if (clickedNum) flash.add('m' + clickedNum);
        flashBoxes(flash);
        const n = Object.keys(overrides).length;
        if (!filled) {
          setStatus('No projected slots to fill — every knockout team here is already decided.', 'warn');
        } else if (clickedNum && moved.size) {
          setStatus(`Pick applied — ${moved.size} downstream slot${moved.size > 1 ? 's' : ''} ` +
                    `updated. ${n} forced pick${n > 1 ? 's' : ''} active.`, 'ok');
        } else if (clickedNum) {
          setStatus(`Pick locked in. (It matched the model's projection, so nothing ` +
                    `downstream changed.) ${n} forced pick${n > 1 ? 's' : ''} active.`, 'ok');
        } else if (n) {
          setStatus(`Showing your what-if with ${n} forced pick${n > 1 ? 's' : ''} — ` +
                    `${filled} projected slots re-resolved around ${n > 1 ? 'them' : 'it'}.`, 'ok');
        } else {
          setStatus(`${filled} slots projected from the model. Click any italic team to force it through.`, 'ok');
        }
      })
      .catch(err => {                       // never fail silently — tell the user
        setStatus('Could not load the projection (' + err.message + '). Please retry.', 'error');
      });
  }

  // Resolve which projected team a click is aiming at. We accept a click anywhere
  // on a projected match box — not just the thin team row — and map it to the
  // nearer of the box's two projected teams by vertical position. At the default
  // "Fit" zoom the rows are only ~10px tall, so demanding a pixel-perfect hit on
  // the text made the feature feel dead ("I click a country and nothing happens").
  function pickTarget(e) {
    const exact = e.target.closest('.bm-side.predicted');
    if (exact && inner.contains(exact)) return exact;
    const box = e.target.closest('.bmatch.has-pred');
    if (!box || !inner.contains(box)) return null;
    const sides = Array.from(box.querySelectorAll('.bm-side.predicted[data-team]'));
    if (sides.length <= 1) return sides[0] || null;
    // choose the projected side whose vertical centre is closest to the click
    let best = null, bestD = Infinity;
    sides.forEach(s => {
      const r = s.getBoundingClientRect();
      const d = Math.abs(e.clientY - (r.top + r.height / 2));
      if (d < bestD) { bestD = d; best = s; }
    });
    return best;
  }

  // click a projected team (or anywhere on its box) to force/unforce it as winner
  inner.addEventListener('click', e => {
    if (!projected) return;
    const side = pickTarget(e);
    if (!side) return;
    const num = side.dataset.match, team = side.dataset.team;
    if (!num || !team) return;
    if (overrides[num] === team) delete overrides[num];   // toggle the pick off
    else overrides[num] = team;                           // set / switch the pick
    applyProjection(num);
  });

  if (resetBtn) {
    resetBtn.addEventListener('click', () => { overrides = {}; applyProjection(); });
  }

  if (predToggle) {
    predToggle.querySelectorAll('button').forEach(b => {
      b.addEventListener('click', () => {
        predToggle.querySelectorAll('button').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        projected = b.dataset.pred === '1';
        depthSel.disabled = !projected;
        inner.classList.toggle('projecting', projected);
        if (pickHint) pickHint.hidden = !projected;
        if (projected) { applyProjection(); } else { clearProjection(); setStatus(''); }
        updateResetBtn();
      });
    });
    depthSel.addEventListener('change', () => { if (projected) applyProjection(); });
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  function boot() { drawLinks(); fit(); }
  if (document.readyState === 'complete') boot();
  else window.addEventListener('load', boot);
  let rt;
  window.addEventListener('resize', () => {
    clearTimeout(rt); rt = setTimeout(() => { drawLinks(); }, 150);
  });
})();
