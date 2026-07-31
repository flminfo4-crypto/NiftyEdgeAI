/**
 * Wires the Dashboard (index.html) to the live NiftyEdgeAI backend
 * (backend/, run with `uvicorn app.main:app`) instead of the hand-authored
 * dummy numbers baked into the markup.
 *
 * Scope: Dashboard only, for this pass — see docs task list ("Wire Dashboard
 * frontend to live mock backend"). Every other page still shows its static
 * mock data. If the backend isn't running (the common case when just opening
 * these files from disk), every fetch fails fast and the page silently keeps
 * its original static numbers — nothing breaks, nothing looks broken.
 */
(function () {
  // Only do anything on pages that actually have live-bindable elements.
  if (!document.querySelector("[data-live]")) return;

  // Candidate backends, tried in order: an explicit override, then a locally
  // running `run.bat` backend, then the deployed server.
  var API_BASES = window.NIFTYEDGE_API_BASES || [
    window.NIFTYEDGE_API_BASE,
    "http://localhost:8000/api/v1",
    "http://52.66.168.49:8000/api/v1",
  ].filter(Boolean);
  var API_BASE = null;
  var FETCH_TIMEOUT_MS = 2500;

  function fetchWithTimeout(url) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, FETCH_TIMEOUT_MS);
    return fetch(url, { signal: controller.signal }).then(
      function (res) { clearTimeout(timer); return res; },
      function (err) { clearTimeout(timer); throw err; }
    );
  }

  function pickBase(i) {
    i = i || 0;
    if (API_BASE) return Promise.resolve(API_BASE);
    if (i >= API_BASES.length) return Promise.reject(new Error("no backend reachable"));
    return fetchWithTimeout(API_BASES[i] + "/system/status")
      .then(function (res) {
        if (!res.ok) throw new Error("bad status");
        API_BASE = API_BASES[i];
        window.NIFTYEDGE_ACTIVE_API_BASE = API_BASE;
        return API_BASE;
      })
      .catch(function () { return pickBase(i + 1); });
  }

  function fetchJSON(path) {
    return pickBase().then(function (base) {
      return fetchWithTimeout(base + path).then(function (res) {
        if (!res.ok) throw new Error(path + " -> " + res.status);
        return res.json();
      });
    });
  }

  function setText(key, text) {
    document.querySelectorAll('[data-live="' + key + '"]').forEach(function (el) {
      el.textContent = text;
    });
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

  function applyQuotes(quotes) {
    quotes.forEach(function (q) {
      if (q.symbol === "NIFTY50") {
        setText("nifty-ltp", q.ltp.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
        setText("nifty-change", fmtSigned(q.change) + " (" + fmtSigned(q.changePct) + "%) ▲");
      } else if (q.symbol === "INDIAVIX") {
        setText("vix-ltp", q.ltp.toFixed(2));
        setText("vix-change", fmtSigned(q.change) + " (" + fmtSigned(q.changePct) + "%)");
      }
    });
  }

  function applyBias(bias) {
    setText("bias-headline", bias.headline);
    setText("bias-subtext", bias.subtext);
    setText("bias-confidence", bias.confidencePct + "%");
    bias.factors.forEach(function (f) {
      var el = document.querySelector('[data-live="bias-factor-' + f.key + '"]');
      if (!el) return;
      el.textContent = f.value;
      el.classList.remove("text-red", "text-green");
      el.classList.add(f.direction === "BULLISH" ? "text-green" : f.direction === "BEARISH" ? "text-red" : "");
    });
  }

  function applySignals(active) {
    setText("signal-primary-action", active.primary.action);
    setText("signal-primary-confidence", active.primary.confidencePct + "%");
    setText("signal-alt-action", active.alternative.action);
    setText("signal-alt-confidence", active.alternative.confidencePct + "%");
    setText("signal-alt-entry", active.alternative.entryZone);

    var primaryColor = active.bias.direction === "BEARISH" ? "#ef4444" : "#22c55e";
    var ring = document.querySelector('[data-live="signal-primary-ring"]');
    if (ring) ring.style.background = "conic-gradient(" + primaryColor + " " + active.primary.confidencePct + "%, var(--border-soft) 0)";
    var altRing = document.querySelector('[data-live="signal-alt-ring"]');
    if (altRing) altRing.style.background = "conic-gradient(#22c55e " + active.alternative.confidencePct + "%, var(--border-soft) 0)";
  }

  function applyMargins(margins) {
    setText("margin-used", fmtINR(margins.used));
    setText("margin-available", fmtINR(margins.available));
  }

  function applyPositions(positions) {
    var tbody = document.querySelector('[data-live="positions-tbody"]');
    if (tbody) {
      tbody.innerHTML = positions.map(function (p) {
        var strike = p.instrument.replace(/^NIFTY\d{2}[A-Z]{3}/, "");
        var qtySign = p.side === "LONG" ? "+" : "-";
        var qtyClass = p.side === "LONG" ? "text-green" : "text-red";
        var pnlClass = p.pnl >= 0 ? "text-green" : "text-red";
        return (
          "<tr><td>" + strike.replace("CE", " CE").replace("PE", " PE") + "</td>" +
          '<td class="' + qtyClass + '">' + qtySign + p.quantityLots + "</td>" +
          "<td>" + p.ltp.toFixed(2) + "</td>" +
          '<td class="' + pnlClass + '">' + fmtINRSigned(p.pnl) + "</td></tr>"
        );
      }).join("");
    }

    var totalPnl = positions.reduce(function (sum, p) { return sum + p.pnl; }, 0);
    var totalCost = positions.reduce(function (sum, p) { return sum + p.avgPrice * p.quantityLots; }, 0);
    var totalPct = totalCost ? (totalPnl / totalCost) * 100 : 0;
    var totalEl = document.querySelector('[data-live="positions-total-pnl"]');
    if (totalEl) {
      totalEl.classList.remove("text-red", "text-green");
      totalEl.classList.add(totalPnl >= 0 ? "text-green" : "text-red");
      totalEl.innerHTML = fmtINRSigned(totalPnl) + ' <small style="font-weight:600; font-size:11px;">(' + fmtSigned(totalPct) + "%)</small>";
    }
  }

  function markStatus(ok) {
    var el = document.querySelector('[data-live="api-status"]');
    if (!el) return;
    var dot = el.querySelector(".dot");
    el.childNodes[el.childNodes.length - 1].textContent = ok ? " API Connected (live)" : " Static Data (backend offline)";
    if (dot) dot.style.background = ok ? "" : "var(--amber, #eab308)";
  }

  Promise.all([
    fetchJSON("/market/quote?symbols=NIFTY50,INDIAVIX"),
    fetchJSON("/signals/bias"),
    fetchJSON("/signals/active"),
    fetchJSON("/positions/open"),
    fetchJSON("/positions/margins"),
  ])
    .then(function (results) {
      applyQuotes(results[0]);
      applyBias(results[1]);
      applySignals(results[2]);
      applyPositions(results[3]);
      applyMargins(results[4]);
      markStatus(true);
    })
    .catch(function () {
      // Backend not running / unreachable — keep the static mock numbers as-is.
      markStatus(false);
    });
})();
