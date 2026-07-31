/**
 * Wires cpr-dashboard.html to the live backend. NIFTY50 and SENSEX are fetched
 * sequentially (not in parallel) since Dhan's quote endpoint is throttled to
 * ~1/sec globally — the backend self-throttles too, but staying sequential
 * here avoids relying on that alone. Loads once (no auto-refresh).
 */
(function () {
  var NE = window.NE;
  var symbolFor = { n: "NIFTY50", s: "SENSEX" };

  function setupText(prefix, d) {
    var bias = d.cprRelationship === "Ascending" ? "Bullish" : d.cprRelationship === "Descending" ? "Bearish" : "Neutral";
    var headline = d.cprWidthLabel + " " + d.cprRelationship.toLowerCase() + " CPR — " + bias + " tilt";
    var body;
    if (d.cprRelationship === "Ascending") {
      body = "A break above TC " + NE.fmtNum(d.tc, 2) + " can push it toward R1 " + NE.fmtNum(d.r1, 2) +
        ". As long as it holds above PDL " + NE.fmtNum(d.pdl, 2) + ", buyers stay in control; a close back below BC " +
        NE.fmtNum(d.bc, 2) + " would flip the bias neutral-to-cautious.";
    } else if (d.cprRelationship === "Descending") {
      body = "A break below BC " + NE.fmtNum(d.bc, 2) + " can push it toward S1 " + NE.fmtNum(d.s1, 2) +
        ". As long as it holds below PDH " + NE.fmtNum(d.pdh, 2) + ", sellers stay in control; a close back above TC " +
        NE.fmtNum(d.tc, 2) + " would flip the bias neutral-to-constructive.";
    } else {
      body = "Pivot is roughly unchanged from the prior session — expect a range-bound tone between BC " +
        NE.fmtNum(d.bc, 2) + " and TC " + NE.fmtNum(d.tc, 2) + " until price commits through PDH " +
        NE.fmtNum(d.pdh, 2) + " or PDL " + NE.fmtNum(d.pdl, 2) + ".";
    }
    return { headline: headline, body: body };
  }

  function render(prefix, d) {
    NE.setText(prefix + "-ltp", NE.fmtNum(d.ltp, 2));
    NE.setText(prefix + "-change", NE.fmtSigned(d.change, 2) + " (" + NE.fmtSigned(d.changePct, 2) + "%)");
    NE.setText(prefix + "-width-label", d.cprWidthLabel);
    NE.setText(prefix + "-width-pct", d.cprWidthPct.toFixed(2) + "%");
    var relEl = document.querySelector('[data-live="' + prefix + '-relationship"]');
    if (relEl) {
      relEl.textContent = d.cprRelationship;
      relEl.classList.remove("up", "down");
      relEl.classList.add(d.cprRelationship === "Ascending" ? "up" : d.cprRelationship === "Descending" ? "down" : "");
    }
    NE.setText(prefix + "-day-range", d.dayRange.toFixed(2) + " pts");
    NE.setText(prefix + "-tc", NE.fmtNum(d.tc, 2));
    NE.setText(prefix + "-pivot", NE.fmtNum(d.pivot, 2));
    NE.setText(prefix + "-bc", NE.fmtNum(d.bc, 2));
    NE.setText(prefix + "-r1", NE.fmtNum(d.r1, 2));
    NE.setText(prefix + "-r2", NE.fmtNum(d.r2, 2));
    NE.setText(prefix + "-s1", NE.fmtNum(d.s1, 2));
    NE.setText(prefix + "-s2", NE.fmtNum(d.s2, 2));
    NE.setText(prefix + "-pdh", NE.fmtNum(d.pdh, 2));
    NE.setText(prefix + "-pdl", NE.fmtNum(d.pdl, 2));

    var text = setupText(prefix, d);
    NE.setText(prefix + "-setup-headline", text.headline);
    NE.setText(prefix + "-setup-body", text.body);
  }

  NE.fetchJSON("/market/cpr-dashboard?underlying=" + symbolFor.n)
    .then(function (n) {
      render("n", n);
      return NE.fetchJSON("/market/cpr-dashboard?underlying=" + symbolFor.s);
    })
    .then(function (s) {
      render("s", s);
      return NE.fetchJSON("/market/quote?symbols=INDIAVIX");
    })
    .then(function (quotes) {
      if (quotes.length) NE.setText("vix-ltp", quotes[0].ltp.toFixed(2));
      NE.setText("cpr-timestamp", new Date().toLocaleString("en-IN", {
        day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
      }) + " IST");
    })
    .catch(function () {
      NE.setText("cpr-timestamp", "Live data unavailable — showing static example");
    });
})();
