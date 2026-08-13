/**
 * Wires greeks-oi.html to GET /market/strike-greeks and GET /market/gamma-profile.
 *
 * Where greeks-chart.html plots the rolling ATM strike alone, this page walks
 * the whole near-money ladder and lets any subset of its CE/PE legs be
 * overlaid. Because the metric selector plots ONE Greek at a time, every
 * visible series shares units — so unlike greeks-chart.html this axis carries
 * real values instead of a normalized 0-100% scale.
 *
 * Click-triggered only, never on a timer: one ladder load fans out into
 * (depth*2+1) * 2 Dhan option-contract fetches per distinct ATM strike, which
 * is several times what the ATM-only grid costs (see the 429 history in
 * backend/app/services/market_data.py). Only the header ticker polls.
 */
(function () {
  var NE = window.NE;

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.body).getPropertyValue(name);
    return v ? v.trim() : fallback;
  }

  // -- ladder vocabulary ---------------------------------------------------
  // One hue per moneyness rung, shared by both sides; CE draws solid and PE
  // dashed. That keeps "CE ATM vs PE ATM" legible as one pair rather than as
  // two unrelated colors, which is the comparison this page exists for.

  var SLOTS = ["ITM3", "ITM2", "ITM1", "ATM", "OTM1", "OTM2", "OTM3"];
  var SLOT_LABEL = {
    ITM3: "ITM 3", ITM2: "ITM 2", ITM1: "ITM 1", ATM: "ATM",
    OTM1: "OTM 1", OTM2: "OTM 2", OTM3: "OTM 3",
  };
  // Fixed hexes for the two outermost rungs: no app-wide CSS var reads as a
  // hue distinct from the five already in use, same convention
  // composite-profile-chart.js uses for its extended palette.
  var SLOT_COLOR = {
    ITM3: function () { return "#06b6d4"; },
    ITM2: function () { return cssVar("--blue", "#3b82f6"); },
    ITM1: function () { return cssVar("--purple", "#a855f7"); },
    ATM: function () { return cssVar("--amber", "#f5a623"); },
    OTM1: function () { return cssVar("--pink", "#ec4899"); },
    OTM2: function () { return cssVar("--green", "#22c55e"); },
    OTM3: function () { return "#fb923c"; },
  };

  var METRICS = {
    gamma: { label: "Gamma", dp: 5, signed: false },
    gex: { label: "Gamma × OI (GEX)", compact: true, signed: true },
    delta: { label: "Delta", dp: 3, signed: true },
    theta: { label: "Theta", dp: 2, signed: true },
    vega: { label: "Vega", dp: 2, signed: false },
    iv: { label: "IV %", dp: 2, signed: false },
    ltp: { label: "Premium (LTP)", dp: 2, signed: false },
  };

  // Mirrors _MAX_RANGE_DAYS in backend/app/services/strike_greeks_service.py —
  // the "All" preset must not build a range the server will reject.
  var MAX_DAYS = { "1m": 2, "5m": 5, "15m": 10, "30m": 10 };

  var state = {
    data: null,        // last /strike-greeks payload
    profile: null,     // last /gamma-profile payload
    sell: null,        // last /sell-candidates payload
    selected: { CE_ATM: true, PE_ATM: true },
  };

  // -- ticker/footer refresh, every 2s ------------------------------------

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
  setInterval(loadTicker, 2000);

  // -- formatting -----------------------------------------------------------

  function fmtPrice(n) { return Math.round(n).toLocaleString("en-IN"); }

  // OI and GEX both run into the millions — compacted to Indian Cr/L, same
  // convention as data-analysis-page.js and greeks-chart-page.js.
  function fmtCompact(n) {
    if (n == null) return "—";
    var v = Number(n), abs = Math.abs(v);
    if (abs >= 1e7) return (v / 1e7).toFixed(2) + " Cr";
    if (abs >= 1e5) return (v / 1e5).toFixed(2) + " L";
    if (abs >= 1e3) return (v / 1e3).toFixed(1) + " K";
    return abs < 1 ? v.toFixed(4) : v.toFixed(1);
  }

  function fmtCompactSigned(n) {
    return n == null ? "—" : (n >= 0 ? "+" : "−") + fmtCompact(Math.abs(n));
  }

  function fmtMetric(metric, v) {
    if (v == null) return "—";
    var m = METRICS[metric];
    return m.compact ? fmtCompact(v) : v.toFixed(m.dp);
  }

  // -- filters --------------------------------------------------------------

  function latestWeekday() {
    var d = new Date();
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
    return d;
  }

  function iso(d) {
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function backWeekdays(from, sessions) {
    var d = new Date(from.getTime()), left = sessions - 1;
    while (left > 0) {
      d.setDate(d.getDate() - 1);
      if (d.getDay() !== 0 && d.getDay() !== 6) left--;
    }
    return d;
  }

  function el(id) { return document.getElementById(id); }

  /** Presets write into the same From/To inputs the custom mode exposes, so
   *  the resolved dates are always visible rather than implied. */
  function applyPreset() {
    var preset = el("gl-preset").value;
    var interval = el("gl-interval").value;
    var custom = preset === "custom";
    el("gl-from").disabled = !custom;
    el("gl-to").disabled = !custom;
    if (custom) return;

    var to = latestWeekday(), from;
    if (preset === "today") from = to;
    else if (preset === "3") from = backWeekdays(to, 3);
    else if (preset === "5") from = backWeekdays(to, 5);
    else {
      // "all" and "expiry" both take the widest window the interval allows;
      // "expiry" then filters the returned buckets down to expiry sessions.
      from = new Date(to.getTime());
      from.setDate(from.getDate() - MAX_DAYS[interval]);
    }
    el("gl-from").value = iso(from);
    el("gl-to").value = iso(to);
  }

  function showError(msg) {
    var e = document.querySelector('[data-live="gl-error"]');
    if (!e) return;
    if (msg) { e.textContent = msg; e.style.display = ""; } else { e.style.display = "none"; }
  }

  function setLoading(loading) {
    document.querySelectorAll('[data-live="gl-load-btn"]').forEach(function (btn) {
      btn.disabled = loading;
      btn.textContent = loading ? "Loading…" : "Load Ladder";
    });
  }

  // -- leg selector ---------------------------------------------------------

  function activeSlots() {
    var depth = parseInt(el("gl-depth").value, 10);
    return SLOTS.filter(function (s) {
      return s === "ATM" || parseInt(s.slice(3), 10) <= depth;
    });
  }

  /** Legs the user ticked AND that the current depth still exposes. */
  function selectedLegs() {
    var out = [];
    activeSlots().forEach(function (slot) {
      ["CE", "PE"].forEach(function (side) {
        var key = side + "_" + slot;
        if (state.selected[key]) out.push({ key: key, side: side, slot: slot });
      });
    });
    return out;
  }

  function legStrike(slot, side) {
    if (!state.data || !state.data.buckets.length) return null;
    var last = state.data.buckets[state.data.buckets.length - 1];
    var leg = last.legs[side + "_" + slot];
    return leg ? leg.strike : null;
  }

  function buildLegTable() {
    var slots = activeSlots();
    var html = '<thead><tr><th>Moneyness</th><th style="text-align:left;">CE (Call)</th>' +
      '<th style="text-align:left;">PE (Put)</th></tr></thead><tbody>';
    slots.forEach(function (slot) {
      var color = SLOT_COLOR[slot]();
      html += "<tr" + (slot === "ATM" ? ' class="atm"' : "") + ">";
      html += '<td><span class="legend-dot" style="background:' + color + ';"></span>' + SLOT_LABEL[slot] + "</td>";
      ["CE", "PE"].forEach(function (side) {
        var key = side + "_" + slot;
        var strike = legStrike(slot, side);
        html += '<td style="text-align:left;"><label style="display:inline-flex; align-items:center; gap:6px; cursor:pointer;">' +
          '<input type="checkbox" data-leg="' + key + '"' + (state.selected[key] ? " checked" : "") + " /> " +
          '<span style="color:var(--text-muted); font-variant-numeric:tabular-nums;">' +
          (strike == null ? "—" : fmtPrice(strike)) + "</span></label></td>";
      });
      html += "</tr>";
    });
    html += "</tbody>";
    var table = el("gl-leg-table");
    table.innerHTML = html;
    table.querySelectorAll("input[data-leg]").forEach(function (box) {
      box.addEventListener("change", function () {
        state.selected[box.getAttribute("data-leg")] = box.checked;
        redraw();
      });
    });
  }

  function applyLegPreset(preset) {
    var slots = activeSlots();
    state.selected = {};
    slots.forEach(function (slot) {
      ["CE", "PE"].forEach(function (side) {
        var on = preset === "all" ||
          (preset === "atm" && slot === "ATM") ||
          (preset === "ce" && side === "CE") ||
          (preset === "pe" && side === "PE");
        if (on) state.selected[side + "_" + slot] = true;
      });
    });
    buildLegTable();
    redraw();
  }

  // -- shared chart helpers -------------------------------------------------

  var TOP = 14, BOTTOM = 30, LEFT = 74, RIGHT = 16, PANE_GAP = 12;

  // Browsers cap a canvas' backing store around 16384px per side and blank the
  // whole element past it. Ten legs over a multi-day 1m range would sail well
  // beyond that, so the desired width is clamped in DEVICE pixels — buckets
  // just get tighter instead of the chart silently disappearing.
  var MAX_DEVICE_PX = 16000;

  function boundedWidth(desired) {
    return Math.floor(Math.min(desired, MAX_DEVICE_PX / (window.devicePixelRatio || 1)));
  }

  function niceStep(range) {
    if (!range) return 1;
    var raw = range / 5;
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var norm = raw / mag;
    return (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  }

  function extend(base, patch) {
    var out = {}, k;
    for (k in base) if (base.hasOwnProperty(k)) out[k] = base[k];
    for (k in patch) if (patch.hasOwnProperty(k)) out[k] = patch[k];
    return out;
  }

  function palette() {
    return {
      border: cssVar("--border", "#1e2635"),
      text: cssVar("--text-primary", "#e7ebf3"),
      textMuted: cssVar("--text-muted", "#5c6478"),
      bg: cssVar("--bg-card-alt", "#131a27"),
      green: cssVar("--green", "#22c55e"),
      red: cssVar("--red", "#ef4444"),
      amber: cssVar("--amber", "#f5a623"),
      blue: cssVar("--blue", "#3b82f6"),
    };
  }

  function sizeCanvas(canvas, cssWidth, cssHeight) {
    var dpr = window.devicePixelRatio || 1;
    canvas.style.width = cssWidth + "px";
    canvas.style.height = cssHeight + "px";
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);
    return ctx;
  }

  function emptyCanvas(canvas, msg) {
    var ctx = sizeCanvas(canvas, (canvas.parentNode && canvas.parentNode.clientWidth) || 900, 120);
    ctx.fillStyle = cssVar("--text-muted", "#5c6478");
    ctx.font = "12px sans-serif";
    ctx.fillText(msg, 12, 30);
  }

  /** Linear scale over [lo,hi] mapped onto a pane, padded and optionally
   *  forced to include zero so a sign change stays visible. */
  function scale(values, top, height, includeZero) {
    var vals = values.filter(function (v) { return v != null; });
    var lo = vals.length ? Math.min.apply(null, vals) : 0;
    var hi = vals.length ? Math.max.apply(null, vals) : 1;
    if (includeZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (lo === hi) { lo -= 1; hi += 1; }
    var pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;
    return {
      lo: lo, hi: hi,
      y: function (v) { return v == null ? null : top + height - ((v - lo) / (hi - lo)) * height; },
    };
  }

  function drawLine(ctx, plotX, y, values, color, dashed) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    if (dashed) ctx.setLineDash([5, 3]);
    ctx.beginPath();
    var started = false;
    for (var i = 0; i < values.length; i++) {
      var py = y(values[i]);
      if (py == null) { started = false; continue; }
      var x = plotX(i);
      if (!started) { ctx.moveTo(x, py); started = true; } else { ctx.lineTo(x, py); }
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawTooltipBox(ctx, x, cssWidth, lines, colors) {
    ctx.font = "11px sans-serif";
    var tw = Math.max.apply(null, lines.map(function (t) { return ctx.measureText(t).width; }));
    var boxW = tw + 16, boxH = lines.length * 15 + 8;
    var boxX = Math.min(x + 8, cssWidth - boxW - 4);
    ctx.fillStyle = colors.bg;
    ctx.strokeStyle = colors.border;
    ctx.globalAlpha = 0.97;
    ctx.fillRect(boxX, TOP + 4, boxW, boxH);
    ctx.strokeRect(boxX, TOP + 4, boxW, boxH);
    ctx.globalAlpha = 1;
    ctx.fillStyle = colors.text;
    lines.forEach(function (line, li) { ctx.fillText(line, boxX + 8, TOP + 18 + li * 15); });
  }

  // Bound once per canvas; the renderer stashes its latest args on the canvas
  // before drawing so a hover redraw always reads current state, never a
  // stale closure.
  function attachHover(canvas, stateKey, redrawFn) {
    if (canvas["_neHover_" + stateKey]) return;
    canvas["_neHover_" + stateKey] = true;
    var pending = false, pendingX;
    function flush() { pending = false; if (canvas[stateKey]) redrawFn(pendingX); }
    canvas.addEventListener("mousemove", function (evt) {
      pendingX = evt.clientX - canvas.getBoundingClientRect().left;
      if (!pending) { pending = true; requestAnimationFrame(flush); }
    });
    canvas.addEventListener("mouseleave", function () {
      pendingX = null;
      if (!pending) { pending = true; requestAnimationFrame(flush); }
    });
  }

  function nearestIndex(plotX, n, hoverX) {
    var idx = 0, best = Infinity;
    for (var i = 0; i < n; i++) {
      var d = Math.abs(plotX(i) - hoverX);
      if (d < best) { best = d; idx = i; }
    }
    return idx;
  }

  function drawXLabels(ctx, plotX, labels, plotW, baselineY, colors) {
    var maxTicks = Math.max(2, Math.floor(plotW / 90));
    var every = Math.max(1, Math.ceil(labels.length / maxTicks));
    ctx.textAlign = "center";
    ctx.fillStyle = colors.textMuted;
    for (var i = 0; i < labels.length; i += every) ctx.fillText(labels[i], plotX(i), baselineY);
    ctx.textAlign = "left";
  }

  /** Horizontal gridlines + left-axis labels for one pane. */
  function drawPaneAxis(ctx, sc, top, height, plotRight, colors, fmt) {
    var step = niceStep(sc.hi - sc.lo);
    for (var v = Math.ceil(sc.lo / step) * step; v <= sc.hi; v += step) {
      var gy = sc.y(v);
      if (gy < top - 1 || gy > top + height + 1) continue;
      ctx.strokeStyle = colors.border;
      ctx.globalAlpha = 0.3;
      ctx.beginPath();
      ctx.moveTo(LEFT, gy);
      ctx.lineTo(plotRight, gy);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = colors.textMuted;
      ctx.fillText(fmt(v), 4, gy + 3);
    }
  }

  // -- the ladder comparison chart -----------------------------------------
  // Up to three stacked panes on one shared time axis: spot candles (optional),
  // the selected metric for every ticked leg on a REAL-value axis, and an OI
  // pane of one bar per leg per bucket. Panes are allocated proportionally so
  // hiding one gives its pixels to the others rather than leaving a gap.

  function renderLadder(canvas, opts) {
    if (!canvas) return;
    opts = opts || {};
    var buckets = opts.buckets || [];
    var legs = opts.legs || [];
    var colors = palette();

    canvas._neLadder = opts;
    attachHover(canvas, "_neLadder", function (x) {
      renderLadder(canvas, extend(canvas._neLadder, { hover: x }));
    });

    if (!buckets.length) { emptyCanvas(canvas, "No data — pick a range and click Load Ladder."); return; }
    if (!legs.length) { emptyCanvas(canvas, "No legs selected — tick a CE or PE box above."); return; }

    var n = buckets.length;
    var metric = opts.metric;
    var oiMode = opts.oiMode;
    var showPrice = opts.showPrice;

    // Each leg needs its own bar slot in the OI pane, so wider selections need
    // a wider canvas; the container scrolls horizontally rather than crushing.
    var perBucket = oiMode === "off" ? 14 : Math.max(14, legs.length * 4 + 6);
    var container = (canvas.parentNode && canvas.parentNode.clientWidth) || 900;
    var cssWidth = boundedWidth(Math.max(container, LEFT + RIGHT + n * perBucket));
    // Bars are laid out from the width actually granted, so a clamped canvas
    // still packs every bucket in rather than overflowing off the right edge.
    perBucket = (cssWidth - LEFT - RIGHT) / Math.max(1, n);

    var panes = [];
    if (showPrice) panes.push({ key: "price", weight: 0.85 });
    panes.push({ key: "metric", weight: 1.6 });
    if (oiMode !== "off") panes.push({ key: "oi", weight: 0.75 });

    var chartH = 200 + panes.length * 110;
    var totalWeight = panes.reduce(function (a, p) { return a + p.weight; }, 0);
    var usableH = chartH - PANE_GAP * (panes.length - 1);
    var cursor = TOP;
    var pane = {};
    panes.forEach(function (p) {
      var h = Math.round((p.weight / totalWeight) * usableH);
      pane[p.key] = { top: cursor, h: h };
      cursor += h + PANE_GAP;
    });

    var ctx = sizeCanvas(canvas, cssWidth, TOP + chartH + BOTTOM);
    var plotW = cssWidth - LEFT - RIGHT;
    var plotRight = cssWidth - RIGHT;
    var plotX = function (i) { return LEFT + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW); };
    ctx.font = "10.5px 'SFMono-Regular',Consolas,monospace";

    // -- price pane ---------------------------------------------------------
    if (showPrice) {
      var pv = [];
      buckets.forEach(function (b) { pv.push(b.spotHigh, b.spotLow, b.atmStrike); });
      var priceSc = scale(pv, pane.price.top, pane.price.h, false);
      drawPaneAxis(ctx, priceSc, pane.price.top, pane.price.h, plotRight, colors, fmtPrice);

      var bucketPx = plotW / Math.max(1, n - 1);
      var bodyW = Math.max(1, Math.min(8, bucketPx * 0.42));
      buckets.forEach(function (b, i) {
        var x = plotX(i);
        var up = b.spotClose >= b.spotOpen;
        ctx.strokeStyle = ctx.fillStyle = up ? colors.green : colors.red;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, priceSc.y(b.spotHigh));
        ctx.lineTo(x, priceSc.y(b.spotLow));
        ctx.stroke();
        var yO = priceSc.y(b.spotOpen), yC = priceSc.y(b.spotClose);
        ctx.globalAlpha = 0.9;
        ctx.fillRect(x - bodyW / 2, Math.min(yO, yC), bodyW, Math.max(1, Math.abs(yC - yO)));
        ctx.globalAlpha = 1;
      });
      drawLine(ctx, plotX, priceSc.y, buckets.map(function (b) { return b.atmStrike; }), colors.amber, true);
    }

    // -- metric pane --------------------------------------------------------
    var series = legs.map(function (leg) {
      return buckets.map(function (b) {
        var l = b.legs[leg.key];
        return l ? l[metric] : null;
      });
    });
    var flat = [];
    series.forEach(function (s) { flat = flat.concat(s); });
    var metricSc = scale(flat, pane.metric.top, pane.metric.h, METRICS[metric].signed);
    drawPaneAxis(ctx, metricSc, pane.metric.top, pane.metric.h, plotRight, colors, function (v) {
      return METRICS[metric].compact ? fmtCompact(v) : v.toFixed(METRICS[metric].dp);
    });
    if (metricSc.lo < 0 && metricSc.hi > 0) {
      ctx.save();
      ctx.strokeStyle = colors.textMuted;
      ctx.globalAlpha = 0.55;
      ctx.beginPath();
      ctx.moveTo(LEFT, metricSc.y(0));
      ctx.lineTo(plotRight, metricSc.y(0));
      ctx.stroke();
      ctx.restore();
    }
    legs.forEach(function (leg, li) {
      drawLine(ctx, plotX, metricSc.y, series[li], SLOT_COLOR[leg.slot](), leg.side === "PE");
    });

    // -- OI pane ------------------------------------------------------------
    var oiSeries = null, oiSc = null;
    if (oiMode !== "off") {
      var field = oiMode === "change" ? "oiChange" : "oi";
      oiSeries = legs.map(function (leg) {
        return buckets.map(function (b) {
          var l = b.legs[leg.key];
          return l ? l[field] : null;
        });
      });
      var oiFlat = [];
      oiSeries.forEach(function (s) { oiFlat = oiFlat.concat(s); });
      oiSc = scale(oiFlat, pane.oi.top, pane.oi.h, true);
      drawPaneAxis(ctx, oiSc, pane.oi.top, pane.oi.h, plotRight, colors, fmtCompact);

      var zeroY = oiSc.y(0);
      ctx.save();
      ctx.strokeStyle = colors.textMuted;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.moveTo(LEFT, zeroY);
      ctx.lineTo(plotRight, zeroY);
      ctx.stroke();
      ctx.restore();

      var slotW = Math.max(1, (perBucket - 6) / legs.length);
      var barW = Math.max(1, slotW - 1);
      legs.forEach(function (leg, li) {
        ctx.fillStyle = SLOT_COLOR[leg.slot]();
        // PE bars are drawn hollow so a call and a put on the same rung stay
        // distinguishable in the OI pane too, matching the solid/dashed
        // convention used for the lines above.
        var hollow = leg.side === "PE";
        ctx.strokeStyle = ctx.fillStyle;
        for (var i = 0; i < n; i++) {
          var v = oiSeries[li][i];
          if (v == null) continue;
          var x = plotX(i) - (legs.length * slotW) / 2 + li * slotW;
          var y = oiSc.y(v);
          var top = Math.min(zeroY, y), h = Math.max(1, Math.abs(y - zeroY));
          ctx.globalAlpha = 0.85;
          if (hollow) ctx.strokeRect(x + 0.5, top + 0.5, barW, h);
          else ctx.fillRect(x, top, barW, h);
          ctx.globalAlpha = 1;
        }
      });
    }

    drawXLabels(ctx, plotX, buckets.map(function (b) { return b.time; }), plotW, TOP + chartH + 16, colors);

    if (opts.hover != null) {
      var idx = nearestIndex(plotX, n, opts.hover);
      var x = plotX(idx);
      var b = buckets[idx];
      ctx.save();
      ctx.strokeStyle = colors.textMuted;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, TOP);
      ctx.lineTo(x, TOP + chartH);
      ctx.stroke();
      ctx.restore();

      var lines = [b.time + (b.isExpiryDay ? "  · EXPIRY DAY" : "")];
      if (showPrice) {
        lines.push("Spot  O " + fmtPrice(b.spotOpen) + "  H " + fmtPrice(b.spotHigh) +
          "  L " + fmtPrice(b.spotLow) + "  C " + fmtPrice(b.spotClose));
      }
      lines.push("ATM " + fmtPrice(b.atmStrike) + (b.dte == null ? "" : "   DTE " + b.dte.toFixed(2)));
      legs.forEach(function (leg, li) {
        var l = b.legs[leg.key] || {};
        var row = leg.side + " " + SLOT_LABEL[leg.slot] + " " +
          (l.strike == null ? "—" : fmtPrice(l.strike)) + ":  " +
          METRICS[metric].label + " " + fmtMetric(metric, series[li][idx]);
        if (oiMode !== "off") {
          row += "   OI " + (oiMode === "change" ? fmtCompactSigned(oiSeries[li][idx]) : fmtCompact(oiSeries[li][idx]));
        }
        lines.push(row);
      });
      if (b.netGex != null) lines.push("Net GEX (ladder): " + fmtCompactSigned(b.netGex));
      drawTooltipBox(ctx, x, cssWidth, lines, colors);
    }
  }

  // -- net GEX timeline -----------------------------------------------------

  function renderGexTimeline(canvas, opts) {
    if (!canvas) return;
    opts = opts || {};
    var buckets = opts.buckets || [];
    var colors = palette();

    canvas._neGex = opts;
    attachHover(canvas, "_neGex", function (x) {
      renderGexTimeline(canvas, extend(canvas._neGex, { hover: x }));
    });

    var withGex = buckets.filter(function (b) { return b.netGex != null; });
    if (!withGex.length) {
      emptyCanvas(canvas, "No gamma exposure for this range — needs both a solved IV and real OI on the ladder.");
      return;
    }

    var n = buckets.length;
    var container = (canvas.parentNode && canvas.parentNode.clientWidth) || 900;
    var cssWidth = boundedWidth(Math.max(container, LEFT + RIGHT + n * 8));
    var chartH = 190;
    var ctx = sizeCanvas(canvas, cssWidth, TOP + chartH + BOTTOM);
    var plotW = cssWidth - LEFT - RIGHT;
    var plotRight = cssWidth - RIGHT;
    var plotX = function (i) { return LEFT + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW); };
    ctx.font = "10.5px 'SFMono-Regular',Consolas,monospace";

    var values = buckets.map(function (b) { return b.netGex; });
    var sc = scale(values, TOP, chartH, true);
    drawPaneAxis(ctx, sc, TOP, chartH, plotRight, colors, fmtCompact);

    var zeroY = sc.y(0);
    ctx.save();
    ctx.strokeStyle = colors.textMuted;
    ctx.globalAlpha = 0.6;
    ctx.beginPath();
    ctx.moveTo(LEFT, zeroY);
    ctx.lineTo(plotRight, zeroY);
    ctx.stroke();
    ctx.restore();

    var barW = Math.max(1, Math.min(7, (plotW / Math.max(1, n - 1)) * 0.6));
    buckets.forEach(function (b, i) {
      if (b.netGex == null) return;
      var y = sc.y(b.netGex);
      ctx.fillStyle = b.netGex >= 0 ? colors.green : colors.red;
      ctx.globalAlpha = 0.85;
      ctx.fillRect(plotX(i) - barW / 2, Math.min(zeroY, y), barW, Math.max(1, Math.abs(y - zeroY)));
      ctx.globalAlpha = 1;
    });

    drawXLabels(ctx, plotX, buckets.map(function (b) { return b.time; }), plotW, TOP + chartH + 16, colors);

    if (opts.hover != null) {
      var idx = nearestIndex(plotX, n, opts.hover);
      var b2 = buckets[idx];
      var x = plotX(idx);
      ctx.save();
      ctx.strokeStyle = colors.textMuted;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, TOP);
      ctx.lineTo(x, TOP + chartH);
      ctx.stroke();
      ctx.restore();
      drawTooltipBox(ctx, x, cssWidth, [
        b2.time + (b2.isExpiryDay ? "  · EXPIRY DAY" : ""),
        "Net GEX: " + fmtCompactSigned(b2.netGex) + "  (" + (b2.netGex >= 0 ? "damping" : "amplifying") + ")",
        "Call GEX " + fmtCompactSigned(b2.callGex) + "   Put GEX " + fmtCompactSigned(b2.putGex),
        "Peak gamma strike: " + (b2.peakGammaStrike == null ? "—" : fmtPrice(b2.peakGammaStrike)),
      ], colors);
    }
  }

  // -- live gamma profile by strike ----------------------------------------
  // Strikes on the vertical axis, GEX on the horizontal: calls run right,
  // puts left, from a centred zero. Spot, the zero-gamma flip and the two
  // walls are drawn as labelled horizontal markers.

  function renderGammaProfile(canvas, profile) {
    if (!canvas) return;
    var colors = palette();
    if (!profile || !profile.strikes.length) {
      emptyCanvas(canvas, "No chain data — click Refresh.");
      return;
    }

    var strikes = profile.strikes;
    var rowH = 20;
    var chartH = Math.max(160, strikes.length * rowH);
    var cssWidth = (canvas.parentNode && canvas.parentNode.clientWidth) || 900;
    var ctx = sizeCanvas(canvas, cssWidth, TOP + chartH + BOTTOM);
    var plotW = cssWidth - LEFT - RIGHT;
    var centerX = LEFT + plotW / 2;
    ctx.font = "10.5px 'SFMono-Regular',Consolas,monospace";

    var maxAbs = 0;
    strikes.forEach(function (s) {
      maxAbs = Math.max(maxAbs, Math.abs(s.ceGex || 0), Math.abs(s.peGex || 0));
    });
    if (!maxAbs) maxAbs = 1;
    var halfW = plotW / 2 - 8;
    var gexX = function (v) { return centerX + (v / maxAbs) * halfW; };

    // strikes descend down the canvas, like an option chain
    var ordered = strikes.slice().sort(function (a, b) { return b.strike - a.strike; });
    var strikeY = function (i) { return TOP + i * rowH + rowH / 2; };
    var priceToY = function (price) {
      var hi = ordered[0].strike, lo = ordered[ordered.length - 1].strike;
      if (hi === lo) return strikeY(0);
      return TOP + rowH / 2 + ((hi - price) / (hi - lo)) * (chartH - rowH);
    };

    ctx.save();
    ctx.strokeStyle = colors.border;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    ctx.moveTo(centerX, TOP);
    ctx.lineTo(centerX, TOP + chartH);
    ctx.stroke();
    ctx.restore();

    ordered.forEach(function (s, i) {
      var y = strikeY(i);
      ctx.fillStyle = colors.textMuted;
      ctx.fillText(fmtPrice(s.strike), 4, y + 3);
      [["ceGex", colors.green], ["peGex", colors.red]].forEach(function (pair) {
        var v = s[pair[0]];
        if (!v) return;
        var x = gexX(v);
        ctx.fillStyle = pair[1];
        ctx.globalAlpha = 0.8;
        ctx.fillRect(Math.min(centerX, x), y - rowH * 0.32, Math.max(1, Math.abs(x - centerX)), rowH * 0.64);
        ctx.globalAlpha = 1;
      });
    });

    function marker(price, color, label, dash) {
      if (price == null) return;
      var y = priceToY(price);
      if (y < TOP || y > TOP + chartH) return;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.4;
      if (dash) ctx.setLineDash([5, 3]);
      ctx.beginPath();
      ctx.moveTo(LEFT, y);
      ctx.lineTo(cssWidth - RIGHT, y);
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = color;
      ctx.fillText(label, LEFT + 6, y - 4);
    }
    marker(profile.spotPrice, colors.blue, "Spot " + fmtPrice(profile.spotPrice), false);
    marker(profile.zeroGammaStrike, colors.amber, "Gamma flip " + fmtPrice(profile.zeroGammaStrike), true);

    ctx.textAlign = "center";
    ctx.fillStyle = colors.textMuted;
    ctx.fillText("← Put GEX", centerX - halfW / 2, TOP + chartH + 16);
    ctx.fillText("Call GEX →", centerX + halfW / 2, TOP + chartH + 16);
    ctx.textAlign = "left";
  }

  // -- KPI tiles ------------------------------------------------------------

  function tile(label, value, detail, cls) {
    return '<div class="stat-block"><div class="k">' + label + '</div>' +
      '<div class="v sm' + (cls ? " " + cls : "") + '">' + value + "</div>" +
      '<div class="d text-muted">' + (detail || "&nbsp;") + "</div></div>";
  }

  function renderTiles() {
    var host = document.querySelector('[data-live="gl-gex-tiles"]');
    if (!host) return;
    var p = state.profile;
    var buckets = visibleBuckets();
    var last = null;
    for (var i = buckets.length - 1; i >= 0; i--) {
      if (buckets[i].netGex != null) { last = buckets[i]; break; }
    }

    var html = "";
    if (last) {
      html += tile("Net GEX (ladder, last bucket)", fmtCompactSigned(last.netGex),
        last.netGex >= 0 ? "Hedging damps moves" : "Hedging amplifies moves",
        last.netGex >= 0 ? "text-green" : "text-red");
      html += tile("Peak Gamma Strike", last.peakGammaStrike == null ? "—" : fmtPrice(last.peakGammaStrike),
        "Largest |GEX| on the ladder");
    } else {
      html += tile("Net GEX (ladder)", "—", "No solved IV + OI yet");
      html += tile("Peak Gamma Strike", "—", "&nbsp;");
    }
    if (p) {
      html += tile("Chain Regime", p.gammaRegime === "POSITIVE" ? "Positive" : "Negative",
        "Full chain · " + fmtCompactSigned(p.netGex),
        p.gammaRegime === "POSITIVE" ? "text-green" : "text-red");
      html += tile("Zero-Gamma Flip", p.zeroGammaStrike == null ? "—" : fmtPrice(p.zeroGammaStrike),
        p.zeroGammaStrike == null ? "No crossing in range"
          : "Spot " + fmtPrice(p.spotPrice) + " is " + (p.spotPrice >= p.zeroGammaStrike ? "above" : "below"));
      html += tile("Walls", (p.callWall == null ? "—" : fmtPrice(p.callWall)) + " / " +
        (p.putWall == null ? "—" : fmtPrice(p.putWall)), "Call wall / Put wall");
    } else {
      html += tile("Chain Regime", "—", "Click Refresh");
      html += tile("Zero-Gamma Flip", "—", "&nbsp;");
      html += tile("Walls", "—", "&nbsp;");
    }
    host.innerHTML = html;
  }

  function renderLegend() {
    var legs = selectedLegs();
    var html = legs.map(function (leg) {
      var color = SLOT_COLOR[leg.slot]();
      var style = leg.side === "PE"
        ? "display:inline-block; width:14px; border-top:2px dashed " + color + "; vertical-align:middle; margin-right:5px;"
        : "display:inline-block; width:14px; border-top:2px solid " + color + "; vertical-align:middle; margin-right:5px;";
      var strike = legStrike(leg.slot, leg.side);
      return '<span><span style="' + style + '"></span>' + leg.side + " " + SLOT_LABEL[leg.slot] +
        (strike == null ? "" : " · " + fmtPrice(strike)) + "</span>";
    }).join("");
    NE.setHTML("gl-legend", html || '<span style="color:var(--text-muted);">No legs selected</span>');
  }

  // -- candidate sell structures -------------------------------------------
  // Renders what /market/sell-candidates priced, in the order the API returned
  // it. Deliberately NOT sorted by credit, R:R or any other "best first"
  // ordering — the comparison between structures is the point, and ranking
  // them would turn a reference table into a recommendation.

  function legLine(l) {
    var cls = l.side === "SELL" ? "text-red" : "text-green";
    return '<span class="' + cls + '" style="font-variant-numeric:tabular-nums;">' +
      l.side + " " + fmtPrice(l.strike) + " " + l.optionType + "</span>" +
      '<span style="color:var(--text-muted);"> @ ' + l.ltp.toFixed(2) + "</span>";
  }

  function candidateCard(c) {
    var riskBadge = c.definedRisk
      ? '<span class="badge badge-green">Defined risk</span>'
      : '<span class="badge badge-red">Undefined risk</span>';
    var maxLoss = c.maxLoss == null
      ? '<b class="text-red">No structural cap</b>'
      : "<b>" + c.maxLoss.toFixed(2) + " pts</b> (" + NE.fmtINR(c.maxLossRupees) + ")";

    var rows = [
      ["Net credit", "<b class=\"text-green\">" + c.netCredit.toFixed(2) + " pts</b> (" + NE.fmtINR(c.netCreditRupees) + ")"],
      ["Max loss", maxLoss],
      ["Risk : reward", c.riskReward == null ? "—" : c.riskReward.toFixed(2) + " : 1"],
      ["Breakeven" + (c.breakevens.length > 1 ? "s" : ""), c.breakevens.map(fmtPrice).join("  ·  ")],
      ["Profit zone", c.profitZonePct == null ? "—" : c.profitZonePct.toFixed(2) + "% of spot"],
    ];

    return '<div style="border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:10px;">' +
      '<div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;">' +
        '<b style="font-size:12.5px;">' + c.name + "</b>" + riskBadge +
      "</div>" +
      '<div style="font-size:11px; margin-bottom:8px; display:flex; flex-wrap:wrap; gap:12px;">' +
        c.legs.map(legLine).join("") +
      "</div>" +
      '<div style="display:flex; flex-wrap:wrap; gap:16px; font-size:11px; margin-bottom:8px;">' +
        rows.map(function (r) {
          return '<span style="color:var(--text-muted);">' + r[0] + ": </span>" + r[1];
        }).join("") +
      "</div>" +
      '<div style="font-size:11px; margin-bottom:8px; color:var(--text-muted);">' +
        "Net position Greeks &mdash; Δ " + c.netDelta.toFixed(3) +
        " · Γ " + c.netGamma.toFixed(5) +
        " · Θ " + c.netTheta.toFixed(2) +
        " · V " + c.netVega.toFixed(2) +
        (c.netGamma < 0 && c.netTheta > 0 ? "  (short gamma, long theta — the premium-selling profile)" : "") +
      "</div>" +
      '<div style="font-size:10.5px; line-height:1.5; color:var(--text-secondary);">' + c.rationale + "</div>" +
      "</div>";
  }

  function renderSellCandidates() {
    var host = document.querySelector('[data-live="gl-sell-list"]');
    if (!host) return;
    var d = state.sell;
    if (!d) {
      host.innerHTML = '<div class="empty-state" style="font-size:11.5px; color:var(--text-muted);">' +
        'Click "Build" to price candidate structures off the live chain.</div>';
      return;
    }
    if (!d.candidates.length) {
      host.innerHTML = '<div class="empty-state" style="font-size:11.5px; color:var(--text-muted);">' +
        "No structure could be priced from this chain — every candidate needs real premiums on all of its legs, " +
        "and at least one leg had no quote.</div>";
      return;
    }
    host.innerHTML = d.candidates.map(candidateCard).join("");
  }

  function loadSellCandidates() {
    var underlying = el("gl-underlying").value;
    var btn = document.querySelector('[data-live="gl-sell-btn"]');
    if (btn) { btn.disabled = true; btn.textContent = "Building…"; }
    NE.fetchJSONLong("/market/sell-candidates?underlying=" + underlying + "&width=15", 30000)
      .then(function (d) {
        state.sell = d;
        NE.setText("gl-sell-sub", "Expiry " + d.expiry + " · spot " + fmtPrice(d.spotPrice) +
          " · lot " + d.lotSize + " · " + d.candidates.length + " structures");
        renderSellCandidates();
      })
      .catch(function (err) {
        NE.setText("gl-sell-sub", "Build failed: " + err.message);
      })
      .then(function () {
        if (btn) { btn.disabled = false; btn.textContent = "Build"; }
      });
  }

  // -- render orchestration -------------------------------------------------

  /** "Expiry sessions only" is a display filter over what was fetched, so the
   *  ladder is loaded once and re-sliced without another broker round-trip. */
  function visibleBuckets() {
    if (!state.data) return [];
    var buckets = state.data.buckets;
    if (el("gl-preset").value === "expiry") {
      return buckets.filter(function (b) { return b.isExpiryDay; });
    }
    return buckets;
  }

  function redraw() {
    var metric = el("gl-metric").value;
    var buckets = visibleBuckets();
    var legs = selectedLegs();

    renderLegend();
    renderTiles();
    NE.setText("gl-chart-title", "Leg Comparison · " + METRICS[metric].label);

    renderLadder(document.querySelector('[data-live="gl-ladder-canvas"]'), {
      buckets: buckets, legs: legs, metric: metric,
      oiMode: el("gl-oi-mode").value,
      showPrice: el("gl-show-price").checked,
    });
    renderGexTimeline(document.querySelector('[data-live="gl-gex-canvas"]'), { buckets: buckets });
    renderGammaProfile(document.querySelector('[data-live="gl-profile-canvas"]'), state.profile);
    renderSellCandidates();

    if (state.data) {
      var sub = buckets.length + " buckets · " + state.data.interval + " · ATM ±" + state.data.depth +
        " (step " + state.data.strikeStep + ") · " + legs.length + " legs";
      NE.setText("gl-chart-sub", sub);
      NE.setText("gl-gex-sub", buckets.length ? "Net gamma exposure across the selected ladder" : "No buckets to show");
      if (!buckets.length && el("gl-preset").value === "expiry") {
        showError("No expiry sessions inside this range — the " + state.data.expiryKind +
          " contract expires " + (state.data.expiryDate || "—") +
          ". Pick a range that contains that date, or switch the filter off.");
      }
    }
  }

  // -- load -----------------------------------------------------------------

  function loadLadder() {
    var underlying = el("gl-underlying").value;
    var from = el("gl-from").value;
    var to = el("gl-to").value;
    var expiry = el("gl-expiry").value;
    var interval = el("gl-interval").value;
    var depth = el("gl-depth").value;

    if (!from || !to) { showError("Pick a From and To date."); return; }
    if (from > to) { showError("From date must be on or before To date."); return; }
    showError(null);
    setLoading(true);

    NE.fetchJSONLong("/market/strike-greeks?underlying=" + underlying + "&from=" + from + "&to=" + to +
        "&expiry=" + expiry + "&interval=" + interval + "&depth=" + depth)
      .then(function (d) {
        state.data = d;
        buildLegTable();
        var meta = "Expiry used: " + (d.expiryDate || "—") + " (" + d.expiryKind + ") · Source: " +
          (d.source === "broker" ? "live broker data" : "mock data — no live broker connected");
        if (d.note) meta += " · " + d.note;
        NE.setText("gl-meta", meta);
        redraw();
        if (!d.buckets.length) showError("No candles for this range — market closed or data unavailable.");
      })
      .catch(function (err) { showError("Load failed: " + err.message); })
      .then(function () { setLoading(false); });
  }

  function loadProfile() {
    var underlying = el("gl-underlying").value;
    var btn = document.querySelector('[data-live="gl-profile-btn"]');
    if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
    NE.fetchJSONLong("/market/gamma-profile?underlying=" + underlying + "&width=15", 30000)
      .then(function (p) {
        state.profile = p;
        NE.setText("gl-profile-sub", "Expiry " + p.expiry + " · spot " + fmtPrice(p.spotPrice) +
          " · gamma " + p.gammaSource);
        redraw();
      })
      .catch(function (err) {
        NE.setText("gl-profile-sub", "Load failed: " + err.message);
      })
      .then(function () {
        if (btn) { btn.disabled = false; btn.textContent = "Refresh"; }
      });
  }

  // -- wiring ---------------------------------------------------------------

  applyPreset();
  buildLegTable();
  redraw();

  el("gl-preset").addEventListener("change", function () { applyPreset(); redraw(); });
  el("gl-interval").addEventListener("change", applyPreset);
  el("gl-depth").addEventListener("change", function () { buildLegTable(); redraw(); });
  el("gl-metric").addEventListener("change", redraw);
  el("gl-oi-mode").addEventListener("change", redraw);
  el("gl-show-price").addEventListener("change", redraw);

  document.querySelectorAll("[data-legs-preset]").forEach(function (btn) {
    btn.addEventListener("click", function () { applyLegPreset(btn.getAttribute("data-legs-preset")); });
  });
  document.querySelectorAll('[data-live="gl-load-btn"]').forEach(function (btn) {
    btn.addEventListener("click", loadLadder);
  });
  document.querySelectorAll('[data-live="gl-profile-btn"]').forEach(function (btn) {
    btn.addEventListener("click", loadProfile);
  });
  document.querySelectorAll('[data-live="gl-sell-btn"]').forEach(function (btn) {
    btn.addEventListener("click", loadSellCandidates);
  });

  // Canvases are sized off clientWidth at render time, so without this a
  // resized window keeps the old width until the next load.
  window.addEventListener("resize", redraw);
})();
