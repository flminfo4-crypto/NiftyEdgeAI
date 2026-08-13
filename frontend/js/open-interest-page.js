/**
 * Wires open-interest.html to the live backend. Refreshes on the shared REFRESH_MS cadence — all the
 * calls below share one cached/serialized option-chain fetch per expiry on
 * the backend side (see app/services/market_data.py), staying under Dhan's
 * 1 request / 3s cap.
 */
(function () {
  // Live-refresh cadence. Kept deliberately slow: every open tab is its own
  // polling stream against the broker's rate limit, and Dhan answers a hot
  // one with 429 plus a warning about blocking the account (see the cache
  // notes in backend/app/services/market_data.py).
  var REFRESH_MS = 30000;

  var NE = window.NE;
  var UNDERLYING = "NIFTY50";
  var STRIKE_RANGE = 8;

  function wallRows(rows, key, colorVar, textClass) {
    var max = Math.max.apply(null, rows.map(function (r) { return r[key]; })) || 1;
    return rows.map(function (r) {
      var pct = Math.max(4, (r[key] / max) * 100).toFixed(0);
      return (
        '<div class="wall-row"><div class="wall-strike">' + NE.fmtNum(r.strike, 0) + '</div>' +
        '<div class="wall-bar-track"><div class="wall-bar" style="width:' + pct + '%; background:var(' + colorVar + ');"></div></div>' +
        '<div class="wall-val ' + textClass + '">' + (r[key] / 1e5).toFixed(1) + "L</div></div>"
      );
    }).join("");
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
      NE.setText("oi-buildup-note-expiry", expiryLabel);

      function load() {
        Promise.all([
        NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
        NE.fetchJSON("/market/oi-summary?underlying=" + UNDERLYING + "&expiry=" + expiry),
        NE.fetchJSON("/market/option-chain?underlying=" + UNDERLYING + "&expiry=" + expiry),
        NE.fetchJSON("/market/oi-buildup?underlying=" + UNDERLYING + "&expiry=" + expiry),
      ])
        .then(function (results) {
          var quotes = results[0], oi = results[1], chain = results[2], buildup = results[3];

          NE.applyHeaderTicker(quotes);
          NE.applyFooterTicker(quotes);

          NE.setText("pcr-value", oi.pcr.toFixed(2));
          NE.setText("oc-pcr", oi.pcr.toFixed(2));
          var bullish = oi.pcr > 1.1, bearish = oi.pcr < 0.9;
          var label = bullish ? "Bullish" : bearish ? "Bearish" : "Neutral";
          NE.setText("pcr-label", label);
          NE.setText("oc-pcr-label", label);

          NE.setText("oc-max-pain", NE.fmtNum(oi.maxPain, 0));
          NE.setText("oc-max-pain-2", NE.fmtNum(oi.maxPain, 0));
          NE.setText("oc-max-pain-dist", "Spot " + Math.abs(oi.maxPain - chain.spotPrice).toFixed(0) + " pts away");

          var totalCeOi = chain.rows.reduce(function (s, r) { return s + r.ceOi; }, 0);
          var totalPeOi = chain.rows.reduce(function (s, r) { return s + r.peOi; }, 0);
          NE.setText("oc-total-ce-oi", (totalCeOi / 1e5).toFixed(1) + "L");
          NE.setText("oc-total-pe-oi", (totalPeOi / 1e5).toFixed(1) + "L");

          var sorted = chain.rows.slice().sort(function (a, b) { return a.strike - b.strike; });
          var atm = sorted.reduce(function (best, r) {
            return Math.abs(r.strike - chain.spotPrice) < Math.abs(best.strike - chain.spotPrice) ? r : best;
          }, sorted[0]);
          var atmIdx = sorted.indexOf(atm);
          var nearRows = sorted.slice(Math.max(0, atmIdx - STRIKE_RANGE), atmIdx + STRIKE_RANGE + 1);

          document.querySelector('[data-live="oi-by-strike-ce"]').innerHTML = wallRows(nearRows, "ceOi", "--red", "text-red");
          document.querySelector('[data-live="oi-by-strike-pe"]').innerHTML = wallRows(nearRows, "peOi", "--green", "text-green");

          var buildupByStrike = {};
          buildup.forEach(function (b) { buildupByStrike[b.strike] = b; });
          var hasBaseline = buildup.some(function (b) { return b.ceSignal !== "Insufficient Data"; });
          var noteEl = document.querySelector('[data-live="oi-buildup-note"]');
          if (noteEl) noteEl.style.display = hasBaseline ? "none" : "";

          var signalBadge = {
            "Long Buildup": "badge-green", "Short Buildup": "badge-red",
            "Short Covering": "badge-blue", "Long Unwinding": "badge-amber",
            "Neutral": "badge-gray", "Insufficient Data": "badge-gray",
          };
          document.querySelector('[data-live="oi-buildup-tbody"]').innerHTML = nearRows.map(function (r) {
            var b = buildupByStrike[r.strike];
            if (!b) return "";
            var rowClass = r.strike === atm.strike ? ' class="atm"' : "";
            return (
              "<tr" + rowClass + "><td>" + NE.fmtNum(r.strike, 0) + "</td>" +
              "<td>" + NE.fmtSigned(b.ceOiChange, 0) + "</td><td>" + NE.fmtSigned(b.ceLtpChangePct, 1) + "%</td>" +
              '<td><span class="badge ' + (signalBadge[b.ceSignal] || "badge-gray") + '">' + b.ceSignal + "</span></td>" +
              "<td>" + NE.fmtSigned(b.peOiChange, 0) + "</td><td>" + NE.fmtSigned(b.peLtpChangePct, 1) + "%</td>" +
              '<td><span class="badge ' + (signalBadge[b.peSignal] || "badge-gray") + '">' + b.peSignal + "</span></td></tr>"
            );
          }).join("");

          NE.setText("oi-page-sub", "OI buildup classification & strike-wise distribution · NIFTY 50 · " + expiryLabel);
          NE.markStatus(true);
          NE.stampRefresh();
        })
        .catch(function () { NE.markStatus(false); });
      }

      load();
      setInterval(load, REFRESH_MS);
    })
    .catch(function () { NE.markStatus(false); });
})();
