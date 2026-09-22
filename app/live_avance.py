"""
Avance del operativo contra el plan de estiba, y proyección de lo que falta.

LA PREGUNTA QUE CONTESTA
    "¿Cuándo vuelve a entrar mercadería a nuestro depósito?"
    No la contesta el porcentaje descargado: la contesta el ORDEN del plan de
    estiba. Un producto nuestro que está último en una bodega no está
    disponible hasta que salga todo lo ajeno que tiene encima.

CÓMO SE SABE QUÉ SE DESCARGÓ DE CADA ÍTEM
    Los partes informan kilos por bodega y destino, no por ítem. Así que se
    recorre el plan en orden y se lo va consumiendo con lo descargado de esa
    bodega: lo que entra dentro del acumulado ya salió, lo que queda afuera
    falta. Es una aproximación, y por eso el ítem que queda a mitad de camino
    se marca "en curso" en vez de fingir una precisión que no hay.

BRUTO CONTRA NETO
    El plan viene en toneladas netas y la balanza pesa bruto, así que una
    bodega puede cerrar arriba del 100 %. No se corrige: se muestra como está,
    porque ese desvío también es información.
"""
from __future__ import annotations

from app.models_live import (
    OperationLiveBodegaData,
    OperationLiveShift,
    OperationLiveStowItem,
)

DESTINOS_KG = {
    "MTR": "kg_deposito_mtr",
    "TERCERO": "kg_tercero",
    "CV": "kg_cv_mtr",
    "DIRECTO": "kg_directo_mtr",
}
# El plan marca el destino previsto y el parte el real. Para medir avance alcanza
# con "nuestro" contra "ajeno": el costado de vapor y el directo también son
# carga que sale por nuestra cuenta, aunque no pase por el depósito.
NUESTRO = ("MTR", "CV", "DIRECTO")

RITMO_POR_DEFECTO = 450.0   # t por bodega y turno, si todavía no hay partes


def _kg_por_bodega(db, session_id: int) -> dict[int, dict[str, int]]:
    """Kilos descargados por bodega y destino, sumando todos los turnos."""
    filas = (
        db.query(OperationLiveBodegaData)
        .join(OperationLiveShift,
              OperationLiveBodegaData.shift_id == OperationLiveShift.id)
        .filter(OperationLiveShift.session_id == session_id)
        .all()
    )
    out: dict[int, dict[str, int]] = {}
    for f in filas:
        b = out.setdefault(f.bodega_number, {d: 0 for d in DESTINOS_KG})
        for destino, campo in DESTINOS_KG.items():
            b[destino] += int(getattr(f, campo, 0) or 0)
    return out


def ritmo_por_bodega_turno(db, session_id: int, dias: int = 3) -> float:
    """Toneladas que mueve una bodega en un turno, promedio de los últimos días.

    Es el número que sirve para proyectar: una bodega, una mano, seis horas.
    Se toman los últimos días CON PARTE, no los del calendario, para que un fin
    de semana sin actividad no ensucie el promedio.
    """
    turnos = (
        db.query(OperationLiveShift)
        .filter(OperationLiveShift.session_id == session_id)
        .order_by(OperationLiveShift.shift_date.desc())
        .all()
    )
    if not turnos:
        return 0.0
    fechas = sorted({t.shift_date for t in turnos}, reverse=True)[:dias]
    pares: dict[tuple, float] = {}
    for t in turnos:
        if t.shift_date not in fechas:
            continue
        for f in t.bodega_data:
            kg = sum(int(getattr(f, c, 0) or 0) for c in DESTINOS_KG.values())
            clave = (t.shift_date, t.shift_start, f.bodega_number)
            pares[clave] = pares.get(clave, 0) + kg / 1000
    return round(sum(pares.values()) / len(pares), 1) if pares else 0.0


def turnos_resumen(db, session_id: int) -> list[dict]:
    """Un renglón por turno, con los kilos separados por destino.

    Es lo que alimenta el gráfico de ritmo: cada barra es un turno y se parte
    entre lo nuestro y lo que va al depósito de un tercero.
    """
    turnos = (
        db.query(OperationLiveShift)
        .filter(OperationLiveShift.session_id == session_id)
        .order_by(OperationLiveShift.shift_date, OperationLiveShift.shift_start)
        .all()
    )
    out = []
    for t in turnos:
        kg = {d: 0 for d in DESTINOS_KG}
        viajes, bodegas = 0, set()
        for f in t.bodega_data:
            for destino, campo in DESTINOS_KG.items():
                kg[destino] += int(getattr(f, campo, 0) or 0)
            viajes += int(f.viajes_mtr or 0)
            bodegas.add(f.bodega_number)
        nuestro = sum(kg[d] for d in NUESTRO)
        out.append({
            "fecha": t.shift_date, "inicio": t.shift_start, "fin": t.shift_end,
            "numero": t.shift_number,
            "kg": nuestro + kg["TERCERO"], "kg_nuestro": nuestro,
            "kg_tercero": kg["TERCERO"], "kg_cv": kg["CV"],
            "viajes": viajes, "bodegas": sorted(bodegas),
            "notas": t.notes or "",
        })
    return out


