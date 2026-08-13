/**
 * Wires positions.html to the live backend. Refreshes on the shared REFRESH_MS cadence — see
 * js/ne-common.js for the shared fetch/format helpers.
 */
(function () {
  // Live-refresh cadence. Kept deliberately slow: every open tab is its own
  // polling stream against the broker's rate limit, and Dhan answers a hot
  // one with 429 plus a warning about blocking the account (see the cache
  // notes in backend/app/services/market_data.py).
  var REFRESH_MS = 30000;

  var NE = window.NE;

  function load() {
    Promise.all([
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
    NE.fetchJSON("/positions/open"),
    NE.fetchJSON("/positions/margins"),
  ])
    .then(function (results) {
      var quotes = results[0], positions = results[1], margins = results[2];
      NE.applyHeaderTicker(quotes);
      NE.applyFooterTicker(quotes);
      NE.applyMarginsAndExposure(margins);

      var tbody = document.querySelector('[data-live="open-positions-tbody"]');
      if (tbody) {
        if (positions.length) {
          tbody.innerHTML = positions.map(function (p) {
            var qtySign = p.side === "LONG" ? "+" : "-";
            var qtyClass = p.side === "LONG" ? "text-green" : "text-red";
            var pnlClass = p.pnl >= 0 ? "text-green" : "text-red";
            var badgeClass = p.side === "LONG" ? "badge-green" : "badge-red";
            return (
              "<tr><td>" + p.instrument + "</td>" +
              '<td><span class="badge ' + badgeClass + '">' + p.side + "</span></td>" +
              '<td class="' + qtyClass + '">' + qtySign + p.quantityLots + "</td>" +
              "<td>" + p.avgPrice.toFixed(2) + "</td>" +
              "<td>" + p.ltp.toFixed(2) + "</td>" +
              "<td>&mdash;</td><td>&mdash;</td>" +
              '<td class="' + pnlClass + '">' + NE.fmtINRSigned(p.pnl) + "</td>" +
              '<td class="' + pnlClass + '">' + NE.fmtSigned(p.pnlPct) + "%</td>" +
              '<td><button class="btn btn-sm">Exit</button></td></tr>'
            );
          }).join("");
        } else {
          tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No open positions</td></tr>';
        }
      }
      NE.setText("open-positions-title", "Open Positions (" + positions.length + ")");

      var totalPnl = positions.reduce(function (sum, p) { return sum + p.pnl; }, 0);
      var totalCost = positions.reduce(function (sum, p) { return sum + p.avgPrice * p.quantityLots; }, 0);
      var totalPct = totalCost ? (totalPnl / totalCost) * 100 : 0;
      var pnlEl = document.querySelector('[data-live="positions-total-pnl"]');
      NE.setClass(pnlEl, totalPnl >= 0 ? "text-green" : "text-red", true);
      NE.setText("positions-total-pnl", NE.fmtINRSigned(totalPnl));
      var pctEl = document.querySelector('[data-live="pos-open-pnl-pct"]');
      NE.setClass(pctEl, totalPnl >= 0 ? "text-green" : "text-red", true);
      NE.setText("pos-open-pnl-pct", NE.fmtSigned(totalPct) + "%");

      // "Day's P&L" = today's realized P&L (closed trades) + today's open unrealized P&L.
      NE.fetchJSON("/positions/closed")
        .then(function (closed) {
          var realized = closed.reduce(function (sum, c) { return sum + c.pnl; }, 0);
          var dayPnl = realized + totalPnl;
          var dayEl = document.querySelector('[data-live="pos-day-pnl"]');
          NE.setClass(dayEl, dayPnl >= 0 ? "text-green" : "text-red", true);
          NE.setText("pos-day-pnl", NE.fmtINRSigned(dayPnl));
          document.querySelector('[data-live="pos-day-pnl-pct"]').textContent = "Realized " + NE.fmtINRSigned(realized);

          var ctbody = document.querySelector('[data-live="closed-positions-tbody"]');
          if (ctbody) {
            if (closed.length) {
              ctbody.innerHTML = closed.map(function (c) {
                var pnlClass = c.pnl >= 0 ? "text-green" : "text-red";
                var badgeClass = c.side === "BUY" ? "badge-green" : "badge-red";
                var when = new Date(c.closedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
                return (
                  "<tr><td>" + c.instrument + "</td>" +
                  '<td><span class="badge ' + badgeClass + '">' + c.side + "</span></td>" +
                  "<td>" + c.quantity + "</td>" +
                  "<td>" + c.entryPrice.toFixed(2) + "</td>" +
                  "<td>" + c.exitPrice.toFixed(2) + "</td>" +
                  '<td class="' + pnlClass + '">' + NE.fmtINRSigned(c.pnl) + "</td>" +
                  "<td>" + when + "</td></tr>"
                );
              }).join("");
            } else {
              ctbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No closed positions today</td></tr>';
            }
          }
        })
        .catch(function () {});

      NE.markStatus(true);
      NE.stampRefresh();
    })
    .catch(function () {
      NE.markStatus(false);
    });
  }

  load();
  setInterval(load, REFRESH_MS);
})();
