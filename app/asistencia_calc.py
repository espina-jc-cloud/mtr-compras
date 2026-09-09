"""
Motor de cálculo del módulo Asistencia.

Este archivo es la ÚNICA fuente de verdad del cálculo de horas extra. Los
routers no calculan nada: cargan marcaciones y llaman a recalcular_jornada().

Pipeline (ver models_asistencia.py para el detalle del modelo):

    marcaciones  →  derivar_bloques()  →  bloques  →  recalcular_jornada()  →  jornada

REGLA DE ORO
    recalcular_jornada() NUNCA pisa un campo decidido por una persona
    (extra aprobada, motivo, estado, buque). Si el derivado cambia y había una
    decisión tomada, marca `revisar=True` y sigue. Nunca corrige en silencio.
"""
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models_asistencia import (
    AsistenciaBloque, AsistenciaConfig, AsistenciaExcepcionJornada,
    AsistenciaFeriado, AsistenciaJornada, AsistenciaJornadaTramo,
    AsistenciaMarcacion, AsistenciaPersona,
    hhmm_a_min,
)

_MIN_DIA = 24 * 60


# ══════════════════════════════════════════════════════════════════════════════
# Configuración
# ══════════════════════════════════════════════════════════════════════════════

def get_config(db: Session) -> AsistenciaConfig:
    """Fila única de configuración. La crea con defaults si no existe, para que
    el módulo nunca falle por falta de seed."""
    cfg = db.query(AsistenciaConfig).first()
    if cfg is None:
        cfg = AsistenciaConfig()
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def redondear_extra(minutos: int, cfg: AsistenciaConfig) -> int:
    """Redondea al múltiplo más cercano y descarta lo que no llega al mínimo.

    Sin esto, todos los días aparecen extras de 3 minutos por diferencias de
    reloj y el tablero pierde credibilidad en una semana.
    """
    if minutos is None or minutos <= 0:
        return 0
    if minutos < (cfg.minimo_computable_min or 0):
        return 0
    paso = cfg.redondeo_min or 1
    if paso <= 1:
        return int(minutos)
    return int(round(minutos / paso) * paso)


# ══════════════════════════════════════════════════════════════════════════════
# Jornada esperada
# ══════════════════════════════════════════════════════════════════════════════

def jornada_esperada(db: Session, persona: AsistenciaPersona, fecha: date) -> dict:
    """Resuelve qué se esperaba de esta persona ese día.

    Tres niveles, gana el más específico:
        3. excepción persona × fecha   (turno de operativo, franco)
        2. feriado                     (el día pasa a no laborable)
        1. tramo de la jornada tipo    (L-V 08-16, sáb 08-12, dom libre)

    Los tercerizados no tienen jornada tipo: devuelven no laborable y su
    esperado sale del pedido al proveedor.
    """
    cfg = get_config(db)
    base = {
        "laborable": False,
        "hora_entrada": None,
        "hora_salida": None,
        "minutos_esperados": 0,
        "tolerancia_salida": cfg.tolerancia_salida_min or 0,
        "pausa_min": 0,
        "limite_normal": None,   # hasta qué hora se pueden cumplir las horas exigidas
        "desde_100": None,       # a partir de qué hora todo es al 100 %
        "computa_extra": True,   # False = régimen rotativo con francos
        "origen": "sin_jornada",
    }

    # ── Nivel 3: excepción explícita ─────────────────────────────────────────
    exc = (
        db.query(AsistenciaExcepcionJornada)
        .filter(AsistenciaExcepcionJornada.persona_id == persona.id,
                AsistenciaExcepcionJornada.fecha == fecha)
        .first()
    )
    if exc is not None:
        base.update(
            laborable=bool(exc.laborable),
            hora_entrada=exc.hora_entrada,
            hora_salida=exc.hora_salida,
            origen="excepcion",
        )
        base["minutos_esperados"] = _duracion(exc.hora_entrada, exc.hora_salida) if exc.laborable else 0
        return base

    # ── Nivel 2: feriado ─────────────────────────────────────────────────────
    feriado = db.query(AsistenciaFeriado).filter(AsistenciaFeriado.fecha == fecha).first()
    if feriado is not None:
        base["origen"] = "feriado"
        return base

    # ── Nivel 1: tramo del patrón horario ────────────────────────────────────
    if persona.jornada_tipo_id is None:
        return base

    tramo = (
        db.query(AsistenciaJornadaTramo)
        .filter(AsistenciaJornadaTramo.jornada_tipo_id == persona.jornada_tipo_id,
                AsistenciaJornadaTramo.dia_semana == fecha.weekday())
        .first()
    )
    if tramo is None or not tramo.laborable:
        base["origen"] = "no_laborable"
        return base

    jt = persona.jornada_tipo
    base.update(
        laborable=True,
        hora_entrada=tramo.hora_entrada,
        hora_salida=tramo.hora_salida,
        minutos_esperados=_duracion(tramo.hora_entrada, tramo.hora_salida),
        pausa_min=(jt.pausa_min or 0) if jt else 0,
        tolerancia_salida=(jt.tolerancia_salida_min if jt and jt.tolerancia_salida_min is not None
                           else cfg.tolerancia_salida_min or 0),
        limite_normal=hhmm_a_min(tramo.hora_limite_normal),
        desde_100=hhmm_a_min(tramo.hora_desde_100),
        computa_extra=bool(jt.computa_extra) if jt else True,
        origen="jornada_tipo",
    )
    return base


