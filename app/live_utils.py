"""
Helpers de cálculo para el módulo Operativos en Tiempo Real.

Todas las funciones son puras: reciben datos, devuelven datos calculados.
Sin dependencias de FastAPI, SQLAlchemy ni modelos — testeables de forma aislada.

Decisión de diseño sobre acumulados
─────────────────────────────────────
El dashboard live incluye TODOS los turnos de la sesión (abiertos y cerrados)
en los acumulados. Esto es intencional: el operativo se muestra "vivo" con
el dato más reciente, aunque el turno esté abierto. Cualquier función que
quiera solo turnos cerrados debe filtrar la lista de bodega_rows antes de pasar.
"""

from __future__ import annotations
from decimal import Decimal
from typing import Any

from app.product_normalize import normalize_product


# ── Etiquetas de UI ───────────────────────────────────────────────────────────

MOTIVO_LABELS: dict[str, str] = {
    "espera_estiba":          "Espera de estiba",
    "falta_camiones":         "Falta de camiones",
    "lluvia":                 "Lluvia",
    "cambio_turno":           "Cambio de turno",
    "falla_equipo":           "Falla de equipo",
    "espera_ajuste":          "Espera de ajuste",
    "falla_energia":          "Falla de energía",
    "otro":                   "Otro",
}

FUNCION_LABELS: dict[str, str] = {
    "guinchero":               "Guinchero",
    "limpieza":                "Limpieza",
    "pinche":                  "Pinche",
    "maquinista_retro":        "Maquinista Retro",
    "maquinista_autoelevador": "Maquinista Autoelevador",
    "apuntador":               "Apuntador",
    "otro":                    "Otro",
}

EQUIPO_TIPOS: list[str] = [
    "pala", "retro", "autoelevador", "guinche", "tractor", "otro"
]

TURNO_RANGES: list[str] = ["00A06", "06A12", "12A18", "18A00"]

MOTIVO_TIPOS: list[str] = list(MOTIVO_LABELS.keys())

# Umbrales para el semáforo de reconciliación
DELTA_OK_KG      = 500     # ≤ 500 kg: verde
DELTA_WARN_KG    = 2_000   # ≤ 2000 kg: amarillo


# ── Tiempo ────────────────────────────────────────────────────────────────────

def parse_hhmm(s: str | None) -> int | None:
    """
    'HH:MM' → minutos desde medianoche.
    Devuelve None si el string es vacío, None, o no parseable.
    """
    if not s:
        return None
    try:
        parts = s.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def hhmm_diff_minutes(desde: str | None, hasta: str | None) -> int | None:
    """
    Diferencia en minutos entre dos strings 'HH:MM'.
    Soporta turno nocturno: si hasta < desde (en minutos), asume cruce de medianoche
    y suma 1440 (24h). Devuelve None si alguno es inválido o hasta es None.

    Ejemplo: desde='23:00', hasta='01:30' → 150 minutos (correcto).
    """
    d = parse_hhmm(desde)
    h = parse_hhmm(hasta)
    if d is None or h is None:
        return None
    diff = h - d
    if diff < 0:
        diff += 1440  # cruce de medianoche
    return diff


def format_minutes(minutes: int | None) -> str:
    """
    Formatea minutos como 'Xh Ym'. Ej: 95 → '1h 35m', 45 → '45m', 0 → '0m'.
    """
    if minutes is None:
        return "—"
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def hhmm_to_hours(desde: str | None, hasta: str | None) -> float | None:
    """Diferencia en horas (float con 2 decimales). None si no calculable."""
    mins = hhmm_diff_minutes(desde, hasta)
    if mins is None:
        return None
    return round(mins / 60, 2)


# ── Cálculos por fila de bodega ───────────────────────────────────────────────

def bodega_mtr_total(row: Any) -> int:
    """
    Total MTR de una fila de bodega_data.
    row puede ser un modelo ORM o cualquier objeto con los atributos necesarios.
    """
    dep = getattr(row, "kg_deposito_mtr", 0) or 0
    dir_ = getattr(row, "kg_directo_mtr", 0) or 0
    cv  = getattr(row, "kg_cv_mtr", 0) or 0
    return dep + dir_ + cv


def bodega_delta(row: Any) -> int | None:
    """
    Delta MTR vs Cooperativa para una fila de bodega_data.
    delta = kg_total_mtr - kg_coop. None si kg_coop es None.
    """
    mtr_total = bodega_mtr_total(row)
    kg_coop = getattr(row, "kg_coop", None)
    if kg_coop is None:
        return None
    return mtr_total - kg_coop


