/**
 * Wires orders.html to the live backend. Refreshes every 2s. The order book
 * is read-only and safe to wire; the "New Order" form stays static/disabled
 * (it would submit a real order with real money).
 */
(function () {
  var NE = window.NE;

  function load() {
    Promise.all([
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
    NE.fetchJSON("/orders"),
    NE.fetchJSON("/positions/margins"),
  ])
    .then(function (results) {
      var quotes = results[0], orders = results[1], margins = results[2];
      NE.applyHeaderTicker(quotes);
      NE.applyFooterTicker(quotes);
      NE.setText("margin-available", NE.fmtINR(margins.available));

      var open = orders.filter(function (o) { return o.status === "PENDING"; });
      var executed = orders.filter(function (o) { return o.status === "EXECUTED"; });
      var cancelled = orders.filter(function (o) { return o.status === "CANCELLED"; });

      NE.setText("orders-tab-open", "Open (" + open.length + ")");
      NE.setText("orders-tab-executed", "Executed (" + executed.length + ")");
      NE.setText("orders-tab-cancelled", "Cancelled (" + cancelled.length + ")");
      NE.setText("orders-today-count", String(orders.length));

      var fillRate = orders.length ? (executed.length / orders.length) * 100 : 0;
      NE.setText("orders-fill-rate", fillRate.toFixed(1) + "%");

      var slippages = executed
        .filter(function (o) { return o.price != null && o.filledPrice != null; })
        .map(function (o) { return Math.abs(o.filledPrice - o.price); });
      var avgSlippage = slippages.length ? slippages.reduce(function (a, b) { return a + b; }, 0) / slippages.length : 0;
      NE.setText("orders-avg-slippage", avgSlippage.toFixed(2) + " pts");

      var tbody = document.querySelector('[data-live="orders-tbody"]');
      if (tbody) {
        if (orders.length) {
          tbody.innerHTML = orders.map(function (o) {
            var badgeClass = o.side === "BUY" ? "badge-green" : "badge-red";
            var statusBadge = { PENDING: "badge-amber", EXECUTED: "badge-green", REJECTED: "badge-red", CANCELLED: "badge-gray" }[o.status] || "badge-gray";
            var when = new Date(o.placedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
            var price = o.filledPrice != null ? o.filledPrice : o.price;
            var triggerPrice = o.triggerPrice != null ? o.triggerPrice.toFixed(2) : "—";
            var lastCell = o.status === "PENDING" ? '<button class="btn btn-sm">Cancel</button>' : "—";
            return (
              "<tr><td>" + when + "</td><td>" + o.instrument + "</td><td>" + o.orderType + "</td>" +
              '<td><span class="badge ' + badgeClass + '">' + o.side + "</span></td>" +
              "<td>" + o.quantityLots + "</td><td>" + (price != null ? price.toFixed(2) : "—") + "</td>" +
              "<td>" + triggerPrice + "</td>" +
              '<td><span class="badge ' + statusBadge + '">' + o.status + "</span></td>" +
              "<td>" + lastCell + "</td></tr>"
            );
          }).join("");
        } else {
          tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:var(--text-muted);">No orders placed yet</td></tr>';
        }
      }

      NE.markStatus(true);
      NE.stampRefresh();
    })
    .catch(function () {
      NE.markStatus(false);
    });
  }

  load();
  setInterval(load, 2000);
})();
