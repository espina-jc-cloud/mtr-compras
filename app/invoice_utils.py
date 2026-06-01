"""
Funciones de cálculo para el módulo de Factura Cooperativa y Conciliación.

Todas las funciones son puras: reciben datos (listas de ORM rows u objetos
compatibles), devuelven datos calculados. Sin dependencias de FastAPI ni DB.
Testeables con SimpleNamespace o dicts planos.

Regla de oro (invariante del módulo):
    La conciliación usa SIEMPRE _revisado, nunca _recibido.
    Si una línea tiene _revisado = None → aún no revisada → no entra al cálculo.
    Este principio se aplica en reconciliation_data() y en calculate_invoice_totals()
    cuando use_revisado=True.

Flujo esperado:
    1. MTR recibe el parte cooperativo.
    2. Carga los datos en invoice_tonnage_lines / invoice_labor_lines (_recibido).
    3. Entra a invoice_review y confirma/corrige cada línea (_revisado).
    4. Solo después de eso, la factura pasa a 'reviewed' y se habilita la conciliación.
    5. reconciliation_data() usa exclusivamente los campos _revisado.
"""

from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.product_normalize import normalize_product
from app.live_utils import tns_by_product_from_session


# ── Helpers internos ──────────────────────────────────────────────────────────

def _d(val: Any) -> Decimal:
    """
    Convierte un valor nullable (int, float, Decimal, str) a Decimal.
    None / falsy → Decimal("0"). Nunca lanza excepción.
    """
    if val is None:
        return Decimal("0")
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("0")


def _d_or_none(val: Any) -> Decimal | None:
    """Igual que _d() pero preserva None como None (campo no revisado)."""
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None


def _q2(val: Decimal) -> Decimal:
    """Redondea a 2 decimales con ROUND_HALF_UP."""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ── calculate_invoice_totals ──────────────────────────────────────────────────