def avance(db, session) -> dict:
    """Plan contra real, bodega por bodega, y qué falta en cada una.

    {"bodegas": [...], "totales": {...}, "ritmo": float,
     "proxima_mtr": {...}|None, "sin_plan": bool}
    """
    items = (
        db.query(OperationLiveStowItem)
        .filter(OperationLiveStowItem.session_id == session.id)
        .order_by(OperationLiveStowItem.bodega_number,
                  OperationLiveStowItem.orden)
        .all()
    )
    descargado = _kg_por_bodega(db, session.id)
    ritmo_real = ritmo_por_bodega_turno(db, session.id)

    if not items:
        return {"bodegas": [], "totales": {}, "ritmo": ritmo_real,
                "proxima_mtr": None, "sin_plan": True,
                "descargado_sin_plan": descargado}

    ritmo = ritmo_real or RITMO_POR_DEFECTO

    por_bodega: dict[int, list] = {}
    for it in items:
        por_bodega.setdefault(it.bodega_number, []).append(it)

    bodegas, proxima = [], None
    tot = {"plan": 0.0, "plan_mtr": 0.0, "desc": 0.0, "desc_mtr": 0.0,
           "resta": 0.0, "resta_mtr": 0.0}

    for num in sorted(por_bodega):
        lista = por_bodega[num]
        kg = descargado.get(num, {d: 0 for d in DESTINOS_KG})
        desc_t = sum(kg.values()) / 1000
        desc_mtr_t = sum(kg[d] for d in NUESTRO) / 1000

        plan_t = sum(float(i.mt_net or 0) for i in lista)
        plan_mtr_t = sum(float(i.mt_net or 0) for i in lista if i.destino in NUESTRO)

        acum, turnos_acum, detalle = 0.0, 0.0, []
        resta_t = resta_mtr_t = 0.0
        for it in lista:
            mt = float(it.mt_net or 0)
            ini, fin = acum, acum + mt
            acum = fin
            restante = min(mt, max(0.0, fin - desc_t))
            if restante <= 0.5:
                estado = "descargado"
            elif ini < desc_t:
                estado = "en curso"
            else:
                estado = "pendiente"

            desde = turnos_acum / ritmo
            turnos_acum += restante
            hasta = turnos_acum / ritmo

            detalle.append({"item": it, "restante": round(restante, 1),
                            "estado": estado,
                            "desde": round(desde, 1), "hasta": round(hasta, 1)})
            resta_t += restante
            if it.destino in NUESTRO:
                resta_mtr_t += restante
                if restante > 0.5 and (proxima is None or desde < proxima["desde"]):
                    proxima = {"bodega": num, "item": it,
                               "restante": round(restante, 1),
                               "desde": round(desde, 1),
                               "horas": round(desde * 6),
                               "antes_t": round(desde * ritmo)}

        bodegas.append({
            "numero": num, "items": detalle,
            "plan": round(plan_t, 1), "plan_mtr": round(plan_mtr_t, 1),
            "desc": round(desc_t, 1), "desc_mtr": round(desc_mtr_t, 1),
            "resta": round(resta_t, 1), "resta_mtr": round(resta_mtr_t, 1),
            "pct": round(desc_t / plan_t * 100) if plan_t else 0,
            "turnos": round(resta_t / ritmo, 1),
        })
        for k, v in (("plan", plan_t), ("plan_mtr", plan_mtr_t), ("desc", desc_t),
                     ("desc_mtr", desc_mtr_t), ("resta", resta_t),
                     ("resta_mtr", resta_mtr_t)):
            tot[k] += v

    tot = {k: round(v, 1) for k, v in tot.items()}
    tot["pct"] = round(tot["desc"] / tot["plan"] * 100) if tot["plan"] else 0
    return {"bodegas": bodegas, "totales": tot, "ritmo": ritmo,
            "ritmo_real": ritmo_real, "proxima_mtr": proxima, "sin_plan": False}
