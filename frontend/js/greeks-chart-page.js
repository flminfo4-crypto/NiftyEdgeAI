/**
 * Wires greeks-chart.html to GET /market/atm-analysis — the same rolling-ATM
 * endpoint data-analysis.html uses, since it already derives Delta/Gamma per
 * intraday bucket from the real traded premium. Everything (candles, ATM
 * strike, Delta, Gamma, OI) is drawn as ONE combined overlay chart rather
 * than separate cards, so price action and the derived Greeks/OI can be read
 * against the same timeline at a glance. Click-triggered only, same as
 * data-analysis.html: one load can fan out into several Dhan option-contract
 * fetches server-side, so it must never sit on a polling timer (see the 429
 * history documented in backend/app/services/market_data.py).
 */
(function () {
  var NE = window.NE;

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.body).getPropertyValue(name);
    return v ? v.trim() : fallback;
  }

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

  // -- filters -----------------------------------------------------------

  function defaultDates() {
    // default: the most recent weekday (markets closed on weekends)
    var d = new Date();
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
    var iso = d.toISOString().slice(0, 10);
    var fromEl = document.getElementById("gc-from");
    var toEl = document.getElementById("gc-to");
    if (fromEl && !fromEl.value) fromEl.value = iso;
    if (toEl && !toEl.value) toEl.value = iso;
  }
  defaultDates();

  function showError(msg) {
    var el = document.querySelector('[data-live="gc-error"]');
    if (!el) return;
    if (msg) { el.textContent = msg; el.style.display = ""; }
    else { el.style.display = "none"; }
  }

  function setLoading(loading) {
    document.querySelectorAll('[data-live="gc-load-btn"]').forEach(function (btn) {
      btn.disabled = loading;
      btn.textContent = loading ? "Loading…" : "Load Data";
    });
  }

  // -- shared chart helpers -------------------------------------------------

  var TOP_MARGIN = 14, BOTTOM_MARGIN = 30, LEFT_MARGIN = 64, RIGHT_MARGIN = 48, CHART_H = 460;
  var PRICE_PANE_RATIO = 0.74; // fraction of CHART_H given to price/Delta/Gamma
  var PANE_GAP = 10;
  var MIN_BUCKET_WIDTH = 15; // CSS px per bucket — a candle body + 2 OI bars

  function niceStep(range) {
    if (!range) return 1;
    var raw = range / 5;
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var norm = raw / mag;
    var step = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
    return step * mag;
  }

  function extend(base, patch) {
    var out = {}, k;
    for (k in base) if (base.hasOwnProperty(k)) out[k] = base[k];
    for (k in patch) if (patch.hasOwnProperty(k)) out[k] = patch[k];
    return out;
  }

  function fmtPrice(n) {
    return Math.round(n).toLocaleString("en-IN");
  }

  // OI runs into the millions — compact to Indian Cr/L, same convention as
  // data-analysis-page.js's fmtQty, so the two pages read consistently.
  function fmtQty(n) {
    if (n == null) return "—";
    var v = Number(n), abs = Math.abs(v);
    if (abs >= 1e7) return (v / 1e7).toFixed(2) + " Cr";
    if (abs >= 1e5) return (v / 1e5).toFixed(2) + " L";
    return Math.round(v).toLocaleString("en-IN");
  }

  function fmtQtySigned(n) {
    return n == null ? "—" : (n >= 0 ? "+" : "−") + fmtQty(Math.abs(n));
  }

  // Bound once per canvas. renderCombinedChart stashes its latest args on
  // canvas._neCombined before drawing, so a hover-triggered redraw always
  // reads whatever is currently stored rather than a stale closure.
  function attachHover(canvas, stateKey, redraw) {
    if (canvas["_neHoverBound_" + stateKey]) return;
    canvas["_neHoverBound_" + stateKey] = true;
    var pending = false, pendingX;

    function flush() {
      pending = false;
      if (!canvas[stateKey]) return;
      redraw(pendingX);
    }

    function schedule(x) {
      pendingX = x;
      if (pending) return;
      pending = true;
      requestAnimationFrame(flush);
    }

    canvas.addEventListener("mousemove", function (evt) {
      var rect = canvas.getBoundingClientRect();
      schedule(evt.clientX - rect.left);
    });
    canvas.addEventListener("mouseleave", function () { schedule(null); });
  }

  function nearestIndex(plotX, n, hoverX) {
    var idx = 0, best = Infinity;
    for (var i = 0; i < n; i++) {
      var d = Math.abs(plotX(i) - hoverX);
      if (d < best) { best = d; idx = i; }
    }
    return idx;
  }

  function drawCrosshairLine(ctx, x, colors, top, height) {
    ctx.save();
    ctx.strokeStyle = colors.textMuted;
    ctx.globalAlpha = 0.5;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, top + height);
    ctx.stroke();
    ctx.restore();
  }

  function drawTooltipBox(ctx, x, cssWidth, lines, colors) {
    ctx.font = "11px sans-serif";
    var tw = Math.max.apply(null, lines.map(function (t) { return ctx.measureText(t).width; }));
    var boxW = tw + 16, boxH = lines.length * 15 + 8;
    var boxX = Math.min(x + 8, cssWidth - boxW - 4);
    var boxY = TOP_MARGIN + 4;
    ctx.fillStyle = colors.bg;
    ctx.strokeStyle = colors.border;
    ctx.globalAlpha = 0.97;
    ctx.fillRect(boxX, boxY, boxW, boxH);
    ctx.strokeRect(boxX, boxY, boxW, boxH);
    ctx.globalAlpha = 1;
    ctx.fillStyle = colors.text;
    lines.forEach(function (line, li) {
      ctx.fillText(line, boxX + 8, boxY + 14 + li * 15);
    });
  }

  // A series' own min/max mapped onto the full price pane height — used to
  // overlay Delta and Gamma (utterly different units from price) on the same
  // pixels as the candles. The axis this feeds is a shared 0-100% scale, not
  // a real unit; hover always reports the real value, never the %.
  function normalizer(valueArrays, paneTop, paneH) {
    var vals = [];
    valueArrays.forEach(function (arr) { arr.forEach(function (v) { if (v != null) vals.push(v); }); });
    var lo = vals.length ? Math.min.apply(null, vals) : 0;
    var hi = vals.length ? Math.max.apply(null, vals) : 1;
    if (lo === hi) { lo -= 1; hi += 1; }
    return {
      lo: lo, hi: hi,
      y: function (v) { return v == null ? null : paneTop + paneH - ((v - lo) / (hi - lo)) * paneH; },
    };
  }

  function drawLine(ctx, plotX, y, values, color) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    var started = false;
    for (var i = 0; i < values.length; i++) {
      var v = values[i];
      var py = y(v);
      if (py == null) { started = false; continue; }
      var x = plotX(i);
      if (!started) { ctx.moveTo(x, py); started = true; } else { ctx.lineTo(x, py); }
    }
    ctx.stroke();
  }

  // -- the combined chart ---------------------------------------------------
  // One canvas, three overlapping layers sharing the same x (time) axis:
  //  1. Price pane (top ~74%): OHLC candles + ATM strike (dashed) on the
  //     real price scale (left axis), with CE/PE Delta and CE/PE Gamma
  //     overlaid on top, each normalized to a shared 0-100% scale (right
  //     axis) since their units have nothing in common with price.
  //  2. OI pane (bottom ~26%): CE (left) / PE (right) OI change bars, colored
  //     on their own sign — green build-up, red unwind — kept separate from
  //     price so real OI numbers aren't distorted onto the price scale.
  // A single hover crosshair spans both panes and one tooltip lists every
  // series' real value at that bucket.

  function renderCombinedChart(canvas, categories, data, opts) {
    if (!canvas) return;
    opts = opts || {};
    canvas._neCombined = { categories: categories, data: data, opts: opts };
    attachHover(canvas, "_neCombined", function (x) {
      renderCombinedChart(canvas, canvas._neCombined.categories, canvas._neCombined.data, extend(canvas._neCombined.opts, { hover: x }));
    });

    var colors = {
      border: cssVar("--border", "#1e2635"),
      text: cssVar("--text-primary", "#e7ebf3"),
      textMuted: cssVar("--text-muted", "#5c6478"),
      bg: cssVar("--bg-card-alt", "#131a27"),
      green: cssVar("--green", "#22c55e"),
      red: cssVar("--red", "#ef4444"),
      amber: cssVar("--amber", "#f5a623"),
      blue: cssVar("--blue", "#3b82f6"),
      purple: cssVar("--purple", "#a855f7"),
      pink: cssVar("--pink", "#ec4899"),
      // No app-wide CSS var reads as a hue distinct from amber/pink/purple/
      // blue/green/red, and PE Gamma needs its own color so it doesn't read
      // as "the same line" as the ATM strike overlay (amber) — fixed hex,
      // same convention composite-profile-chart.js uses for its extended
      // per-letter palette.
      orange: "#fb923c",
    };

    var n = categories.length;
    var containerWidth = (canvas.parentNode && canvas.parentNode.clientWidth) || 900;
    var neededWidth = LEFT_MARGIN + RIGHT_MARGIN + Math.max(1, n) * MIN_BUCKET_WIDTH;
    var cssWidth = Math.max(containerWidth, neededWidth);

    var totalH = TOP_MARGIN + CHART_H + BOTTOM_MARGIN;
    var dpr = window.devicePixelRatio || 1;
    canvas.style.width = cssWidth + "px";
    canvas.style.height = totalH + "px";
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(totalH * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, totalH);

    if (!n) {
      ctx.fillStyle = colors.textMuted;
      ctx.font = "12px sans-serif";
      ctx.fillText("No data — pick a range and click Load Data.", 12, 24);
      return;
    }

    var pricePaneH = Math.round(CHART_H * PRICE_PANE_RATIO);
    var oiPaneTop = TOP_MARGIN + pricePaneH + PANE_GAP;
    var oiPaneH = CHART_H - pricePaneH - PANE_GAP;

    var plotW = cssWidth - LEFT_MARGIN - RIGHT_MARGIN;
    var plotX = function (i) { return LEFT_MARGIN + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW); };
    var bucketPx = plotW / Math.max(1, n - 1);

    // -- price scale (left axis): candle highs/lows + ATM strike overlay --
    var priceVals = [];
    data.ohlc.forEach(function (c) { if (c) { priceVals.push(c.high, c.low); } });
    data.atmStrike.forEach(function (v) { if (v != null) priceVals.push(v); });
    var priceLo = priceVals.length ? Math.min.apply(null, priceVals) : 0;
    var priceHi = priceVals.length ? Math.max.apply(null, priceVals) : 1;
    if (priceLo === priceHi) { priceLo -= 1; priceHi += 1; }
    var pricePad = (priceHi - priceLo) * 0.08;
    priceLo -= pricePad; priceHi += pricePad;
    var priceY = function (v) { return TOP_MARGIN + pricePaneH - ((v - priceLo) / (priceHi - priceLo)) * pricePaneH; };

    // -- normalized overlays sharing the price pane's pixels --------------
    var deltaNorm = normalizer([data.ceDelta, data.peDelta], TOP_MARGIN, pricePaneH);
    var gammaNorm = normalizer([data.ceGamma, data.peGamma], TOP_MARGIN, pricePaneH);

    // -- OI pane scale ------------------------------------------------------
    var oiVals = data.ceOiChange.concat(data.peOiChange).filter(function (v) { return v != null; }).concat([0]);
    var oiLo = Math.min.apply(null, oiVals), oiHi = Math.max.apply(null, oiVals);
    if (oiLo === oiHi) { oiLo -= 1; oiHi += 1; }
    var oiPad = (oiHi - oiLo) * 0.12;
    oiLo -= oiPad; oiHi += oiPad;
    var oiY = function (v) { return oiPaneTop + oiPaneH - ((v - oiLo) / (oiHi - oiLo)) * oiPaneH; };
    var oiZeroY = oiY(0);
    var oiBarW = Math.max(1, Math.min(6, bucketPx * 0.32));

    // -- price gridlines + left axis labels --------------------------------
    ctx.font = "10.5px 'SFMono-Regular',Consolas,monospace";
    var priceStep = niceStep(priceHi - priceLo);
    for (var pv = Math.ceil(priceLo / priceStep) * priceStep; pv <= priceHi; pv += priceStep) {
      var gy = priceY(pv);
      ctx.strokeStyle = colors.border;
      ctx.beginPath();
      ctx.moveTo(LEFT_MARGIN, gy);
      ctx.lineTo(cssWidth - RIGHT_MARGIN, gy);
      ctx.globalAlpha = 0.3;
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = colors.textMuted;
      ctx.fillText(fmtPrice(pv), 4, gy + 3);
    }

    // -- right axis: shared 0-100% scale for Delta/Gamma overlays ----------
    ctx.textAlign = "left";
    [0, 0.25, 0.5, 0.75, 1].forEach(function (pct) {
      var gy = TOP_MARGIN + pricePaneH - pct * pricePaneH;
      ctx.fillStyle = colors.textMuted;
      ctx.fillText((pct * 100).toFixed(0) + "%", cssWidth - RIGHT_MARGIN + 6, gy + 3);
    });

    // -- x-axis time labels (shared, drawn once at the very bottom) --------
    var maxTicks = Math.max(2, Math.floor(plotW / 90));
    var tickEvery = Math.max(1, Math.ceil(n / maxTicks));
    ctx.textAlign = "center";
    ctx.fillStyle = colors.textMuted;
    for (var i = 0; i < n; i += tickEvery) {
      ctx.fillText(categories[i], plotX(i), TOP_MARGIN + CHART_H + 16);
    }
    ctx.textAlign = "left";

    // -- candles + ATM strike overlay --------------------------------------
    var bodyW = Math.max(1, Math.min(8, bucketPx * 0.42));
    for (var ci = 0; ci < n; ci++) {
      var c = data.ohlc[ci];
      if (!c) continue;
      var x = plotX(ci);
      var up = c.close >= c.open;
      var col = up ? colors.green : colors.red;
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, priceY(c.high));
      ctx.lineTo(x, priceY(c.low));
      ctx.stroke();
      var yO = priceY(c.open), yC = priceY(c.close);
      var top = Math.min(yO, yC), h = Math.max(1, Math.abs(yC - yO));
      ctx.fillStyle = col;
      ctx.globalAlpha = 0.9;
      ctx.fillRect(x - bodyW / 2, top, bodyW, h);
      ctx.globalAlpha = 1;
    }
    ctx.strokeStyle = colors.amber;
    ctx.lineWidth = 1.3;
    ctx.setLineDash([4, 3]);
    ctx.globalAlpha = 0.85;
    ctx.beginPath();
    var started = false;
    for (var ai = 0; ai < n; ai++) {
      var ov = data.atmStrike[ai];
      if (ov == null) { started = false; continue; }
      var ox = plotX(ai), oy = priceY(ov);
      if (!started) { ctx.moveTo(ox, oy); started = true; } else { ctx.lineTo(ox, oy); }
    }
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;

    // -- normalized Delta / Gamma overlays ---------------------------------
    drawLine(ctx, plotX, deltaNorm.y, data.ceDelta, colors.purple);
    drawLine(ctx, plotX, deltaNorm.y, data.peDelta, colors.pink);
    drawLine(ctx, plotX, gammaNorm.y, data.ceGamma, colors.blue);
    drawLine(ctx, plotX, gammaNorm.y, data.peGamma, colors.orange);

    // -- OI pane: separator, zero line, CE/PE bars -------------------------
    ctx.strokeStyle = colors.border;
    ctx.globalAlpha = 0.6;
    ctx.beginPath();
    ctx.moveTo(LEFT_MARGIN, oiPaneTop - PANE_GAP / 2);
    ctx.lineTo(cssWidth - RIGHT_MARGIN, oiPaneTop - PANE_GAP / 2);
    ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.font = "10px 'SFMono-Regular',Consolas,monospace";
    ctx.fillStyle = colors.textMuted;
    ctx.fillText("OI Δ " + fmtQty(oiHi), 4, oiPaneTop + 8);
    ctx.fillText("0", 4, oiZeroY + 3);
    ctx.fillText("OI Δ " + fmtQty(oiLo), 4, oiPaneTop + oiPaneH);

    ctx.strokeStyle = colors.textMuted;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    ctx.moveTo(LEFT_MARGIN, oiZeroY);
    ctx.lineTo(cssWidth - RIGHT_MARGIN, oiZeroY);
    ctx.stroke();
    ctx.globalAlpha = 1;

    for (var oi = 0; oi < n; oi++) {
      var x2 = plotX(oi);
      var ce = data.ceOiChange[oi], pe = data.peOiChange[oi];
      if (ce != null) {
        var ceY1 = oiY(ce);
        ctx.fillStyle = ce >= 0 ? colors.green : colors.red;
        ctx.globalAlpha = 0.85;
        ctx.fillRect(x2 - 1 - oiBarW, Math.min(oiZeroY, ceY1), oiBarW, Math.max(1, Math.abs(ceY1 - oiZeroY)));
        ctx.globalAlpha = 1;
      }
      if (pe != null) {
        var peY1 = oiY(pe);
        ctx.fillStyle = pe >= 0 ? colors.green : colors.red;
        ctx.globalAlpha = 0.85;
        ctx.fillRect(x2 + 1, Math.min(oiZeroY, peY1), oiBarW, Math.max(1, Math.abs(peY1 - oiZeroY)));
        ctx.globalAlpha = 1;
      }
    }

    if (opts.hover != null) {
      drawCombinedHover(ctx, opts.hover, {
        n: n, plotX: plotX, categories: categories, data: data,
        priceY: priceY, cssWidth: cssWidth, oiPaneTop: oiPaneTop, oiPaneH: oiPaneH,
      }, colors);
    }
  }

  function drawCombinedHover(ctx, hoverX, scale, colors) {
    var idx = nearestIndex(scale.plotX, scale.n, hoverX);
    var x = scale.plotX(idx);
    drawCrosshairLine(ctx, x, colors, TOP_MARGIN, scale.oiPaneTop + scale.oiPaneH - TOP_MARGIN);

    var d = scale.data;
    var c = d.ohlc[idx];
    var tipLines = [scale.categories[idx]];
    if (c) {
      tipLines.push("O " + fmtPrice(c.open) + "  H " + fmtPrice(c.high) + "  L " + fmtPrice(c.low) + "  C " + fmtPrice(c.close));
      ctx.beginPath();
      ctx.arc(x, scale.priceY(c.close), 3, 0, Math.PI * 2);
      ctx.fillStyle = c.close >= c.open ? colors.green : colors.red;
      ctx.fill();
    } else {
      tipLines.push("No candle for this bucket");
    }
    tipLines.push("ATM Strike: " + (d.atmStrike[idx] == null ? "—" : fmtPrice(d.atmStrike[idx])));
    tipLines.push("CE Delta: " + (d.ceDelta[idx] == null ? "—" : d.ceDelta[idx].toFixed(2)) +
      "   PE Delta: " + (d.peDelta[idx] == null ? "—" : d.peDelta[idx].toFixed(2)));
    tipLines.push("CE Gamma: " + (d.ceGamma[idx] == null ? "—" : d.ceGamma[idx].toFixed(5)) +
      "   PE Gamma: " + (d.peGamma[idx] == null ? "—" : d.peGamma[idx].toFixed(5)));
    tipLines.push("CE OI Δ: " + fmtQtySigned(d.ceOiChange[idx]) + "   PE OI Δ: " + fmtQtySigned(d.peOiChange[idx]));

    drawTooltipBox(ctx, x, scale.cssWidth, tipLines, colors);
  }

  // -- load ----------------------------------------------------------------

  function loadCharts() {
    var underlying = document.getElementById("gc-underlying").value;
    var from = document.getElementById("gc-from").value;
    var to = document.getElementById("gc-to").value;
    var expiry = document.getElementById("gc-expiry").value;
    var interval = document.getElementById("gc-interval").value;

    if (!from || !to) { showError("Pick a From and To date."); return; }
    if (from > to) { showError("From date must be on or before To date."); return; }
    showError(null);
    setLoading(true);

    NE.fetchJSONLong("/market/atm-analysis?underlying=" + underlying + "&from=" + from + "&to=" + to +
        "&expiry=" + expiry + "&interval=" + interval)
      .then(function (d) {
        var rows = d.rows || [];
        var categories = rows.map(function (r) { return r.time; });

        renderCombinedChart(document.querySelector('[data-live="gc-combined-canvas"]'), categories, {
          ohlc: rows.map(function (r) {
            return r.spotOpen == null ? null : { open: r.spotOpen, high: r.spotHigh, low: r.spotLow, close: r.spotClose };
          }),
          atmStrike: rows.map(function (r) { return r.atmStrike; }),
          ceDelta: rows.map(function (r) { return r.ceDelta; }),
          peDelta: rows.map(function (r) { return r.peDelta; }),
          ceGamma: rows.map(function (r) { return r.ceGamma; }),
          peGamma: rows.map(function (r) { return r.peGamma; }),
          ceOiChange: rows.map(function (r) { return r.ceOiChange; }),
          peOiChange: rows.map(function (r) { return r.peOiChange; }),
        });

        NE.setText("gc-chart-sub", rows.length + " buckets · " + interval + " · rolling ATM (step " + d.strikeStep + ")");
        var meta = "Expiry used: " + (d.expiryDate || "—") + " (" + d.expiryKind + ") · Source: " +
          (d.source === "broker" ? "live broker data" : "mock data — no live broker connected");
        if (d.note) meta += " · " + d.note;
        NE.setText("gc-meta", meta);

        if (!rows.length) showError("No candles for this range — market closed or data unavailable.");
      })
      .catch(function (err) {
        showError("Load failed: " + err.message);
      })
      .then(function () { setLoading(false); });
  }

  document.querySelectorAll('[data-live="gc-load-btn"]').forEach(function (btn) {
    btn.addEventListener("click", loadCharts);
  });

  // Redraw at the current size/theme on window resize or theme change —
  // the canvas is sized off clientWidth at render time, so a stale canvas
  // would otherwise keep yesterday's width until the next Load Data click.
  window.addEventListener("resize", function () {
    var canvas = document.querySelector('[data-live="gc-combined-canvas"]');
    if (canvas && canvas._neCombined) {
      renderCombinedChart(canvas, canvas._neCombined.categories, canvas._neCombined.data, canvas._neCombined.opts);
    }
  });
})();
