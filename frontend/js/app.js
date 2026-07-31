// NiftyEdge Pro Trading Terminal — shared behavior

var NIFTYEDGE_THEME_KEY = 'niftyedge-theme';

function niftyedgeApplyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.querySelectorAll('[data-set-theme]').forEach(function (btn) {
    btn.classList.toggle('active', btn.getAttribute('data-set-theme') === theme);
  });
}

document.addEventListener('DOMContentLoaded', function () {
  // Theme switcher: any element with [data-set-theme="light|dark|terminal"]
  var current = document.documentElement.getAttribute('data-theme') || 'dark';
  niftyedgeApplyTheme(current);
  document.querySelectorAll('[data-set-theme]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var theme = btn.getAttribute('data-set-theme');
      try { localStorage.setItem(NIFTYEDGE_THEME_KEY, theme); } catch (e) {}
      niftyedgeApplyTheme(theme);
    });
  });

  // Live clock in topbar
  var clockEl = document.querySelector('[data-clock]');
  function tick() {
    if (!clockEl) return;
    var d = new Date();
    var h = d.getHours() % 12 || 12;
    var m = String(d.getMinutes()).padStart(2, '0');
    var s = String(d.getSeconds()).padStart(2, '0');
    var ap = d.getHours() >= 12 ? 'PM' : 'AM';
    clockEl.textContent = h + ':' + m + ':' + s + ' ' + ap;
  }
  tick();
  setInterval(tick, 1000);

  // Generic tab groups: <div class="tabs" data-tabgroup="x"> with .tab[data-tab] and
  // panels marked [data-tabpanel][data-tabgroup="x"]
  document.querySelectorAll('.tabs[data-tabgroup]').forEach(function (group) {
    var name = group.getAttribute('data-tabgroup');
    var panels = document.querySelectorAll('[data-tabpanel][data-tabgroup="' + name + '"]');
    group.querySelectorAll('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        group.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        var target = tab.getAttribute('data-tab');
        panels.forEach(function (p) {
          p.style.display = (p.getAttribute('data-tabpanel') === target) ? '' : 'none';
        });
      });
    });
  });

  // Simple toggle switches (visual only)
  document.querySelectorAll('.switch[data-toggle]').forEach(function (sw) {
    sw.addEventListener('click', function () {
      sw.classList.toggle('on');
    });
  });
});
