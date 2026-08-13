/**
 * Wires strategies.html to the live backend — full CRUD over
 * /api/v1/strategies. Built-in strategies (hand-written code in
 * ai-engine/backtest.py) can only be relabeled/activated/deactivated here;
 * custom strategies (built from a template's params) support full
 * add/edit/delete. Both kinds run through the exact same backtest engine,
 * so a strategy created here shows up in the Backtester/Strategy Lab
 * pickers immediately once active.
 */
(function () {
  // Live-refresh cadence. Kept deliberately slow: every open tab is its own
  // polling stream against the broker's rate limit, and Dhan answers a hot
  // one with 429 plus a warning about blocking the account (see the cache
  // notes in backend/app/services/market_data.py).
  var REFRESH_MS = 30000;

  var NE = window.NE;
  var API_BASE = window.NIFTYEDGE_API_BASE || "http://localhost:8000/api/v1";
  var templates = [];
  var strategies = [];
  var editingKey = null; // null = add mode, else editing this key

  function toSnake(s) {
    return s.replace(/([A-Z])/g, function (m) { return "_" + m.toLowerCase(); });
  }

  // The backend returns `params` in camelCase like every other field (see
  // strategy_config_service._params_to_camel), but the template param
  // schema's `name` (and the `data-param` attrs below) are snake_case,
  // matching the Python dataclass fields they configure — convert once here
  // rather than carrying two spellings through the rest of the page.
  function paramsToSnake(params) {
    var out = {};
    Object.keys(params || {}).forEach(function (k) { out[toSnake(k)] = params[k]; });
    return out;
  }

  function apiFetch(path, method, body) {
    var opts = { method: method };
    if (body !== undefined) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    return fetch(API_BASE + path, opts).then(function (res) {
      if (res.status === 204) return null;
      return res.json().then(function (data) {
        if (!res.ok) throw new Error(data.detail || String(res.status));
        return data;
      }, function () {
        if (!res.ok) throw new Error(String(res.status));
        return null;
      });
    });
  }

  // -- ticker/footer, every REFRESH_MS ----------------------------------

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

  // -- param form rendering ------------------------------------------------

  function templateByKey(key) {
    return templates.filter(function (t) { return t.template === key; })[0];
  }

  function renderParamFields(tmpl, values) {
    values = values || {};
    var wrap = document.getElementById("strat-params-wrap");
    wrap.innerHTML = tmpl.params.map(function (p) {
      var val = values[p.name] !== undefined ? values[p.name] : p.default;
      var input;
      if (p.type === "bool") {
        input = '<select class="form-control" data-param="' + p.name + '">' +
          '<option value="true"' + (val ? " selected" : "") + '>Yes</option>' +
          '<option value="false"' + (!val ? " selected" : "") + '>No</option></select>';
      } else {
        var step = p.type === "float" ? "0.01" : "1";
        input = '<input class="form-control" type="number" step="' + step + '" data-param="' + p.name + '"' +
          (p.min != null ? ' min="' + p.min + '"' : "") + (p.max != null ? ' max="' + p.max + '"' : "") +
          ' value="' + val + '" />';
      }
      return '<div class="form-group"><label>' + p.label + "</label>" + input + "</div>";
    }).join("");
  }

  function readParamFields(tmpl) {
    var params = {};
    tmpl.params.forEach(function (p) {
      var el = document.querySelector('[data-param="' + p.name + '"]');
      if (!el) return;
      if (p.type === "bool") params[p.name] = el.value === "true";
      else if (p.type === "int") params[p.name] = parseInt(el.value, 10);
      else params[p.name] = parseFloat(el.value);
    });
    return params;
  }

  // -- add/edit form ------------------------------------------------------

  function showForm(mode, row) {
    editingKey = mode === "edit" ? row.key : null;
    var isBuiltin = mode === "edit" && row.isBuiltin;
    document.querySelector('[data-live="strat-form-title"]').textContent =
      mode === "add" ? "Add Strategy" : "Edit " + row.label;
    document.getElementById("strat-name").value = mode === "edit" ? row.label : "";

    var templateSelect = document.getElementById("strat-template");
    var paramsWrap = document.getElementById("strat-params-wrap");
    if (isBuiltin) {
      // Built-ins are real code — only the display name and active flag
      // are editable here, not the template/params picker.
      templateSelect.parentElement.style.display = "none";
      paramsWrap.style.display = "none";
      paramsWrap.innerHTML = "";
    } else {
      templateSelect.parentElement.style.display = "";
      paramsWrap.style.display = "";
      var tmplKey = mode === "edit" ? row.template : (templates[0] && templates[0].template);
      templateSelect.value = tmplKey;
      templateSelect.disabled = mode === "edit"; // template can't change on an existing strategy
      var tmpl = templateByKey(tmplKey);
      document.querySelector('[data-live="strat-template-desc"]').textContent = tmpl ? tmpl.description : "";
      if (tmpl) renderParamFields(tmpl, mode === "edit" ? paramsToSnake(row.params) : {});
    }

    var activeSwitch = document.getElementById("strat-form-active");
    activeSwitch.classList.toggle("on", mode === "edit" ? row.active : true);

    document.querySelector('[data-live="strat-form-error"]').style.display = "none";
    document.querySelector('[data-live="strat-form-wrap"]').style.display = "";
    document.getElementById("strat-name").focus();
  }

  function hideForm() {
    editingKey = null;
    document.querySelector('[data-live="strat-form-wrap"]').style.display = "none";
  }

  function showFormError(msg) {
    var el = document.querySelector('[data-live="strat-form-error"]');
    el.textContent = msg;
    el.style.display = "";
  }

  function saveForm() {
    var name = document.getElementById("strat-name").value.trim();
    var active = document.getElementById("strat-form-active").classList.contains("on");
    var isBuiltinEdit = editingKey && strategies.some(function (s) { return s.key === editingKey && s.isBuiltin; });

    if (isBuiltinEdit) {
      apiFetch("/strategies/" + encodeURIComponent(editingKey), "PUT", { label: name, active: active })
        .then(function () { hideForm(); load(); })
        .catch(function (e) { showFormError(e.message); });
      return;
    }

    if (!name) { showFormError("Name is required."); return; }
    var tmplKey = document.getElementById("strat-template").value;
    var tmpl = templateByKey(tmplKey);
    if (!tmpl) { showFormError("Pick a template."); return; }
    var params = readParamFields(tmpl);

    var req = editingKey
      ? apiFetch("/strategies/" + encodeURIComponent(editingKey), "PUT", { label: name, params: params, active: active })
      : apiFetch("/strategies", "POST", { label: name, template: tmplKey, params: params, active: active });

    req.then(function () { hideForm(); load(); })
      .catch(function (e) { showFormError(e.message); });
  }

  // -- table ----------------------------------------------------------------

  function fmtParams(row) {
    if (!row.params) return "&mdash;";
    return Object.keys(row.params).map(function (k) {
      var v = row.params[k];
      return k + "=" + (typeof v === "boolean" ? (v ? "y" : "n") : v);
    }).join(", ");
  }

  function fmtUpdated(row) {
    if (!row.updatedAt) return "&mdash;";
    return new Date(row.updatedAt).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  }

  function renderTable() {
    var tbody = document.querySelector('[data-live="strat-tbody"]');
    if (!strategies.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">No strategies.</td></tr>';
      return;
    }
    tbody.innerHTML = strategies.map(function (row) {
      var typeBadge = row.isBuiltin
        ? '<span class="badge badge-blue">Built-in</span>'
        : '<span class="badge badge-amber">' + (templateByKey(row.template) ? templateByKey(row.template).label : row.template) + "</span>";
      var switchHtml = '<div class="switch' + (row.active ? " on" : "") + '" data-action="toggle" data-key="' + row.key + '" data-active="' + row.active + '"></div>';
      var deleteBtn = row.isBuiltin
        ? '<span style="color:var(--text-muted); font-size:11px;">code</span>'
        : '<button class="btn btn-sm btn-outline-red" data-action="delete" data-key="' + row.key + '">Delete</button>';
      return (
        "<tr>" +
        "<td>" + row.label + "</td>" +
        "<td>" + typeBadge + "</td>" +
        '<td style="font-size:11px; color:var(--text-muted);">' + fmtParams(row) + "</td>" +
        "<td>" + switchHtml + "</td>" +
        "<td>" + fmtUpdated(row) + "</td>" +
        '<td class="flex gap-8">' +
        '<button class="btn btn-sm" data-action="edit" data-key="' + row.key + '">Edit</button>' +
        deleteBtn +
        "</td>" +
        "</tr>"
      );
    }).join("");

    NE.setText("strat-count-sub", strategies.length + " total · " +
      strategies.filter(function (r) { return r.active; }).length + " active");
  }

  function toggleActive(key, currentlyActive) {
    apiFetch("/strategies/" + encodeURIComponent(key), "PUT", { active: !currentlyActive })
      .then(load)
      .catch(function (e) { window.alert("Couldn't update: " + e.message); });
  }

  function deleteStrategy(key) {
    if (!window.confirm("Delete this strategy? This can't be undone.")) return;
    apiFetch("/strategies/" + encodeURIComponent(key), "DELETE")
      .then(load)
      .catch(function (e) { window.alert("Couldn't delete: " + e.message); });
  }

  document.querySelector('[data-live="strat-tbody"]').addEventListener("click", function (e) {
    var el = e.target.closest("[data-action]");
    if (!el) return;
    var key = el.getAttribute("data-key");
    var action = el.getAttribute("data-action");
    if (action === "toggle") {
      toggleActive(key, el.getAttribute("data-active") === "true");
    } else if (action === "delete") {
      deleteStrategy(key);
    } else if (action === "edit") {
      var row = strategies.filter(function (s) { return s.key === key; })[0];
      if (row) showForm("edit", row);
    }
  });

  document.querySelector('[data-live="strat-add-btn"]').addEventListener("click", function () { showForm("add", null); });
  document.querySelector('[data-live="strat-cancel-btn"]').addEventListener("click", hideForm);
  document.querySelector('[data-live="strat-save-btn"]').addEventListener("click", saveForm);
  document.getElementById("strat-form-active").addEventListener("click", function () { this.classList.toggle("on"); });
  document.getElementById("strat-template").addEventListener("change", function () {
    var tmpl = templateByKey(this.value);
    document.querySelector('[data-live="strat-template-desc"]').textContent = tmpl ? tmpl.description : "";
    if (tmpl) renderParamFields(tmpl, {});
  });

  // -- load -----------------------------------------------------------------

  function load() {
    apiFetch("/strategies?include_inactive=true", "GET")
      .then(function (rows) {
        strategies = rows;
        renderTable();
      })
      .catch(function () {
        document.querySelector('[data-live="strat-tbody"]').innerHTML =
          '<tr><td colspan="6" style="text-align:center; color:var(--red);">Couldn’t load strategies.</td></tr>';
      });
  }

  function loadTemplates() {
    apiFetch("/strategies/templates", "GET").then(function (rows) {
      templates = rows;
      var select = document.getElementById("strat-template");
      select.innerHTML = templates.map(function (t) {
        return '<option value="' + t.template + '">' + t.label + "</option>";
      }).join("");
    });
  }

  loadTemplates();
  load();
  loadTicker();
  setInterval(loadTicker, REFRESH_MS);
})();