def delta_status(delta: int | None) -> str:
    """
    Semáforo de reconciliación.
    Devuelve: 'ok' | 'warn' | 'alert' | 'none'
    """
    if delta is None:
        return "none"
    abs_delta = abs(delta)
    if abs_delta <= DELTA_OK_KG:
        return "ok"
    if abs_delta <= DELTA_WARN_KG:
        return "warn"
    return "alert"


# ── Cálculos por turno ────────────────────────────────────────────────────────

def shift_summary_by_product(bodega_rows: list[Any]) -> dict[str, dict]:
    """
    Agrupa filas de bodega_data por producto y acumula los totales del turno.

    Devuelve:
    {
        "MAP": {
            "viajes_mtr":     int | None,
            "kg_deposito":    int,
            "kg_directo":     int,
            "kg_cv":          int,
            "kg_total_mtr":   int,
            "viajes_coop":    int | None,
            "kg_coop":        int | None,
            "delta":          int | None,
            "delta_status":   str,          # 'ok' | 'warn' | 'alert' | 'none'
        },
        ...
    }

    Si una bodega tiene múltiples filas para el mismo producto (caso multiproducto
    dentro de la misma bodega), se suman.
    """
    result: dict[str, dict] = {}

    for row in bodega_rows:
        prod = getattr(row, "product", None) or "—"
        if prod not in result:
            result[prod] = {
                "viajes_mtr":   None,
                "kg_deposito":  0,
                "kg_directo":   0,
                "kg_cv":        0,
                "kg_total_mtr": 0,
                "viajes_coop":  None,
                "kg_coop":      None,
                "delta":        None,
                "delta_status": "none",
            }
        r = result[prod]

        # Viajes: suma (None + int = int)
        vmtr = getattr(row, "viajes_mtr", None)
        if vmtr is not None:
            r["viajes_mtr"] = (r["viajes_mtr"] or 0) + vmtr

        vcoop = getattr(row, "viajes_coop", None)
        if vcoop is not None:
            r["viajes_coop"] = (r["viajes_coop"] or 0) + vcoop

        # Kg MTR
        r["kg_deposito"]  += getattr(row, "kg_deposito_mtr", 0) or 0
        r["kg_directo"]   += getattr(row, "kg_directo_mtr",  0) or 0
        r["kg_cv"]        += getattr(row, "kg_cv_mtr",       0) or 0
        r["kg_total_mtr"]  = r["kg_deposito"] + r["kg_directo"] + r["kg_cv"]

        # Kg Coop
        kc = getattr(row, "kg_coop", None)
        if kc is not None:
            r["kg_coop"] = (r["kg_coop"] or 0) + kc

    # Calcular delta, semáforo y viajes_total ahora que están todos los totales
    for prod, r in result.items():
        if r["kg_coop"] is not None:
            r["delta"] = r["kg_total_mtr"] - r["kg_coop"]
            r["delta_status"] = delta_status(r["delta"])
        has_vm = r["viajes_mtr"] is not None
        has_vc = r["viajes_coop"] is not None
        r["viajes_total"] = (
            (r["viajes_mtr"] or 0) + (r["viajes_coop"] or 0)
            if (has_vm or has_vc) else None
        )

    return result


