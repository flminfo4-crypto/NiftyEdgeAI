/**
 * Shared canvas renderer for the multi-session composite chart used by both
 * market-profile.html (TPO letters, from /market/tpo-profile/composite) and
 * volume-profile.html (volume bars, from /market/volume-profile/composite).
 * Sessions share one continuous price axis so each session's POC lines up
 * with the others — the whole point of a composite view.
 *
 * Usage: NE.renderCompositeChart(canvasEl, sessions, { mode: "tpo" | "volume", ltp, tick, bracketMinutes })
 * `sessions` is oldest-first, matching the two composite endpoints above.
 *
 * Each session gets a fixed minimum column width so a chart with many
 * sessions overflows its (overflow-x:auto) wrapper instead of squeezing
 * columns unreadably thin — that's what gives the wrapper a real scrollbar.
 * Hovering the canvas draws a price/date crosshair, reusing the same
 * renderer so the overlay never drifts out of sync with the chart under it.
 *
 * Below the chart, a labeled stats table (Session / TPO Size / Range / IB /
 * Volume / High / VAH / POC / VAL / Low) runs per session — row labels are
 * drawn once in the left margin, values per column, POC row highlighted —
 * modelled on the session-summary panel in professional TPO tools.
 */
(function () {
  var HEADER_H = 40; // top strip: global title line (y~14) + per-column structure label (y~34)
  var CHART_H = 640;
  var LEFT_MARGIN = 150;
  var RIGHT_MARGIN = 84;
  var MIN_COL_WIDTH = { tpo: 210, volume: 130 };
  var ROW_H = 22; // stats-table row height

  // Fixed palette for TPO letters (cycles per bracket letter, A/B/C/...) and
  // for the per-row profile bars — intentionally independent of the site
  // theme so the profile reads the same in light/dark/terminal mode.
  var LETTER_COLORS = [
    "#f87171", "#fb923c", "#fbbf24", "#facc15", "#a3e635", "#4ade80",
    "#34d399", "#2dd4bf", "#22d3ee", "#60a5fa", "#818cf8", "#a78bfa",
    "#e879f9", "#f472b6",
  ];
  var ROW_OUTSIDE = "#8b93a1";
  var ROW_VALUE_AREA = "#ec1e79";
  var ROW_POC = "#f5c518";
  var IB_TICK = "#ef4444";
  var POC_LINE = "#f5c518";
  var SPRINT_LINE = "#22d3ee";
  var TABLE_ROW_A = "#16241d";
  var TABLE_ROW_B = "#1f2f27";
  var TABLE_ROW_POC = "#7a4a12";

  // The whole canvas — background included — uses this fixed palette rather
  // than the site's light/dark/terminal CSS variables. Every profile color
  // above (letters, bars, table) was chosen against a dark backdrop, the
  // same convention every reference TPO tool uses; pulling colors from the
  // page theme made the chart flip between readable and washed-out
  // depending which theme the user had selected. One fixed palette keeps it
  // legible and identical everywhere.
  var CANVAS_BG = "#0b0f16";
  var CHART_COLORS = {
    border: "#232a3b",
    textMuted: "#8b93a1",
    text: "#e7ecf5",
    blue: "#3b82f6",
    green: "#22c55e",
    amber: "#f5a623",
  };

  function fmt(n) {
    return Math.round(n).toLocaleString("en-IN");
  }

  function extend(base, patch) {
    var out = {}, k;
    for (k in base) if (base.hasOwnProperty(k)) out[k] = base[k];
    for (k in patch) if (patch.hasOwnProperty(k)) out[k] = patch[k];
    return out;
  }

  function dateLabel(sessionDate) {
    var d = new Date(sessionDate + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  }

  // Trailing mean of `keyFn(session)` over the up-to-10 sessions ending at
  // index i — same bounded trailing-window convention the backend uses for
  // vol_ma, just computed client-side since every displayed session is
  // already in hand.
  function trailingAvg(sessions, i, keyFn) {
    var start = Math.max(0, i - 9);
    var sum = 0, n = 0;
    for (var j = start; j <= i; j++) { sum += keyFn(sessions[j]); n++; }
    return n ? sum / n : 0;
  }

  function tableRows(mode, s, i, sessions, opts) {
    if (mode === "tpo") {
      var avgRange = trailingAvg(sessions, i, function (x) { return x.sessionHigh - x.sessionLow; });
      var avgIbRange = trailingAvg(sessions, i, function (x) { return x.ibRange; });
      var tpoCount = s.rows.reduce(function (a, r) { return a + r.count; }, 0);
      return [
        { label: "Session", value: dateLabel(s.sessionDate) },
        { label: "TPO Size", value: opts.bracketMinutes ? (opts.bracketMinutes + " min") : "—" },
        { label: "TPO Count", value: String(tpoCount) },
        { label: "Range / Avg", value: (s.sessionHigh - s.sessionLow).toFixed(0) + " / " + avgRange.toFixed(0) },
        { label: "IB Range / Avg", value: s.ibRange.toFixed(0) + " / " + avgIbRange.toFixed(0) },
        { label: "Volume", value: (s.volume / 1e5).toFixed(1) + "L" },
        { label: "Session High", value: fmt(s.sessionHigh) },
        { label: "Value Area High", value: fmt(s.vah) },
        { label: "POC", value: fmt(s.poc), highlight: true },
        { label: "Value Area Low", value: fmt(s.val) },
        { label: "Session Low", value: fmt(s.sessionLow) },
      ];
    }
    return [
      { label: "Session", value: dateLabel(s.sessionDate) },
      { label: "Volume", value: (s.totalVolume / 1e5).toFixed(1) + "L" },
      { label: "Vol MA(" + s.volMaWindow + ")", value: (s.volMa / 1e5).toFixed(1) + "L" },
      { label: "Session High", value: fmt(s.sessionHigh) },
      { label: "Value Area High", value: fmt(s.vah) },
      { label: "POC", value: fmt(s.poc), highlight: true },
      { label: "Value Area Low", value: fmt(s.val) },
      { label: "Session Low", value: fmt(s.sessionLow) },
    ];
  }

  function letterColor(ch) {
    var idx = ch.charCodeAt(0) - 65; // 'A' = 65
    if (idx < 0) idx = 0;
    return LETTER_COLORS[idx % LETTER_COLORS.length];
  }

  // Draws each character of a TPO letter-string in its own bracket color,
  // truncating (rather than horizontally squashing) whatever doesn't fit —
  // the column is wide enough in practice that this only trims extremes.
  function drawLetters(ctx, letters, x, yMid, rowH, availW) {
    var fontSize = Math.max(7, Math.min(10, rowH - 1));
    ctx.font = fontSize + "px 'SFMono-Regular',Consolas,monospace";
    var charW = ctx.measureText("A").width || fontSize * 0.62;
    var maxChars = Math.max(1, Math.floor(availW / charW));
    var shown = letters.length > maxChars ? letters.slice(0, maxChars) : letters;
    var prevBaseline = ctx.textBaseline;
    ctx.textBaseline = "middle";
    ctx.globalAlpha = rowH < 7 ? 0.75 : 1;
    for (var i = 0; i < shown.length; i++) {
      ctx.fillStyle = letterColor(shown.charAt(i));
      ctx.fillText(shown.charAt(i), x + i * charW, yMid);
    }
    ctx.globalAlpha = 1;
    ctx.textBaseline = prevBaseline;
  }

  function renderCompositeChart(canvas, sessions, opts) {
    opts = opts || {};
    var mode = opts.mode || "tpo";
    var ltp = opts.ltp;
    var tick = opts.tick || 5.0;
    var weightKey = mode === "tpo" ? "count" : "volume";

    // cache the data behind this render so the hover handler can re-invoke
    // us on mousemove without the caller having to know about crosshairs
    canvas._neLastRender = { sessions: sessions, opts: opts };
    attachHover(canvas);

    var rowCount = sessions.length ? tableRows(mode, sessions[0], 0, sessions, opts).length
      : (mode === "tpo" ? 11 : 8);
    var statH = rowCount * ROW_H + 10;
    var axisH = 26;
    var totalH = HEADER_H + CHART_H + statH + axisH;

    var minColWidth = MIN_COL_WIDTH[mode] || 100;
    var containerWidth = (canvas.parentNode && canvas.parentNode.clientWidth) || 900;
    var neededWidth = LEFT_MARGIN + RIGHT_MARGIN + Math.max(1, sessions.length) * minColWidth;
    var cssWidth = Math.max(containerWidth, neededWidth);

    var dpr = window.devicePixelRatio || 1;
    canvas.style.width = cssWidth + "px";
    canvas.style.height = totalH + "px";
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(totalH * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    var colors = CHART_COLORS;

    ctx.fillStyle = CANVAS_BG;
    ctx.fillRect(0, 0, cssWidth, totalH);

    if (!sessions.length) {
      ctx.fillStyle = colors.textMuted;
      ctx.font = "13px sans-serif";
      ctx.fillText("No composite data available", 12, 24);
      return;
    }

    var colWidth = (cssWidth - LEFT_MARGIN - RIGHT_MARGIN) / sessions.length;

    var globalMin = Math.min.apply(null, sessions.map(function (s) { return s.sessionLow; }));
    var globalMax = Math.max.apply(null, sessions.map(function (s) { return s.sessionHigh; }));
    var pad = (globalMax - globalMin) * 0.04 || tick * 4;
    globalMin -= pad;
    globalMax += pad;

    function priceToY(price) {
      return HEADER_H + CHART_H - ((price - globalMin) / (globalMax - globalMin)) * CHART_H;
    }

    // -- header line: underlying / TPO size / interval, like a charting platform's title bar --
    if (opts.underlying) {
      var headerText = opts.underlying +
        (mode === "tpo" ? "  ·  TPO Size: " + tick + "  ·  Distribution: Daily  ·  TPO Interval: " +
          (opts.bracketMinutes || "—") + " min" : "  ·  Volume Profile  ·  Distribution: Daily");
      ctx.fillStyle = colors.textMuted;
      ctx.font = "11.5px sans-serif";
      ctx.fillText(headerText, 4, 14);
    }

    // -- left-margin composite histogram (aggregate weight across all shown sessions) --
    var agg = {};
    sessions.forEach(function (s) {
      s.rows.forEach(function (r) {
        agg[r.price] = (agg[r.price] || 0) + r[weightKey];
      });
    });
    var maxAgg = Math.max.apply(null, Object.keys(agg).map(function (p) { return agg[p]; })) || 1;
    ctx.fillStyle = colors.border;
    Object.keys(agg).forEach(function (priceStr) {
      var price = parseFloat(priceStr);
      var y = priceToY(price);
      var rowH = (CHART_H / ((globalMax - globalMin) / tick));
      var w = (agg[priceStr] / maxAgg) * (LEFT_MARGIN - 10);
      ctx.fillRect(LEFT_MARGIN - 6 - w, y - rowH / 2, w, Math.max(1, rowH - 1));
    });

    // -- gridlines + right-side price axis --
    ctx.strokeStyle = colors.border;
    ctx.fillStyle = colors.textMuted;
    ctx.font = "12px 'SFMono-Regular',Consolas,monospace";
    var priceStep = niceStep(globalMax - globalMin);
    for (var p = Math.ceil(globalMin / priceStep) * priceStep; p <= globalMax; p += priceStep) {
      var gy = priceToY(p);
      ctx.beginPath();
      ctx.moveTo(LEFT_MARGIN, gy);
      ctx.lineTo(cssWidth - RIGHT_MARGIN, gy);
      ctx.globalAlpha = 0.35;
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillText(fmt(p), cssWidth - RIGHT_MARGIN + 6, gy + 4);
    }

    // -- stats table: row labels drawn once in the left margin --
    var statY = HEADER_H + CHART_H + 8;
    var headerRows = tableRows(mode, sessions[0], 0, sessions, opts);
    headerRows.forEach(function (rowSpec, ri) {
      var ry = statY + ri * ROW_H;
      ctx.fillStyle = rowSpec.highlight ? TABLE_ROW_POC : (ri % 2 === 0 ? TABLE_ROW_A : TABLE_ROW_B);
      ctx.globalAlpha = 0.92;
      ctx.fillRect(0, ry, cssWidth - RIGHT_MARGIN, ROW_H - 2);
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#e5e7eb";
      ctx.font = "bold 12px sans-serif";
      ctx.textBaseline = "middle";
      ctx.fillText(rowSpec.label, 8, ry + ROW_H / 2 - 1, LEFT_MARGIN - 12);
      ctx.textBaseline = "alphabetic";
    });

    // -- per-session columns --
    var pocPoints = [];
    sessions.forEach(function (s, i) {
      var x0 = LEFT_MARGIN + i * colWidth;

      var rowH = CHART_H / ((globalMax - globalMin) / tick);
      var maxRowWeight = Math.max.apply(null, s.rows.map(function (r) { return r[weightKey]; })) || 1;

      s.rows.forEach(function (r) {
        var y = priceToY(r.price);
        var inVa = mode === "tpo" ? r.inValueArea : (r.price <= s.vah && r.price >= s.val);
        var isPoc = mode === "tpo" ? r.isPoc : r.price === s.poc;
        var barColor = isPoc ? ROW_POC : (inVa ? ROW_VALUE_AREA : ROW_OUTSIDE);
        var w = (r[weightKey] / maxRowWeight) * (colWidth - 8);
        var barH = Math.max(1, rowH - 1);

        ctx.fillStyle = barColor;
        ctx.globalAlpha = 0.9;
        ctx.fillRect(x0 + 2, y - barH / 2, mode === "tpo" ? Math.max(w, 3) : w, barH);
        ctx.globalAlpha = 1;

        if (mode === "tpo") {
          drawLetters(ctx, r.letters, x0 + 4, y, rowH, colWidth - 12);
        }
      });

      // Initial Balance tick marks — short red bars at IB high/low, the
      // convention for marking IB extremes on a TPO chart.
      if (mode === "tpo") {
        ["ibHigh", "ibLow"].forEach(function (key) {
          var yy = priceToY(s[key]);
          ctx.strokeStyle = IB_TICK;
          ctx.lineWidth = 2;
          ctx.globalAlpha = 0.9;
          ctx.beginPath();
          ctx.moveTo(x0, yy);
          ctx.lineTo(x0 + 20, yy);
          ctx.stroke();
          ctx.globalAlpha = 1;
          ctx.lineWidth = 1;
        });

        // MidLine — the session's high/low midpoint, a common TPO reference
        // level for where price is trading relative to the day's center.
        var midPrice = (s.sessionHigh + s.sessionLow) / 2;
        var midY = priceToY(midPrice);
        ctx.strokeStyle = colors.amber;
        ctx.setLineDash([2, 2]);
        ctx.globalAlpha = 0.65;
        ctx.beginPath();
        ctx.moveTo(x0, midY);
        ctx.lineTo(x0 + colWidth - 4, midY);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.fillStyle = colors.amber;
        ctx.font = "10px sans-serif";
        ctx.textAlign = "right";
        ctx.fillText("Mid", x0 + colWidth - 6, midY - 3);
        ctx.textAlign = "left";

        var extUp = s.sessionHigh - s.ibHigh;
        var extDown = s.ibLow - s.sessionLow;
        if (extUp <= 0.01 && extDown <= 0.01) {
          var lbl = "No IB Breakout";
          ctx.font = "bold 10px sans-serif";
          var tw = ctx.measureText(lbl).width;
          var by = HEADER_H + 18;
          ctx.fillStyle = IB_TICK;
          ctx.globalAlpha = 0.85;
          ctx.fillRect(x0 + 2, by, tw + 10, 16);
          ctx.globalAlpha = 1;
          ctx.fillStyle = "#fff";
          ctx.font = "bold 10px sans-serif";
          ctx.fillText(lbl, x0 + 7, by + 12);
        }
      }

      pocPoints.push({ x: x0 + colWidth / 2, y: priceToY(s.poc) });

      // poor high/low persistence lines — extend right until a later session
      // resolves them (trades through), or to the chart's right edge
      ["poorHigh", "poorLow"].forEach(function (key) {
        var level = s[key];
        if (level == null) return;
        var resolvedAt = sessions.length;
        for (var j = i + 1; j < sessions.length; j++) {
          var resolved = key === "poorHigh" ? sessions[j].sessionHigh > level : sessions[j].sessionLow < level;
          if (resolved) { resolvedAt = j; break; }
        }
        var xEnd = LEFT_MARGIN + resolvedAt * colWidth;
        var y = priceToY(level);
        ctx.strokeStyle = colors.blue;
        ctx.setLineDash([3, 3]);
        ctx.globalAlpha = 0.5;
        ctx.beginPath();
        ctx.moveTo(x0, y);
        ctx.lineTo(xEnd, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.fillStyle = colors.blue;
        ctx.font = "11px sans-serif";
        ctx.fillText(key === "poorHigh" ? "PoorH" : "PoorL", x0 + 2, y - 3);
      });

      // POC reference line — extends right until a later session trades
      // through it (no longer "virgin"), or to the chart's right edge if it
      // never gets revisited within the displayed window.
      (function () {
        var pocResolvedAt = sessions.length;
        for (var j = i + 1; j < sessions.length; j++) {
          if (sessions[j].sessionLow <= s.poc && s.poc <= sessions[j].sessionHigh) { pocResolvedAt = j; break; }
        }
        var xEnd = LEFT_MARGIN + pocResolvedAt * colWidth;
        var py = priceToY(s.poc);
        ctx.strokeStyle = POC_LINE;
        ctx.setLineDash([5, 3]);
        ctx.globalAlpha = 0.8;
        ctx.beginPath();
        ctx.moveTo(x0, py);
        ctx.lineTo(xEnd, py);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.fillStyle = POC_LINE;
        ctx.font = "bold 10.5px sans-serif";
        ctx.fillText(pocResolvedAt === sessions.length ? "VPoC" : "POC", xEnd - 30, py - 3);
      })();

      // sPrint-H / sPrint-L — single-print extremes, the same persistence
      // convention as poorHigh/poorLow: extend right until a later session's
      // range trades through the level, or to the chart's right edge.
      if (mode === "tpo" && s.singlePrints && s.singlePrints.length) {
        [
          { level: Math.max.apply(null, s.singlePrints), label: "sPrint-H" },
          { level: Math.min.apply(null, s.singlePrints), label: "sPrint-L" },
        ].forEach(function (sp) {
          var resolvedAt = sessions.length;
          for (var j = i + 1; j < sessions.length; j++) {
            if (sessions[j].sessionLow <= sp.level && sp.level <= sessions[j].sessionHigh) { resolvedAt = j; break; }
          }
          var xEnd = LEFT_MARGIN + resolvedAt * colWidth;
          var y = priceToY(sp.level);
          ctx.strokeStyle = SPRINT_LINE;
          ctx.setLineDash([2, 2]);
          ctx.globalAlpha = 0.75;
          ctx.beginPath();
          ctx.moveTo(x0, y);
          ctx.lineTo(xEnd, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.globalAlpha = 1;
          ctx.fillStyle = SPRINT_LINE;
          ctx.font = "10px sans-serif";
          ctx.fillText(sp.label, xEnd - ctx.measureText(sp.label).width - 4, y - 3);
        });
      }

      // structure label (TPO mode only — needs a "prev session" comparison)
      if (mode === "tpo" && s.structureLabel) {
        ctx.fillStyle = colors.blue;
        ctx.font = "11px sans-serif";
        ctx.globalAlpha = 0.85;
        ctx.fillText(s.structureLabel, x0 + 2, 34, colWidth - 4);
        ctx.globalAlpha = 1;
      }

      // stats table — this column's values, on top of the shared row backgrounds
      var rows = tableRows(mode, s, i, sessions, opts);
      rows.forEach(function (rowSpec, ri) {
        var ry = statY + ri * ROW_H;
        ctx.fillStyle = rowSpec.highlight ? "#ffd8a8" : "#d1fae5";
        ctx.font = (rowSpec.highlight ? "bold " : "") + "12px 'SFMono-Regular',Consolas,monospace";
        ctx.textBaseline = "middle";
        ctx.fillText(rowSpec.value, x0 + 6, ry + ROW_H / 2 - 1, colWidth - 10);
        ctx.textBaseline = "alphabetic";
      });

      // table column separator
      ctx.strokeStyle = colors.border;
      ctx.globalAlpha = 0.6;
      ctx.beginPath();
      ctx.moveTo(x0, statY);
      ctx.lineTo(x0, statY + rowCount * ROW_H);
      ctx.stroke();
      ctx.globalAlpha = 1;

      // date axis
      ctx.fillStyle = colors.text;
      ctx.font = "bold 12.5px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(dateLabel(s.sessionDate), x0 + colWidth / 2, totalH - 7);
      ctx.textAlign = "left";

      // column separator (chart area)
      ctx.strokeStyle = colors.border;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.moveTo(x0, HEADER_H);
      ctx.lineTo(x0, HEADER_H + CHART_H);
      ctx.stroke();
      ctx.globalAlpha = 1;
    });

    // POC trend line connecting sessions
    ctx.strokeStyle = colors.blue;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    pocPoints.forEach(function (pt, i) { i === 0 ? ctx.moveTo(pt.x, pt.y) : ctx.lineTo(pt.x, pt.y); });
    ctx.stroke();
    ctx.setLineDash([]);

    // live LTP marker
    if (ltp != null && ltp >= globalMin && ltp <= globalMax) {
      var ly = priceToY(ltp);
      ctx.strokeStyle = colors.green;
      ctx.setLineDash([4, 3]);
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      ctx.moveTo(LEFT_MARGIN, ly);
      ctx.lineTo(cssWidth - RIGHT_MARGIN, ly);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle = colors.green;
      ctx.font = "bold 12px 'SFMono-Regular',Consolas,monospace";
      ctx.fillText(fmt(ltp), cssWidth - RIGHT_MARGIN + 6, ly + 4);
    }

    // mouse-hover crosshair — price on the right axis, session date on the
    // bottom axis, both tagged at the cursor's position
    if (opts.hover) {
      drawCrosshair(ctx, opts.hover, {
        globalMin: globalMin, globalMax: globalMax,
        LEFT_MARGIN: LEFT_MARGIN, RIGHT_MARGIN: RIGHT_MARGIN,
        HEADER_H: HEADER_H, CHART_H: CHART_H,
        cssWidth: cssWidth, colWidth: colWidth, sessions: sessions,
      }, colors);
    }
  }

  function drawCrosshair(ctx, hover, scale, colors) {
    var x = hover.x, y = hover.y;
    if (y < scale.HEADER_H || y > scale.HEADER_H + scale.CHART_H) return;
    if (x < scale.LEFT_MARGIN || x > scale.cssWidth - scale.RIGHT_MARGIN) return;

    var price = scale.globalMin +
      (scale.HEADER_H + scale.CHART_H - y) / scale.CHART_H * (scale.globalMax - scale.globalMin);

    ctx.save();
    ctx.strokeStyle = colors.text;
    ctx.globalAlpha = 0.55;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(scale.LEFT_MARGIN, y);
    ctx.lineTo(scale.cssWidth - scale.RIGHT_MARGIN, y);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x, scale.HEADER_H);
    ctx.lineTo(x, scale.HEADER_H + scale.CHART_H);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ctx.restore();

    // price tag — right axis
    var priceText = fmt(price);
    ctx.font = "bold 13px 'SFMono-Regular',Consolas,monospace";
    var tw = ctx.measureText(priceText).width;
    var tagX = scale.cssWidth - scale.RIGHT_MARGIN + 2;
    var tagH = 20;
    ctx.fillStyle = colors.blue;
    ctx.fillRect(tagX, y - tagH / 2, tw + 14, tagH);
    ctx.fillStyle = "#fff";
    ctx.textBaseline = "middle";
    ctx.fillText(priceText, tagX + 7, y + 1);

    // date tag — bottom axis, for the column under the cursor
    var idx = Math.min(scale.sessions.length - 1,
      Math.max(0, Math.floor((x - scale.LEFT_MARGIN) / scale.colWidth)));
    var s = scale.sessions[idx];
    if (s) {
      var dLabel = dateLabel(s.sessionDate);
      ctx.font = "bold 12.5px sans-serif";
      var dw = ctx.measureText(dLabel).width;
      var dx = scale.LEFT_MARGIN + idx * scale.colWidth + scale.colWidth / 2;
      var tagY = scale.HEADER_H + scale.CHART_H + 2;
      ctx.fillStyle = colors.blue;
      ctx.fillRect(dx - dw / 2 - 7, tagY, dw + 14, 18);
      ctx.fillStyle = "#fff";
      ctx.textAlign = "center";
      ctx.fillText(dLabel, dx, tagY + 13);
      ctx.textAlign = "left";
    }
    ctx.textBaseline = "alphabetic";
  }

  // Wired once per canvas. Reuses renderCompositeChart itself to draw the
  // crosshair so the overlay is always built from the exact same scale as
  // whatever is currently on screen — no separately-tracked state to drift.
  function attachHover(canvas) {
    if (canvas._neHoverBound) return;
    canvas._neHoverBound = true;
    var pending = false;
    var pendingEvt; // undefined = no pending update, null = pending clear

    function flush() {
      pending = false;
      var last = canvas._neLastRender;
      if (!last) return;
      var hover = null;
      if (pendingEvt) {
        var rect = canvas.getBoundingClientRect();
        hover = { x: pendingEvt.clientX - rect.left, y: pendingEvt.clientY - rect.top };
      }
      renderCompositeChart(canvas, last.sessions, extend(last.opts, { hover: hover }));
    }

    function schedule(evt) {
      pendingEvt = evt || null;
      if (pending) return;
      pending = true;
      requestAnimationFrame(flush);
    }

    canvas.addEventListener("mousemove", schedule);
    canvas.addEventListener("mouseleave", function () { schedule(null); });
  }

  function niceStep(range) {
    var raw = range / 10;
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var norm = raw / mag;
    var step = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
    return step * mag;
  }

  window.NE = window.NE || {};
  window.NE.renderCompositeChart = renderCompositeChart;
})();
