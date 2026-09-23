"""
Lo que se puede preguntarle a un operativo terminado.

DE DÓNDE SALE TODO
    De los pesajes, uno por camión. No hay ningún total guardado a mano: cada
    número de acá se recalcula sobre los mismos renglones que mandó balanza, así
    que no pueden contradecirse entre sí.

LAS PREGUNTAS QUE CONTESTA
    Cuánto entró y a qué ritmo · en qué turno se movió más · qué transportista
    puso los camiones · cuánto tarda un camión adentro de la planta · cuánto se
    despega nuestra balanza de la del origen.

    La permanencia es la que nadie mira y la que más cuesta: un camión que
    tarda cuarenta minutos en vez de quince no es un camión lento, son tres
    viajes que ese día no se hicieron.

UNA ACLARACIÓN SOBRE LOS PROMEDIOS
    La permanencia va en mediana y percentil 90, no en promedio. Un solo camión
    que quedó trabado seis horas mueve el promedio lo suficiente como para
    esconder que el resto entró y salió en doce minutos.
"""
from __future__ import annotations

from datetime import timedelta

# strftime("%a") devuelve el día en inglés: depende del locale del servidor,
# que en Railway es el de por defecto. Se arma acá para que no dependa de eso.
DIAS_ES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

# Una permanencia mayor a esto no es un camión esperando: es un pesaje mal
# cargado o un camión que quedó adentro por otra cosa. Cuenta aparte.
TOPE_PERMANENCIA = timedelta(hours=6)


def _t(kg) -> float:
    return round((kg or 0) / 1000, 1)


def _percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    orden = sorted(valores)
    i = min(int(round((len(orden) - 1) * p)), len(orden) - 1)
    return orden[i]


def _agrupar(pesajes, clave, etiqueta_vacia="Sin identificar"):
    grupos = {}
    for x in pesajes:
        k = (clave(x) or etiqueta_vacia).strip() or etiqueta_vacia
        g = grupos.setdefault(k, {"nombre": k, "viajes": 0, "kg": 0})
        g["viajes"] += 1
        g["kg"] += x.neto or 0
    return grupos


def _con_porcentaje(grupos: dict, total_kg: int) -> list[dict]:
    out = []
    for g in grupos.values():
        out.append({**g, "t": _t(g["kg"]),
                    "pct": round(g["kg"] / total_kg * 100) if total_kg else 0,
                    "promedio_t": _t(g["kg"] / g["viajes"]) if g["viajes"] else 0})
    return sorted(out, key=lambda x: -x["kg"])


def analizar(op) -> dict:
    """El análisis completo de un operativo, a partir de sus pesajes."""
    pesajes = list(op.pesajes)
    total_kg = sum(p.neto or 0 for p in pesajes)
    total_origen = sum(p.origen or 0 for p in pesajes)
    viajes = len(pesajes)

    momentos = [p.entrada for p in pesajes if p.entrada]
    desde, hasta = (min(momentos), max(momentos)) if momentos else (None, None)
    horas = ((hasta - desde).total_seconds() / 3600) if desde and hasta else 0

    # ── Permanencia en planta ────────────────────────────────────────────────
    minutos, largos = [], 0
    for p in pesajes:
        if not (p.entrada and p.salida):
            continue
        d = p.salida - p.entrada
        if d.total_seconds() <= 0:
            continue
        if d > TOPE_PERMANENCIA:
            largos += 1
            continue
        minutos.append(d.total_seconds() / 60)

    # ── Por día ──────────────────────────────────────────────────────────────
    por_dia = {}
    for p in pesajes:
        if not p.entrada:
            continue
        d = por_dia.setdefault(p.entrada.date(), {"fecha": p.entrada.date(),
                                                  "viajes": 0, "kg": 0})
        d["viajes"] += 1
        d["kg"] += p.neto or 0
    dias = [{**d, "t": _t(d["kg"]),
             "etiqueta": f"{DIAS_ES[d['fecha'].weekday()]} {d['fecha']:%d/%m}"}
            for d in sorted(por_dia.values(), key=lambda x: x["fecha"])]

    # ── Por hora del día: dónde está el pico ─────────────────────────────────
    por_hora = {h: 0 for h in range(24)}
    for p in pesajes:
        if p.entrada:
            por_hora[p.entrada.hour] += p.neto or 0
    horas_dia = [{"hora": h, "t": _t(kg)} for h, kg in sorted(por_hora.items())]

    transportes = _con_porcentaje(_agrupar(pesajes, lambda p: p.transporte), total_kg)
    turnos = _con_porcentaje(_agrupar(pesajes, lambda p: p.turno, "Sin turno"), total_kg)
    camiones = _con_porcentaje(_agrupar(pesajes, lambda p: p.patente, "—"), total_kg)

    dias_con_movimiento = len(dias) or 1
    return {
        "viajes": viajes,
        "neto_t": _t(total_kg),
        "origen_t": _t(total_origen),
        "diferencia_kg": total_kg - total_origen,
        "diferencia_pct": (round((total_kg - total_origen) / total_origen * 100, 3)
                           if total_origen else None),
        "promedio_t": _t(total_kg / viajes) if viajes else 0,
        "desde": desde, "hasta": hasta,
        "horas": round(horas, 1),
        "dias_con_movimiento": len(dias),
        "ritmo_t_dia": round(_t(total_kg) / dias_con_movimiento, 1),
        "ritmo_t_hora": round(_t(total_kg) / horas, 1) if horas else 0,
        "viajes_dia": round(viajes / dias_con_movimiento, 1),

        "permanencia": {
            "mediana": round(_percentil(minutos, 0.5) or 0),
            "p90": round(_percentil(minutos, 0.9) or 0),
            "minimo": round(min(minutos)) if minutos else 0,
            "medidos": len(minutos),
            "largos": largos,
        },
        "dias": dias,
        "horas_dia": horas_dia,
        "transportes": transportes,
        "turnos": turnos,
        "camiones": camiones,
        "camiones_distintos": len([c for c in camiones if c["nombre"] != "—"]),
    }
