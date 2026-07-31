/**
 * Wires strategy-signals.html to the live backend: active signals, real
 * signal-attribution history, and real hit-rate/R:R/calibration stats from
 * signal_ledger (recorded when each signal fires, reconciled against real
 * price action since). Refreshes every 2s — signal_ledger.record_signal()
 * debounces repeat signals itself, so this doesn't spam the ledger.
 */
(function () {
  var NE = window.NE;
  var resultBadge = { "Target Hit": "badge-green", "SL Hit": "badge-red", "Open": "badge-amber", "Expired": "badge-gray" };

  function load() {
    Promise.all([
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
    NE.fetchJSON("/signals/active"),
    NE.fetchJSON("/signals/history"),
    NE.fetchJSON("/signals/stats"),
  ])
    .then(function (results) {
      var quotes = results[0], active = results[1], history = results[2], stats = results[3];

      NE.applyHeaderTicker(quotes);
      NE.applyFooterTicker(quotes);

      NE.setText("signal-primary-action", active.primary.action + " · " + active.primary.instrument);
      NE.setText("bias-headline", active.bias.headline);
      NE.setText("signal-primary-confidence", active.primary.confidencePct + "%");
      NE.setText("signal-primary-entry", active.primary.entryZone);
      NE.setText("signal-primary-target", active.primary.target);
      NE.setText("signal-primary-sl", active.primary.stopLoss);
      var when = new Date(active.primary.generatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      NE.setText("signal-primary-time", when);
      NE.setText("signal-primary-time-2", when);

      if (active.primary.reasoning && active.primary.reasoning.length) {
        NE.setHTML("signal-primary-reasoning-badges", active.primary.reasoning.map(function (r) {
          return '<span class="badge badge-gray">' + r + "</span>";
        }).join(""));
      }

      var ring = document.querySelector('[data-live="signal-primary-ring"]');
      if (ring) ring.style.background = "conic-gradient(" + (active.bias.direction === "BEARISH" ? "#ef4444" : "#22c55e") + " " + active.primary.confidencePct + "%, var(--border-soft) 0)";

      NE.setText("signal-alt-action", active.alternative.action + " · " + active.alternative.instrument);
      NE.setText("signal-alt-entry", active.alternative.entryZone);
      NE.setText("signal-alt-confidence", active.alternative.confidencePct + "%");
      var altRing = document.querySelector('[data-live="signal-alt-ring"]');
      if (altRing) altRing.style.background = "conic-gradient(#22c55e " + active.alternative.confidencePct + "%, var(--border-soft) 0)";
      if (active.alternative.reasoning && active.alternative.reasoning.length) {
        NE.setHTML("signal-alt-reasoning-badges", active.alternative.reasoning.map(function (r) {
          return '<span class="badge badge-gray">' + r + "</span>";
        }).join(""));
      }

      // -- Signal History table (real predictions + real reconciled outcomes) --------
      var tbody = document.querySelector('[data-live="signal-history-tbody"]');
      if (tbody) {
        if (history.length) {
          tbody.innerHTML = history.map(function (row) {
            var pnlClass = row.pnl >= 0 ? "text-green" : "text-red";
            var badge = resultBadge[row.result] || "badge-gray";
            return (
              "<tr><td>" + row.when + "</td><td>" + row.signal + "</td><td>" + row.confidencePct + "%</td>" +
              "<td>" + row.entry + "</td><td>" + row.target + "</td><td>" + row.stopLoss + "</td>" +
              '<td><span class="badge ' + badge + '">' + row.result + "</span></td>" +
              '<td class="' + pnlClass + '">' + NE.fmtINRSigned(row.pnl) + "</td></tr>"
            );
          }).join("");
        } else {
          tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted);">No signals recorded yet</td></tr>';
        }
      }

      // -- Stat cards ------------------------------------------------------------------
      NE.setText("stat-today-hit-rate", stats.todayHitRatePct.toFixed(0) + "%");
      NE.setText("stat-30d-hit-rate", stats.hitRatePct.toFixed(1) + "%");
      NE.setText("stat-avg-rr", stats.avgRiskReward ? "1 : " + stats.avgRiskReward.toFixed(1) : "—");
      NE.setText("stat-30d-sample", stats.resolvedCount + " resolved, " + stats.openCount + " open");

      // -- Performance by Strategy -------------------------------------------------------
      var stratEl = document.querySelector('[data-live="strategy-performance-rows"]');
      if (stratEl) {
        if (stats.byStrategy.length) {
          stratEl.innerHTML = stats.byStrategy.map(function (s) {
            var cls = s.hitRatePct >= 70 ? "text-green" : s.hitRatePct >= 50 ? "text-amber" : "text-red";
            return '<div class="stat-row"><span class="k">' + s.strategy + '</span><span class="v ' + cls + '">' +
              s.hitRatePct.toFixed(0) + "% hit (n=" + s.sampleSize + ")</span></div>";
          }).join("");
        } else {
          stratEl.innerHTML = '<div class="stat-row"><span class="k" style="color:var(--text-muted);">No resolved signals yet</span></div>';
        }
      }

      // -- Confidence Calibration --------------------------------------------------------
      var buckets = stats.confidenceCalibration;
      if (buckets.length) {
        var w = 260, h = 90;
        var points = buckets.map(function (b, i) {
          var x = buckets.length > 1 ? (i / (buckets.length - 1)) * w : w / 2;
          var y = h - (b.hitRatePct / 100) * h;
          return x.toFixed(0) + "," + y.toFixed(0);
        }).join(" ");
        document.querySelector('[data-live="calibration-polyline"]').setAttribute("points", points);
        var best = buckets[buckets.length - 1];
        NE.setText("calibration-note", "Real calibration from " + stats.resolvedCount + " resolved signal(s) over the period: " +
          buckets.map(function (b) { return b.confidenceRange + " confidence → " + b.hitRatePct.toFixed(0) + "% hit (n=" + b.sampleSize + ")"; }).join("; ") + ".");
      }

      NE.markStatus(true);
      NE.stampRefresh();
    })
    .catch(function () { NE.markStatus(false); });
  }

  load();
  setInterval(load, 2000);
})();
