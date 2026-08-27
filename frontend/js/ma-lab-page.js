/**
 * Wires ma-lab.html to POST /backtests/template — a moving-average crossover
 * run that returns every trade inline.
 *
 * The point of this page is the trade-by-trade grid, not the headline: when a
 * crossover system is judged on a summary alone it is impossible to see that
 * the return came from three trades and the other forty bled. So every row is
 * rendered, with a running equity column, and the win/loss counts are shown
 * next to the hit rate rather than instead of it.
 *
 * Click-triggered; only the header ticker polls.
 */
(function () {
  // Live-refresh cadence. Kept deliberately slow: every open tab is its own
  // polling stream against the broker's rate limit, and Dhan answers a hot
  // one with 429 plus a warning about blocking the account (see the cache
  // notes in backend/app/services/market_data.py).
  var REFRESH_MS = 30000;

  var NE = window.NE;
  var state = { data: null, filter: "all" };

  // Daily-bar engine: entries are taken at the session open and exits at a
  // session close, so these are the real boundaries the fill is modelled at
  // rather than invented clock times. See the note in the page footer.
  var SESSION_OPEN = "09:15";
  var SESSION_CLOSE = "15:30";

  // -- ticker/footer refresh, every REFRESH_MS ---------------------------

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
  setInterval(loadTicker, REFRESH_MS);

  function el(id) { return document.getElementById(id); }

  function showError(msg) {
    var e = document.querySelector('[data-live="ml-error"]');
    if (!e) return;
    if (msg) { e.textContent = msg; e.style.display = ""; } else { e.style.display = "none"; }
  }

  function setLoading(loading) {
    document.querySelectorAll('[data-live="ml-run-btn"]').forEach(function (b) {
      b.disabled = loading;
      b.textContent = loading ? "Running…" : "Run";
    });
  }

  // -- inputs ---------------------------------------------------------------

  /** Parses the single moving-average box. Accepts "15,20", "15 20", "15/20"
   *  or "15-20" — one field is less to fill in than two, but it has to be
   *  forgiving about how the pair is typed or it just moves the friction. */
  function parseMAs(text) {
    var nums = String(text || "").match(/\d+/g);
    if (!nums || nums.length < 2) return { error: 'Enter two numbers, e.g. "15, 20".' };
    var fast = parseInt(nums[0], 10), slow = parseInt(nums[1], 10);
    if (fast < 2 || slow < 3) return { error: "Periods must be at least 2 and 3." };
    if (fast >= slow) {
      return { error: "The fast average must be shorter than the slow one — got " + fast + " and " + slow + "." };
    }
    return { fast: fast, slow: slow };
  }

  function isoDaysAgo(years) {
    var d = new Date();
    d.setFullYear(d.getFullYear() - years);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function todayIso() {
    var d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function applyPeriod() {
    var v = el("ml-period").value;
    var custom = v === "custom";
    el("ml-from").disabled = !custom;
    el("ml-to").disabled = !custom;
    if (custom) return;
    el("ml-from").value = isoDaysAgo(parseInt(v, 10));
    el("ml-to").value = todayIso();
  }

  // -- formatting -----------------------------------------------------------

  function fmtDate(iso) {
    var d = new Date(iso);
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  }

  function pnlCls(v) { return v > 0 ? "text-green" : v < 0 ? "text-red" : ""; }

  // -- tiles ----------------------------------------------------------------

  function tile(label, value, detail, klass) {
    return '<div class="card"><div class="stat-block"><div class="k">' + label + "</div>" +
      '<div class="v sm' + (klass ? " " + klass : "") + '">' + value + "</div>" +
      '<div class="d text-muted">' + (detail || "&nbsp;") + "</div></div></div>";
  }

  function renderTiles() {
    var host = document.querySelector('[data-live="ml-tiles"]');
    if (!host) return;
    var d = state.data;
    if (!d) { host.innerHTML = ""; return; }
    host.innerHTML =
      tile("Total Trades", String(d.totalTrades), d.sessions + " sessions of data") +
      tile("Hits / Losses",
        '<span class="text-green">' + d.wins + "</span> / <span class=\"text-red\">" + d.losses + "</span>",
        d.winRatePct.toFixed(1) + "% hit rate") +
      tile("Payoff Ratio", d.payoffRatio == null ? "—" : d.payoffRatio.toFixed(2) + " : 1",
        "avg win " + NE.fmtINR(d.avgWin) + " vs loss " + NE.fmtINR(d.avgLoss)) +
      tile("Max Drawdown", d.maxDrawdownPct.toFixed(2) + "%",
        "deepest peak-to-trough fall",
        d.maxDrawdownPct <= -25 ? "text-red" : "text-amber") +
      tile("Net P&L", NE.fmtINRSigned(d.netProfit), d.netProfitPct.toFixed(2) + "% on capital",
        pnlCls(d.netProfit)) +
      tile("Profit Factor", d.profitFactor == null ? "—" : d.profitFactor.toFixed(2),
        "gross win ÷ gross loss") +
      tile("Sharpe", d.sharpeRatio.toFixed(2), "annualised") +
      tile("Data Source", d.source === "broker" ? "Live broker" : "Mock",
        d.source === "broker" ? "real historical candles" : "synthetic — not real market data",
        d.source === "broker" ? "text-green" : "text-amber");
  }

  // -- grid -----------------------------------------------------------------

  function visibleTrades() {
    var d = state.data;
    if (!d) return [];
    if (state.filter === "win") return d.trades.filter(function (t) { return t.result === "WIN"; });
    if (state.filter === "loss") return d.trades.filter(function (t) { return t.result !== "WIN"; });
    return d.trades;
  }

  function renderTable() {
    var table = el("ml-table");
    if (!table) return;
    var d = state.data;
    if (!d) {
      table.innerHTML = '<tbody><tr><td style="color:var(--text-muted);">Set the moving averages and click Run.</td></tr></tbody>';
      NE.setText("ml-count", "");
      return;
    }
    var rows = visibleTrades();
    NE.setText("ml-count", rows.length + " of " + d.trades.length + " shown");
    if (!rows.length) {
      table.innerHTML = '<tbody><tr><td style="color:var(--text-muted);">No trades match this filter.</td></tr></tbody>';
      return;
    }
    var head = "<thead><tr><th>#</th><th>Entry date</th><th>Time</th><th>Exit date</th><th>Time</th>" +
      "<th>Days</th><th>Traded</th><th>Result</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Equity</th></tr></thead>";
    var body = rows.map(function (t) {
      var win = t.result === "WIN";
      return "<tr>" +
        "<td>" + t.n + "</td>" +
        "<td>" + fmtDate(t.openedAt) + "</td>" +
        '<td style="color:var(--text-muted);">' + SESSION_OPEN + "</td>" +
        "<td>" + fmtDate(t.closedAt) + "</td>" +
        '<td style="color:var(--text-muted);">' + SESSION_CLOSE + "</td>" +
        "<td>" + t.daysHeld + "</td>" +
        '<td style="text-align:left;">' + (t.side || t.label) +
          (t.strike ? ' <span style="color:var(--text-muted);">' + NE.fmtNum(t.strike, 0) + "</span>" : "") + "</td>" +
        '<td><span class="badge ' + (win ? "badge-green" : "badge-red") + '">' + (win ? "HIT" : "LOSS") + "</span></td>" +
        "<td>" + t.entryPrice.toFixed(2) + "</td>" +
        "<td>" + t.exitPrice.toFixed(2) + "</td>" +
        '<td class="' + pnlCls(t.pnl) + '"><b>' + NE.fmtINRSigned(t.pnl) + "</b></td>" +
        "<td>" + NE.fmtINR(t.equityAfter) + "</td>" +
        "</tr>";
    }).join("");
    table.innerHTML = head + "<tbody>" + body + "</tbody>";
  }

  function render() { renderTiles(); renderTable(); }

  // -- CSV ------------------------------------------------------------------

  function copyCsv() {
    var d = state.data;
    if (!d || !d.trades.length) return;
    var lines = ["n,entry_date,entry_time,exit_date,exit_time,days_held,traded,result,entry_price,exit_price,pnl,equity_after"];
    visibleTrades().forEach(function (t) {
      lines.push([
        t.n, t.openedAt.slice(0, 10), SESSION_OPEN, t.closedAt.slice(0, 10), SESSION_CLOSE,
        t.daysHeld, '"' + (t.side || t.label) + '"', t.result === "WIN" ? "HIT" : "LOSS",
        t.entryPrice, t.exitPrice, t.pnl, t.equityAfter,
      ].join(","));
    });
    var text = lines.join("\n");
    var btn = document.querySelector('[data-live="ml-csv-btn"]');
    function done(ok) {
      if (!btn) return;
      btn.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(function () { btn.textContent = "Copy CSV"; }, 1500);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else {
      done(false);
    }
  }

  // -- run ------------------------------------------------------------------

  function run() {
    var mas = parseMAs(el("ml-mas").value);
    if (mas.error) { showError(mas.error); return; }
    var from = el("ml-from").value, to = el("ml-to").value;
    if (!from || !to) { showError("Pick a From and To date."); return; }
    if (from > to) { showError("From date must be on or before To date."); return; }
    showError(null);
    setLoading(true);

    var body = {
      template: "sma_crossover",
      params: {
        fastMa: mas.fast,
        slowMa: mas.slow,
        crossOnly: el("ml-cross-only").checked,
      },
      instrument: el("ml-instrument").value,
      from: from,
      to: to,
      initialCapital: parseFloat(el("ml-capital").value) || 100000,
      positionSizeLots: parseInt(el("ml-lots").value, 10) || 1,
      stopLossPct: parseFloat(el("ml-stop").value) || 1.5,
      targetPct: parseFloat(el("ml-target").value) || 3.0,
    };

    NE.postJSON("/backtests/template", body, 300000)
      .then(function (d) {
        state.data = d;
        NE.setText("ml-meta",
          d.underlying + " · SMA " + d.params.fastMa + "/" + d.params.slowMa +
          (body.params.crossOnly ? " · crossover bars only" : " · every bar in trend") +
          " · " + d.fromDate + " → " + d.toDate +
          " · " + d.totalTrades + " trades · capital " + NE.fmtINR(d.startingCapital) +
          " · " + body.positionSizeLots + " lot(s)");
        render();
        if (!d.totalTrades) {
          showError("No trades in this range — the averages may never have crossed, or the data is too short.");
        }
      })
      .catch(function (err) { showError("Run failed: " + err.message); })
      .then(function () { setLoading(false); });
  }

  // -- wiring ---------------------------------------------------------------

  applyPeriod();
  render();

  el("ml-period").addEventListener("change", applyPeriod);
  el("ml-mas").addEventListener("keydown", function (e) { if (e.key === "Enter") run(); });
  document.querySelectorAll('[data-live="ml-run-btn"]').forEach(function (b) {
    b.addEventListener("click", run);
  });
  document.querySelectorAll("[data-ml-filter]").forEach(function (b) {
    b.addEventListener("click", function () {
      state.filter = b.getAttribute("data-ml-filter");
      document.querySelectorAll("[data-ml-filter]").forEach(function (o) {
        o.classList.toggle("btn-primary", o === b);
      });
      renderTable();
    });
  });
  document.querySelectorAll('[data-live="ml-csv-btn"]').forEach(function (b) {
    b.addEventListener("click", copyCsv);
  });
})();
