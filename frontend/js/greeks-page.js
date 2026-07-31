/**
 * Wires greeks.html to the live backend. Refreshes every 2s — same backend-
 * side option-chain caching as options-chain.html / open-interest.html keeps
 * this under Dhan's rate limit.
 */
(function () {
  var NE = window.NE;
  var UNDERLYING = "NIFTY50";
  var STRIKE_RANGE = 4;

  function fmtGreek(n, decimals) {
    var sign = n >= 0 ? "+" : "";
    return sign + n.toFixed(decimals);
  }

  function daysToExpiry(expiry) {
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var exp = new Date(expiry + "T00:00:00");
    return Math.round((exp - today) / 86400000);
  }

  function spansWeekend(expiry) {
    var today = new Date();
    var exp = new Date(expiry + "T00:00:00");
    for (var d = new Date(today); d <= exp; d.setDate(d.getDate() + 1)) {
      var day = d.getDay();
      if (day === 0 || day === 6) return true;
    }
    return false;
  }

  NE.fetchJSON("/market/expiries?underlying=" + UNDERLYING)
    .then(function (data) {
      var expiries = data.expiries || [];
      var select = document.querySelector('[data-live="expiry-select"]');
      if (select && expiries.length) {
        select.innerHTML = expiries.map(function (e) { return "<option>" + e + "</option>"; }).join("");
      }
      var expiry = expiries[0];
      if (!expiry) { NE.markStatus(false); return; }
      var expiryLabel = new Date(expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }).toUpperCase();

      function load() {
        Promise.all([
        NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
        NE.fetchJSON("/market/option-chain?underlying=" + UNDERLYING + "&expiry=" + expiry),
        NE.fetchJSON("/positions/greeks?underlying=" + UNDERLYING + "&expiry=" + expiry),
      ])
        .then(function (results) {
          var quotes = results[0], chain = results[1], greeks = results[2];

          NE.applyHeaderTicker(quotes);
          NE.applyFooterTicker(quotes);

          NE.setText("risk-net-delta", fmtGreek(greeks.netDelta, 2));
          NE.setText("risk-net-gamma", fmtGreek(greeks.netGamma, 2));
          NE.setText("greeks-net-theta", fmtGreek(greeks.netTheta, 2));
          NE.setText("greeks-net-theta-2", "₹" + fmtGreek(greeks.netTheta, 2));
          NE.setText("greeks-net-vega", fmtGreek(greeks.netVega, 2));
          NE.setText("greeks-net-vega-2", fmtGreek(greeks.netVega, 2));
          NE.setText("greeks-net-rho", fmtGreek(greeks.netRho, 2));
          NE.setText("greeks-vega-pnl", "₹" + fmtGreek(-greeks.netVega, 2) + " (1% IV drop)");

          var sorted = chain.rows.slice().sort(function (a, b) { return a.strike - b.strike; });
          var atm = sorted.reduce(function (best, r) {
            return Math.abs(r.strike - chain.spotPrice) < Math.abs(best.strike - chain.spotPrice) ? r : best;
          }, sorted[0]);
          if (atm) NE.setText("atm-iv", (((atm.ceIv + atm.peIv) / 2).toFixed(1)) + "%");

          var atmIdx = sorted.indexOf(atm);
          var nearRows = sorted.slice(Math.max(0, atmIdx - STRIKE_RANGE), atmIdx + STRIKE_RANGE + 1);
          document.querySelector('[data-live="greeks-full-tbody"]').innerHTML = nearRows.map(function (r) {
            var isAtm = r.strike === atm.strike;
            var strikeCell = isAtm
              ? '<td style="background:rgba(245,166,35,.18); text-align:center; font-weight:800; color:var(--amber);">' + NE.fmtNum(r.strike, 0) + " &#9642;</td>"
              : '<td style="background:var(--bg-card-alt); text-align:center; font-weight:700;">' + NE.fmtNum(r.strike, 0) + "</td>";
            var rowClass = isAtm ? ' class="atm"' : "";
            return (
              "<tr" + rowClass + '><td class="text-green">' + r.ceDelta.toFixed(2) + "</td><td>" + r.ceGamma.toFixed(4) +
              '</td><td class="text-red">' + r.ceTheta.toFixed(2) + "</td><td>" + r.ceVega.toFixed(2) + "</td><td>" + r.ceRho.toFixed(2) + "</td>" +
              strikeCell +
              '<td class="text-red">' + r.peDelta.toFixed(2) + "</td><td>" + r.peGamma.toFixed(4) +
              '</td><td class="text-red">' + r.peTheta.toFixed(2) + "</td><td>" + r.peVega.toFixed(2) + "</td><td>" + r.peRho.toFixed(2) + "</td></tr>"
            );
          }).join("");

          var peakGammaRow = chain.rows.reduce(function (best, r) {
            var exposure = r.ceGamma * r.ceOi + r.peGamma * r.peOi;
            var bestExposure = best.ceGamma * best.ceOi + best.peGamma * best.peOi;
            return exposure > bestExposure ? r : best;
          }, chain.rows[0]);
          if (peakGammaRow) {
            NE.setText("gex-callout", "Peak gamma exposure near " + NE.fmtNum(peakGammaRow.strike, 0) +
              " — expect pinning / mean reversion close to this strike into expiry.");
          }

          var dte = daysToExpiry(expiry);
          NE.setText("greeks-dte", dte + " DTE");
          NE.setText("greeks-weekend-decay", spansWeekend(expiry) ? "Yes" : "No");
          NE.setText("greeks-page-sub", "Option Greeks by strike · NIFTY 50 · " + expiryLabel);

          NE.markStatus(true);
          NE.stampRefresh();
        })
        .catch(function () { NE.markStatus(false); });
      }

      load();
      setInterval(load, 2000);
    })
    .catch(function () { NE.markStatus(false); });
})();
