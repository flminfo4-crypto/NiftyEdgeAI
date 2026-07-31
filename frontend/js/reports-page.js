/**
 * Wires reports.html to the live backend (month-to-date real P&L/charges from
 * actual trade history — empty/zero until real trades exist). Refreshes
 * every 2s. "Available Reports" download list stays static — see the
 * HTML comment next to it (real PDF/XLSX generation is out of scope here).
 */
(function () {
  var NE = window.NE;

  function load() {
    Promise.all([
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
    NE.fetchJSON("/reports/summary"),
  ])
    .then(function (results) {
      var quotes = results[0], r = results[1];
      NE.applyHeaderTicker(quotes);
      NE.applyFooterTicker(quotes);

      var netEl = document.querySelector('[data-live="rep-net-pnl"]');
      NE.setClass(netEl, r.netPnl >= 0 ? "text-green" : "text-red", true);
      NE.setText("rep-net-pnl", NE.fmtINRSigned(r.netPnl));

      var realizedEl = document.querySelector('[data-live="rep-realized-pnl"]');
      NE.setClass(realizedEl, r.realizedPnl >= 0 ? "text-green" : "text-red", true);
      NE.setText("rep-realized-pnl", NE.fmtINRSigned(r.realizedPnl));

      var unrealizedEl = document.querySelector('[data-live="rep-unrealized-pnl"]');
      NE.setClass(unrealizedEl, r.unrealizedPnl >= 0 ? "text-green" : "text-red", true);
      NE.setText("rep-unrealized-pnl", NE.fmtINRSigned(r.unrealizedPnl));

      NE.setText("rep-charges-total", "-" + NE.fmtINR(r.charges.total));

      NE.setText("rep-charge-brokerage", NE.fmtINR(r.charges.brokerage));
      NE.setText("rep-charge-stt", NE.fmtINR(r.charges.stt));
      NE.setText("rep-charge-exchange", NE.fmtINR(r.charges.exchangeCharges));
      NE.setText("rep-charge-gst", NE.fmtINR(r.charges.gst));
      NE.setText("rep-charge-sebi", NE.fmtINR(r.charges.sebiStampDuty));
      NE.setText("rep-charge-grand-total", NE.fmtINR(r.charges.total));

      NE.setText("rep-winning-days", String(r.winningDays));
      NE.setText("rep-losing-days", String(r.losingDays));
      var totalDays = r.winningDays + r.losingDays;
      var winPct = totalDays ? (r.winningDays / totalDays) * 100 : 0;
      document.querySelector('[data-live="rep-winloss-meter"]').style.width = winPct.toFixed(0) + "%";
      NE.setText("rep-winloss-pct", totalDays ? winPct.toFixed(0) + "% winning days this period" : "No closed trading days yet this period");

      var chartEl = document.querySelector('[data-live="rep-daily-pnl-chart"]');
      if (r.dailyPnl.length) {
        var maxAbs = Math.max.apply(null, r.dailyPnl.map(function (d) { return Math.abs(d.pnl); })) || 1;
        chartEl.innerHTML = r.dailyPnl.map(function (d) {
          var pct = Math.max(4, (Math.abs(d.pnl) / maxAbs) * 100);
          var color = d.pnl >= 0 ? "var(--green)" : "var(--red)";
          return '<div class="bar" title="' + d.date + ': ' + NE.fmtINRSigned(d.pnl) + '" style="height:' + pct.toFixed(0) + '%; background:' + color + ';"></div>';
        }).join("");
      } else {
        chartEl.innerHTML = '<div style="width:100%; text-align:center; color:var(--text-muted); font-size:12px;">No closed trades yet this period</div>';
      }

      NE.markStatus(true);
      NE.stampRefresh();
    })
    .catch(function () { NE.markStatus(false); });
  }

  load();
  setInterval(load, 2000);
})();