def calculate_invoice_totals(
    tonnage_lines: list[Any],
    labor_lines:   list[Any],
    cargo_lines:   list[Any],
    supa_pct:         Decimal = Decimal("9.5"),
    contrib_coop_pct: Decimal = Decimal("85.0"),
    iva_pct:          Decimal = Decimal("21.0"),
    iibb_pct:         Decimal | None = None,
) -> dict:
    """
    Calcula los totales económicos de una factura cooperativa.

    Siempre calcula AMBAS bases (_recibido y _revisado) en un solo paso,
    para tener trazabilidad completa.

    Estructura de cálculo:
        base_tonelaje = Σ (tns_habiles * tarifa_hab + tns_inhab * tarifa_inhab + ...)
        base_jornales = Σ (cantidad * precio_unitario)
        base_cargo    = Σ subtotal de cargo_lines

        supa_monto         = base_jornales * supa_pct / 100
        contrib_coop_monto = base_jornales * contrib_coop_pct / 100
        adm_total          = base_jornales + supa_monto + contrib_coop_monto

        subtotal_gravado   = base_tonelaje + base_cargo + adm_total
        iva_monto          = subtotal_gravado * iva_pct / 100
        iibb_monto         = subtotal_gravado * iibb_pct / 100  (0 si iibb_pct is None)
        total_calculado    = subtotal_gravado + iva_monto + iibb_monto

    Args:
        tonnage_lines: lista de OperationLiveInvoiceTonnageLine (o compatibles).
        labor_lines:   lista de OperationLiveInvoiceLaborLine.
        cargo_lines:   lista de OperationLiveInvoiceCargoLine.
        supa_pct, contrib_coop_pct, iva_pct, iibb_pct: porcentajes configurables.

    Returns dict con todos los campos para poblar OperationLiveInvoiceTotals:
        {
            "base_tonelaje_recibido":  Decimal,
            "base_jornales_recibido":  Decimal,
            "base_tonelaje_revisado":  Decimal,   # solo líneas con _revisado != None
            "base_jornales_revisado":  Decimal,   # solo líneas con _revisado != None
            "base_cargo":              Decimal,   # sin dualidad
            "supa_pct":                Decimal,
            "contrib_coop_pct":        Decimal,
            "iva_pct":                 Decimal,
            "iibb_pct":                Decimal | None,
            "supa_monto":              Decimal,   # calculado sobre _revisado
            "contrib_coop_monto":      Decimal,   # calculado sobre _revisado
            "adm_total":               Decimal,   # sobre _revisado
            "iva_monto":               Decimal,   # sobre _revisado
            "iibb_monto":              Decimal,   # sobre _revisado (0 si sin iibb)
            "total_factura_calculado": Decimal,   # total oficial (revisado)
            # Metadatos de completitud:
            "tonnage_lines_total":    int,
            "tonnage_lines_reviewed": int,
            "labor_lines_total":      int,
            "labor_lines_reviewed":   int,
            "all_reviewed":           bool,
        }
    """
    supa_pct         = _d(supa_pct)
    contrib_coop_pct = _d(contrib_coop_pct)
    iva_pct          = _d(iva_pct)
    iibb_pct_d       = _d_or_none(iibb_pct)

    # ── Tonelaje ──────────────────────────────────────────────────────────────
    base_ton_rec = Decimal("0")
    base_ton_rev = Decimal("0")
    t_total = t_reviewed = 0

    for line in tonnage_lines:
        t_total += 1
        tar_h = _d(getattr(line, "tarifa_habiles",          None))
        tar_i = _d(getattr(line, "tarifa_inhabiles",        None))
        tar_e = _d(getattr(line, "tarifa_extraordinarias",  None))

        # Recibido
        h_rec = _d(getattr(line, "tns_habiles_recibido",         None))
        i_rec = _d(getattr(line, "tns_inhabiles_recibido",       None))
        e_rec = _d(getattr(line, "tns_extraordinarias_recibido", None))
        base_ton_rec += h_rec * tar_h + i_rec * tar_i + e_rec * tar_e

        # Revisado: solo si al menos un campo _revisado no es None
        h_rev = _d_or_none(getattr(line, "tns_habiles_revisado",         None))
        i_rev = _d_or_none(getattr(line, "tns_inhabiles_revisado",       None))
        e_rev = _d_or_none(getattr(line, "tns_extraordinarias_revisado", None))

        if any(v is not None for v in (h_rev, i_rev, e_rev)):
            t_reviewed += 1
            base_ton_rev += _d(h_rev) * tar_h + _d(i_rev) * tar_i + _d(e_rev) * tar_e

    # ── Jornales ──────────────────────────────────────────────────────────────
    base_jorn_rec = Decimal("0")
    base_jorn_rev = Decimal("0")
    l_total = l_reviewed = 0

    for line in labor_lines:
        l_total += 1

        # Recibido
        cant_rec  = _d(getattr(line, "cantidad_recibido",        None))
        prec_rec  = _d(getattr(line, "precio_unitario_recibido", None))
        base_jorn_rec += cant_rec * prec_rec

        # Revisado: solo si al menos un campo _revisado no es None
        cant_rev  = _d_or_none(getattr(line, "cantidad_revisado",        None))
        prec_rev  = _d_or_none(getattr(line, "precio_unitario_revisado", None))

        if any(v is not None for v in (cant_rev, prec_rev)):
            l_reviewed += 1
            base_jorn_rev += _d(cant_rev) * _d(prec_rev)

    # ── Ítems especiales (cargo) — sin dualidad ───────────────────────────────
    base_cargo = Decimal("0")
    for line in cargo_lines:
        subtotal = _d_or_none(getattr(line, "subtotal", None))
        if subtotal is not None:
            base_cargo += subtotal
        else:
            # Calcular subtotal si no está guardado explícitamente
            cant  = _d(getattr(line, "cantidad",        None))
            precio = _d(getattr(line, "precio_unitario", None))
            base_cargo += cant * precio

    # ── Cálculos oficiales sobre base revisada ────────────────────────────────
    supa_monto         = _q2(base_jorn_rev * supa_pct / Decimal("100"))
    contrib_coop_monto = _q2(base_jorn_rev * contrib_coop_pct / Decimal("100"))
    adm_total          = _q2(base_jorn_rev + supa_monto + contrib_coop_monto)

    subtotal_gravado   = _q2(base_ton_rev + base_cargo + adm_total)
    iva_monto          = _q2(subtotal_gravado * iva_pct / Decimal("100"))
    iibb_monto         = _q2(subtotal_gravado * iibb_pct_d / Decimal("100")) if iibb_pct_d else Decimal("0")
    total_calculado    = _q2(subtotal_gravado + iva_monto + iibb_monto)

    all_reviewed = (
        t_total > 0
        and t_reviewed == t_total
        and (l_total == 0 or l_reviewed == l_total)
    )

    return {
        "base_tonelaje_recibido":  _q2(base_ton_rec),
        "base_jornales_recibido":  _q2(base_jorn_rec),
        "base_tonelaje_revisado":  _q2(base_ton_rev),
        "base_jornales_revisado":  _q2(base_jorn_rev),
        "base_cargo":              _q2(base_cargo),
        "supa_pct":                supa_pct,
        "contrib_coop_pct":        contrib_coop_pct,
        "iva_pct":                 iva_pct,
        "iibb_pct":                iibb_pct_d,
        "supa_monto":              supa_monto,
        "contrib_coop_monto":      contrib_coop_monto,
        "adm_total":               adm_total,
        "iva_monto":               iva_monto,
        "iibb_monto":              iibb_monto,
        "total_factura_calculado": total_calculado,
        # Metadatos de completitud (para UI y validaciones)
        "tonnage_lines_total":    t_total,
        "tonnage_lines_reviewed": t_reviewed,
        "labor_lines_total":      l_total,
        "labor_lines_reviewed":   l_reviewed,
        "all_reviewed":           all_reviewed,
    }


