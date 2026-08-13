/**
 * Wires settings.html to the live backend: real broker connection info (masked
 * client ID, never the access token) and the risk limits actually enforced by
 * risk_engine.py. Data & Refresh / Notification toggles stay static — they're
 * preferences with no persistence backend yet. Refreshes on the shared REFRESH_MS cadence.
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
    NE.fetchJSON("/system/broker-info"),
    NE.fetchJSON("/system/risk-limits"),
  ])
    .then(function (results) {
      var quotes = results[0], broker = results[1], risk = results[2];
      NE.applyHeaderTicker(quotes);
      NE.applyFooterTicker(quotes);

      NE.setText("broker-label", broker.brokerLabel);
      NE.setText("broker-client-id", broker.clientIdMasked || "—");
      NE.setValue("broker-client-id-input", broker.clientIdMasked || "");

      var badge = document.querySelector('[data-live="broker-connection-badge"]');
      if (badge) {
        badge.textContent = broker.connected ? "Connected" : "Disconnected";
        badge.classList.remove("badge-green", "badge-red");
        badge.classList.add(broker.connected ? "badge-green" : "badge-red");
      }
      NE.setText("broker-last-sync", new Date(broker.lastSyncAt).toLocaleTimeString("en-IN", {
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      }));

      NE.setValue("risk-max-daily-loss", NE.fmtINR(risk.maxDailyLoss));
      NE.setValue("risk-max-lots", risk.maxLotsPerOrder + " lots");
      NE.setValue("risk-max-exposure", risk.maxExposurePct.toFixed(0) + "%");

      NE.markStatus(true);
      NE.stampRefresh();
    })
    .catch(function () { NE.markStatus(false); });
  }

  load();
  setInterval(load, REFRESH_MS);
})();
