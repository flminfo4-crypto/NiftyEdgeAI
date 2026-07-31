/**
 * Wires options-chain.html to the live backend. Loads once (no auto-refresh) —
 * both to match the rest of the app and because Dhan caps option-chain
 * requests at 1/3s per underlying+expiry.
 */
(function () {
  var NE = window.NE;
  var UNDERLYING = "NIFTY50";
  var currentChain = null;
  var currentRange = 8;

  function renderTable() {
    if (!currentChain) return;
    var rows = currentChain.rows.slice().sort(function (a, b) { return a.strike - b.strike; });
    var atm = rows.reduce(function (best, r) {
      return Math.abs(r.strike - currentChain.spotPrice) < Math.abs(best.strike - currentChain.spotPrice) ? r : best;
    }, rows[0]);

    if (currentRange > 0) {
      var atmIdx = rows.indexOf(atm);
      rows = rows.slice(Math.max(0, atmIdx - currentRange), atmIdx + currentRange + 1);
    }

    var html = rows.map(function (r) {
      var isAtm = r.strike === atm.strike;
      var strikeCell = isAtm
        ? '<td style="background:rgba(245,166,35,.18); text-align:center; font-weight:800; color:var(--amber);">' + NE.fmtNum(r.strike, 0) + " &#9642;</td>"
        : '<td style="background:var(--bg-card-alt); text-align:center; font-weight:700;">' + NE.fmtNum(r.strike, 0) + "</td>";
      var rowClass = isAtm ? ' class="atm"' : "";
      return (
        "<tr" + rowClass + "><td>" + NE.fmtNum(r.ceOi, 0) + "</td><td>" + NE.fmtSigned(r.ceOiChange, 0) + "</td><td>" +
        NE.fmtNum(r.ceVolume, 0) + "</td><td>" + r.ceIv.toFixed(1) + "</td><td>" + r.ceLtp.toFixed(2) + "</td>" +
        strikeCell +
        "<td>" + r.peLtp.toFixed(2) + "</td><td>" + r.peIv.toFixed(1) + "</td><td>" + NE.fmtNum(r.peVolume, 0) +
        "</td><td>" + NE.fmtSigned(r.peOiChange, 0) + "</td><td>" + NE.fmtNum(r.peOi, 0) + "</td></tr>"
      );
    }).join("");
    document.querySelector('[data-live="oc-full-tbody"]').innerHTML = html;
  }

  function showLoadError() {
    var tbody = document.querySelector('[data-live="oc-full-tbody"]');
    if (tbody) {
      tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; color:var(--text-muted);">' +
        "Couldn't load the option chain (backend unreachable or rate-limited) — try reloading in a few seconds.</td></tr>";
    }
    NE.markStatus(false);
  }

  var rangeSelect = document.querySelector('[data-live="oc-strike-range"]');
  if (rangeSelect) {
    rangeSelect.addEventListener("change", function () {
      currentRange = parseInt(rangeSelect.value, 10) || 0;
      renderTable();
    });
  }

  NE.fetchJSON("/market/expiries?underlying=" + UNDERLYING)
    .then(function (data) {
      var expiries = data.expiries || [];
      var select = document.querySelector('[data-live="expiry-select"]');
      if (select && expiries.length) {
        select.innerHTML = expiries.map(function (e) { return "<option>" + e + "</option>"; }).join("");
      }
      var expiry = expiries[0];
      if (!expiry) { showLoadError(); return; }
      var expiryLabel = new Date(expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }).toUpperCase();

      Promise.all([
        NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
        NE.fetchJSON("/market/oi-summary?underlying=" + UNDERLYING + "&expiry=" + expiry),
        NE.fetchJSON("/market/option-chain?underlying=" + UNDERLYING + "&expiry=" + expiry),
      ])
        .then(function (results) {
          var quotes = results[0], oi = results[1], chain = results[2];
          currentChain = chain;

          NE.applyHeaderTicker(quotes);
          NE.applyFooterTicker(quotes);

          NE.setText("pcr-value", oi.pcr.toFixed(2));
          NE.setText("oc-pcr", oi.pcr.toFixed(2));
          var bullish = oi.pcr > 1.1, bearish = oi.pcr < 0.9;
          var label = bullish ? "Bullish" : bearish ? "Bearish" : "Neutral";
          NE.setText("pcr-label", label);
          NE.setText("oc-pcr-label", label);

          NE.setText("oc-max-pain", NE.fmtNum(oi.maxPain, 0));
          NE.setText("oc-max-pain-dist", Math.abs(oi.maxPain - chain.spotPrice).toFixed(0) + " pts from LTP");

          var totalCeOi = chain.rows.reduce(function (s, r) { return s + r.ceOi; }, 0);
          var totalPeOi = chain.rows.reduce(function (s, r) { return s + r.peOi; }, 0);
          NE.setText("oc-total-ce-oi", (totalCeOi / 1e5).toFixed(1) + "L");
          NE.setText("oc-total-pe-oi", (totalPeOi / 1e5).toFixed(1) + "L");

          var atm = chain.rows.reduce(function (best, r) {
            return Math.abs(r.strike - chain.spotPrice) < Math.abs(best.strike - chain.spotPrice) ? r : best;
          }, chain.rows[0]);
          if (atm) {
            var straddle = atm.ceLtp + atm.peLtp;
            NE.setText("straddle-price", "₹" + straddle.toFixed(2));
            NE.setText("oc-straddle-pct", ((straddle / chain.spotPrice) * 100).toFixed(2) + "% of spot");
            NE.setText("atm-iv", (((atm.ceIv + atm.peIv) / 2).toFixed(1)) + "%");
          }

          NE.setText("oc-page-sub", "NIFTY 50 · " + expiryLabel + " Expiry · " + chain.rows.length + " strikes");
          renderTable();
          NE.markStatus(true);
          NE.stampRefresh();
        })
        .catch(showLoadError);
    })
    .catch(showLoadError);
})();