# ── invoice_differences ───────────────────────────────────────────────────────

def invoice_differences(invoice: Any) -> dict:
    """
    Compara _recibido vs _revisado para todas las líneas de tonelaje y jornales.

    Diseñado para:
    - invoice_detail.html: mostrar qué líneas difieren / no están revisadas.
    - invoice_review.html: pre-cargar el formulario con los valores actuales.

    Args:
        invoice: objeto OperationLiveInvoice (o compatible con .tonnage_lines y
                 .labor_lines como atributos iterables).

    Returns dict:
    {
        "tonnage_diffs": [
            {
                "line": <orm_row>,
                "not_reviewed": bool,   # True si todos los _revisado son None
                "has_any_diff": bool,   # True si algún _revisado != _recibido
                "fields": {
                    "tns_habiles":         {"recibido": Decimal|None, "revisado": Decimal|None, "differs": bool},
                    "tns_inhabiles":       {...},
                    "tns_extraordinarias": {...},
                    "manos":               {"recibido": int|None, "revisado": int|None, "differs": bool},
                },
            }, ...
        ],
        "labor_diffs": [
            {
                "line": <orm_row>,
                "not_reviewed": bool,
                "has_any_diff": bool,
                "fields": {
                    "cantidad":        {"recibido": int|None, "revisado": int|None, "differs": bool},
                    "precio_unitario": {"recibido": Decimal|None, "revisado": Decimal|None, "differs": bool},
                },
            }, ...
        ],
        "unreviewed_tonnage_count": int,
        "unreviewed_labor_count":   int,
        "diff_tonnage_count":       int,   # líneas revisadas con diferencia
        "diff_labor_count":         int,
        "total_issues":             int,   # unreviewed + con_diferencia
    }
    """
    tonnage_diffs = []
    labor_diffs   = []

    for line in (getattr(invoice, "tonnage_lines", None) or []):
        h_rec = _d_or_none(getattr(line, "tns_habiles_recibido",         None))
        i_rec = _d_or_none(getattr(line, "tns_inhabiles_recibido",       None))
        e_rec = _d_or_none(getattr(line, "tns_extraordinarias_recibido", None))
        m_rec = getattr(line, "manos_recibido", None)

        h_rev = _d_or_none(getattr(line, "tns_habiles_revisado",         None))
        i_rev = _d_or_none(getattr(line, "tns_inhabiles_revisado",       None))
        e_rev = _d_or_none(getattr(line, "tns_extraordinarias_revisado", None))
        m_rev = getattr(line, "manos_revisado", None)

        not_reviewed = all(v is None for v in (h_rev, i_rev, e_rev, m_rev))

        def _differs(rec, rev):
            if rev is None:
                return False  # no revisado aún → no "diferencia", sino "sin revisar"
            if rec is None:
                return rev != Decimal("0") if isinstance(rev, Decimal) else rev != 0
            return rec != rev

        fields = {
            "tns_habiles":         {"recibido": h_rec, "revisado": h_rev, "differs": _differs(h_rec, h_rev)},
            "tns_inhabiles":       {"recibido": i_rec, "revisado": i_rev, "differs": _differs(i_rec, i_rev)},
            "tns_extraordinarias": {"recibido": e_rec, "revisado": e_rev, "differs": _differs(e_rec, e_rev)},
            "manos":               {"recibido": m_rec, "revisado": m_rev, "differs": _differs(m_rec, m_rev)},
        }
        has_any_diff = any(f["differs"] for f in fields.values())

        tonnage_diffs.append({
            "line":         line,
            "not_reviewed": not_reviewed,
            "has_any_diff": has_any_diff,
            "fields":       fields,
        })

    for line in (getattr(invoice, "labor_lines", None) or []):
        c_rec  = getattr(line, "cantidad_recibido", None)
        p_rec  = _d_or_none(getattr(line, "precio_unitario_recibido", None))
        c_rev  = getattr(line, "cantidad_revisado", None)
        p_rev  = _d_or_none(getattr(line, "precio_unitario_revisado", None))

        not_reviewed = c_rev is None and p_rev is None

        def _idiffers(rec, rev):
            if rev is None:
                return False
            return rec != rev

        fields = {
            "cantidad":        {"recibido": c_rec, "revisado": c_rev, "differs": _idiffers(c_rec, c_rev)},
            "precio_unitario": {"recibido": p_rec, "revisado": p_rev, "differs": _idiffers(p_rec, p_rev)},
        }
        has_any_diff = any(f["differs"] for f in fields.values())

        labor_diffs.append({
            "line":         line,
            "not_reviewed": not_reviewed,
            "has_any_diff": has_any_diff,
            "fields":       fields,
        })

    unreviewed_t = sum(1 for d in tonnage_diffs if d["not_reviewed"])
    unreviewed_l = sum(1 for d in labor_diffs   if d["not_reviewed"])
    diff_t       = sum(1 for d in tonnage_diffs if not d["not_reviewed"] and d["has_any_diff"])
    diff_l       = sum(1 for d in labor_diffs   if not d["not_reviewed"] and d["has_any_diff"])

    return {
        "tonnage_diffs":           tonnage_diffs,
        "labor_diffs":             labor_diffs,
        "unreviewed_tonnage_count": unreviewed_t,
        "unreviewed_labor_count":   unreviewed_l,
        "diff_tonnage_count":       diff_t,
        "diff_labor_count":         diff_l,
        "total_issues":             unreviewed_t + unreviewed_l + diff_t + diff_l,
    }


