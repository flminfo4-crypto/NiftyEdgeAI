/**
 * Wires data-analysis.html to GET /market/atm-analysis. The header/footer
 * ticker refreshes on the shared REFRESH_MS cadence like the rest of the app, but the grid itself is
 * click-triggered only — one load can fan out into several Dhan option-
 * contract fetches server-side, so it must never sit on a polling timer
 * (see the 429 history documented in backend/app/services/market_data.py).
 */
(function () {
  // Live-refresh cadence. Kept deliberately slow: every open tab is its own
  // polling stream against the broker's rate limit, and Dhan answers a hot
  // one with 429 plus a warning about blocking the account (see the cache
  // notes in backend/app/services/market_data.py).
  var REFRESH_MS = 30000;

  var NE = window.NE;

  // -- ticker/footer refresh, every REFRESH_MS ---------------------------

  function loadTicker() {
    NE.fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX")
      .then(function (quotes) {
        NE.applyHeaderTicker(quotes);
        NE.applyFooterTicker(quotes);
        NE.markStatus(true);
        NE.stampRefresh();
      })
      .catch(function () { NE.markStatus(false); });
  }
  loadTicker();
  setInterval(loadTicker, REFRESH_MS);

  // -- filters ---------------------------------------------------------------

  function defaultDates() {
    // default: the most recent weekday (markets closed on weekends)
    var d = new Date();
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
    var iso = d.toISOString().slice(0, 10);
    var fromEl = document.getElementById("da-from");
    var toEl = document.getElementById("da-to");
    if (fromEl && !fromEl.value) fromEl.value = iso;
    if (toEl && !toEl.value) toEl.value = iso;
  }
  defaultDates();

  function showError(msg) {
    var el = document.querySelector('[data-live="da-error"]');
    if (!el) return;
    if (msg) { el.textContent = msg; el.style.display = ""; }
    else { el.style.display = "none"; }
  }

  function setLoading(loading) {
    ["da-load-btn", "da-load-btn-top"].forEach(function (key) {
      document.querySelectorAll('[data-live="' + key + '"]').forEach(function (btn) {
        btn.disabled = loading;
        btn.textContent = loading ? "Loading…" : "Load Data";
      });
    });
  }

  // -- grid ------------------------------------------------------------------

  function fmt(n) {
    return n == null ? "—" : Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtNum(n, dp) {
    return n == null ? "—" : Number(n).toFixed(dp == null ? 2 : dp);
  }

  // Volume/OI run into the millions — compact to Indian Cr/L so the columns
  // stay readable at a glance instead of showing 7-8 raw digits.
  function fmtQty(n) {
    if (n == null) return "—";
    var v = Number(n), abs = Math.abs(v);
    if (abs >= 1e7) return (v / 1e7).toFixed(2) + " Cr";
    if (abs >= 1e5) return (v / 1e5).toFixed(2) + " L";
    return Math.round(v).toLocaleString("en-IN");
  }

  function fmtQtySigned(n) {
    if (n == null) return '<span style="color:var(--text-muted);">—</span>';
    var cls = n >= 0 ? "text-green" : "text-red";
    return '<span class="' + cls + '">' + (n >= 0 ? "+" : "−") + fmtQty(Math.abs(n)) + "</span>";
  }

  function renderGrid(d) {
    var tbody = document.querySelector('[data-live="da-grid-tbody"]');
    if (!tbody) return;
    if (!d.rows || !d.rows.length) {
      tbody.innerHTML = '<tr><td colspan="28" style="text-align:center; color:var(--text-muted);">No candles for this range — market closed or data unavailable.</td></tr>';
      return;
    }
    var prevAtm = null;
    tbody.innerHTML = d.rows.map(function (r) {
      var atmRolled = prevAtm !== null && r.atmStrike !== prevAtm;
      prevAtm = r.atmStrike;
      var atmCell = '<td class="mono"' + (atmRolled ? ' style="color:var(--blue); font-weight:700;"' : "") + ">" + Number(r.atmStrike).toLocaleString("en-IN") + "</td>";
      return (
        "<tr>" +
        '<td style="white-space:nowrap;">' + r.time + "</td>" +
        '<td class="mono">' + fmt(r.spotClose) + "</td>" +
        '<td class="mono" style="white-space:nowrap;">' + fmt(r.spotHigh) + " / " + fmt(r.spotLow) + "</td>" +
        atmCell +
        '<td class="mono col-price text-green">' + fmt(r.ceHigh) + "</td>" +
        '<td class="mono col-price text-red">' + fmt(r.ceLow) + "</td>" +
        '<td class="mono col-price">' + fmt(r.ceClose) + "</td>" +
        '<td class="mono col-price text-green">' + fmt(r.peHigh) + "</td>" +
        '<td class="mono col-price text-red">' + fmt(r.peLow) + "</td>" +
        '<td class="mono col-price">' + fmt(r.peClose) + "</td>" +
        '<td class="mono col-price">' + fmt(r.straddle) + "</td>" +
        '<td class="mono col-voloi">' + fmtQty(r.ceVolume) + "</td>" +
        '<td class="mono col-voloi">' + fmtQty(r.ceOi) + "</td>" +
        '<td class="mono col-voloi">' + fmtQtySigned(r.ceOiChange) + "</td>" +
        '<td class="mono col-voloi">' + fmtQty(r.peVolume) + "</td>" +
        '<td class="mono col-voloi">' + fmtQty(r.peOi) + "</td>" +
        '<td class="mono col-voloi">' + fmtQtySigned(r.peOiChange) + "</td>" +
        '<td class="mono col-greeks">' + (r.ceIv == null ? "—" : fmtNum(r.ceIv) + "%") + "</td>" +
        '<td class="mono col-greeks">' + fmtNum(r.ceDelta, 3) + "</td>" +
        '<td class="mono col-greeks">' + (r.ceGamma == null ? "—" : Number(r.ceGamma).toFixed(5)) + "</td>" +
        '<td class="mono col-greeks text-red">' + fmtNum(r.ceTheta) + "</td>" +
        '<td class="mono col-greeks">' + fmtNum(r.ceVega) + "</td>" +
        '<td class="mono col-greeks">' + (r.peIv == null ? "—" : fmtNum(r.peIv) + "%") + "</td>" +
        '<td class="mono col-greeks">' + fmtNum(r.peDelta, 3) + "</td>" +
        '<td class="mono col-greeks">' + (r.peGamma == null ? "—" : Number(r.peGamma).toFixed(5)) + "</td>" +
        '<td class="mono col-greeks text-red">' + fmtNum(r.peTheta) + "</td>" +
        '<td class="mono col-greeks">' + fmtNum(r.peVega) + "</td>" +
        '<td style="font-size:11px; color:var(--text-secondary); min-width:220px;">' + r.reason + "</td>" +
        "</tr>"
      );
    }).join("");
  }

  // -- column group toggles -------------------------------------------------

  function syncColumns() {
    var table = document.getElementById("da-grid-table");
    if (!table) return;
    [["da-col-price", "hide-price"], ["da-col-voloi", "hide-voloi"], ["da-col-greeks", "hide-greeks"]]
      .forEach(function (pair) {
        var box = document.getElementById(pair[0]);
        table.classList.toggle(pair[1], !(box && box.checked));
      });
  }
  ["da-col-price", "da-col-voloi", "da-col-greeks"].forEach(function (id) {
    var box = document.getElementById(id);
    if (box) box.addEventListener("change", syncColumns);
  });
  syncColumns();

  function loadGrid() {
    var underlying = document.getElementById("da-underlying").value;
    var from = document.getElementById("da-from").value;
    var to = document.getElementById("da-to").value;
    var expiry = document.getElementById("da-expiry").value;
    var interval = document.getElementById("da-interval").value;

    if (!from || !to) { showError("Pick a From and To date."); return; }
    if (from > to) { showError("From date must be on or before To date."); return; }
    showError(null);
    setLoading(true);

    NE.fetchJSONLong("/market/atm-analysis?underlying=" + underlying + "&from=" + from + "&to=" + to +
        "&expiry=" + expiry + "&interval=" + interval)
      .then(function (d) {
        renderGrid(d);
        NE.setText("da-grid-sub", d.rows.length + " rows · " + interval + " · rolling ATM (step " + d.strikeStep + ")");
        var meta = "Expiry used: " + (d.expiryDate || "—") + " (" + d.expiryKind + ") · Source: " +
          (d.source === "broker" ? "live broker data" : "mock data — no live broker connected");
        if (d.note) meta += " · " + d.note;
        NE.setText("da-meta", meta);
      })
      .catch(function (err) {
        showError("Load failed: " + err.message);
      })
      .then(function () { setLoading(false); });
  }

  ["da-load-btn", "da-load-btn-top"].forEach(function (key) {
    document.querySelectorAll('[data-live="' + key + '"]').forEach(function (btn) {
      btn.addEventListener("click", loadGrid);
    });
  });
})();