def shift_totals(bodega_rows: list[Any]) -> dict:
    """
    Totales globales de un turno (todos los productos sumados).

    Devuelve:
    {
        "kg_deposito":  int,
        "kg_directo":   int,
        "kg_cv":        int,
        "kg_total_mtr": int,
        "kg_coop":      int | None,
        "delta":        int | None,
        "delta_status": str,
        "viajes_mtr":   int | None,   # None si ninguna bodega tiene dato
        "viajes_coop":  int | None,
        "viajes_total": int | None,   # viajes_mtr + viajes_coop (si al menos uno existe)
    }
    """
    kg_dep = kg_dir = kg_cv = kg_coop_total = 0
    v_mtr = v_coop = 0
    has_coop = has_v_mtr = has_v_coop = False

    for row in bodega_rows:
        kg_dep  += getattr(row, "kg_deposito_mtr", 0) or 0
        kg_dir  += getattr(row, "kg_directo_mtr",  0) or 0
        kg_cv   += getattr(row, "kg_cv_mtr",       0) or 0
        kc       = getattr(row, "kg_coop", None)
        if kc is not None:
            kg_coop_total += kc
            has_coop = True
        vm = getattr(row, "viajes_mtr", None)
        if vm is not None:
            v_mtr += vm
            has_v_mtr = True
        vc = getattr(row, "viajes_coop", None)
        if vc is not None:
            v_coop += vc
            has_v_coop = True

    kg_mtr = kg_dep + kg_dir + kg_cv
    kg_coop = kg_coop_total if has_coop else None
    d = (kg_mtr - kg_coop) if kg_coop is not None else None

    viajes_mtr   = v_mtr  if has_v_mtr  else None
    viajes_coop  = v_coop if has_v_coop else None
    viajes_total = (
        (viajes_mtr or 0) + (viajes_coop or 0)
        if (has_v_mtr or has_v_coop) else None
    )

    return {
        "kg_deposito":  kg_dep,
        "kg_directo":   kg_dir,
        "kg_cv":        kg_cv,
        "kg_total_mtr": kg_mtr,
        "kg_coop":      kg_coop,
        "delta":        d,
        "delta_status": delta_status(d),
        "viajes_mtr":   viajes_mtr,
        "viajes_coop":  viajes_coop,
        "viajes_total": viajes_total,
    }


# ── Cálculos acumulados (nivel sesión) ────────────────────────────────────────

def session_product_accumulated(all_bodega_rows: list[Any], product: str) -> dict:
    """
    Acumulado de un producto a lo largo de todos los turnos de la sesión.

    INCLUYE turnos abiertos y cerrados. Esto es intencional: el dashboard
    muestra el operativo en tiempo real, no solo lo que ya se cerró.

    all_bodega_rows: TODAS las filas de bodega_data de la sesión (sin filtrar
    por turno). El caller es responsable de pasar las correctas.

    Devuelve:
    {
        "kg_deposito":    int,
        "kg_directo":     int,
        "kg_cv":          int,
        "kg_total_mtr":   int,
        "kg_coop":        int | None,
        "delta":          int | None,
        "delta_status":   str,
    }
    """
    filtered = [
        row for row in all_bodega_rows
        if (getattr(row, "product", None) or "") == product
    ]
    return shift_totals(filtered)


def session_totals_by_product(
    all_bodega_rows: list[Any],
    session_products: list[Any],
) -> list[dict]:
    """
    Devuelve una lista de dicts, uno por producto de la sesión, con:
    - product
    - client
    - kg_contracted
    - kg_deposito, kg_directo, kg_cv, kg_total_mtr
    - kg_coop
    - delta, delta_status
    - restan        (kg_contracted - kg_coop | None si kg_contracted o kg_coop es None)
    - progreso_pct  (0–100 float | None si kg_contracted es None)

    INCLUYE turnos abiertos y cerrados (comportamiento explícito del dashboard live).
    """
    results = []
    for sp in session_products:
        prod = sp.product
        acum = session_product_accumulated(all_bodega_rows, prod)

        contracted = sp.kg_contracted
        kg_coop    = acum["kg_coop"]

        restan = None
        if contracted is not None and kg_coop is not None:
            restan = contracted - kg_coop

        progreso_pct = None
        if contracted and kg_coop is not None and contracted > 0:
            progreso_pct = round(min(kg_coop / contracted * 100, 100), 1)

        results.append({
            "product":       prod,
            "client":        sp.client,
            "kg_contracted": contracted,
            **acum,
            "restan":        restan,
            "progreso_pct":  progreso_pct,
        })
    return results


