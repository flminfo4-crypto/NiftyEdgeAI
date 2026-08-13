/**
 * Wires strategy-lab.html to GET /backtests/lab — the all-strategy sweep.
 *
 * The endpoint runs every registered strategy AND every parameterised
 * template over the same candles, split into an in-sample period used for
 * ranking and an untouched out-of-sample period. This page's job is to make
 * the in/out gap impossible to miss, because that gap is the finding: a
 * strategy that only works in-sample is overfit, and a table that shows only
 * headline returns would hide exactly that.
 *
 * Click-triggered, never polled. One sweep runs ~30 backtests over years of
 * candles server-side; it is cached ~30min there, but putting it on a timer
 * would be pure load. Only the header ticker refreshes.
 */
(function () {
  var NE = window.NE;
  var state = { data: null };

  // -- ticker/footer refresh, every 2s -------------------------------------

  function loadTicker() {
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX")
      .then(function (quotes) {
        NE.applyHeaderTicker(quotes);
        NE.applyFooterTicker(quotes);
        NE.markStatus(true);
        NE.stampRefresh();
      })
      .catch(function () { NE.markStatus(false); });
  }
  loadTicker();
  setInterval(loadTicker, 2000);

  function el(id) { return document.getElementById(id); }

  function showError(msg) {
    var e = document.querySelector('[data-live="lab-error"]');
    if (!e) return;
    if (msg) { e.textContent = msg; e.style.display = ""; } else { e.style.display = "none"; }
  }

  function setLoading(loading) {
    document.querySelectorAll('[data-live="lab-run-btn"]').forEach(function (b) {
      b.disabled = loading;
      b.textContent = loading ? "Running…" : "Run Sweep";
    });
  }

  // -- formatting ----------------------------------------------------------

  function pct(v, dp) {
    return v == null ? "—" : v.toFixed(dp == null ? 1 : dp) + "%";
  }

  function signedPct(v, dp) {
    if (v == null) return "—";
    return (v >= 0 ? "+" : "") + v.toFixed(dp == null ? 2 : dp) + "%";
  }

  function cls(v, invert) {
    if (v == null || v === 0) return "";
    var good = invert ? v < 0 : v > 0;
    return good ? "text-green" : "text-red";
  }

  // -- tiles ---------------------------------------------------------------

  function tile(label, value, detail, klass) {
    return '<div class="card"><div class="stat-block"><div class="k">' + label + "</div>" +
      '<div class="v sm' + (klass ? " " + klass : "") + '">' + value + "</div>" +
      '<div class="d text-muted">' + (detail || "&nbsp;") + "</div></div></div>";
  }

  function renderTiles() {
    var host = document.querySelector('[data-live="lab-tiles"]');
    if (!host) return;
    var d = state.data;
    if (!d) { host.innerHTML = ""; return; }
    var rows = d.strategies;
    var traded = rows.filter(function (r) { return r.outSample.totalTrades > 0; });
    var best = rows[0];
    // "Held up" = scored at all out-of-sample AND didn't collapse relative to
    // in-sample. Both halves matter: a strategy can keep a positive score
    // while still losing most of its edge on unseen data.
    var heldUp = rows.filter(function (r) { return r.outScore > 0 && r.generalization > -5; });

    host.innerHTML =
      tile("Strategies Swept", String(rows.length),
        traded.length + " traded out-of-sample") +
      tile("Held Up Out-of-Sample", String(heldUp.length),
        heldUp.length + " of " + rows.length + " kept their score",
        heldUp.length ? "text-green" : "text-red") +
      tile("Best Out-of-Sample", best ? best.label.slice(0, 22) : "—",
        best ? "score " + best.outScore.toFixed(1) + " · gen " + best.generalization.toFixed(1) : "&nbsp;") +
      tile("Data Window", d.inSampleDays + " / " + d.outSampleDays,
        "in-sample / out-of-sample sessions");
  }

  // -- table ---------------------------------------------------------------

  var SORTS = {
    out_score: function (a, b) { return b.outScore - a.outScore; },
    generalization: function (a, b) { return b.generalization - a.generalization; },
    out_weekly: function (a, b) { return (b.outSample.medianWeeklyPct || -99) - (a.outSample.medianWeeklyPct || -99); },
    out_dd: function (a, b) { return (b.outSample.maxDrawdownPct || -999) - (a.outSample.maxDrawdownPct || -999); },
  };

  function renderTable() {
    var table = el("lab-table");
    if (!table) return;
    var d = state.data;
    if (!d) {
      table.innerHTML = '<tbody><tr><td style="color:var(--text-muted);">Click "Run Sweep" to backtest every strategy.</td></tr></tbody>';
      return;
    }
    var rows = d.strategies.slice();
    if (el("lab-only-traded").checked) {
      rows = rows.filter(function (r) { return r.outSample.totalTrades > 0 || r.inSample.totalTrades > 0; });
    }
    rows.sort(SORTS[el("lab-sort").value] || SORTS.out_score);

    if (!rows.length) {
      table.innerHTML = '<tbody><tr><td style="color:var(--text-muted);">No strategy produced any trades on this data.</td></tr></tbody>';
      return;
    }

    var head = "<thead><tr>" +
      "<th>Strategy</th><th>Trades</th><th>Win %</th>" +
      "<th>IS median wk</th><th>IS score</th>" +
      "<th>OOS median wk</th><th>OOS wk &ge;1%</th><th>OOS max DD</th><th>OOS score</th>" +
      "<th>Generalization</th></tr></thead>";

    var body = rows.map(function (r) {
      var i = r.inSample, o = r.outSample;
      var badge = r.source === "template"
        ? ' <span class="badge badge-blue">template</span>' : "";
      // The generalization gap is the point of the page, so it gets the only
      // strong colour treatment in the row.
      var genCls = r.generalization >= 0 ? "text-green" : (r.generalization < -10 ? "text-red" : "text-amber");
      return "<tr>" +
        "<td>" + r.label + badge + "</td>" +
        "<td>" + i.totalTrades + " / " + o.totalTrades + "</td>" +
        "<td>" + pct(o.winRatePct, 1) + "</td>" +
        '<td class="' + cls(i.medianWeeklyPct) + '">' + signedPct(i.medianWeeklyPct) + "</td>" +
        "<td>" + r.inScore.toFixed(1) + "</td>" +
        '<td class="' + cls(o.medianWeeklyPct) + '">' + signedPct(o.medianWeeklyPct) + "</td>" +
        "<td>" + pct(o.pctWeeksHit1pct, 1) + "</td>" +
        '<td class="' + cls(o.maxDrawdownPct, true) + '">' + pct(o.maxDrawdownPct, 1) + "</td>" +
        "<td><b>" + r.outScore.toFixed(1) + "</b></td>" +
        '<td class="' + genCls + '"><b>' + (r.generalization >= 0 ? "+" : "") + r.generalization.toFixed(1) + "</b></td>" +
        "</tr>";
    }).join("");

    table.innerHTML = head + "<tbody>" + body + "</tbody>";
  }

  function render() {
    renderTiles();
    renderTable();
  }

  // -- load ----------------------------------------------------------------

  function runSweep() {
    var q = "?instrument=" + el("lab-instrument").value +
      "&years=" + el("lab-years").value +
      "&capital=" + (parseFloat(el("lab-capital").value) || 100000) +
      "&lots=" + (parseInt(el("lab-lots").value, 10) || 1);
    showError(null);
    setLoading(true);
    // Long timeout on purpose: ~30 strategies x years of candles, twice
    // (in-sample and out-of-sample), is genuinely slow on a cold cache.
    NE.fetchJSONLong("/backtests/lab" + q, 300000)
      .then(function (d) {
        state.data = d;
        NE.setText("lab-meta",
          d.underlying + " · " + d.dataFrom + " → " + d.dataTo +
          " · split " + d.splitDate +
          " · " + d.strategies.length + " strategies · capital " + NE.fmtINR(d.startingCapital) +
          " · " + d.positionSizeLots + " lot(s)");
        var note = document.querySelector('[data-live="lab-note"]');
        if (note) {
          if (d.note) { note.textContent = d.note; note.style.display = ""; }
          else { note.style.display = "none"; }
        }
        render();
        if (!d.strategies.length) showError("The sweep returned no strategies — see the note above.");
      })
      .catch(function (err) { showError("Sweep failed: " + err.message); })
      .then(function () { setLoading(false); });
  }

  // -- wiring --------------------------------------------------------------

  render();
  document.querySelectorAll('[data-live="lab-run-btn"]').forEach(function (b) {
    b.addEventListener("click", runSweep);
  });
  el("lab-sort").addEventListener("change", render);
  el("lab-only-traded").addEventListener("change", render);
})();
