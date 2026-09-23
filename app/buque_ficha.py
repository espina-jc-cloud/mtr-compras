"""
Un buque, una verdad: el estado y el recorrido completo, en un solo lugar.

EL PROBLEMA QUE RESUELVE
    Hasta acá cada módulo guardaba su propio estado del mismo buque. Próximos
    Arribos tenía un campo que alguien ponía a mano, Operativos Live tenía otro
    y el resumen de balanza un tercero. El MV KOCIEWIE terminó de descargar el
    11/09 —balanza mandó el resumen con 67 viajes— y Próximos Arribos lo seguía
    mostrando como que venía. El OCEAN INNOVATION figuraba a la vez como
    esperado, como cerrado y como en curso.

    No era un error de carga: es que nadie tenía por qué conciliarlos. Cuál de
    las tres verdades veía el que tenía que decidir dependía de en qué pantalla
    había entrado.

CÓMO SE DECIDE AHORA
    El estado no se guarda: se deriva, y gana la evidencia más fuerte. Que
    balanza haya mandado el resumen cerrado es un hecho —pesó cada camión—;
    que el arribo diga "esperado" es una intención que alguien escribió antes.
    Entre un hecho y una intención, gana el hecho.

        resumen cerrado                     → terminado
        hubo movimiento en los últimos días → descargando
        el arribo dice cancelado            → cancelado
        hay ETB                             → confirmado
        sólo la nominación                  → esperado

    "Descargando" pide movimiento reciente, no que el operativo esté abierto.
    Tres resúmenes de balanza quedaron sin "Fecha Finalizacion" porque nunca
    llegó la versión final —IC PROGRESS es de noviembre de 2025— y un turno de
    Live se queda abierto siempre que alguien se olvida de cerrarlo. Tomar eso
    como "está descargando ahora" llenaba la pantalla de barcos que se fueron
    hace meses, que es justo lo que hay que evitar: si la sección de lo urgente
    tiene ruido, se deja de mirar.

LO QUE NO HACE
    No escribe el estado derivado en ningún lado. Si mañana cambia la regla,
    cambia acá y todo el sistema cambia con ella; si se guardara, habría que
    recalcular el pasado y volveríamos a tener dos verdades.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func

from app.lineup_parser import canon_vessel
from app.models_arribos import ProximoArribo
from app.models_buques import BuqueOperativo
from app.models_live import OperationLiveSession, OperationLiveShift

ESPERADO, CONFIRMADO, DESCARGANDO, TERMINADO, CANCELADO = (
    "esperado", "confirmado", "descargando", "terminado", "cancelado")

# Orden en que se muestran: primero lo que está pasando ahora.
ORDEN = {DESCARGANDO: 0, CONFIRMADO: 1, ESPERADO: 2, TERMINADO: 3, CANCELADO: 4}

# Días sin un solo movimiento tras los cuales un operativo abierto ya no está
# pasando. Una descarga entera dura dos o tres días; cuatro sin pesar un camión
# significan que terminó y nadie lo cerró.
DIAS_SIN_MOVIMIENTO = 4

ETIQUETA = {ESPERADO: "Esperado", CONFIRMADO: "Confirmado",
            DESCARGANDO: "Descargando", TERMINADO: "Terminado",
            CANCELADO: "Cancelado"}

CSS = {
    ESPERADO:    "bg-gray-100 text-gray-600",
    CONFIRMADO:  "bg-indigo-50 text-indigo-700",
    DESCARGANDO: "bg-amber-50 text-amber-700",
    TERMINADO:   "bg-emerald-50 text-emerald-700",
    CANCELADO:   "bg-red-50 text-red-600",
}


def estado_de(arribo, live, resumen, ultima=None, hoy=None) -> str:
    """El estado real del buque, por la evidencia más fuerte que haya.

    `ultima` es la fecha del último movimiento real —el último pesaje o el
    último turno—; sin ella, un operativo abierto se da por terminado.
    """
    hoy = hoy or date.today()
    if resumen is not None and resumen.cerrado:
        return TERMINADO
    hubo_movimiento = (ultima is not None
                       and (hoy - ultima).days <= DIAS_SIN_MOVIMIENTO)
    if hubo_movimiento and (resumen is not None
                            or (live is not None and live.status == "active")):
        return DESCARGANDO
    if ultima is not None:
        # Hubo movimiento alguna vez y hace días que no: el buque se fue, lo
        # haya cerrado alguien o no. Vale igual para un operativo de Live que
        # quedó abierto sin resumen de balanza.
        return TERMINADO
    if arribo is not None:
        if arribo.estado == "cancelado":
            return CANCELADO
        if arribo.estado in ("amarrado", "operando"):
            return DESCARGANDO
        if arribo.estado == "finalizado":
            return TERMINADO
        if arribo.etb or arribo.fecha_estimada:
            return CONFIRMADO
    return ESPERADO


def _fecha_orden(arribo, resumen):
    """Por qué fecha se ordena el buque en la pantalla."""
    if resumen is not None and resumen.inicio:
        return resumen.inicio
    if arribo is not None and arribo.fecha_estimada:
        return arribo.fecha_estimada
    return None


def fichas(db, incluir_terminados: bool = True) -> list[dict]:
    """Un renglón por buque, uniendo las tres fuentes por el nombre normalizado.

    Se arma en memoria y no con SQL: son decenas de filas, no millones, y el
    emparejado por nombre normalizado no se puede expresar en un JOIN.
    """
    hoy = date.today()
    # El último turno cargado de cada operativo Live, en una sola consulta.
    ultimo_turno = dict(
        db.query(OperationLiveShift.session_id,
                 func.max(OperationLiveShift.shift_date))
        .group_by(OperationLiveShift.session_id).all())

    por_canon: dict[str, dict] = {}

    def entrada(canon, nombre):
        return por_canon.setdefault(canon, {
            "canon": canon, "buque": nombre,
            "arribo": None, "live": None, "resumen": None, "operativos": []})

    for a in (db.query(ProximoArribo)
              .filter(ProximoArribo.deleted_at.is_(None))
              .order_by(ProximoArribo.updated_at.desc()).all()):
        e = entrada(a.buque_canon, a.buque)
        if e["arribo"] is None:
            e["arribo"] = a

    for s in (db.query(OperationLiveSession)
              .order_by(OperationLiveSession.id.desc()).all()):
        e = entrada(canon_vessel(s.ship_name), s.ship_name)
        if e["live"] is None or s.status == "active":
            e["live"] = s

    for o in (db.query(BuqueOperativo)
              .order_by(BuqueOperativo.inicio.desc().nullslast()).all()):
        e = entrada(o.buque_canon, o.buque)
        e["operativos"].append(o)
        if e["resumen"] is None:
            e["resumen"] = o

    out = []
    for e in por_canon.values():
        a, live, r = e["arribo"], e["live"], e["resumen"]
        # El último movimiento real: el último pesaje o el último turno cargado.
        senales = [d for d in (
            (r.fin or r.inicio) if r is not None else None,
            ultimo_turno.get(live.id) if live is not None else None,
        ) if d]
        ultima = max(senales) if senales else None
        estado = estado_de(a, live, r, ultima, hoy)
        # Sesiones de Live vacías, sin arribo ni resumen: son pruebas.
        if a is None and r is None and ultima is None:
            continue
        if estado == TERMINADO and not incluir_terminados:
            continue
        out.append({
            **e,
            "estado": estado,
            "etiqueta": ETIQUETA[estado],
            "css": CSS[estado],
            # El nombre lindo es el del resumen de balanza si existe: ya viene
            # limpio de fechas y del cliente pegado.
            "buque": (r.buque if r is not None else
                      (a.buque if a is not None else e["buque"])),
            "cliente": ((r.cliente if r is not None else None)
                        or (a.cliente if a is not None else None)),
            "producto": ((r.producto if r is not None else None)
                         or (a.mercaderia if a is not None else None)),
            "fecha": _fecha_orden(a, r),
            "toneladas": round(int(r.neto_kg or 0) / 1000) if r is not None else None,
            "viajes": (r.viajes if r is not None else None),
            "ultima_actividad": ultima,
        })
    return sorted(out, key=lambda x: (ORDEN[x["estado"]],
                                      x["fecha"] or date(2100, 1, 1),
                                      x["buque"]))


def panorama(fichas_: list[dict], hoy: date | None = None) -> dict:
    """Los cuatro números de la portada."""
    hoy = hoy or date.today()
    mes = [f for f in fichas_
           if f["estado"] == TERMINADO and f["fecha"]
           and (f["fecha"].year, f["fecha"].month) == (hoy.year, hoy.month)]
    return {
        "descargando": sum(1 for f in fichas_ if f["estado"] == DESCARGANDO),
        "por_venir": sum(1 for f in fichas_
                         if f["estado"] in (ESPERADO, CONFIRMADO)),
        "terminados_mes": len(mes),
        "toneladas_mes": sum(f["toneladas"] or 0 for f in mes),
    }