def session_grand_total(product_summaries: list[dict]) -> dict:
    """
    Total general de la sesión (todos los productos sumados).
    Recibe la lista producida por session_totals_by_product().

    Devuelve:
    {
        "kg_deposito":    int,
        "kg_directo":     int,
        "kg_cv":          int,
        "kg_total_mtr":   int,
        "kg_coop":        int | None,
        "kg_contracted":  int | None,
        "delta":          int | None,
        "delta_status":   str,
        "restan":         int | None,
        "progreso_pct":   float | None,
        "viajes_mtr":     int | None,
        "viajes_coop":    int | None,
        "viajes_total":   int | None,
    }
    """
    kg_dep = kg_dir = kg_cv = 0
    kg_coop_total = kg_contracted_total = 0
    has_coop = has_contracted = False
    v_mtr = v_coop = 0
    has_v_mtr = has_v_coop = False

    for ps in product_summaries:
        kg_dep += ps["kg_deposito"]
        kg_dir += ps["kg_directo"]
        kg_cv  += ps["kg_cv"]
        if ps["kg_coop"] is not None:
            kg_coop_total += ps["kg_coop"]
            has_coop = True
        if ps["kg_contracted"] is not None:
            kg_contracted_total += ps["kg_contracted"]
            has_contracted = True
        if ps.get("viajes_mtr") is not None:
            v_mtr += ps["viajes_mtr"]
            has_v_mtr = True
        if ps.get("viajes_coop") is not None:
            v_coop += ps["viajes_coop"]
            has_v_coop = True

    kg_mtr        = kg_dep + kg_dir + kg_cv
    kg_coop       = kg_coop_total       if has_coop       else None
    kg_contracted = kg_contracted_total if has_contracted else None
    d             = (kg_mtr - kg_coop)  if kg_coop is not None else None

    restan = None
    if kg_contracted is not None and kg_coop is not None:
        restan = kg_contracted - kg_coop

    progreso_pct = None
    if kg_contracted and kg_coop is not None and kg_contracted > 0:
        progreso_pct = round(min(kg_coop / kg_contracted * 100, 100), 1)

    viajes_mtr   = v_mtr  if has_v_mtr  else None
    viajes_coop  = v_coop if has_v_coop else None
    viajes_total = (
        (viajes_mtr or 0) + (viajes_coop or 0)
        if (has_v_mtr or has_v_coop) else None
    )

    return {
        "kg_deposito":   kg_dep,
        "kg_directo":    kg_dir,
        "kg_cv":         kg_cv,
        "kg_total_mtr":  kg_mtr,
        "kg_coop":       kg_coop,
        "kg_contracted": kg_contracted,
        "delta":         d,
        "delta_status":  delta_status(d),
        "restan":        restan,
        "progreso_pct":  progreso_pct,
        "viajes_mtr":    viajes_mtr,
        "viajes_coop":   viajes_coop,
        "viajes_total":  viajes_total,
    }


# ── Cálculos de demoras ───────────────────────────────────────────────────────

def delay_minutes_by_type(delays: list[Any]) -> dict[str, int]:
    """
    Agrupa demoras por motivo_tipo y suma minutos.
    Excluye demoras sin 'hasta' (aún abiertas).

    Devuelve: { motivo_tipo: minutos_totales }
    """
    result: dict[str, int] = {}
    for d in delays:
        mins = hhmm_diff_minutes(
            getattr(d, "desde", None),
            getattr(d, "hasta", None),
        )
        if mins is None:
            continue
        tipo = getattr(d, "motivo_tipo", None) or "otro"
        result[tipo] = result.get(tipo, 0) + mins
    return result


def delay_total_minutes(delays: list[Any]) -> int:
    """Total de minutos de demoras de un turno (excluye abiertas)."""
    return sum(delay_minutes_by_type(delays).values())


# ── Cálculos de equipos ───────────────────────────────────────────────────────

def equipment_hours_by_empresa(equipment_rows: list[Any]) -> dict[str, float]:
    """
    Suma horas de equipos por empresa.
    Excluye filas sin hasta.
    Devuelve: { empresa: horas_totales }
    """
    result: dict[str, float] = {}
    for eq in equipment_rows:
        hrs = hhmm_to_hours(
            getattr(eq, "desde", None),
            getattr(eq, "hasta", None),
        )
        if hrs is None:
            continue
        empresa = getattr(eq, "empresa", None) or "—"
        result[empresa] = round(result.get(empresa, 0.0) + hrs, 2)
    return result


def equipment_total_hours(equipment_rows: list[Any]) -> float:
    """Total de horas de equipos de un turno (excluye sin hasta)."""
    return round(sum(equipment_hours_by_empresa(equipment_rows).values()), 2)


# ── Helpers de formato para templates ────────────────────────────────────────

def fmt_kg(value: int | None) -> str:
    """Formatea kg con separadores de miles. None → '—'."""
    if value is None:
        return "—"
    return f"{value:,}".replace(",", ".")


