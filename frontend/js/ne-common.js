/**
 * Shared helpers for wiring any frontend/*.html page to the live NiftyEdgeAI
 * backend — fetch/format/DOM helpers plus the small pieces nearly every page
 * repeats (header ticker, footer ticker, market-open badge, margins/exposure).
 * Page-specific scripts (js/api.js for the Dashboard, js/positions-page.js,
 * etc.) build on top of window.NE.
 */
window.NE = (function () {
  var API_BASE = (window.NIFTYEDGE_API_BASE || "http://localhost:8000/api/v1");
  var FETCH_TIMEOUT_MS = 4000;

  function fetchJSON(path) {
    return fetchJSONLong(path, FETCH_TIMEOUT_MS);
  }

  // For click-triggered heavy endpoints (backtests, ATM analysis) whose first
  // load can legitimately take tens of seconds server-side — the default 4s
  // abort would kill them mid-flight.
  function fetchJSONLong(path, timeoutMs) {
    return requestJSON("GET", path, null, timeoutMs);
  }

  // POST with the same timeout handling and, importantly, the same error
  // unwrapping: a rejected backtest explains itself in `detail`, and that
  // message is the whole difference between "Run failed: 400" and something
  // the user can act on.
  function postJSON(path, body, timeoutMs) {
    return requestJSON("POST", path, body, timeoutMs);
  }

  function requestJSON(method, path, body, timeoutMs) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || 180000);
    var init = { method: method, signal: controller.signal };
    if (body != null) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    return fetch(API_BASE + path, init)
      .then(function (res) {
        clearTimeout(timer);
        if (res.ok) return res.json();
        // Prefer the API's own explanation. Several endpoints reject with
        // guidance the user can act on ("range too large for 1m — max 2
        // days", "narrow the dates or reduce the ladder depth"); a bare
        // status code throws all of that away.
        return res.json().catch(function () { return null; }).then(function (body) {
          throw new Error((body && body.detail) || path + " -> " + res.status);
        });
      })
      .catch(function (err) {
        clearTimeout(timer);
        throw err;
      });
  }

  function setText(key, text) {
    document.querySelectorAll('[data-live="' + key + '"]').forEach(function (el) {
      el.textContent = text;
    });
  }

  function setHTML(key, html) {
    document.querySelectorAll('[data-live="' + key + '"]').forEach(function (el) {
      el.innerHTML = html;
    });
  }

  function setValue(key, value) {
    document.querySelectorAll('[data-live-value="' + key + '"]').forEach(function (el) {
      el.value = value;
    });
  }

  function setClass(el, cls, addIf) {
    if (!el) return;
    el.classList.remove("text-red", "text-green");
    if (addIf != null) el.classList.add(cls);
  }

  function fmtINR(n) {
    return "₹" + Math.round(n).toLocaleString("en-IN");
  }

  function fmtINRSigned(n) {
    var sign = n < 0 ? "-" : "+";
    return sign + "₹" + Math.abs(Math.round(n)).toLocaleString("en-IN");
  }

  function fmtSigned(n, decimals) {
    decimals = decimals == null ? 2 : decimals;
    var sign = n >= 0 ? "+" : "";
    return sign + n.toFixed(decimals);
  }

  function fmtNum(n, decimals) {
    return n.toLocaleString("en-IN", { minimumFractionDigits: decimals || 0, maximumFractionDigits: decimals || 2 });
  }

  function isMarketOpenIST(now) {
    var ist = new Date(now.getTime() + 330 * 60000); // UTC+5:30
    var day = ist.getUTCDay();
    var minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
    return day >= 1 && day <= 5 && minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 30;
  }

  function applyMarketOpenBadges() {
    var open = isMarketOpenIST(new Date());
    ["market-open-badge", "footer-market-open"].forEach(function (key) {
      document.querySelectorAll('[data-live="' + key + '"]').forEach(function (el) {
        var dot = el.querySelector(".dot");
        el.childNodes[el.childNodes.length - 1].textContent = open ? " Market Open" : " Market Closed";
        el.classList.toggle("live", open);
        if (dot) dot.style.background = open ? "" : "var(--text-muted, #8b93a7)";
      });
    });
  }

  var QUOTE_LABELS = { NIFTY50: "Nifty 50", NIFTYBANK: "Nifty Bank", FINNIFTY: "FinNifty", SENSEX: "Sensex", INDIAVIX: "India VIX" };
  var QUOTE_ORDER = ["NIFTY50", "NIFTYBANK", "FINNIFTY", "SENSEX", "INDIAVIX"];

  function applyHeaderTicker(quotes) {
    quotes.forEach(function (q) {
      if (q.symbol === "NIFTY50") {
        setText("nifty-ltp", fmtNum(q.ltp, 2));
        setText("nifty-change", fmtSigned(q.change) + " (" + fmtSigned(q.changePct) + "%) ▲");
      } else if (q.symbol === "INDIAVIX") {
        setText("vix-ltp", q.ltp.toFixed(2));
        setText("vix-change", fmtSigned(q.change) + " (" + fmtSigned(q.changePct) + "%)");
      }
    });
  }

  function applyFooterTicker(quotes) {
    var bySymbol = {};
    quotes.forEach(function (q) { bySymbol[q.symbol] = q; });
    var html = QUOTE_ORDER.filter(function (sym) { return bySymbol[sym]; })
      .map(function (sym) {
        var q = bySymbol[sym];
        var dir = q.change >= 0 ? "up" : "down";
        var arrow = q.change >= 0 ? "&#9650;" : "&#9660;";
        return (
          '<span class="item">' + QUOTE_LABELS[sym] + " <b>" + fmtNum(q.ltp, 2) + '</b> <span class="' + dir + '">' +
          arrow + " " + fmtSigned(q.changePct) + "%</span></span>"
        );
      }).join("");
    setHTML("footer-tickers", html);
  }

  function applyMarginsAndExposure(margins) {
    setText("margin-used", fmtINR(margins.used));
    setText("margin-available", fmtINR(margins.available));
    var total = margins.used + margins.available;
    var pct = total ? (margins.used / total) * 100 : 0;
    setText("risk-exposure-pct", pct.toFixed(0) + "%");
    document.querySelectorAll('[data-live="risk-exposure-meter"]').forEach(function (el) {
      el.style.width = Math.min(100, pct).toFixed(0) + "%";
    });
  }

  function markStatus(ok) {
    var el = document.querySelector('[data-live="api-status"]');
    if (!el) return;
    var dot = el.querySelector(".dot");
    el.childNodes[el.childNodes.length - 1].textContent = ok ? " API Connected (live)" : " Static Data (backend offline)";
    if (dot) dot.style.background = ok ? "" : "var(--amber, #eab308)";
  }

  function stampRefresh() {
    setText("last-refresh", new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
  }

  applyMarketOpenBadges();

  return {
    fetchJSON: fetchJSON,
    fetchJSONLong: fetchJSONLong,
    postJSON: postJSON,
    setText: setText,
    setHTML: setHTML,
    setValue: setValue,
    setClass: setClass,
    fmtINR: fmtINR,
    fmtINRSigned: fmtINRSigned,
    fmtSigned: fmtSigned,
    fmtNum: fmtNum,
    applyHeaderTicker: applyHeaderTicker,
    applyFooterTicker: applyFooterTicker,
    applyMarginsAndExposure: applyMarginsAndExposure,
    applyMarketOpenBadges: applyMarketOpenBadges,
    markStatus: markStatus,
    stampRefresh: stampRefresh,
  };
})();
