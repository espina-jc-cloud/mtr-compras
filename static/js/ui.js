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

  /* ── Confirmación destructiva (reemplaza confirm() nativo) ───────────────────
   *  form[data-confirm="mensaje"]   → intercepta el submit y pide confirmación
   *                                   con un diálogo propio (rojo, foco atrapado).
   *  [data-confirm] en link/botón    → idem para acciones que no son un form.
   *  Fallback: si <dialog> no está soportado, usa confirm() nativo.
   *  window.mtrConfirm(msg, onOk, opts) queda disponible para uso manual.
   */
  var _confirmDialog = null;
  function ensureConfirmDialog() {
    if (_confirmDialog) return _confirmDialog;
    var d = document.createElement("dialog");
    d.className = "mtr-confirm rounded-2xl shadow-2xl p-0 w-[90vw] max-w-sm backdrop:bg-black/50";
    d.innerHTML =
      '<div class="p-5">' +
        '<div class="flex items-start gap-3">' +
          '<span class="w-9 h-9 rounded-full bg-red-100 text-red-600 flex items-center justify-center flex-shrink-0 text-lg">⚠</span>' +
          '<div class="min-w-0">' +
            '<p class="text-sm font-bold text-gray-900 mb-1">Confirmar acción</p>' +
            '<p class="mtr-confirm-msg text-sm text-gray-600 break-words"></p>' +
          '</div>' +
        '</div>' +
        '<div class="flex gap-2 mt-5">' +
          '<button type="button" class="mtr-confirm-cancel flex-1 border border-gray-200 text-gray-600 hover:bg-gray-50 text-sm font-semibold py-2.5 rounded-xl">Cancelar</button>' +
          '<button type="button" class="mtr-confirm-ok flex-1 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold py-2.5 rounded-xl">Eliminar</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(d);
    _confirmDialog = d;
    return d;
  }
  function mtrConfirm(message, onOk, opts) {
    opts = opts || {};
    // Fallback sin <dialog>.
    if (typeof HTMLDialogElement === "undefined" || !HTMLDialogElement.prototype.showModal) {
      if (window.confirm(message || "¿Confirmás esta acción?")) onOk();
      return;
    }
    var d = ensureConfirmDialog();
    d.querySelector(".mtr-confirm-msg").textContent = message || "¿Confirmás esta acción?";
    var okBtn = d.querySelector(".mtr-confirm-ok");
    var cancel = d.querySelector(".mtr-confirm-cancel");
    okBtn.textContent = opts.okLabel || "Eliminar";
    function done(run) {
      okBtn.onclick = cancel.onclick = d.onclick = d.oncancel = null;
      try { d.close(); } catch (e) {}
      if (run) onOk();
    }
    okBtn.onclick = function () { done(true); };
    cancel.onclick = function () { done(false); };
    d.onclick = function (e) { if (e.target === d) done(false); };   // click en backdrop
    d.oncancel = function () { done(false); };                       // tecla Esc
    d.showModal();
    cancel.focus();
  }
  window.mtrConfirm = mtrConfirm;

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form[data-confirm]") || form._mtrConfirmed) return;
    e.preventDefault();
    mtrConfirm(form.getAttribute("data-confirm"), function () {
      form._mtrConfirmed = true;
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    });
  }, true);

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-confirm]");
    if (!el || el.tagName === "FORM") return;
    if (el.closest("form[data-confirm]")) return;   // el form ya lo maneja
    e.preventDefault();
    mtrConfirm(el.getAttribute("data-confirm"), function () {
      if (el.tagName === "A" && el.href) window.location.href = el.href;
      else if (el.form) { el.form._mtrConfirmed = true; el.form.requestSubmit(); }
    });
  }, true);
})();
