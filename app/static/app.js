(function () {
  function debounce(fn, ms) { var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); }; }
  function params(form) {
    var p = new URLSearchParams();
    new FormData(form).forEach(function (v, k) { if (k !== 'csrf_token') p.append(k, v); });
    return p;
  }
  function getJSON(url, cb) {
    fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); }).then(cb).catch(function () {});
  }

  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (ev) { if (!window.confirm(f.getAttribute('data-confirm'))) ev.preventDefault(); });
  });

  var ef = document.getElementById('entry-form');
  if (ef) {
    var out = document.getElementById('preview');
    var update = function () {
      var item = ef.elements.category.value === 'item';
      ef.querySelectorAll('.only-item').forEach(function (n) { n.hidden = !item; });
      ef.querySelectorAll('.only-money').forEach(function (n) { n.hidden = item; });
      var ct = ef.elements.cap_type.value;
      document.getElementById('cap-row').hidden = ct === 'none';
      var p = params(ef);
      getJSON(ef.getAttribute('data-preview') + '?' + p.toString(), function (d) {
        if (d.error) { out.textContent = d.error; return; }
        out.textContent = 'Wochensatz ' + d.rate + ' – ' + d.rows.map(function (r) {
          return 'Nach ' + r.weeks + (r.weeks === 1 ? ' Woche: ' : ' Wochen: ') + r.text;
        }).join(' · ') + ' · ' + d.cap;
      });
    };
    var deb = debounce(update, 250);
    ef.addEventListener('input', deb); ef.addEventListener('change', deb);
    update();
  }

  var pf = document.getElementById('payment-form');
  if (pf) {
    var pout = document.getElementById('preview');
    var upd = function () {
      var ret = pf.elements.kind && pf.elements.kind.value === 'return';
      pf.querySelectorAll('.only-money').forEach(function (n) { n.hidden = ret; });
      pf.querySelectorAll('.only-return').forEach(function (n) { n.hidden = !ret; });
      var p = params(pf);
      var ex = pf.getAttribute('data-exclude');
      if (ex) p.append('exclude', ex);
      getJSON(pf.getAttribute('data-preview') + '?' + p.toString(), function (d) {
        if (d.error) { pout.textContent = 'Saldo vorher: ' + d.before + ' – ' + d.error; return; }
        var t = 'Saldo vorher: ' + d.before + ' → nachher: ' + d.after +
          ' (offener Aufpreis ' + d.open_surcharge + ', offene Hauptschuld ' + d.open_principal + ')';
        if (d.overpaid) t += ' – Überzahlt um ' + d.overpaid;
        pout.textContent = t;
      });
    };
    var dep = debounce(upd, 250);
    pf.addEventListener('input', dep); pf.addEventListener('change', dep);
    upd();
  }
})();
