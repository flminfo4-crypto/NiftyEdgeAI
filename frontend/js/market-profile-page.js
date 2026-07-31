/**
 * Wires market-profile.html to the live backend (VAH/VAL/POC + previous-session
 * comparison). The TPO letter-grid itself stays static — see the HTML comment
 * next to it for why. Refreshes every 2s.
 */
(function () {
  var NE = window.NE;
  var UNDERLYING = "NIFTY50";

  function overlapPct(vah1, val1, vah2, val2) {
    var overlapHigh = Math.min(vah1, vah2), overlapLow = Math.max(val1, val2);
    var overlap = Math.max(0, overlapHigh - overlapLow);
    var union = Math.max(vah1, vah2) - Math.min(val1, val2);
    return union ? (overlap / union) * 100 : 0;
  }

  function load() {
    Promise.all([
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
    NE.fetchJSON("/market/profile?underlying=" + UNDERLYING),
    NE.fetchJSON("/market/profile/previous-day?underlying=" + UNDERLYING),
    NE.fetchJSON("/market/candles?symbol=" + UNDERLYING + "&interval=5m"),
  ])
    .then(function (results) {
      var quotes = results[0], today = results[1], prev = results[2], candles = results[3];

      NE.applyHeaderTicker(quotes);
      NE.applyFooterTicker(quotes);

      NE.setText("pvv-vah", NE.fmtNum(today.vah, 2));
      NE.setText("pvv-poc", NE.fmtNum(today.poc, 2));
      NE.setText("pvv-val", NE.fmtNum(today.val, 2));
      var vaWidth = today.vah - today.val;
      var vaPct = today.poc ? (vaWidth / today.poc) * 100 : 0;
      NE.setText("mp-va-width", vaWidth.toFixed(0) + " pts (" + vaPct.toFixed(0) + "%)");

      if (candles.length) {
        var highs = candles.map(function (c) { return c.high; });
        var lows = candles.map(function (c) { return c.low; });
        NE.setText("mp-range", NE.fmtNum(Math.min.apply(null, lows), 0) + " – " + NE.fmtNum(Math.max.apply(null, highs), 0));
      }

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

      NE.markStatus(true);
      NE.stampRefresh();
    })
    .catch(function () { NE.markStatus(false); });
  }

  load();
  setInterval(load, 2000);
})();
