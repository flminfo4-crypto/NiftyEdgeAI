/**
 * Shared canvas renderer for the multi-session composite chart used by both
 * market-profile.html (TPO letters, from /market/tpo-profile/composite) and
 * volume-profile.html (volume bars, from /market/volume-profile/composite).
 * Sessions share one continuous price axis so each session's POC lines up
 * with the others — the whole point of a composite view.
 *
 * Usage: NE.renderCompositeChart(canvasEl, sessions, { mode: "tpo" | "volume", ltp, tick })
 * `sessions` is oldest-first, matching the two composite endpoints above.
 */
(function () {
  var HEADER_H = 20;
  var CHART_H = 520;
  var LEFT_MARGIN = 110;
  var RIGHT_MARGIN = 70;

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.body).getPropertyValue(name);
    return v ? v.trim() : fallback;
  }

  function fmt(n) {
    return Math.round(n).toLocaleString("en-IN");
  }

  function statLines(mode, s) {
    if (mode === "tpo") {
      return [
        "IBR: " + s.ibRange.toFixed(0),
        "IB: " + fmt(s.ibLow) + " - " + fmt(s.ibHigh),
        "PoC: " + fmt(s.poc),
        "VA: " + fmt(s.val) + " - " + fmt(s.vah) + " (" + (s.vah - s.val).toFixed(0) + ")",
        "Vol: " + (s.volume / 1e5).toFixed(1) + "L",
        "Vol MA(" + s.volMaWindow + "): " + (s.volMa / 1e5).toFixed(1) + "L",
      ];
    }
    return [
      "PoC: " + fmt(s.poc),
      "VA: " + fmt(s.val) + " - " + fmt(s.vah) + " (" + (s.vah - s.val).toFixed(0) + ")",
      "Vol: " + (s.totalVolume / 1e5).toFixed(1) + "L",
      "Vol MA(" + s.volMaWindow + "): " + (s.volMa / 1e5).toFixed(1) + "L",
    ];
  }

  function dateLabel(sessionDate) {
    var d = new Date(sessionDate + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  }

  function renderCompositeChart(canvas, sessions, opts) {
    opts = opts || {};
    var mode = opts.mode || "tpo";
    var ltp = opts.ltp;
    var tick = opts.tick || 5.0;
    var weightKey = mode === "tpo" ? "count" : "volume";

    var statH = mode === "tpo" ? 108 : 76;
    var axisH = 24;
    var totalH = HEADER_H + CHART_H + statH + axisH;

    var cssWidth = canvas.clientWidth || canvas.parentNode.clientWidth || 900;
    var dpr = window.devicePixelRatio || 1;
    canvas.style.height = totalH + "px";
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(totalH * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    var colors = {
      border: cssVar("--border", "#232a3b"),
      textMuted: cssVar("--text-muted", "#7c8797"),
      text: cssVar("--text-primary", "#e7ecf5"),
      pink: cssVar("--pink", "#ec4899"),
      blue: cssVar("--blue", "#3b82f6"),
      green: cssVar("--green", "#22c55e"),
      amber: cssVar("--amber", "#f5a623"),
      purple: cssVar("--purple", "#a855f7"),
      red: cssVar("--red", "#ef4444"),
    };

    ctx.clearRect(0, 0, cssWidth, totalH);

    if (!sessions.length) {
      ctx.fillStyle = colors.textMuted;
      ctx.font = "12px sans-serif";
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
    ctx.font = "10px 'SFMono-Regular',Consolas,monospace";
    var priceStep = niceStep(globalMax - globalMin);
    for (var p = Math.ceil(globalMin / priceStep) * priceStep; p <= globalMax; p += priceStep) {
      var gy = priceToY(p);
      ctx.beginPath();
      ctx.moveTo(LEFT_MARGIN, gy);
      ctx.lineTo(cssWidth - RIGHT_MARGIN, gy);
      ctx.globalAlpha = 0.35;
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillText(fmt(p), cssWidth - RIGHT_MARGIN + 6, gy + 3);
    }

    // -- per-session columns --
    var pocPoints = [];
    sessions.forEach(function (s, i) {
      var x0 = LEFT_MARGIN + i * colWidth;

      if (mode === "tpo") {
        ctx.fillStyle = colors.purple;
        ctx.globalAlpha = 0.08;
        ctx.fillRect(x0, priceToY(s.ibHigh), colWidth - 4, priceToY(s.ibLow) - priceToY(s.ibHigh));
        ctx.globalAlpha = 1;
      }

      ctx.fillStyle = colors.amber;
      ctx.globalAlpha = 0.07;
      ctx.fillRect(x0, priceToY(s.vah), colWidth - 4, priceToY(s.val) - priceToY(s.vah));
      ctx.globalAlpha = 1;

      var rowH = CHART_H / ((globalMax - globalMin) / tick);
      var maxRowWeight = Math.max.apply(null, s.rows.map(function (r) { return r[weightKey]; })) || 1;

      s.rows.forEach(function (r) {
        var y = priceToY(r.price);
        var inVa = mode === "tpo" ? r.inValueArea : (r.price <= s.vah && r.price >= s.val);
        var isPoc = mode === "tpo" ? r.isPoc : r.price === s.poc;
        var color = isPoc ? colors.blue : (inVa ? colors.pink : colors.textMuted);

        if (mode === "tpo") {
          ctx.fillStyle = color;
          ctx.font = Math.max(6, Math.min(9, rowH - 1)) + "px 'SFMono-Regular',Consolas,monospace";
          ctx.globalAlpha = rowH < 5 ? 0.6 : 1;
          ctx.fillText(r.letters, x0 + 2, y + rowH / 2 - 1, colWidth - 8);
          ctx.globalAlpha = 1;
        } else {
          var w = (r[weightKey] / maxRowWeight) * (colWidth - 8);
          ctx.fillStyle = color;
          ctx.globalAlpha = 0.85;
          ctx.fillRect(x0 + 2, y - Math.max(1, rowH - 1) / 2, w, Math.max(1, rowH - 1));
          ctx.globalAlpha = 1;
        }
      });

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
        ctx.font = "9px sans-serif";
        ctx.fillText(key === "poorHigh" ? "PoorH" : "PoorL", x0 + 2, y - 2);
      });

      // structure label (TPO mode only — needs a "prev session" comparison)
      if (mode === "tpo" && s.structureLabel) {
        ctx.fillStyle = colors.blue;
        ctx.font = "9px sans-serif";
        ctx.globalAlpha = 0.85;
        ctx.fillText(s.structureLabel, x0 + 2, 12, colWidth - 4);
        ctx.globalAlpha = 1;
      }

      // stat box
      var statY = HEADER_H + CHART_H + 14;
      ctx.fillStyle = colors.textMuted;
      ctx.font = "10px 'SFMono-Regular',Consolas,monospace";
      statLines(mode, s).forEach(function (line, li) {
        ctx.fillText(line, x0 + 2, statY + li * 14, colWidth - 4);
      });

      // date axis
      ctx.fillStyle = colors.text;
      ctx.font = "10.5px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(dateLabel(s.sessionDate), x0 + colWidth / 2, totalH - 6);
      ctx.textAlign = "left";

      // column separator
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
      ctx.font = "bold 10px 'SFMono-Regular',Consolas,monospace";
      ctx.fillText(fmt(ltp), cssWidth - RIGHT_MARGIN + 6, ly + 3);
    }
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