# ── reconciliation_data ───────────────────────────────────────────────────────

# Centímetros de tolerancia para status_sugerido en conciliación
RECON_OK_TN   = Decimal("0.50")   # ≤ 0.5 tn: verde
RECON_WARN_TN = Decimal("2.00")   # ≤ 2.0 tn: amarillo


def reconciliation_data(
    bodega_rows:      list[Any],
    tonnage_lines:    list[Any],
    session_products: list[Any],
) -> list[dict]:
    """
    Calcula los datos de conciliación por producto.

    INVARIANTE CENTRAL: usa exclusivamente _revisado de tonnage_lines.
    Las líneas con todos los campos _revisado = None se cuentan pero NO se
    suman. Si hay líneas sin revisar, el producto queda "pendiente de revisión".

    Lógica de asignación de producto en líneas de factura:
    - Si line.product no es None → normalize_product(line.product)
    - Si line.product es None → SIEMPRE "SIN_PRODUCTO". NUNCA se infiere.

    REGLA CRÍTICA: no se infiere producto automáticamente bajo ninguna circunstancia.
    Preferimos una línea marcada como pendiente antes que una asignación incorrecta.
    Las líneas "SIN_PRODUCTO" se agrupan en una entrada especial al final
    de la lista con "excluded=True" (no entran a la conciliación automática).

    Args:
        bodega_rows:      todas las filas de bodega_data de la sesión.
        tonnage_lines:    todas las líneas de tonelaje de la factura.
                          Se usan SOLO los campos _revisado.
        session_products: lista de OperationLiveSessionProduct (o compatible con
                          atributo .product).

    Returns list[dict], una entrada por producto conocido de la sesión, más
    una entrada "SIN_PRODUCTO" al final si hay líneas sin producto asignado.

    Cada dict tiene:
    {
        "product":                   str,
        "tns_mtr":                   Decimal,           # del dashboard live (bodega_data)
        "tns_coop_abordo_revisado":  Decimal | None,    # None si no hay líneas abordo revisadas
        "tns_coop_fiscal_revisado":  Decimal | None,    # None si no hay líneas fiscal revisadas
        "tns_coop_total_revisado":   Decimal | None,
        "diferencia_abordo":         Decimal | None,    # tns_mtr - tns_coop_abordo
        "diferencia_fiscal":         Decimal | None,
        "status_sugerido":           str,               # 'ok'|'diferencia'|'sin_datos'|'pendiente'
        "has_unreviewed_lines":      bool,
        "unreviewed_count":          int,
        "excluded":                  bool,              # True solo para SIN_PRODUCTO
    }
    """
    # ── Paso 1: MTR side (bodega_data) ───────────────────────────────────────
    tns_mtr_by_product = tns_by_product_from_session(bodega_rows)

    # ── Paso 2: Determinar productos del operativo ────────────────────────────
    session_prod_set: list[str] = []
    for sp in (session_products or []):
        p = normalize_product(getattr(sp, "product", None) or "")
        if p and p not in session_prod_set:
            session_prod_set.append(p)

    # Fallback: si no hay session_products, derivar de bodega_rows
    if not session_prod_set:
        session_prod_set = list(tns_mtr_by_product.keys())

    # NOTA: single_product eliminado — nunca se infiere producto automáticamente.

    # ── Paso 3: Procesar líneas de tonelaje de la factura ─────────────────────
    # Acumuladores por producto por guinche_tipo
    # Estructura: {product: {"abordo": Decimal, "fiscal": Decimal}}
    coop_by_product: dict[str, dict[str, Decimal]] = {}
    unreviewed_by_product: dict[str, int] = {}
    sin_producto: dict[str, Decimal] = {"abordo": Decimal("0"), "fiscal": Decimal("0")}
    sin_producto_unreviewed = 0
    has_sin_producto = False

    for line in (tonnage_lines or []):
        # Determinar producto de la línea.
        # REGLA CRÍTICA: si no viene product, es SIN_PRODUCTO. NUNCA inferir.
        raw_prod = getattr(line, "product", None)
        if raw_prod:
            line_prod = normalize_product(raw_prod) or raw_prod.upper().strip()
        else:
            line_prod = "SIN_PRODUCTO"   # producto no informado — nunca inferir

        guinche = (getattr(line, "guinche_tipo", None) or "abordo").lower()
        if guinche not in ("abordo", "fiscal"):
            guinche = "abordo"

        # ── INVARIANTE: usar solo _revisado ──────────────────────────────────
        h_rev = _d_or_none(getattr(line, "tns_habiles_revisado",         None))
        i_rev = _d_or_none(getattr(line, "tns_inhabiles_revisado",       None))
        e_rev = _d_or_none(getattr(line, "tns_extraordinarias_revisado", None))

        line_not_reviewed = all(v is None for v in (h_rev, i_rev, e_rev))

        if line_prod == "SIN_PRODUCTO":
            has_sin_producto = True
            if line_not_reviewed:
                sin_producto_unreviewed += 1
            else:
                tns_line = _d(h_rev) + _d(i_rev) + _d(e_rev)
                sin_producto[guinche] += tns_line
            continue

        # Inicializar acumuladores si primera vez para este producto
        if line_prod not in coop_by_product:
            coop_by_product[line_prod]    = {"abordo": Decimal("0"), "fiscal": Decimal("0")}
            unreviewed_by_product[line_prod] = 0

        if line_not_reviewed:
            unreviewed_by_product[line_prod] += 1
        else:
            tns_line = _d(h_rev) + _d(i_rev) + _d(e_rev)
            coop_by_product[line_prod][guinche] += tns_line

    # ── Paso 4: Construir resultado por producto del operativo ────────────────
    result: list[dict] = []

    for prod in session_prod_set:
        tns_mtr = tns_mtr_by_product.get(prod, Decimal("0"))

        coop_data   = coop_by_product.get(prod, None)
        unrev_count = unreviewed_by_product.get(prod, 0)

        if coop_data is None:
            # No hay líneas de factura para este producto
            coop_abordo = None
            coop_fiscal = None
            coop_total  = None
            diff_abordo = None
            diff_fiscal = None
            status_sug  = "sin_datos"
        else:
            coop_abordo = _q2(coop_data["abordo"]) if coop_data["abordo"] != Decimal("0") else None
            coop_fiscal = _q2(coop_data["fiscal"]) if coop_data["fiscal"] != Decimal("0") else None
            coop_total  = _q2((coop_abordo or Decimal("0")) + (coop_fiscal or Decimal("0")))
            diff_abordo = _q2(tns_mtr - coop_abordo) if coop_abordo is not None else None
            diff_fiscal = _q2(tns_mtr - coop_fiscal) if coop_fiscal is not None else None

            if unrev_count > 0:
                status_sug = "pendiente"
            elif coop_total == Decimal("0"):
                status_sug = "sin_datos"
            else:
                # Usar diferencia_fiscal como referencia principal si existe,
                # si no, diferencia_abordo, si no, comparar con total
                ref_diff = diff_fiscal if diff_fiscal is not None else diff_abordo
                if ref_diff is None:
                    # Solo hay total, comparar directamente
                    ref_diff = _q2(tns_mtr - coop_total)
                abs_diff = abs(ref_diff)
                if abs_diff <= RECON_OK_TN:
                    status_sug = "ok"
                elif abs_diff <= RECON_WARN_TN:
                    status_sug = "diferencia_menor"
                else:
                    status_sug = "diferencia"

        result.append({
            "product":                  prod,
            "tns_mtr":                  _q2(tns_mtr),
            "tns_coop_abordo_revisado": coop_abordo,
            "tns_coop_fiscal_revisado": coop_fiscal,
            "tns_coop_total_revisado":  coop_total,
            "diferencia_abordo":        diff_abordo,
            "diferencia_fiscal":        diff_fiscal,
            "status_sugerido":          status_sug,
            "has_unreviewed_lines":     unrev_count > 0,
            "unreviewed_count":         unrev_count,
            "excluded":                 False,
        })

    # ── Paso 5: Entrada SIN_PRODUCTO si hay líneas sin producto asignado ──────
    if has_sin_producto:
        sp_abordo = _q2(sin_producto["abordo"]) if sin_producto["abordo"] != Decimal("0") else None
        sp_fiscal = _q2(sin_producto["fiscal"]) if sin_producto["fiscal"] != Decimal("0") else None
        sp_total  = None
        if sp_abordo is not None or sp_fiscal is not None:
            sp_total = _q2((sp_abordo or Decimal("0")) + (sp_fiscal or Decimal("0")))

        result.append({
            "product":                  "SIN_PRODUCTO",
            "tns_mtr":                  Decimal("0"),    # MTR no tiene contraparte directa
            "tns_coop_abordo_revisado": sp_abordo,
            "tns_coop_fiscal_revisado": sp_fiscal,
            "tns_coop_total_revisado":  sp_total,
            "diferencia_abordo":        None,
            "diferencia_fiscal":        None,
            "status_sugerido":          "pendiente",
            "has_unreviewed_lines":     sin_producto_unreviewed > 0,
            "unreviewed_count":         sin_producto_unreviewed,
            "excluded":                 True,   # requiere asignación manual de producto
        })

    return result


