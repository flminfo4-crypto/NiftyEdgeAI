/**
 * Wires market-profile.html to the live TPO profile (GET /market/tpo-profile).
 *
 * The letter grid, day type, Initial Balance, single prints and poor
 * high/low are all rendered from the real intraday session — none of it is
 * hardcoded any more. Session (today / previous) and bracket size
 * (15/30/60 min) come from the two selectors above the profile.
 *
 * Refreshes every 2s like the rest of the app; the profile endpoint reads
 * cached candles server-side so the poll is cheap.
 */
(function () {
  var NE = window.NE;
  var UNDERLYING = "NIFTY50";

  function fmt0(n) { return NE.fmtNum(n, 0); }
  function fmt2(n) { return NE.fmtNum(n, 2); }

  function badge(el, text, cls) {
    if (!el) return;
    el.textContent = text;
    el.className = "badge " + cls;
  }

  var DAY_TYPE_CLASS = {
    "Trend Day": "badge-green",
    "Double-Distribution Trend Day": "badge-green",
    "Normal Variation Day": "badge-amber",
    "Normal Day": "badge-blue",
    "Neutral Day": "badge-gray",
    "Trading Range Day": "badge-gray",
  };

  function renderGrid(p) {
    var wrap = document.querySelector('[data-live="mp-tpo-grid"]');
    if (!wrap) return;
    if (!p.rows || !p.rows.length) {
      wrap.innerHTML = '<div style="color:var(--text-muted);">No profile rows for this session.</div>';
      return;
    }
    var maxCount = Math.max.apply(null, p.rows.map(function (r) { return r.count; }));
    wrap.innerHTML = p.rows.map(function (r) {
      var tags = [];
      if (r.price === p.poc) tags.push('<span class="badge badge-blue" style="margin-left:6px;">POC</span>');
      if (r.price === p.vah) tags.push('<span class="badge badge-red" style="margin-left:6px;">VAH</span>');
      if (r.price === p.val) tags.push('<span class="badge badge-red" style="margin-left:6px;">VAL</span>');
      if (r.price === p.ibHigh) tags.push('<span class="badge badge-blue" style="margin-left:6px;">IB High</span>');
      if (r.price === p.ibLow) tags.push('<span class="badge badge-blue" style="margin-left:6px;">IB Low</span>');
      if (r.isSinglePrint) tags.push('<span class="badge badge-amber" style="margin-left:6px;">single</span>');

      // shade the value area so the 70% zone is readable at a glance
      var bg = r.inValueArea ? "background:rgba(245,166,35,.08);" : "";
      var letterColor = r.isPoc ? "color:var(--blue); font-weight:700;"
        : r.isSinglePrint ? "color:var(--amber);" : "";
      // proportional bar so the distribution's shape is visible without a chart
      var barW = Math.round((r.count / maxCount) * 100);
      return (
        '<div class="flex items-center" style="gap:10px;' + bg + '">' +
          '<span style="width:70px; color:var(--text-muted); text-align:right;">' + fmt0(r.price) + "</span>" +
          '<span style="display:inline-block; width:56px; height:8px; background:linear-gradient(90deg,var(--blue) ' +
            barW + "%, transparent " + barW + '%); border-radius:2px; opacity:.45;"></span>' +
          '<span style="' + letterColor + '">' + r.letters + "</span>" +
          tags.join("") +
        "</div>"
      );
    }).join("");
  }

  function renderStats(p) {
    NE.setText("mp-subtitle",
      "TPO distribution · NIFTY 50 · " + p.bracketMinutes + "-min brackets · " +
      p.sessionDate + " · " + p.brackets + " periods");

    badge(document.querySelector('[data-live="mp-day-type"]'),
          p.dayType, DAY_TYPE_CLASS[p.dayType] || "badge-gray");

    NE.setText("mp-brackets", p.brackets + " × " + p.bracketMinutes + " min");
    NE.setText("mp-range", fmt0(p.dayLow) + " – " + fmt0(p.dayHigh));
    NE.setText("pvv-vah", fmt2(p.vah));
    NE.setText("pvv-poc", fmt2(p.poc));
    NE.setText("pvv-val", fmt2(p.val));
    var vaWidth = p.vah - p.val;
    NE.setText("mp-va-width", vaWidth.toFixed(0) + " pts (" +
      (p.dayRange ? (vaWidth / p.dayRange * 100).toFixed(0) : "0") + "% of range)");
    NE.setText("mp-ib", fmt0(p.ibLow) + " – " + fmt0(p.ibHigh));
    NE.setText("mp-ib-range", p.ibRange.toFixed(0) + " pts");

    var extEl = document.querySelector('[data-live="mp-range-ext"]');
    if (extEl) {
      var up = p.rangeExtensionUp, dn = p.rangeExtensionDown;
      extEl.classList.remove("text-green", "text-red");
      if (up > 0 && dn > 0) extEl.textContent = "+" + up.toFixed(0) + " up / -" + dn.toFixed(0) + " down";
      else if (up > 0) { extEl.textContent = "+" + up.toFixed(0) + " pts (up)"; extEl.classList.add("text-green"); }
      else if (dn > 0) { extEl.textContent = "-" + dn.toFixed(0) + " pts (down)"; extEl.classList.add("text-red"); }
      else extEl.textContent = "none (contained in IB)";
    }

    var openEl = document.querySelector('[data-live="mp-open-type"]');
    if (openEl) {
      openEl.textContent = p.openType;
      openEl.title = p.openReasoning || "";
    }
    NE.setText("mp-buying-tail", p.buyingTail && p.buyingTail.length
      ? p.buyingTail.map(fmt0).join(", ") : "none");
    NE.setText("mp-selling-tail", p.sellingTail && p.sellingTail.length
      ? p.sellingTail.map(fmt0).join(", ") : "none");

    NE.setText("mp-single-prints", p.singlePrints && p.singlePrints.length
      ? p.singlePrints.map(fmt0).join(", ") : "none");
    badge(document.querySelector('[data-live="mp-poor-high"]'),
          p.poorHigh ? fmt0(p.dayHigh) : "None", p.poorHigh ? "badge-red" : "badge-gray");
    badge(document.querySelector('[data-live="mp-poor-low"]'),
          p.poorLow ? fmt0(p.dayLow) : "None", p.poorLow ? "badge-red" : "badge-gray");
    NE.setText("mp-open-close", fmt0(p.openPrice) + " → " + fmt0(p.closePrice));

    var prev = p.previousSession;
    if (prev) {
      NE.setText("mp-prev-vah", fmt2(prev.prevVah));
      NE.setText("mp-prev-poc", fmt2(prev.prevPoc));
      NE.setText("mp-prev-val", fmt2(prev.prevVal));
      NE.setText("mp-va-overlap", prev.overlapPct.toFixed(0) + "%");
      var migEl = document.querySelector('[data-live="mp-poc-migration"]');
      if (migEl) {
        migEl.classList.remove("text-red", "text-green");
        migEl.classList.add(prev.pocMigration >= 0 ? "text-green" : "text-red");
        migEl.textContent = NE.fmtSigned(prev.pocMigration, 2) + " pts (" +
          (prev.pocMigration >= 0 ? "up" : "down") + ")";
      }
      NE.setText("mp-va-note",
        prev.relationship.replace(/_/g, " ").toLowerCase() +
        " vs " + prev.prevSessionDate + " — " + prev.bias.toLowerCase() +
        "; value areas overlap " + prev.overlapPct.toFixed(0) + "%.");
    } else {
      NE.setText("mp-va-note", "Previous session profile unavailable for comparison.");
    }

    // day-type reasoning on hover, so the label is auditable rather than opaque
    var dtEl = document.querySelector('[data-live="mp-day-type"]');
    if (dtEl && p.reasoning) dtEl.title = p.reasoning;
  }

  function load() {
    var sessionEl = document.getElementById("mp-session");
    var bracketEl = document.getElementById("mp-bracket");
    var previous = sessionEl && sessionEl.value === "previous";
    var bracket = bracketEl ? bracketEl.value : "30";

    Promise.all([
      NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
      NE.fetchJSON("/market/tpo-profile?underlying=" + UNDERLYING +
                   "&previous=" + previous + "&bracket=" + bracket),
    ])
      .then(function (r) {
        NE.applyHeaderTicker(r[0]);
        NE.applyFooterTicker(r[0]);
        renderGrid(r[1]);
        renderStats(r[1]);
        NE.markStatus(true);
        NE.stampRefresh();
      })
      .catch(function () { NE.markStatus(false); });
  }

  // Virgin POCs need a multi-session fetch server-side, so they load on their
  // own slow cadence rather than on the 2s profile poll.
  function renderVirginPocs(d) {
    var el = document.querySelector('[data-live="mp-virgin-pocs"]');
    if (!el) return;
    var list = d && d.virginPocs;
    if (!list || !list.length) {
      el.innerHTML = '<span style="color:var(--text-muted);">None untested in the last ' +
        ((d && d.sessionsScanned) || 0) + " sessions.</span>";
      return;
    }
    el.innerHTML = list.map(function (v) {
      var dir = v.above ? "text-green" : "text-red";
      var arrow = v.above ? "▲" : "▼";
      return (
        '<div class="flex justify-between" style="padding:4px 0; border-bottom:1px solid var(--border-soft);">' +
          '<span class="mono">' + fmt0(v.poc) + "</span>" +
          '<span style="color:var(--text-muted); font-size:11px;">' + v.date +
            " · " + v.sessionsAgo + "d ago</span>" +
          '<span class="' + dir + ' mono">' + arrow + " " +
            Math.abs(Math.round(v.distance)).toLocaleString("en-IN") + "</span>" +
        "</div>"
      );
    }).join("");
  }

  function loadVirginPocs() {
    NE.fetchJSONLong("/market/virgin-pocs?underlying=" + UNDERLYING, 60000)
      .then(renderVirginPocs)
      .catch(function () {
        var el = document.querySelector('[data-live="mp-virgin-pocs"]');
        if (el) el.innerHTML = '<span style="color:var(--text-muted);">Unavailable.</span>';
      });
  }

  ["mp-session", "mp-bracket"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", load);
  });

  load();
  setInterval(load, 2000);
  loadVirginPocs();
  setInterval(loadVirginPocs, 300000); // 5 min — prior-session POCs barely move
})();
