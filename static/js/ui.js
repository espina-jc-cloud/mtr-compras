/**
 * MTR UI enhancers — Fase 4 del rediseño.
 * Progresivos y sin dependencias: si JS falla, todo sigue funcionando
 * con el submit clásico. Cero cambios de backend.
 *
 *  [data-autofilter]        en un <form method="get">  → auto-submit al cambiar
 *                            selects/fechas e ingresar texto (debounce 450ms).
 *  [data-remember="clave"]  en el mismo form           → guarda el último filtro
 *                            usado y muestra un acceso "Últimos filtros".
 *  table[data-sort]                                     → click en <th> ordena
 *                            filas client-side (numérico o texto, asc/desc).
 *  Formularios: primer input visible recibe foco en /new|/edit;
 *  Cmd/Ctrl+Enter envía el form activo.
 */
(function () {
  "use strict";

  /* ── Auto-filtro ─────────────────────────────────────────────────────────── */
  document.querySelectorAll("form[data-autofilter]").forEach(function (form) {
    var t = null;
    form.addEventListener("change", function (e) {
      var el = e.target;
      if (el.matches("select, input[type=date], input[type=checkbox], input[type=radio]")) form.submit();
    });
    form.addEventListener("input", function (e) {
      if (!e.target.matches("input[type=text], input[type=search]")) return;
      clearTimeout(t);
      t = setTimeout(function () { form.submit(); }, 450);
    });
  });

  /* ── Filtros recordados ──────────────────────────────────────────────────── */
  document.querySelectorAll("form[data-remember]").forEach(function (form) {
    var key = "mtr_filters_" + form.dataset.remember;
    var qs = window.location.search.replace(/^\?/, "");
    // Guardar el filtro actual si hay alguno activo.
    if (qs && Array.from(new URLSearchParams(qs).values()).some(function (v) { return v.trim(); })) {
      try { localStorage.setItem(key, qs); } catch (e) {}
      return;
    }
    // Sin filtros activos: ofrecer el último usado (sin auto-aplicar, sin sorpresas).
    var saved;
    try { saved = localStorage.getItem(key); } catch (e) { return; }
    if (!saved) return;
    var a = document.createElement("a");
    a.href = window.location.pathname + "?" + saved;
    a.textContent = "↺ Últimos filtros";
    a.className = "inline-flex items-center text-xs font-medium text-indigo-700 hover:text-indigo-900 px-2 py-1";
    var reset = document.createElement("button");
    reset.type = "button";
    reset.textContent = "×";
    reset.title = "Olvidar filtros guardados";
    reset.className = "text-gray-300 hover:text-gray-500 text-xs px-1";
    reset.addEventListener("click", function () { try { localStorage.removeItem(key); } catch (e) {} a.remove(); reset.remove(); });
    form.appendChild(a);
    form.appendChild(reset);
  });

  /* ── Ordenamiento de tablas ──────────────────────────────────────────────── */
  function cellValue(row, idx) {
    var c = row.children[idx];
    return c ? c.textContent.trim() : "";
  }
  function asNumber(s) {
    // "1.234,5" → 1234.5 · "—" → NaN
    var n = s.replace(/\./g, "").replace(",", ".").replace(/[^0-9.\-]/g, "");
    return n === "" ? NaN : parseFloat(n);
  }
  document.querySelectorAll("table[data-sort]").forEach(function (table) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var ths = table.querySelectorAll("thead th");
    ths.forEach(function (th, idx) {
      th.style.cursor = "pointer";
      th.title = "Ordenar";
      th.addEventListener("click", function () {
        var dir = th.dataset.dir === "asc" ? "desc" : "asc";
        ths.forEach(function (o) { delete o.dataset.dir; o.querySelectorAll(".sort-mark").forEach(function (m) { m.remove(); }); });
        th.dataset.dir = dir;
        var mark = document.createElement("span");
        mark.className = "sort-mark text-indigo-600 ml-1";
        mark.textContent = dir === "asc" ? "↑" : "↓";
        th.appendChild(mark);
        var rows = Array.from(tbody.rows);
        var numeric = rows.slice(0, 12).every(function (r) {
          var v = cellValue(r, idx);
          return v === "" || v === "—" || !isNaN(asNumber(v));
        });
        rows.sort(function (a, b) {
          var va = cellValue(a, idx), vb = cellValue(b, idx), cmp;
          if (numeric) {
            var na = asNumber(va), nb = asNumber(vb);
            if (isNaN(na)) return 1; if (isNaN(nb)) return -1;
            cmp = na - nb;
          } else {
            cmp = va.localeCompare(vb, "es", { sensitivity: "base" });
          }
          return dir === "asc" ? cmp : -cmp;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  });

  /* ── Formularios: foco inicial + Cmd/Ctrl+Enter ──────────────────────────── */
  if (/\/(new|edit|import)\b/.test(window.location.pathname)) {
    var first = document.querySelector("main form input:not([type=hidden]):not([type=checkbox]):not([type=radio]), main form select, main form textarea");
    if (first && !document.querySelector("[autofocus]")) { try { first.focus({ preventScroll: true }); } catch (e) {} }
  }
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      var form = document.activeElement && document.activeElement.closest("form");
      if (form && form.method.toLowerCase() === "post") form.requestSubmit();
    }
  });
})();
