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
    setText("signal-primary-entry", active.primary.entryZone);
    setText("signal-primary-target", active.primary.target);
    setText("signal-primary-sl", active.primary.stopLoss);
    setText("signal-primary-time", new Date(active.primary.generatedAt).toLocaleTimeString("en-IN", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }));
    var reasoningEl = document.querySelector('[data-live="signal-primary-reasoning"]');
    if (reasoningEl && active.primary.reasoning && active.primary.reasoning.length) {
      reasoningEl.innerHTML = active.primary.reasoning.map(function (r) {
        return '<li class="flex items-start gap-6"><span class="text-green" style="flex-shrink:0;">&#10003;</span> ' + r + "</li>";
      }).join("");
    }

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

  function fmtNum(n, decimals) {
    return n.toLocaleString("en-IN", { minimumFractionDigits: decimals || 0, maximumFractionDigits: decimals || 2 });
  }

  function findAtm(chain) {
    return chain.rows.reduce(function (best, r) {
      return Math.abs(r.strike - chain.spotPrice) < Math.abs(best.strike - chain.spotPrice) ? r : best;
    }, chain.rows[0]);
  }

  // PCR / ATM IV / Straddle ticker items, all derived from the same option
  // chain fetch used elsewhere below — no extra broker call.
  function applyChainTicker(chain) {
    var totalCe = chain.rows.reduce(function (s, r) { return s + r.ceOi; }, 0);
    var totalPe = chain.rows.reduce(function (s, r) { return s + r.peOi; }, 0);
    var pcr = totalCe ? totalPe / totalCe : 0;
    setText("pcr-value", pcr.toFixed(2));
    setText("pcr-label", pcr > 1.1 ? "Bullish" : pcr < 0.9 ? "Bearish" : "Neutral");

    var atm = findAtm(chain);
    if (atm) {
      setText("atm-iv", (((atm.ceIv + atm.peIv) / 2).toFixed(1)) + "%");
      var straddle = atm.ceLtp + atm.peLtp;
      setText("straddle-price", "₹" + straddle.toFixed(2));
      setText("dash-straddle-pct", ((straddle / chain.spotPrice) * 100).toFixed(2) + "%");
    }
  }

  function wallRows(rows, key, colorVar, textClass) {
    var max = Math.max.apply(null, rows.map(function (r) { return r[key]; })) || 1;
    return rows.map(function (r) {
      var pct = Math.max(4, (r[key] / max) * 100).toFixed(0);
      return (
        '<div class="wall-row"><div class="wall-strike">' + fmtNum(r.strike, 0) + '</div>' +
        '<div class="wall-bar-track"><div class="wall-bar" style="width:' + pct + '%; background:var(' + colorVar + ');"></div></div>' +
        '<div class="wall-val ' + textClass + '">' + (r[key] / 1e5).toFixed(2) + "M</div></div>"
      );
    }).join("");
  }

  // Top-2 CE OI (resistance) / top-2 PE OI (support) strikes from the chain.
  function applyOiWalls(chain) {
    var byCe = chain.rows.slice().sort(function (a, b) { return b.ceOi - a.ceOi; }).slice(0, 2);
    var byPe = chain.rows.slice().sort(function (a, b) { return b.peOi - a.peOi; }).slice(0, 2);
    var ceEl = document.querySelector('[data-live="dash-oi-resistance"]');
    var peEl = document.querySelector('[data-live="dash-oi-support"]');
    if (ceEl) ceEl.innerHTML = wallRows(byCe, "ceOi", "--red", "text-red");
    if (peEl) peEl.innerHTML = wallRows(byPe, "peOi", "--green", "text-green");
  }

  // Condensed 5-strike snapshot (2 above ATM, ATM, 2 below) with a
  // Buyer/Seller badge derived from the same buildup classification used on
  // the Open Interest page (Short Buildup/Long Unwinding = writers active =
  // "Seller"; Long Buildup/Short Covering = "Buyer").
  var BUILDUP_TO_BIAS = {
    "Short Buildup": "Seller", "Long Unwinding": "Seller",
    "Long Buildup": "Buyer", "Short Covering": "Buyer",
  };

  function applyOptionChainSnapshot(chain, buildup) {
    var byStrikeBuildup = {};
    buildup.forEach(function (b) { byStrikeBuildup[b.strike] = b; });
    var sorted = chain.rows.slice().sort(function (a, b) { return a.strike - b.strike; });
    var atm = findAtm(chain);
    var atmIdx = sorted.indexOf(atm);
    var rows = sorted.slice(Math.max(0, atmIdx - 2), atmIdx + 3);
    var tbody = document.querySelector('[data-live="dash-oc-tbody"]');
    if (!tbody) return;
    tbody.innerHTML = rows.slice().reverse().map(function (r) {
      var b = byStrikeBuildup[r.strike];
      var ceBias = (b && BUILDUP_TO_BIAS[b.ceSignal]) || "Neutral";
      var peBias = (b && BUILDUP_TO_BIAS[b.peSignal]) || "Neutral";
      var ceBadge = ceBias === "Seller" ? "badge-red" : ceBias === "Buyer" ? "badge-green" : "badge-gray";
      var peBadge = peBias === "Seller" ? "badge-red" : peBias === "Buyer" ? "badge-green" : "badge-gray";
      var rowClass = r.strike === atm.strike ? ' class="atm"' : "";
      return (
        "<tr" + rowClass + "><td>" + fmtNum(r.strike, 0) + "</td><td>" + (r.ceOi / 1e5).toFixed(1) + "</td>" +
        '<td class="' + (r.ceOiChange >= 0 ? "text-green" : "text-red") + '">' + fmtSigned(r.ceOiChange / 1e5, 1) + "</td>" +
        '<td><span class="badge ' + ceBadge + '">' + ceBias + "</span></td>" +
        "<td>" + (r.peOi / 1e5).toFixed(1) + "</td>" +
        '<td class="' + (r.peOiChange >= 0 ? "text-green" : "text-red") + '">' + fmtSigned(r.peOiChange / 1e5, 1) + "</td>" +
        '<td><span class="badge ' + peBadge + '">' + peBias + "</span></td></tr>"
      );
    }).join("");
  }

  function applyAtmGreeks(chain) {
    var atm = findAtm(chain);
    if (!atm) return;
    setText("dash-greeks-atm-strike", fmtNum(atm.strike, 0));
    setText("dash-greek-ce-delta", fmtSigned(atm.ceDelta, 2));
    setText("dash-greek-pe-delta", fmtSigned(atm.peDelta, 2));
    setText("dash-greek-ce-gamma", atm.ceGamma.toFixed(4));
    setText("dash-greek-pe-gamma", atm.peGamma.toFixed(4));
    setText("dash-greek-ce-theta", fmtSigned(atm.ceTheta, 2));
    setText("dash-greek-pe-theta", fmtSigned(atm.peTheta, 2));
    setText("dash-greek-ce-vega", atm.ceVega.toFixed(2));
    setText("dash-greek-pe-vega", atm.peVega.toFixed(2));
    setText("dash-greek-ce-rho", fmtSigned(atm.ceRho, 2));
    setText("dash-greek-pe-rho", fmtSigned(atm.peRho, 2));
  }

  function applyPortfolioGreeks(greeks) {
    setText("risk-net-delta", fmtSigned(greeks.netDelta, 2));
    setText("risk-net-gamma", fmtSigned(greeks.netGamma, 2));
    setText("greeks-net-theta", fmtSigned(greeks.netTheta, 2));
  }

  function applyVolumeProfilePanel(profile) {
    var el = document.querySelector('[data-live="dash-vp-rows"]');
    if (!el) return;
    var DISPLAY_WINDOW = 9;
    var pocIdx = profile.rows.reduce(function (bi, r, i) { return r.volume > profile.rows[bi].volume ? i : bi; }, 0);
    var windowRows = profile.rows.slice(Math.max(0, pocIdx - DISPLAY_WINDOW), pocIdx + DISPLAY_WINDOW + 1);
    var maxVol = Math.max.apply(null, windowRows.map(function (r) { return r.volume; })) || 1;
    el.innerHTML = windowRows.map(function (r) {
      var pct = ((r.volume / maxVol) * 100).toFixed(1);
      var color = r.price === profile.poc ? "var(--amber)" : (r.price <= profile.vah && r.price >= profile.val) ? "var(--purple)" : "var(--blue)";
      var priceClass = r.price === profile.poc ? "text-amber bold" : "";
      return (
        '<div class="row"><span class="price ' + priceClass + '">' + fmtNum(r.price, 0) + '</span>' +
        '<div class="track"><div class="fill" style="width:' + pct + '%; background:' + color + ';"></div></div></div>'
      );
    }).join("");
  }

  function applyExposure(margins) {
    var total = margins.used + margins.available;
    var pct = total ? (margins.used / total) * 100 : 0;
    setText("risk-exposure-pct", pct.toFixed(0) + "%");
    document.querySelectorAll('[data-live="risk-exposure-meter"]').forEach(function (el) {
      el.style.width = Math.min(100, pct).toFixed(0) + "%";
    });
  }

  function applyPnlToday(report) {
    var el = document.querySelector('[data-live="dash-pnl-today"]');
    if (!el) return;
    el.classList.remove("text-red", "text-green");
    el.classList.add(report.netPnl >= 0 ? "text-green" : "text-red");
    el.innerHTML = fmtINRSigned(report.netPnl);
  }

  function applyChartHeader(candles) {
    var el = document.querySelector('[data-live="dash-chart-ohlc"]');
    if (!el || !candles.length) return;
    var open = candles[0].open;
    var close = candles[candles.length - 1].close;
    var high = Math.max.apply(null, candles.map(function (c) { return c.high; }));
    var low = Math.min.apply(null, candles.map(function (c) { return c.low; }));
    var change = close - open;
    var changePct = open ? (change / open) * 100 : 0;
    el.innerHTML = (
      "O <b style=\"color:var(--text-primary);\">" + fmtNum(open, 2) + "</b> " +
      "H <b class=\"text-green\">" + fmtNum(high, 2) + "</b> " +
      "L <b class=\"text-red\">" + fmtNum(low, 2) + "</b> " +
      "C <b style=\"color:var(--text-primary);\">" + fmtNum(close, 2) + "</b> " +
      '<span class="' + (change >= 0 ? "text-green" : "text-red") + '">' + fmtSigned(change, 2) + " (" + fmtSigned(changePct, 2) + "%)</span>"
    );
  }

  function applyCandleChart(candles) {
    var g = document.querySelector('[data-live="dash-candle-chart"]');
    if (!g || !candles.length) return;
    var w = 860, h = 230;
    var sorted = candles.slice().sort(function (a, b) { return new Date(a.ts) - new Date(b.ts); });
    var maxP = Math.max.apply(null, sorted.map(function (c) { return c.high; }));
    var minP = Math.min.apply(null, sorted.map(function (c) { return c.low; }));
    var range = (maxP - minP) || 1;
    var pad = range * 0.08;
    var lo = minP - pad, hi = maxP + pad, span = (hi - lo) || 1;
    var slot = w / sorted.length;
    var bodyW = Math.max(1.5, slot * 0.6);
    function y(price) { return h - ((price - lo) / span) * h; }
    g.innerHTML = sorted.map(function (c, i) {
      var x = i * slot + slot / 2;
      var color = c.close >= c.open ? "#22c55e" : "#ef4444";
      var yOpen = y(c.open), yClose = y(c.close), yHigh = y(c.high), yLow = y(c.low);
      var bodyTop = Math.min(yOpen, yClose), bodyH = Math.max(1, Math.abs(yClose - yOpen));
      return (
        '<line x1="' + x.toFixed(1) + '" y1="' + yHigh.toFixed(1) + '" x2="' + x.toFixed(1) + '" y2="' + yLow.toFixed(1) + '" stroke="' + color + '" stroke-width="1"/>' +
        '<rect x="' + (x - bodyW / 2).toFixed(1) + '" y="' + bodyTop.toFixed(1) + '" width="' + bodyW.toFixed(1) + '" height="' + bodyH.toFixed(1) + '" fill="' + color + '"/>'
      );
    }).join("");
  }

  function applyBreadth(b) {
    setText("dash-advancing", b.advancing.toLocaleString("en-IN"));
    setText("dash-declining", b.declining.toLocaleString("en-IN"));
    setText("dash-ad-ratio", (b.declining ? b.advancing / b.declining : b.advancing).toFixed(2));
    setText("dash-new-highs", String(b.newHighs));
    setText("dash-new-lows", String(b.newLows));
  }

  function applyPressure(p) {
    var scoreEl = document.querySelector('[data-live="dash-pressure-score"]');
    if (scoreEl) {
      scoreEl.textContent = (p.netScore >= 0 ? "+" : "") + p.netScore.toFixed(0);
      scoreEl.classList.remove("text-red", "text-green");
      if (p.direction !== "NEUTRAL") scoreEl.classList.add(p.direction === "BULLISH" ? "text-green" : "text-red");
    }
    setText("dash-pressure-label", p.label);
    var badge = document.querySelector('[data-live="dash-pressure-badge"]');
    if (badge) {
      badge.textContent = p.direction === "BULLISH" ? "Bullish" : p.direction === "BEARISH" ? "Bearish" : "Neutral";
      badge.className = "badge " + (p.direction === "BULLISH" ? "badge-green" : p.direction === "BEARISH" ? "badge-red" : "badge-neutral");
    }

    // Bars show conviction (|score|); colour carries the direction, so a
    // strongly-bearish and strongly-bullish read are equally long but differ
    // in colour rather than being mistaken for "more/less activity".
    [["ce", p.cePressure, p.ceDominant], ["pe", p.pePressure, p.peDominant]].forEach(function (t) {
      var side = t[0], val = t[1], dominant = t[2];
      setText("dash-" + side + "-pressure", (val >= 0 ? "+" : "") + val.toFixed(0));
      setText("dash-" + side + "-dominant", dominant ? "(" + dominant + ")" : "");
      var bar = document.querySelector('[data-live="dash-' + side + '-bar"]');
      if (bar) {
        bar.style.width = Math.min(100, Math.abs(val)).toFixed(0) + "%";
        bar.style.background = val > 5 ? "var(--green)" : val < -5 ? "var(--red)" : "var(--text-muted)";
      }
      var valEl = document.querySelector('[data-live="dash-' + side + '-pressure"]');
      if (valEl) {
        valEl.classList.remove("text-red", "text-green");
        if (Math.abs(val) > 5) valEl.classList.add(val > 0 ? "text-green" : "text-red");
      }
    });

    setText("dash-pressure-support", p.supportStrike ? Math.round(p.supportStrike).toLocaleString("en-IN") : "—");
    setText("dash-pressure-resistance", p.resistanceStrike ? Math.round(p.resistanceStrike).toLocaleString("en-IN") : "—");
    setText("dash-pressure-pcr", p.pcrOi.toFixed(2) + " / " + p.pcrVolume.toFixed(2));
  }

  function applyIvRank(iv) {
    setText("iv-rank-value", iv.ivRank.toFixed(1));
    setText("dash-iv-rank-label", iv.ivRank >= 70 ? "High" : iv.ivRank >= 40 ? "Moderate" : "Low");
    setText("dash-iv-percentile", iv.ivPercentile.toFixed(1) + "%");
    var spark = document.querySelector('[data-live="dash-iv-sparkline"]');
    if (spark && iv.history && iv.history.length > 1) {
      var w = 240, h = 64;
      var min = Math.min.apply(null, iv.history), max = Math.max.apply(null, iv.history);
      var range = (max - min) || 1;
      var pts = iv.history.map(function (v, i) {
        var x = (i / (iv.history.length - 1)) * w;
        var y = h - ((v - min) / range) * h;
        return x.toFixed(1) + "," + y.toFixed(1);
      });
      spark.setAttribute("points", pts.join(" "));
    }
  }

  function applyCvd(cvd) {
    var label = (cvd.cumulative >= 0 ? "+" : "-") + Math.abs(cvd.cumulative / 1000).toFixed(1) + "K";
    setText("dash-cvd-total", label);
    var totalEl = document.querySelector('[data-live="dash-cvd-total"]');
    if (totalEl) { totalEl.classList.remove("text-red", "text-green"); totalEl.classList.add(cvd.cumulative >= 0 ? "text-green" : "text-red"); }
    var badgeEl = document.querySelector('[data-live="dash-cvd-badge"]');
    if (badgeEl) {
      badgeEl.textContent = label;
      badgeEl.classList.remove("badge-green", "badge-red");
      badgeEl.classList.add(cvd.cumulative >= 0 ? "badge-green" : "badge-red");
    }
    setText("dash-cvd-note", cvd.cumulative >= 0 ? "Net buying pressure today (volume-based)" : "Net selling pressure today (volume-based)");

    if (cvd.points && cvd.points.length > 1) {
      var w = 860, h = 90;
      var values = cvd.points.map(function (p) { return p.cvd; });
      var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
      var range = (max - min) || 1;
      var pad = range * 0.1;
      var lo = min - pad, hi = max + pad, span = (hi - lo) || 1;
      var pts = cvd.points.map(function (p, i) {
        var x = (i / (cvd.points.length - 1)) * w;
        var y = h - ((p.cvd - lo) / span) * h;
        return x.toFixed(1) + "," + y.toFixed(1);
      });
      var line = document.querySelector('[data-live="dash-cvd-line"]');
      var fill = document.querySelector('[data-live="dash-cvd-fill"]');
      if (line) line.setAttribute("points", pts.join(" "));
      if (fill) fill.setAttribute("points", "0," + h + " " + pts.join(" ") + " " + w + "," + h);

      var timesEl = document.querySelector('[data-live="dash-cvd-times"]');
      if (timesEl) {
        var n = 6, labels = [];
        for (var i = 0; i < n; i++) {
          var idx = Math.round((i / (n - 1)) * (cvd.points.length - 1));
          labels.push(new Date(cvd.points[idx].ts).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }));
        }
        timesEl.innerHTML = labels.map(function (l) { return "<span>" + l + "</span>"; }).join("");
      }
    }
  }

  // NSE cash-market hours, IST, Mon-Fri 9:15-15:30. Doesn't account for
  // exchange holidays — same disclosed simplification used elsewhere in this
  // app (e.g. backend's _prev_trading_day).
  function applyMarketHours() {
    var now = new Date();
    // Date.getTime() is already an absolute UTC epoch value regardless of the
    // browser's local timezone — just add IST's UTC+5:30 offset directly and
    // read the UTC-component getters on the shifted timestamp (same approach
    // as ne-common.js's isMarketOpenIST).
    var ist = new Date(now.getTime() + 330 * 60000);
    var day = ist.getUTCDay();
    var minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
    var openMin = 9 * 60 + 15, closeMin = 15 * 60 + 30;
    var isWeekday = day >= 1 && day <= 5;
    var text;
    if (isWeekday && minutes >= openMin && minutes <= closeMin) {
      text = "Closes 3:30 PM";
    } else if (isWeekday && minutes < openMin) {
      text = "Opens 9:15 AM";
    } else {
      var dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
      var daysAhead = 1, d = day;
      do {
        d = (d + 1) % 7;
        if (d >= 1 && d <= 5) break;
        daysAhead++;
      } while (true);
      text = "Opens 9:15 AM " + (daysAhead === 1 ? "tomorrow" : dayNames[d]);
    }
    setText("dash-market-hours", text);
    setText("dash-market-hours-footer", text);
  }

  var REFRESH_MS = 2000;
  var UNDERLYING = "NIFTY50";
  var hasChainSections = !!document.querySelector('[data-live="dash-oi-resistance"]');

  // A single failing endpoint used to reject the whole Promise.all below and
  // blank every panel on the Dashboard (e.g. volume-profile 404s on a
  // non-trading day, taking down bias, signals, OI and pressure with it).
  // Each panel now degrades on its own: a failed call resolves to null and
  // only that panel keeps its previous/placeholder content.
  function optional(path) {
    return fetchJSON(path).catch(function () { return null; });
  }

  function load(expiry, expiryLabel) {
    var today = new Date().toISOString().slice(0, 10);
    var calls = [
      optional("/market/quote?symbols=NIFTY50,INDIAVIX"),
      optional("/signals/bias"),
      optional("/signals/active"),
      optional("/positions/open"),
      optional("/positions/margins"),
    ];
    if (hasChainSections && expiry) {
      calls.push(
        optional("/market/option-chain?underlying=" + UNDERLYING + "&expiry=" + expiry),
        optional("/market/oi-buildup?underlying=" + UNDERLYING + "&expiry=" + expiry),
        optional("/positions/greeks?underlying=" + UNDERLYING + "&expiry=" + expiry),
        optional("/market/volume-profile?underlying=" + UNDERLYING),
        optional("/reports/summary?from=" + today + "&to=" + today),
        optional("/market/candles?symbol=" + UNDERLYING + "&interval=5m"),
        optional("/market/breadth"),
        optional("/market/iv-rank?underlying=" + UNDERLYING + "&expiry=" + expiry),
        optional("/market/cvd?underlying=" + UNDERLYING),
        optional("/market/pressure?underlying=" + UNDERLYING + "&expiry=" + expiry)
      );
    }

    // Runs an apply function only when its data actually arrived, and never
    // lets one panel's rendering error take down the rest of the Dashboard.
    function panel(fn) {
      var args = Array.prototype.slice.call(arguments, 1);
      if (args[0] == null) return;
      try { fn.apply(null, args); } catch (e) { /* one panel failing is not fatal */ }
    }

    Promise.all(calls)
      .then(function (results) {
        panel(applyQuotes, results[0]);
        panel(applyBias, results[1]);
        panel(applySignals, results[2]);
        panel(applyPositions, results[3]);
        panel(applyMargins, results[4]);
        panel(applyExposure, results[4]);
        if (hasChainSections && expiry) {
          var chain = results[5], buildup = results[6], greeks = results[7], profile = results[8], report = results[9],
            candles = results[10], breadth = results[11], ivRank = results[12], cvd = results[13],
            pressure = results[14];
          setText("dash-expiry-label", expiryLabel);
          panel(applyChainTicker, chain);
          panel(applyOiWalls, chain);
          panel(applyOptionChainSnapshot, chain, buildup);
          panel(applyAtmGreeks, chain);
          panel(applyPortfolioGreeks, greeks);
          panel(applyVolumeProfilePanel, profile);
          panel(applyPnlToday, report);
          panel(applyChartHeader, candles);
          panel(applyCandleChart, candles);
          panel(applyBreadth, breadth);
          panel(applyIvRank, ivRank);
          panel(applyCvd, cvd);
          panel(applyPressure, pressure);
        }
        if (window.NE) window.NE.applyMarketOpenBadges();
        applyMarketHours();
        markStatus(true);
        if (window.NE) window.NE.stampRefresh();
      })
      .catch(function () {
        // Backend not running / unreachable — keep the static mock numbers as-is.
        markStatus(false);
      });
  }

  if (hasChainSections) {
    fetchJSON("/market/expiries?underlying=" + UNDERLYING)
      .then(function (data) {
        var expiry = (data.expiries || [])[0];
        var expiryLabel = expiry
          ? new Date(expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }).toUpperCase()
          : "";
        load(expiry, expiryLabel);
        setInterval(function () { load(expiry, expiryLabel); }, REFRESH_MS);
      })
      .catch(function () {
        load();
        setInterval(load, REFRESH_MS);
      });
  } else {
    load();
    setInterval(load, REFRESH_MS);
  }
})();