def _duracion(hora_desde, hora_hasta) -> int:
    """Minutos entre dos 'HH:MM'. Soporta cruce de medianoche."""
    d, h = hhmm_a_min(hora_desde), hhmm_a_min(hora_hasta)
    if d is None or h is None:
        return 0
    if h <= d:
        h += _MIN_DIA
    return h - d


# ══════════════════════════════════════════════════════════════════════════════
# Capa 2 — Derivación de bloques
# ══════════════════════════════════════════════════════════════════════════════

def derivar_bloques(db: Session, persona: AsistenciaPersona, fecha: date, esperado: dict) -> dict:
    """Reconstruye los bloques de presencia de una persona en una fecha operativa.

    Un bloque es un tramo continuo IN→OUT. Se borran y se rehacen enteros en
    cada recálculo: no viven datos humanos acá.

    Devuelve un dict con los bloques y las anomalías detectadas, para que
    recalcular_jornada() no tenga que volver a recorrerlos.
    """
    cfg = get_config(db)

    # Se borran los objetos ORM en vez de con un DELETE masivo: con
    # `synchronize_session=False` el identity map de la sesión queda con filas
    # que ya no existen y el flush siguiente intenta un UPDATE sobre ellas
    # (StaleDataError). Son pocas filas por persona y día.
    for _b in (db.query(AsistenciaBloque)
               .filter(AsistenciaBloque.persona_id == persona.id,
                       AsistenciaBloque.fecha_operativa == fecha).all()):
        db.delete(_b)
    db.flush()

    # Se traen también las marcaciones del día anterior: un turno que empieza
    # el lunes 22:00 y termina el martes 06:00 aporta minutos a los DOS días.
    marcaciones = (
        db.query(AsistenciaMarcacion)
        .filter(AsistenciaMarcacion.persona_id == persona.id,
                AsistenciaMarcacion.fecha_operativa.in_(
                    [fecha - timedelta(days=1), fecha]),
                AsistenciaMarcacion.anulada_at.is_(None))
        .order_by(AsistenciaMarcacion.ts, AsistenciaMarcacion.id)
        .all()
    )

    # Ventana esperada del día, para saber si un bloque la toca.
    ini_esp = fin_esp = None
    if esperado["laborable"] and esperado["hora_entrada"] and esperado["hora_salida"]:
        ini_esp = datetime.combine(fecha, datetime.min.time()) + timedelta(
            minutes=hhmm_a_min(esperado["hora_entrada"]))
        fin_esp = ini_esp + timedelta(minutes=esperado["minutos_esperados"])

    bloques, abierto = [], None
    out_huerfano = False

    for m in marcaciones:
        if m.tipo == "IN":
            if abierto is not None:
                # Dos IN seguidos: el anterior queda sin egreso, no se descarta.
                bloques.append((abierto, None))
            abierto = m
        else:  # OUT
            if abierto is None:
                # Solo es anomalía si el egreso es de este día: uno del día
                # anterior cuyo ingreso quedó más atrás no ensucia hoy.
                if m.fecha_operativa == fecha:
                    out_huerfano = True
                continue
            bloques.append((abierto, m))
            abierto = None

    if abierto is not None:
        bloques.append((abierto, None))

    dia_ini = datetime.combine(fecha, datetime.min.time())
    dia_fin = dia_ini + timedelta(days=1)

    creados, bloque_largo = [], False
    for m_in, m_out in bloques:
        desde_real = m_in.ts
        hasta_real = m_out.ts if m_out is not None else None

        # ── Corte por día (regla MTR: el día va de 00:00 a 00:00) ────────────
        # Un turno que cruza la medianoche se parte: sus minutos cuentan para
        # cumplir la jornada del día en que efectivamente ocurrieron.
        if hasta_real is None and m_in.fecha_operativa != fecha:
            # Un bloque SIN egreso pertenece solo a su propio día. Si se dejara
            # correr, un olvido de firma del lunes ensuciaría el martes, el
            # miércoles y todo lo que siga.
            continue
        if hasta_real is not None and hasta_real <= dia_ini:
            continue                       # el bloque terminó antes de este día
        if desde_real >= dia_fin:
            continue                       # empieza después
        desde = max(desde_real, dia_ini)
        hasta = min(hasta_real, dia_fin) if hasta_real is not None else None
        if hasta is not None and hasta <= desde:
            continue

        minutos = int((hasta - desde).total_seconds() // 60) if hasta else None

        largo_real = (int((hasta_real - desde_real).total_seconds() // 60)
                      if hasta_real is not None else None)
        if largo_real is not None and largo_real > (cfg.max_horas_bloque or 16) * 60:
            # Ojo: el límite es del BLOQUE, no del día. Un día con dos bloques
            # que suman 16 h es un operativo nocturno válido; un solo bloque de
            # 17 h es un olvido de firma.
            bloque_largo = True

        if not esperado["laborable"]:
            tipo, solapa = "no_laborable", False
        elif ini_esp is None:
            tipo, solapa = "llamado", False
        else:
            fin_cmp = hasta if hasta is not None else fin_esp
            solapa = (desde < fin_esp) and (fin_cmp > ini_esp)
            tipo = "jornada" if solapa else "llamado"

        b = AsistenciaBloque(
            persona_id=persona.id, fecha_operativa=fecha,
            desde=desde, hasta=hasta, minutos=minutos,
            tipo=tipo, solapa_jornada=bool(solapa),
            turno_operativo=(m_in.nota if m_in.fuente == "turno_operativo" else None),
            marcacion_in_id=m_in.id,
            marcacion_out_id=m_out.id if m_out is not None else None,
        )
        db.add(b)
        creados.append(b)

    db.flush()
    # "sin egreso" solo aplica a los bloques abiertos que arrancaron HOY: un
    # bloque de ayer ya recortado a medianoche está cerrado para este día.
    abiertos_hoy = any(
        b.hasta is None and b.desde >= dia_ini for b in creados)
    return {
        "bloques": creados,
        "out_huerfano": out_huerfano,
        "bloque_largo": bloque_largo,
        "sin_egreso": abiertos_hoy,
        "ini_esp": ini_esp,
        "fin_esp": fin_esp,
    }


def _repartir_por_franja(bloques, fecha, limite_normal, desde_100):
    """Reparte los minutos trabajados en las tres franjas horarias del día.

    Devuelve (A, B, C) donde, contando minutos desde las 00:00 de la fecha
    operativa:

        A  antes de `limite_normal`   → cuentan para cumplir la jornada
        B  entre  `limite_normal` y `desde_100` → recargo 50 %
        C  desde  `desde_100`         → recargo 100 %

    Con ambos límites en None (lunes a viernes) todo cae en A: las 8 horas se
    pueden cumplir en cualquier momento del día, que es justamente la regla.
    Con limite=12:00 y desde_100=13:00 se obtiene el sábado.
    """
    INF = 10 ** 9
    lim  = limite_normal if limite_normal is not None else INF
    d100 = desde_100     if desde_100     is not None else INF
    if d100 < lim:
        d100 = lim

    base = datetime.combine(fecha, datetime.min.time())
    a = b = c = 0
    for blq in bloques:
        if blq.hasta is None:
            continue
        ini = int((blq.desde - base).total_seconds() // 60)
        fin = int((blq.hasta - base).total_seconds() // 60)
        a += max(0, min(fin, lim)  - max(ini, 0))
        b += max(0, min(fin, d100) - max(ini, lim))
        c += max(0, fin            - max(ini, d100))
    return a, b, c


# ══════════════════════════════════════════════════════════════════════════════
# Capa 3 — Jornada
# ══════════════════════════════════════════════════════════════════════════════

def recalcular_jornada(db: Session, persona: AsistenciaPersona, fecha: date,
                       marcar_ausente: bool = False,
                       _cascada: bool = True,
                       _solo_si_hay_datos: bool = False) -> AsistenciaJornada:
    """Recalcula la jornada de una persona en una fecha. Idempotente.

    marcar_ausente=True crea la jornada en estado 'ausente' aunque no haya
    marcaciones. Es lo que distingue "no vino" de "todavía no cargué", que sin
    esa distinción hace imposible saber si la planilla está completa.
    """
    cfg = get_config(db)
    esperado = jornada_esperada(db, persona, fecha)
    d = derivar_bloques(db, persona, fecha, esperado)
    bloques = d["bloques"]

    j = (
        db.query(AsistenciaJornada)
        .filter(AsistenciaJornada.persona_id == persona.id,
                AsistenciaJornada.fecha == fecha)
        .first()
    )
    # Recálculo en cascada del día siguiente: si no quedó nada que mostrar, no
    # se inventa una jornada vacía solo porque un turno de ayer rozó este día.
    if _solo_si_hay_datos and not d["bloques"]:
        propias = (db.query(AsistenciaMarcacion)
                   .filter(AsistenciaMarcacion.persona_id == persona.id,
                           AsistenciaMarcacion.fecha_operativa == fecha,
                           AsistenciaMarcacion.anulada_at.is_(None)).count())
        if not propias:
            if j is not None and (j.estado_extra or "na") == "na" \
                    and j.minutos_extra_aprobada is None:
                db.delete(j)
                db.flush()
            return None
    if j is None:
        j = AsistenciaJornada(persona_id=persona.id, fecha=fecha)
        db.add(j)

    tenia_decision = (
        j.minutos_extra_aprobada is not None
        or (j.estado_extra or "na") in ("justificada", "aprobada", "rechazada")
    )
    extra_anterior = j.minutos_extra_detectada or 0

    # ── Snapshot ─────────────────────────────────────────────────────────────
    # Se refresca mientras no haya una decisión humana tomada. Una vez que
    # alguien aprobó algo, el snapshot queda congelado: cambiarlo reescribiría
    # el fundamento de una decisión ya tomada.
    if not tenia_decision:
        j.laborable_esp          = esperado["laborable"]
        j.hora_entrada_esp       = esperado["hora_entrada"]
        j.hora_salida_esp        = esperado["hora_salida"]
        j.minutos_esperados      = esperado["minutos_esperados"]
        j.tolerancia_salida_snap = esperado["tolerancia_salida"]
        j.computa_extra_snap     = esperado.get("computa_extra", True)
        # Solo se completan si están vacíos: la importación los fija según la
        # planilla de origen y un recálculo posterior no puede pisarlos.
        if not j.planta_snap:
            j.planta_snap = persona.planta
        if not j.grupo_snap:
            j.grupo_snap = persona.grupo
        j.tipo_persona_snap      = persona.tipo or "mtr"
        j.proveedor_snap         = persona.proveedor.name if persona.proveedor else None

    # ── Derivados ────────────────────────────────────────────────────────────
    cerrados   = [b for b in bloques if b.hasta is not None]
    de_jornada = [b for b in cerrados if b.tipo == "jornada"]
    anticipada, tarde = 0, False

    j.cantidad_bloques  = len(bloques)
    j.minutos_presencia = sum(b.minutos or 0 for b in cerrados)
    j.minutos_trabajados = max(0, j.minutos_presencia - (esperado["pausa_min"] or 0))
    j.ingreso_real = bloques[0].desde if bloques else None
    j.egreso_real  = cerrados[-1].hasta if cerrados else None

    # ── Extra por duración y franja ──────────────────────────────────────────
    # REGLA REAL (confirmada por MTR el 07/09/2026):
    #
    #   Lunes a viernes  → deben cumplir 8 h, en CUALQUIER franja del día.
    #                      El horario 08:00-16:00 es de referencia, no obligatorio:
    #                      si hay barco se reparten y uno hace la noche. Quien
    #                      cumple sus 8 h de 00:00 a 08:00 NO hizo horas extra.
    #                      Extra = lo que exceda las 8 h. Recargo 50 %.
    #
    #   Sábado           → deben cumplir 4 h, cumplibles entre 00:00 y 12:00.
    #                      12:00 a 13:00  → 50 %.
    #                      13:00 en adelante → 100 %, haya venido antes o no.
    #
    #   Domingo/feriado  → todo al 100 %.
    #
    # Por eso la vara dejó de ser la hora de salida y pasó a ser la DURACIÓN.
    exigido = esperado["minutos_esperados"] or 0
    tol     = esperado["tolerancia_salida"] or 0

    a_normal, b_50, c_100 = _repartir_por_franja(
        cerrados, fecha, esperado["limite_normal"], esperado["desde_100"])

    if esperado["laborable"]:
        cumplido  = min(a_normal, exigido)
        extra_50  = (a_normal - cumplido) + b_50
        extra_100 = c_100
        defecto   = max(0, exigido - a_normal)
    else:
        # Domingo o feriado: no hay jornada que cumplir, toda la presencia es
        # extra al 100 %.
        cumplido, defecto = 0, 0
        extra_50  = 0
        extra_100 = a_normal + b_50 + c_100

    # Régimen rotativo con francos (portería): las horas se compensan con
    # francos, no día a día, así que no hay extra ni deuda que computar. Las
    # horas trabajadas se siguen registrando y mostrando.
    if not esperado.get("computa_extra", True):
        extra_50 = extra_100 = defecto = 0

    # La tolerancia ABSORBE los excesos chicos, no descuenta de los grandes:
    # quedarse 5 minutos no es hora extra, pero quien trabajó 12 h contra 8
    # hizo 4 h, no 3h45. Restar la tolerancia siempre haría que el número del
    # sistema nunca coincida con el que MTR saca a mano, y ahí se pierde la
    # confianza en el tablero.
    # No se aplica al 100 %: sábado tarde, domingo y feriado no son "se quedó
    # un rato", son una convocatoria.
    if extra_50 <= tol:
        extra_50 = 0

    # Informativos
    if d["ini_esp"] is not None and de_jornada:
        ini_real = min(b.desde for b in de_jornada)
        anticipada = max(0, int((d["ini_esp"] - ini_real).total_seconds() // 60))
        tarde = int((ini_real - d["ini_esp"]).total_seconds() // 60) > (cfg.tolerancia_entrada_min or 0)

    fuera_horario = sum(b.minutos or 0 for b in cerrados if b.tipo == "llamado")

    # ── Redondeo ─────────────────────────────────────────────────────────────
    # El mínimo computable se evalúa sobre el total; el redondeo, sobre cada
    # recargo, para que los dos números sirvan para costear por separado.
    total_bruto = extra_50 + extra_100
    if total_bruto < (cfg.minimo_computable_min or 0):
        extra_50 = extra_100 = 0
    else:
        extra_50  = redondear_extra(extra_50, cfg) if extra_50 else 0
        extra_100 = redondear_extra(extra_100, cfg) if extra_100 else 0

    j.minutos_extra_50         = extra_50
    j.minutos_extra_100        = extra_100
    j.minutos_extra_detectada  = extra_50 + extra_100
    j.minutos_extra_no_laborable = extra_100 if not esperado["laborable"] else 0
    j.minutos_extra_prolongacion = 0          # deprecada
    j.minutos_fuera_horario    = fuera_horario
    j.minutos_defecto          = defecto
    j.entrada_anticipada       = anticipada
    j.llegada_tarde            = tarde
    j.tipo_extra_dominante = (
        None if not (extra_50 or extra_100) else ("100" if extra_100 >= extra_50 else "50")
    )

    # ── Estado del registro ──────────────────────────────────────────────────
    if not bloques:
        j.estado_registro = "ausente" if marcar_ausente else "sin_ingreso"
    elif d["out_huerfano"] or d["bloque_largo"]:
        j.estado_registro = "inconsistente"
    elif d["sin_egreso"]:
        # Una jornada sin egreso no computa extra: el sistema no puede inventar
        # que hizo 8 horas. Sale de este estado cuando alguien la corrige.
        j.estado_registro = "sin_egreso"
    else:
        j.estado_registro = "ok"

    # ── Estado de la extra ───────────────────────────────────────────────────
    if not tenia_decision:
        j.estado_extra = "pendiente" if j.minutos_extra_detectada > 0 else "na"
    elif extra_anterior != j.minutos_extra_detectada:
        # Cambió el fundamento de una decisión ya tomada (feriado cargado tarde,
        # corrección posterior). No se toca la decisión: se avisa.
        j.revisar = True

    # Un registro incompleto tampoco computa DEFECTO. Si falta el egreso no
    # sabemos cuánto trabajó: decir que no cumplió 8 h es tan falso como decir
    # que hizo 8 h de extra. Y "ausente" es no haber venido, que es otro tema
    # —no una jornada mal cumplida— y mezclarlos arruina el número.
    if j.estado_registro != "ok":
        j.minutos_defecto = 0

    j.recalculada_at = datetime.utcnow()
    db.flush()

    # Si algún turno de este día cruzó la medianoche, el día siguiente también
    # cambió: sus minutos cuentan allá. Un solo nivel de cascada alcanza —
    # ningún bloque dura más de 24 h.
    if _cascada:
        cruza = any(b.hasta is not None and b.hasta.date() > fecha
                    for b in bloques)
        if cruza:
            recalcular_jornada(db, persona, fecha + timedelta(days=1),
                               _cascada=False, _solo_si_hay_datos=True)
    return j


def recalcular_dia(db: Session, fecha: date, personas=None) -> int:
    """Recalcula todas las jornadas existentes de una fecha. Devuelve cuántas.

    Se usa cuando cambia algo transversal al día: un feriado cargado tarde, la
    configuración de tolerancias o el redondeo.
    """
    if personas is None:
        ids = [
            r[0] for r in db.query(AsistenciaJornada.persona_id)
            .filter(AsistenciaJornada.fecha == fecha).all()
        ]
        personas = db.query(AsistenciaPersona).filter(AsistenciaPersona.id.in_(ids)).all() if ids else []
    for p in personas:
        recalcular_jornada(db, p, fecha)
    return len(personas)


# ══════════════════════════════════════════════════════════════════════════════
# Alta de marcaciones
# ══════════════════════════════════════════════════════════════════════════════

def _ts(fecha: date, hhmm: str, ref: datetime = None) -> datetime:
    """'HH:MM' del día `fecha` → datetime naive en hora local de planta.

    Si `ref` viene (el ingreso del bloque) y la hora resultante es anterior o
    igual, se asume cruce de medianoche y se suma un día. Así 22:00 → 06:00 se
    guarda como un bloque de 8 h y no como uno negativo.
    """
    minutos = hhmm_a_min(hhmm)
    if minutos is None:
        return None
    out = datetime.combine(fecha, datetime.min.time()) + timedelta(minutes=minutos)
    if ref is not None and out <= ref:
        out += timedelta(days=1)
    return out


def guardar_bloque(db: Session, persona: AsistenciaPersona, fecha: date,
                   ingreso: str, egreso: str, user_id=None,
                   fuente: str = "planilla", turno: str = None,
                   reemplazar: bool = True):
    """Alta de un par IN/OUT para una persona en una fecha.

    `reemplazar=True` anula las marcaciones previas de esa fecha antes de
    escribir: es lo que hace que recargar la misma planilla actualice en vez de
    duplicar. Las viejas NUNCA se borran — se marcan anuladas, así la historia
    queda por construcción (requisito de auditoría).
    """
    if reemplazar:
        previas = (
            db.query(AsistenciaMarcacion)
            .filter(AsistenciaMarcacion.persona_id == persona.id,
                    AsistenciaMarcacion.fecha_operativa == fecha,
                    AsistenciaMarcacion.anulada_at.is_(None))
            .all()
        )
        for m in previas:
            m.anulada_at = datetime.utcnow()
            m.anulada_por_id = user_id
            m.anulada_motivo = "Reemplazada por nueva carga de la planilla"

    ts_in = _ts(fecha, ingreso) if ingreso else None
    ts_out = _ts(fecha, egreso, ref=ts_in) if egreso else None

    if ts_in is not None:
        db.add(AsistenciaMarcacion(
            persona_id=persona.id, ts=ts_in, tipo="IN", fecha_operativa=fecha,
            fuente=fuente, nota=turno, usuario_carga_id=user_id,
        ))
    if ts_out is not None:
        db.add(AsistenciaMarcacion(
            persona_id=persona.id, ts=ts_out, tipo="OUT", fecha_operativa=fecha,
            fuente=fuente, nota=turno, usuario_carga_id=user_id,
        ))
    db.flush()


def agregar_bloque(db: Session, persona: AsistenciaPersona, fecha: date,
                   ingreso: str, egreso: str, user_id=None,
                   fuente: str = "planilla", turno: str = None):
    """Suma un bloque más al día sin anular los existentes.

    Es el caso del operativo de buque: trabajó 08-16, se fue, y volvió 22-06.
    """
    ultimo = (
        db.query(AsistenciaMarcacion)
        .filter(AsistenciaMarcacion.persona_id == persona.id,
                AsistenciaMarcacion.fecha_operativa == fecha,
                AsistenciaMarcacion.anulada_at.is_(None))
        .order_by(AsistenciaMarcacion.ts.desc())
        .first()
    )
    ts_in = _ts(fecha, ingreso) if ingreso else None
    if ts_in is not None and ultimo is not None and ts_in <= ultimo.ts:
        ts_in += timedelta(days=1)
    ts_out = _ts(fecha, egreso, ref=ts_in) if egreso else None

    if ts_in is not None:
        db.add(AsistenciaMarcacion(
            persona_id=persona.id, ts=ts_in, tipo="IN", fecha_operativa=fecha,
            fuente=fuente, nota=turno, usuario_carga_id=user_id,
        ))
    if ts_out is not None:
        db.add(AsistenciaMarcacion(
            persona_id=persona.id, ts=ts_out, tipo="OUT", fecha_operativa=fecha,
            fuente=fuente, nota=turno, usuario_carga_id=user_id,
        ))
    db.flush()


def limpiar_dia(db: Session, persona: AsistenciaPersona, fecha: date, user_id=None):
    """Anula todas las marcaciones de una persona en una fecha y borra la jornada.
    Se usa para deshacer una fila cargada por error."""
    for m in (db.query(AsistenciaMarcacion)
              .filter(AsistenciaMarcacion.persona_id == persona.id,
                      AsistenciaMarcacion.fecha_operativa == fecha,
                      AsistenciaMarcacion.anulada_at.is_(None)).all()):
        m.anulada_at = datetime.utcnow()
        m.anulada_por_id = user_id
        m.anulada_motivo = "Fila vaciada desde la carga"
    for _b in (db.query(AsistenciaBloque)
               .filter(AsistenciaBloque.persona_id == persona.id,
                       AsistenciaBloque.fecha_operativa == fecha).all()):
        db.delete(_b)
    for _j in (db.query(AsistenciaJornada)
               .filter(AsistenciaJornada.persona_id == persona.id,
                       AsistenciaJornada.fecha == fecha).all()):
        db.delete(_j)
    db.flush()
