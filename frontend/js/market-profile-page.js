/**
 * Wires market-profile.html to the live backend. The TPO letter-grid is
 * built entirely from real per-price letters returned by
 * /market/profile (see analytics.market_profile_detail on the backend) —
 * nothing on this page is static placeholder content. Refreshes every 2s.
 */
(function () {
  var NE = window.NE;
  var UNDERLYING = "NIFTY50";
  var TICK = 5.0;

  function overlapPct(vah1, val1, vah2, val2) {
    var overlapHigh = Math.min(vah1, vah2), overlapLow = Math.max(val1, val2);
    var overlap = Math.max(0, overlapHigh - overlapLow);
    var union = Math.max(vah1, vah2) - Math.min(val1, val2);
    return union ? (overlap / union) * 100 : 0;
  }

  function nearestRow(rows, target) {
    if (target == null) return null;
    return rows.reduce(function (best, r) {
      return Math.abs(r.price - target) < Math.abs(best.price - target) ? r : best;
    }, rows[0]);
  }

  function dayTypeBadgeClass(dayType) {
    if (dayType === "Trend Day") return "badge-red";
    if (dayType === "Neutral Day") return "badge-gray";
    if (dayType === "Normal Day") return "badge-blue";
    if (dayType === "Normal Variation Day") return "badge-amber";
    return "badge-gray";
  }

  function buildPeriodLegend(rows, periodMinutes) {
    var maxIdx = 0;
    rows.forEach(function (r) {
      r.letters.split("").forEach(function (ch) {
        maxIdx = Math.max(maxIdx, ch.charCodeAt(0) - 65);
      });
    });
    var parts = [];
    var cursor = 9 * 60 + 15; // 09:15 in minutes
    for (var i = 0; i <= maxIdx; i++) {
      var letter = String.fromCharCode(65 + i);
      var start = cursor, end = cursor + periodMinutes;
      parts.push(letter + " " + fmtHM(start) + "–" + fmtHM(end));
      cursor = end;
    }
    return "Periods (" + periodMinutes + "-min): " + parts.join("   ");
  }

  function fmtHM(mins) {
    var h = Math.floor(mins / 60), m = mins % 60;
    return (h < 10 ? "0" + h : h) + ":" + (m < 10 ? "0" + m : m);
  }

  function renderTpoGrid(profile, ltp) {
    var rows = profile.rows;
    if (!rows.length) return "<div style=\"color:var(--text-muted);\">No data</div>";
    var maxCount = Math.max.apply(null, rows.map(function (r) { return r.tpoCount; }));
    var ibHighRow = nearestRow(rows, profile.ibHigh);
    var ibLowRow = nearestRow(rows, profile.ibLow);

    return rows.slice().sort(function (a, b) { return b.price - a.price; }).map(function (r) {
      var inValueArea = r.price <= profile.vah && r.price >= profile.val;
      var isPoc = r.price === profile.poc;
      var inIb = r.price <= profile.ibHigh && r.price >= profile.ibLow;
      var bg = "";
      if (isPoc) bg = "background:rgba(59,130,246,.16);";
      else if (inValueArea) bg = "background:rgba(245,166,35," + (0.06 + 0.12 * (r.tpoCount / maxCount)).toFixed(2) + ");";

      var priceStyle = "width:64px; text-align:right; color:var(--text-muted);";
      var badges = "";
      if (r.price === profile.vah) { priceStyle = "width:64px; text-align:right; color:var(--pink);"; badges += ' <span class="badge badge-red" style="margin-left:6px;">VAH</span>'; }
      if (r.price === profile.val) { priceStyle = "width:64px; text-align:right; color:var(--pink);"; badges += ' <span class="badge badge-red" style="margin-left:6px;">VAL</span>'; }
      if (isPoc) { priceStyle = "width:64px; text-align:right; color:var(--blue); font-weight:800;"; badges += ' <span class="badge badge-blue" style="margin-left:6px;">POC</span>'; }
      if (ltp != null && Math.abs(r.price - ltp) < TICK) { priceStyle = "width:64px; text-align:right; color:var(--green); font-weight:700;"; badges += ' <span class="badge badge-green" style="margin-left:6px;">LTP</span>'; }
      if (ibHighRow && r.price === ibHighRow.price) badges += ' <span class="badge badge-blue" style="margin-left:6px;">IB High</span>';
      if (ibLowRow && r.price === ibLowRow.price) badges += ' <span class="badge badge-blue" style="margin-left:6px;">IB Low</span>';

      var letterColor = inIb ? "color:var(--purple);" : "";
      return (
        '<div class="flex items-center" style="gap:10px; ' + bg + '">' +
        '<span style="' + priceStyle + '">' + NE.fmtNum(r.price, 0) + "</span>" +
        '<span style="' + letterColor + '">' + r.letters + "</span>" + badges +
        "</div>"
      );
    }).join("");
  }

  function rangeExtensionText(profile) {
    var up = profile.rangeExtensionUp || 0, down = profile.rangeExtensionDown || 0;
    if (up > 0 && down > 0) return { text: "+" + up.toFixed(0) + " / -" + down.toFixed(0) + " pts (both)", cls: "text-amber" };
    if (up > 0) return { text: "+" + up.toFixed(0) + " pts (up)", cls: "text-green" };
    if (down > 0) return { text: "-" + down.toFixed(0) + " pts (down)", cls: "text-red" };
    return { text: "None", cls: "" };
  }

  function renderSinglePrints(profile) {
    function row(label, price, badgeCls) {
      var value = price != null ? '<span class="badge ' + badgeCls + '">' + NE.fmtNum(price, 0) + "</span>" : '<span class="badge badge-gray">None</span>';
      return '<li class="flex justify-between"><span>' + label + "</span>" + value + "</li>";
    }
    return (
      row("Poor High", profile.poorHigh, "badge-red") +
      row("Poor Low", profile.poorLow, "badge-red") +
      row("Excess (Selling Tail)", profile.excessHigh, "badge-green") +
      row("Excess (Buying Tail)", profile.excessLow, "badge-green")
    );
  }

  function renderHistory(history) {
    if (!history.length) return '<tr><td colspan="5" style="color:var(--text-muted);">No history available</td></tr>';
    return history.slice().reverse().map(function (row) {
      var d = new Date(row.sessionDate + "T00:00:00");
      var label = d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
      return (
        "<tr><td>" + label + "</td>" +
        "<td>" + NE.fmtNum(row.vah, 0) + "</td>" +
        "<td>" + NE.fmtNum(row.poc, 0) + "</td>" +
        "<td>" + NE.fmtNum(row.val, 0) + "</td>" +
        "<td>" + row.rangePts.toFixed(0) + " pts</td></tr>"
      );
    }).join("");
  }

  function loadComposite(ltp) {
    var canvas = document.querySelector('[data-live="mp-composite-canvas"]');
    if (!canvas) return;
    NE.fetchJSON("/market/profile/composite?underlying=" + UNDERLYING + "&sessions=5")
      .then(function (sessions) {
        NE.renderCompositeChart(canvas, sessions, { mode: "tpo", ltp: ltp, tick: TICK });
      })
      .catch(function () {});
  }

  function load() {
    Promise.all([
      NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
      NE.fetchJSON("/market/profile?underlying=" + UNDERLYING),
      NE.fetchJSON("/market/profile/previous-day?underlying=" + UNDERLYING),
      NE.fetchJSON("/market/profile/history?underlying=" + UNDERLYING + "&days=5"),
    ])
      .then(function (results) {
        var quotes = results[0], today = results[1], prev = results[2], history = results[3];

        NE.applyHeaderTicker(quotes);
        NE.applyFooterTicker(quotes);

        var spot = quotes.filter(function (q) { return q.symbol === "NIFTY50"; })[0];
        var ltp = spot ? spot.ltp : null;

        NE.setHTML("mp-rows", renderTpoGrid(today, ltp));
        NE.setHTML("mp-periods-legend", buildPeriodLegend(today.rows, 30));

        NE.setText("pvv-vah", NE.fmtNum(today.vah, 2));
        NE.setText("pvv-poc", NE.fmtNum(today.poc, 2));
        NE.setText("pvv-val", NE.fmtNum(today.val, 2));
        var vaWidth = today.vah - today.val;
        var vaPct = today.poc ? (vaWidth / today.poc) * 100 : 0;
        NE.setText("mp-va-width", vaWidth.toFixed(0) + " pts (" + vaPct.toFixed(0) + "%)");

        if (today.sessionHigh != null && today.sessionLow != null) {
          NE.setText("mp-range", NE.fmtNum(today.sessionLow, 0) + " – " + NE.fmtNum(today.sessionHigh, 0));
        }

        var dayTypeEl = document.querySelector('[data-live="mp-day-type"]');
        if (dayTypeEl) {
          dayTypeEl.className = "badge " + dayTypeBadgeClass(today.dayType);
          dayTypeEl.textContent = today.dayType || "—";
        }
        NE.setText("mp-open-type", today.openType || "—");

        if (today.ibHigh != null && today.ibLow != null) {
          NE.setText("mp-ib", NE.fmtNum(today.ibLow, 0) + " – " + NE.fmtNum(today.ibHigh, 0));
          NE.setText("mp-ib-range", (today.ibRange || 0).toFixed(0) + " pts");
        }

        var ext = rangeExtensionText(today);
        var extEl = document.querySelector('[data-live="mp-range-extension"]');
        if (extEl) {
          extEl.className = "v " + ext.cls;
          extEl.textContent = ext.text;
        }

        NE.setHTML("mp-single-prints", renderSinglePrints(today));

        NE.setText("mp-prev-vah", NE.fmtNum(prev.vah, 2));
        NE.setText("mp-prev-poc", NE.fmtNum(prev.poc, 2));
        NE.setText("mp-prev-val", NE.fmtNum(prev.val, 2));
        NE.setText("mp-va-overlap", overlapPct(today.vah, today.val, prev.vah, prev.val).toFixed(0) + "%");

        var migration = today.poc - prev.poc;
        var migrationEl = document.querySelector('[data-live="mp-poc-migration"]');
        if (migrationEl) {
          migrationEl.classList.remove("text-red", "text-green");
          migrationEl.classList.add(migration >= 0 ? "text-green" : "text-red");
          migrationEl.textContent = NE.fmtSigned(migration, 2) + " pts (" + (migration >= 0 ? "up" : "down") + ")";
        }

        NE.setHTML("mp-history-tbody", renderHistory(history));

        // The composite chart re-derives 5+ sessions of history server-side —
        // too heavy to refetch every 2s alongside the rest of this page, so
        // it gets its own slower interval below. Still push the freshest LTP
        // into it on every tick so the live-price line stays current.
        lastLtp = ltp;

        NE.markStatus(true);
        NE.stampRefresh();
      })
      .catch(function () { NE.markStatus(false); });
  }

  var lastLtp = null;
  load();
  setInterval(load, 2000);
  loadComposite(null);
  setInterval(function () { loadComposite(lastLtp); }, 30000);
})();
