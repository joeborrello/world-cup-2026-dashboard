/* Golden Boot tracker — client-side re-sort between the current race (goals
 * scored) and the projected finish (goals + projected additional). The table is
 * fully rendered server-side, so the page works with JS disabled; this only
 * reorders the existing rows and re-numbers them. No network calls. */
(function () {
  'use strict';

  var table = document.getElementById('gbTable');
  if (!table) return;
  var tbody = table.tBodies[0];
  var tabs = document.querySelectorAll('.gb-tab');
  if (!tbody || !tabs.length) return;

  var num = function (el, attr) { return parseFloat(el.getAttribute(attr)) || 0; };

  function sortBy(mode) {
    var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr.gb-row'));
    var key = mode === 'proj' ? 'data-proj' : 'data-goals';
    rows.sort(function (a, b) {
      var d = num(b, key) - num(a, key);
      if (d !== 0) return d;
      // stable, sensible tiebreak: the other metric, then player name
      var alt = mode === 'proj' ? 'data-goals' : 'data-proj';
      var d2 = num(b, alt) - num(a, alt);
      if (d2 !== 0) return d2;
      return (a.getAttribute('data-player') || '').localeCompare(
        b.getAttribute('data-player') || '');
    });

    // re-attach in new order and re-rank (ties on the active metric share a rank)
    var prev = null, rank = 0, i = 0;
    rows.forEach(function (row) {
      tbody.appendChild(row);
      i += 1;
      var v = num(row, key);
      if (v !== prev) { rank = i; prev = v; }
      var posCell = row.querySelector('.pos');
      if (posCell) posCell.textContent = rank;
      row.classList.toggle('leader', rank === 1);
    });
    table.classList.toggle('by-proj', mode === 'proj');
  }

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      sortBy(tab.getAttribute('data-sort'));
    });
  });
})();
