/**
 * Wires backtester.html to the live backend. The ticker/footer refresh every
 * 2s like the rest of the app, but running a backtest is a deliberate,
 * click-triggered action (POST /backtests) — it doesn't auto-fire on a
 * timer. Results (stat cards, equity curve, trade log) are real: computed
 * from real historical NIFTY/BANKNIFTY daily prices + real CPR levels, with
 * option premiums modeled via Black-Scholes (see the note under Trade Log —
 * Dhan has no historical option-chain data source, only a live snapshot).
 */
(function () {
  var NE = window.NE;
  var API_BASE = window.NIFTYEDGE_API_BASE || "http://localhost:8000/api/v1";

  function postJSON(path, body) {
    return fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (res) {
      if (!res.ok) return res.json().then(function (e) { throw new Error(e.detail || res.status); }, function () { throw new Error(String(res.status)); });
      return res.json();
    });
  }

  function fmtGreek(n, decimals) {
    var sign = n >= 0 ? "+" : "";
    return sign + n.toFixed(decimals);
  }

  // -- ticker/footer refresh, every 2s ------------------------------------

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

  // -- backtest run ---------------------------------------------------------

  function showError(msg) {
    var el = document.querySelector('[data-live="bt-error"]');
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.style.display = "";
    } else {
      el.style.display = "none";
    }
  }

  function setRunning(running) {
    ["bt-run-btn", "bt-run-btn-top"].forEach(function (key) {
      document.querySelectorAll('[data-live="' + key + '"]').forEach(function (btn) {
        btn.disabled = running;
        btn.textContent = running ? "Running…" : "Run Backtest";
      });
    });
  }

  function renderEquityCurve(equityCurve) {
    var w = 860, h = 220;
    if (!equityCurve.length) return;
    var min = Math.min.apply(null, equityCurve);
    var max = Math.max.apply(null, equityCurve);
    var range = (max - min) || 1;
    var pad = range * 0.05;
    var lo = min - pad, hi = max + pad, span = hi - lo || 1;
    var pts = equityCurve.map(function (v, i) {
      var x = (i / (equityCurve.length - 1 || 1)) * w;
      var y = h - ((v - lo) / span) * h;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    var line = document.querySelector('[data-live="bt-equity-line"]');
    var fill = document.querySelector('[data-live="bt-equity-fill"]');
    var lastNegative = equityCurve[equityCurve.length - 1] < equityCurve[0];
    var color = lastNegative ? "#ef4444" : "#22c55e";
    if (line) { line.setAttribute("points", pts.join(" ")); line.setAttribute("stroke", color); }
    if (fill) { fill.setAttribute("points", "0," + h + " " + pts.join(" ") + " " + w + "," + h); }
  }

  function renderTradeLog(trades) {
    var tbody = document.querySelector('[data-live="bt-trade-log-tbody"]');
    if (!tbody) return;
    if (!trades.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No trades — this strategy didn\'t fire a signal over the selected range.</td></tr>';
      return;
    }
    tbody.innerHTML = trades.slice().reverse().map(function (t) {
      var opened = new Date(t.openedAt).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
      var closed = new Date(t.closedAt).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
      var pnlClass = t.pnl >= 0 ? "text-green" : "text-red";
      var badge = t.result === "WIN" ? "badge-green" : "badge-red";
      return (
        "<tr><td>" + opened + "</td><td>" + closed + "</td><td>" + t.label + "</td>" +
        "<td>" + t.entryPrice.toFixed(2) + "</td><td>" + t.exitPrice.toFixed(2) + "</td>" +
        '<td class="' + pnlClass + '">' + fmtGreek(t.pnl, 0) + "</td>" +
        '<td><span class="badge ' + badge + '">' + (t.result === "WIN" ? "Win" : "Loss") + "</span></td></tr>"
      );
    }).join("");
  }

  function computeStreaksAndHolding(trades) {
    var maxWin = 0, maxLoss = 0, curWin = 0, curLoss = 0, totalHours = 0;
    trades.forEach(function (t) {
      if (t.result === "WIN") { curWin++; curLoss = 0; } else { curLoss++; curWin = 0; }
      maxWin = Math.max(maxWin, curWin);
      maxLoss = Math.max(maxLoss, curLoss);
      totalHours += (new Date(t.closedAt) - new Date(t.openedAt)) / 3600000;
    });
    var avgHours = trades.length ? totalHours / trades.length : 0;
    var days = Math.floor(avgHours / 24);
    var hours = Math.round(avgHours % 24);
    var holdingLabel = trades.length ? (days > 0 ? days + "d " + hours + "h" : hours + "h") : "—";
    return { maxWin: maxWin, maxLoss: maxLoss, holdingLabel: holdingLabel };
  }

  function renderResult(result, trades) {
    NE.setText("bt-net-profit", (result.netProfit >= 0 ? "+" : "-") + "₹" + Math.abs(Math.round(result.netProfit)).toLocaleString("en-IN"));
    var netEl = document.querySelector('[data-live="bt-net-profit"]');
    if (netEl) { netEl.classList.remove("text-red", "text-green"); netEl.classList.add(result.netProfit >= 0 ? "text-green" : "text-red"); }
    NE.setText("bt-net-profit-pct", fmtGreek(result.netProfitPct, 2) + "%");
    var pctEl = document.querySelector('[data-live="bt-net-profit-pct"]');
    if (pctEl) { pctEl.classList.remove("text-red", "text-green"); pctEl.classList.add(result.netProfitPct >= 0 ? "text-green" : "text-red"); }

    NE.setText("bt-win-rate", result.winRatePct.toFixed(1) + "%");
    NE.setText("bt-profit-factor", isFinite(result.profitFactor) ? result.profitFactor.toFixed(2) : "∞");
    NE.setText("bt-sharpe", result.sharpeRatio.toFixed(2));
    NE.setText("bt-max-dd", result.maxDrawdownPct.toFixed(2) + "%");
    NE.setText("bt-max-dd-2", result.maxDrawdownPct.toFixed(2) + "%");
    NE.setText("bt-sortino", result.sortinoRatio.toFixed(2));
    NE.setText("bt-volatility", result.volatilityPct.toFixed(1) + "%");
    var recovery = result.maxDrawdownPct ? Math.abs(result.netProfitPct / result.maxDrawdownPct) : 0;
    NE.setText("bt-recovery-factor", recovery.toFixed(2));

    NE.setText("bt-total-trades", String(result.totalTrades));
    NE.setText("bt-winning-trades", String(result.winningTrades));
    NE.setText("bt-losing-trades", String(result.losingTrades));

    var wins = trades.filter(function (t) { return t.pnl > 0; }).map(function (t) { return t.pnl; });
    var losses = trades.filter(function (t) { return t.pnl <= 0; }).map(function (t) { return t.pnl; });
    var avgWin = wins.length ? wins.reduce(function (a, b) { return a + b; }, 0) / wins.length : 0;
    var avgLoss = losses.length ? losses.reduce(function (a, b) { return a + b; }, 0) / losses.length : 0;
    NE.setText("bt-avg-win", "+₹" + Math.round(avgWin).toLocaleString("en-IN"));
    NE.setText("bt-avg-loss", "-₹" + Math.round(Math.abs(avgLoss)).toLocaleString("en-IN"));

    var streaks = computeStreaksAndHolding(trades);
    NE.setText("bt-max-win-streak", String(streaks.maxWin));
    NE.setText("bt-max-loss-streak", String(streaks.maxLoss));
    NE.setText("bt-avg-holding", streaks.holdingLabel);

    var fromEl = document.getElementById("bt-from"), toEl = document.getElementById("bt-to");
    NE.setText("bt-equity-sub", result.totalTrades + " trades · " + (fromEl ? fromEl.value : "") + " to " + (toEl ? toEl.value : ""));

    renderEquityCurve(result.equityCurve);
    renderTradeLog(trades);
  }

  function readForm() {
    var strategy = document.getElementById("bt-strategy").value;
    var from = document.getElementById("bt-from").value;
    var to = document.getElementById("bt-to").value;
    var instrument = document.getElementById("bt-instrument").value;
    var capital = parseFloat(document.getElementById("bt-capital").value);
    var lots = parseInt(document.getElementById("bt-lots").value, 10);
    var sl = parseFloat(document.getElementById("bt-sl").value);
    var target = parseFloat(document.getElementById("bt-target").value);
    var costsToggle = document.getElementById("bt-costs-toggle");
    var includeCosts = costsToggle ? costsToggle.classList.contains("on") : true;

    if (!from || !to) return { error: "Pick a From and To date." };
    if (new Date(from) >= new Date(to)) return { error: "From date must be before To date." };
    if (!capital || capital <= 0) return { error: "Initial capital must be a positive number." };
    if (!lots || lots <= 0) return { error: "Position size must be a positive number of lots." };
    if (isNaN(sl) || sl <= 0 || isNaN(target) || target <= 0) return { error: "Stop loss and target must be positive percentages." };

    return {
      body: {
        strategy: strategy, instrument: instrument, from: from, to: to,
        initialCapital: capital, positionSizeLots: lots,
        stopLossPct: sl, targetPct: target, includeSlippageAndCosts: includeCosts,
      },
    };
  }

  function runBacktest() {
    var parsed = readForm();
    if (parsed.error) { showError(parsed.error); return; }
    showError(null);
    setRunning(true);
    postJSON("/backtests", parsed.body)
      .then(function (result) {
        return NE.fetchJSON("/backtests/" + result.jobId + "/trades?page_size=500").then(function (tradesResp) {
          renderResult(result, tradesResp.items);
        });
      })
      .catch(function (err) {
        showError("Backtest failed: " + err.message);
      })
      .then(function () { setRunning(false); });
  }

  var costsToggle = document.getElementById("bt-costs-toggle");
  if (costsToggle) {
    costsToggle.addEventListener("click", function () { costsToggle.classList.toggle("on"); });
  }

  ["bt-run-btn", "bt-run-btn-top"].forEach(function (key) {
    document.querySelectorAll('[data-live="' + key + '"]').forEach(function (btn) {
      btn.addEventListener("click", runBacktest);
    });
  });
})();