def staff_summary(staff_rows: list[Any]) -> list[dict]:
    """
    Convierte filas ORM de OperationLiveStaff en dicts legibles para templates.
    Retorna lista de {funcion, label, cantidad, turno_range, empresa}.
    """
    result = []
    for s in staff_rows:
        funcion = getattr(s, "funcion", "") or ""
        funcion_texto = getattr(s, "funcion_texto", "") or ""
        if funcion == "otro" and funcion_texto.strip():
            label = funcion_texto.strip()
        else:
            label = FUNCION_LABELS.get(funcion, funcion)
        result.append({
            "funcion":     funcion,
            "label":       label,
            "cantidad":    getattr(s, "cantidad", 1) or 1,
            "turno_range": getattr(s, "turno_range", None),
            "empresa":     getattr(s, "empresa", "coop") or "coop",
        })
    return result


# ── Fase 2: Funciones para cierre y conciliación ─────────────────────────────

def tns_by_product_from_session(bodega_rows: list[Any]) -> dict[str, Decimal]:
    """
    Suma toneladas MTR por producto normalizado para un conjunto de filas de bodega_data.

    Diseño:
    - Acepta cualquier lista de objetos con atributos product / kg_deposito_mtr /
      kg_directo_mtr / kg_cv_mtr. Funciona con ORM rows o SimpleNamespace (testeable).
    - Usa normalize_product() para evitar duplicados por variantes de nombre
      (ej. "TRIPLE" y "TRIPLE (STP)" se cuentan como el mismo producto).
    - Acumula en kg (enteros, exactos), convierte a tn solo al final.
    - Retorna Decimal con 2 decimales para uso en conciliación (sin pérdida de precisión).
    - Incluye todos los turnos pasados (abiertos y cerrados) — misma semántica live.
    - Si product es None/vacío y normalize no produce nada → clave "SIN_PRODUCTO".

    Returns:
        {"MAIZ": Decimal("8432.10"), "SOJA": Decimal("3201.55"), ...}
    """
    accumulator: dict[str, int] = {}
    for row in bodega_rows:
        raw_prod = getattr(row, "product", None) or ""
        prod = normalize_product(raw_prod)
        if not prod:
            prod = raw_prod.upper().strip() or "SIN_PRODUCTO"

        dep  = int(getattr(row, "kg_deposito_mtr", 0) or 0)
        dir_ = int(getattr(row, "kg_directo_mtr",  0) or 0)
        cv   = int(getattr(row, "kg_cv_mtr",        0) or 0)
        kg_total = dep + dir_ + cv

        accumulator[prod] = accumulator.get(prod, 0) + kg_total

    # Convert kg → tn con 2 decimales (Decimal para precisión en conciliación)
    return {
        prod: (Decimal(str(kg_sum)) / Decimal("1000")).quantize(Decimal("0.01"))
        for prod, kg_sum in accumulator.items()
    }


def all_shifts_closed(session: Any) -> bool:
    """
    True si TODOS los turnos de la sesión tienen status='closed'.
    False si no hay turnos o si alguno no está cerrado.

    Se usa como precondición para habilitar el cierre formal del operativo.
    Un operativo sin turnos no se puede cerrar (no hay nada que conciliar).

    Acepta cualquier objeto con atributo shifts iterable.
    """
    shifts = getattr(session, "shifts", None) or []
    if not shifts:
        return False
    return all(getattr(s, "status", None) == "closed" for s in shifts)


def open_shifts_count(session: Any) -> int:
    """Cantidad de turnos que NO están en status='closed'. Útil para mensajes de UI."""
    shifts = getattr(session, "shifts", None) or []
    return sum(1 for s in shifts if getattr(s, "status", None) != "closed")


def delta_badge(delta: int | None) -> dict:
    """
    Devuelve dict listo para el template con texto y clase CSS del badge de delta.
    {
        "text":       str,    # ej. "+1.200 kg" | "0 kg" | "—"
        "css_class":  str,    # clases Tailwind
        "status":     str,    # 'ok' | 'warn' | 'alert' | 'none'
    }
    """
    status = delta_status(delta)
    css = {
        "ok":    "bg-green-100 text-green-800",
        "warn":  "bg-yellow-100 text-yellow-800",
        "alert": "bg-red-100 text-red-800",
        "none":  "bg-gray-100 text-gray-500",
    }[status]

    if delta is None:
        text = "—"
    elif delta == 0:
        text = "0 kg"
    elif delta > 0:
        text = f"+{fmt_kg(delta)} kg"
    else:
        text = f"−{fmt_kg(abs(delta))} kg"

    return {"text": text, "css_class": css, "status": status}
