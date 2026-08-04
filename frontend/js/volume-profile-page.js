/**
 * Wires volume-profile.html to the live backend. Unlike market-profile.html's
 * TPO grid (a time-weighted proxy), this is a real volume-by-price histogram —
 * NIFTY50's candles carry a real `volume` field. Refreshes every 2s.
 */
(function () {
  var NE = window.NE;
  var UNDERLYING = "NIFTY50";
  var DISPLAY_WINDOW = 15; // buckets each side of POC
  var lastLtp = null;

  function loadComposite(ltp) {
    var canvas = document.querySelector('[data-live="vp-composite-canvas"]');
    if (!canvas) return;
    NE.fetchJSONLong("/market/volume-profile/composite?underlying=" + UNDERLYING + "&sessions=5", 15000)
      .then(function (sessions) {
        NE.renderCompositeChart(canvas, sessions, { mode: "volume", ltp: ltp, tick: 6.0 });
      })
      .catch(function () {});
  }

  function load() {
    Promise.all([
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
    NE.fetchJSON("/market/volume-profile?underlying=" + UNDERLYING),
  ])
    .then(function (results) {
      var quotes = results[0], profile = results[1];
      NE.applyHeaderTicker(quotes);
      NE.applyFooterTicker(quotes);

      var spot = quotes.filter(function (q) { return q.symbol === "NIFTY50"; })[0];
      var ltp = spot ? spot.ltp : null;

      var rows = profile.rows;
      var pocIdx = rows.reduce(function (bi, r, i) { return r.volume > rows[bi].volume ? i : bi; }, 0);
      var windowRows = rows.slice(Math.max(0, pocIdx - DISPLAY_WINDOW), pocIdx + DISPLAY_WINDOW + 1);
      var maxVol = Math.max.apply(null, windowRows.map(function (r) { return r.volume; })) || 1;

      var html = windowRows.slice().reverse().map(function (r) {
        var pct = ((r.volume / maxVol) * 100).toFixed(1);
        var isPoc = r.price === profile.poc;
        var isVah = r.price === profile.vah;
        var isVal = r.price === profile.val;
        var isLtp = ltp != null && Math.abs(r.price - ltp) < 3;
        var color = isPoc ? "var(--amber)" : (r.price <= profile.vah && r.price >= profile.val) ? "var(--blue)" : "var(--border)";
        var label = NE.fmtNum(r.price, 0);
        var priceClass = "";
        if (isPoc) { label += " ▣POC"; priceClass = "text-amber bold"; }
        else if (isVah) { label += " ▣VAH"; priceClass = "text-red"; }
        else if (isVal) { label += " ▣VAL"; priceClass = "text-red"; }
        else if (isLtp) { label += " ▣LTP"; priceClass = "text-green bold"; }
        return (
          '<div class="row"><span class="price ' + priceClass + '">' + label + '</span>' +
          '<div class="track"><div class="fill" style="width:' + pct + '%; background:' + color + ';"></div></div>' +
          '<span style="width:44px;font-size:10.5px;color:var(--text-muted);">' + (r.volume / 1e3).toFixed(0) + "K</span></div>"
        );
      }).join("");
      document.querySelector('[data-live="vp-rows"]').innerHTML = html;

      NE.setText("vp-total-volume", (profile.totalVolume / 1e7).toFixed(2) + " Cr");
      NE.setText("vp-poc-price", NE.fmtNum(profile.poc, 2));
      var pocRow = rows.filter(function (r) { return r.price === profile.poc; })[0];
      NE.setText("vp-poc-volume", pocRow ? (pocRow.volume / 1e5).toFixed(2) + " L" : "—");
      NE.setText("pvv-vah", NE.fmtNum(profile.vah, 2));
      NE.setText("pvv-val", NE.fmtNum(profile.val, 2));

      var vaVolume = rows.filter(function (r) { return r.price >= profile.val && r.price <= profile.vah; })
        .reduce(function (s, r) { return s + r.volume; }, 0);
      NE.setText("vp-va-pct", ((vaVolume / profile.totalVolume) * 100).toFixed(1) + "%");

      var byVolDesc = rows.slice().sort(function (a, b) { return b.volume - a.volume; });
      var byVolAsc = rows.slice().sort(function (a, b) { return a.volume - b.volume; });

      document.querySelector('[data-live="vp-hvn-tbody"]').innerHTML = byVolDesc.slice(0, 5).map(function (r) {
        var role = ltp != null && r.price <= ltp ? "Support" : "Resistance";
        var badge = role === "Support" ? "badge-blue" : "badge-red";
        return "<tr><td>" + NE.fmtNum(r.price, 0) + "</td><td>" + (r.volume / 1e3).toFixed(0) + 'K</td><td><span class="badge ' + badge + '">' + role + "</span></td></tr>";
      }).join("");

      document.querySelector('[data-live="vp-lvn-tbody"]').innerHTML = byVolAsc.slice(0, 3).map(function (r) {
        return "<tr><td>" + NE.fmtNum(r.price, 0) + "</td><td>" + (r.volume / 1e3).toFixed(0) + 'K</td><td><span class="badge badge-gray">Air Gap</span></td></tr>';
      }).join("");

      NE.setText("vp-page-sub", "Volume-by-price distribution · NIFTY 50 · Session");
      lastLtp = ltp;
      NE.markStatus(true);
      NE.stampRefresh();
    })
    .catch(function () { NE.markStatus(false); });
  }

  load();
  setInterval(load, 2000);
  loadComposite(null);
  setInterval(function () { loadComposite(lastLtp); }, 30000);
})();
