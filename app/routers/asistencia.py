"""
Asistencia — Fase 1: carga rápida de la planilla + cierre del día.

La carga es a día vencido: la planilla de hoy se pasa mañana. Por eso la
pantalla de carga abre en AYER y no en hoy, y por eso la pantalla principal
muestra el cierre de un día terminado en vez de un estado en vivo.

Todo el cálculo vive en app/asistencia_calc.py. Acá solo se leen formularios,
se guardan marcaciones y se arma lo que ve la pantalla.
"""
import os
import re
import tempfile
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Depends, File, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.permissions import require_perm
from app.templates import templates
from app.asistencia_import import (buscar_persona, indexar_personas,
                                   normalizar, parsear)
from app.asistencia_calc import (
    agregar_bloque, get_config, guardar_bloque, jornada_esperada, limpiar_dia,
    recalcular_jornada,
)
from app.models_asistencia import (
    AsistenciaAuditLog, AsistenciaBloque, AsistenciaFeriado, AsistenciaJornada,
    AsistenciaJornadaTipo, AsistenciaMotivo, AsistenciaPersona,
    ESTADO_EXTRA_CSS, ESTADO_REGISTRO_CSS, PLANTAS, TURNOS_OPERATIVO,
    min_a_texto,
)

router = APIRouter(prefix="/asistencia")

_ver    = require_perm("asistencia.ver")
_cargar = require_perm("asistencia.cargar")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _parse_fecha(valor, default: date) -> date:
    if not valor:
        return default
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(valor).strip(), fmt).date()
        except ValueError:
            continue
    return default


def _fecha_carga_default(db: Session) -> date:
    """Ayer, salvo que la config diga otra cosa. Abrir en 'hoy' sería el error
    más probable del sistema: cargar la planilla de ayer sobre la fecha de hoy."""
    cfg = get_config(db)
    return date.today() - timedelta(days=cfg.dias_offset_carga or 0)


def _personas_activas(db: Session, planta: str = ""):
    """Nómina activa. Cada planta tiene su propia planilla de portería, así que
    la pantalla de carga se filtra por planta."""
    q = (db.query(AsistenciaPersona)
         .options(joinedload(AsistenciaPersona.proveedor))
         .filter(AsistenciaPersona.activo == True))  # noqa: E712
    if planta:
        q = q.filter(AsistenciaPersona.planta == planta)
    return q.order_by(AsistenciaPersona.orden_planilla,
                      AsistenciaPersona.apellido).all()


def _jornadas_por_persona(db: Session, fecha: date) -> dict:
    return {
        j.persona_id: j
        for j in db.query(AsistenciaJornada).filter(AsistenciaJornada.fecha == fecha).all()
    }


def _bloques_por_persona(db: Session, fecha: date) -> dict:
    out = {}
    for b in (db.query(AsistenciaBloque)
              .filter(AsistenciaBloque.fecha_operativa == fecha)
              .order_by(AsistenciaBloque.desde).all()):
        out.setdefault(b.persona_id, []).append(b)
    return out


def _fila_ctx(db, persona, fecha, jornada=None, bloques=None):
    """Contexto de una fila de la pantalla de carga."""
    esp = jornada_esperada(db, persona, fecha)
    if bloques is None:
        bloques = (
            db.query(AsistenciaBloque)
            .filter(AsistenciaBloque.persona_id == persona.id,
                    AsistenciaBloque.fecha_operativa == fecha)
            .order_by(AsistenciaBloque.desde).all()
        )
    return {
        "p": persona, "fecha": fecha, "j": jornada, "bloques": bloques, "esp": esp,
    }


def _estado_mes(db: Session, anio: int, mes: int, personas_esperadas: int) -> list:
    """Un renglón por día del mes con su estado de carga.

    Es la pieza que hace usable la carga de un mes entero: sin esto, el riesgo
    real de la carga diferida no es no ver el presente, es que se pase un día
    sin cargar y el total del mes quede mal.
    """
    total_dias = monthrange(anio, mes)[1]
    hoy = date.today()

    cargados = dict(
        db.query(AsistenciaJornada.fecha, func.count(AsistenciaJornada.id))
        .filter(AsistenciaJornada.fecha >= date(anio, mes, 1),
                AsistenciaJornada.fecha <= date(anio, mes, total_dias))
        .group_by(AsistenciaJornada.fecha).all()
    )
    feriados = {
        f.fecha: f.nombre
        for f in db.query(AsistenciaFeriado).filter(
            AsistenciaFeriado.fecha >= date(anio, mes, 1),
            AsistenciaFeriado.fecha <= date(anio, mes, total_dias)).all()
    }

    dias = []
    for d in range(1, total_dias + 1):
        f = date(anio, mes, d)
        n = cargados.get(f, 0)
        if f > hoy:
            estado = "futuro"
        elif f.weekday() == 6 or f in feriados:
            estado = "cargado" if n else "no_laborable"
        elif n == 0:
            estado = "vacio"
        elif n < personas_esperadas:
            estado = "incompleto"
        else:
            estado = "cargado"
        dias.append({
            "fecha": f, "dia": d, "n": n, "estado": estado,
            "feriado": feriados.get(f),
            "dow": f.weekday(),
        })
    return dias


# ══════════════════════════════════════════════════════════════════════════════
# Pantalla principal — el cierre del día
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_class=HTMLResponse)
async def dia(request: Request, db: Session = Depends(get_db),
              current_user=Depends(_ver), fecha: str = "", planta: str = ""):
    f = _parse_fecha(fecha, _fecha_carga_default(db))

    q = (db.query(AsistenciaJornada)
         .options(joinedload(AsistenciaJornada.persona),
                  joinedload(AsistenciaJornada.motivo))
         .filter(AsistenciaJornada.fecha == f))
    if planta:
        q = q.filter(AsistenciaJornada.planta_snap == planta)
    jornadas = q.all()
    bloques = _bloques_por_persona(db, f)
    for j in jornadas:
        j._bloques = bloques.get(j.persona_id, [])

    mtr     = [j for j in jornadas if j.tipo_persona_snap != "tercero"]
    terceros = [j for j in jornadas if j.tipo_persona_snap == "tercero"]
    trabajaron = [j for j in mtr if j.cantidad_bloques > 0]

    # Acumulado del mes, hasta la fecha vista inclusive.
    ini_mes = f.replace(day=1)
    extra_mes = (
        db.query(func.coalesce(func.sum(AsistenciaJornada.minutos_extra_detectada), 0))
        .filter(AsistenciaJornada.fecha >= ini_mes, AsistenciaJornada.fecha <= f,
                AsistenciaJornada.tipo_persona_snap != "tercero").scalar() or 0
    )

    kpis = {
        "trabajaron":   len(trabajaron),
        "ausentes":     len([j for j in mtr if j.estado_registro == "ausente"]),
        "extra":        sum(j.minutos_extra_detectada or 0 for j in mtr),
        "e50":          sum(j.minutos_extra_50 or 0 for j in mtr),
        "e100":         sum(j.minutos_extra_100 or 0 for j in mtr),
        "fuera":        sum(j.minutos_fuera_horario or 0 for j in mtr),
        "defecto":      sum(j.minutos_defecto or 0 for j in mtr),
        "extra_mes":    extra_mes,
        "terceros":     len(terceros),
        "horas_terceros": sum(j.minutos_presencia or 0 for j in terceros),
        "personas_con_extra": len([j for j in mtr if (j.minutos_extra_detectada or 0) > 0]),
    }

    # ── Requiere atención — ordenado por criticidad, no por nombre ────────────
    atencion = []
    for j in jornadas:
        if j.estado_registro == "inconsistente":
            atencion.append((0, j, "Registro inconsistente",
                             "Egreso sin ingreso, doble ingreso o bloque de más de 16 h."))
        elif j.estado_registro == "sin_egreso":
            atencion.append((1, j, "Sin egreso",
                             "No computa extra hasta que se corrija."))
        elif j.revisar:
            atencion.append((2, j, "Recalculada tras decisión",
                             "Cambió la extra detectada de una jornada ya resuelta."))
        elif (j.minutos_extra_detectada or 0) > 0 and j.estado_extra == "pendiente":
            atencion.append((3, j, "Extra pendiente", None))
    atencion.sort(key=lambda t: (t[0], -(t[1].minutos_extra_detectada or 0)))

    qp = db.query(func.count(AsistenciaPersona.id)).filter(
        AsistenciaPersona.activo == True,  # noqa: E712
        AsistenciaPersona.tipo == "mtr")
    if planta:
        qp = qp.filter(AsistenciaPersona.planta == planta)
    personas_activas = qp.scalar() or 0

    return templates.TemplateResponse(request, "asistencia/dia.html", {
        "current_user": current_user,
        "fecha": f,
        "planta": planta,
        "plantas": PLANTAS,
        "prev": f - timedelta(days=1),
        "next": f + timedelta(days=1),
        "hoy": date.today(),
        "jornadas": sorted(jornadas, key=lambda j: (
            -(j.minutos_extra_detectada or 0), j.persona.apellido if j.persona else "")),
        "atencion": atencion,
        "kpis": kpis,
        "cargadas": len(jornadas),
        "personas_activas": personas_activas,
        "dias_mes": _estado_mes(db, f.year, f.month, personas_activas),
        "estado_extra_css": ESTADO_EXTRA_CSS,
        "estado_registro_css": ESTADO_REGISTRO_CSS,
        "mt": min_a_texto,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Carga rápida
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/carga", response_class=HTMLResponse)
async def carga(request: Request, db: Session = Depends(get_db),
                current_user=Depends(_cargar), fecha: str = "", planta: str = ""):
    f = _parse_fecha(fecha, _fecha_carga_default(db))

    personas = _personas_activas(db, planta)
    jornadas = _jornadas_por_persona(db, f)
    bloques  = _bloques_por_persona(db, f)

    filas = [_fila_ctx(db, p, f, jornadas.get(p.id), bloques.get(p.id, [])) for p in personas]

    cargadas = len([1 for x in filas if x["j"] is not None])
    sin_egreso = len([1 for x in filas if x["j"] is not None
                      and x["j"].estado_registro == "sin_egreso"])

    personas_mtr = len([p for p in personas if p.tipo == "mtr"])

    return templates.TemplateResponse(request, "asistencia/carga.html", {
        "current_user": current_user,
        "fecha": f,
        "planta": planta,
        "plantas": PLANTAS,
        "prev": f - timedelta(days=1),
        "next": f + timedelta(days=1),
        "hoy": date.today(),
        "filas": filas,
        "total": len(filas),
        "cargadas": cargadas,
        "sin_egreso": sin_egreso,
        "dias_mes": _estado_mes(db, f.year, f.month, personas_mtr),
        "turnos": TURNOS_OPERATIVO,
        "mt": min_a_texto,
    })


@router.post("/carga/fila", response_class=HTMLResponse)
async def guardar_fila(request: Request, db: Session = Depends(get_db),
                       current_user=Depends(_cargar),
                       persona_id: int = Form(...), fecha: str = Form(...),
                       ingreso: str = Form(""), egreso: str = Form("")):
    """Guarda una fila. Devuelve la fila re-renderizada (HTMX).

    Guardado por fila y no por formulario completo: si se corta la conexión o
    se cierra el navegador a mitad de una planilla, no se pierde nada.
    """
    f = _parse_fecha(fecha, date.today())
    persona = db.query(AsistenciaPersona).get(persona_id)
    if persona is None:
        return HTMLResponse("", status_code=404)

    ingreso, egreso = (ingreso or "").strip(), (egreso or "").strip()

    if not ingreso and not egreso:
        limpiar_dia(db, persona, f, current_user.id)
        db.commit()
        return _render_fila(request, db, persona, f, current_user)

    guardar_bloque(db, persona, f, ingreso, egreso, user_id=current_user.id)
    recalcular_jornada(db, persona, f)
    db.commit()
    return _render_fila(request, db, persona, f, current_user)


@router.post("/carga/bloque", response_class=HTMLResponse)
async def sumar_bloque(request: Request, db: Session = Depends(get_db),
                       current_user=Depends(_cargar),
                       persona_id: int = Form(...), fecha: str = Form(...),
                       ingreso: str = Form(""), egreso: str = Form(""),
                       turno: str = Form("")):
    """Agrega un bloque más al día sin pisar el anterior.

    Es el caso del operativo de buque: trabajó 08-16, se fue, volvió 22-06.
    """
    f = _parse_fecha(fecha, date.today())
    persona = db.query(AsistenciaPersona).get(persona_id)
    if persona is None or not (ingreso or "").strip():
        return _render_fila(request, db, persona, f, current_user) if persona else HTMLResponse("", 404)

    agregar_bloque(db, persona, f, ingreso.strip(), (egreso or "").strip(),
                   user_id=current_user.id,
                   fuente="turno_operativo" if turno else "planilla",
                   turno=turno or None)
    recalcular_jornada(db, persona, f)
    db.commit()
    return _render_fila(request, db, persona, f, current_user)


@router.post("/carga/ausente", response_class=HTMLResponse)
async def marcar_ausente(request: Request, db: Session = Depends(get_db),
                         current_user=Depends(_cargar),
                         persona_id: int = Form(...), fecha: str = Form(...)):
    """Ausente explícito. Vacío significa 'todavía no cargué', no 'no vino':
    sin esa distinción no se puede saber si la planilla está completa."""
    f = _parse_fecha(fecha, date.today())
    persona = db.query(AsistenciaPersona).get(persona_id)
    if persona is None:
        return HTMLResponse("", status_code=404)
    limpiar_dia(db, persona, f, current_user.id)
    recalcular_jornada(db, persona, f, marcar_ausente=True)
    db.commit()
    return _render_fila(request, db, persona, f, current_user)


@router.post("/carga/masivo")
async def carga_masiva(db: Session = Depends(get_db), current_user=Depends(_cargar),
                       fecha: str = Form(...), accion: str = Form(...),
                       hora: str = Form(""), planta: str = Form("")):
    """Atajos de masa. En la planilla real 15 de 18 personas entran 08:00:
    este botón ahorra más tiempo que cualquier otra optimización."""
    f = _parse_fecha(fecha, date.today())
    personas = [p for p in _personas_activas(db, planta) if p.tipo == "mtr"]
    jornadas = _jornadas_por_persona(db, f)
    bloques  = _bloques_por_persona(db, f)
    n = 0

    for p in personas:
        j = jornadas.get(p.id)
        bs = bloques.get(p.id, [])
        esp = jornada_esperada(db, p, f)

        if accion == "ingresos":
            if bs or (j and j.estado_registro == "ausente"):
                continue
            h = hora or esp["hora_entrada"] or "08:00"
            guardar_bloque(db, p, f, h, "", user_id=current_user.id)
        elif accion == "egresos":
            if not bs or bs[-1].hasta is not None:
                continue
            h = hora or esp["hora_salida"] or "16:00"
            ing = bs[-1].desde.strftime("%H:%M")
            guardar_bloque(db, p, f, ing, h, user_id=current_user.id)
        elif accion == "ausentes":
            if bs or j is not None:
                continue
            limpiar_dia(db, p, f, current_user.id)
            recalcular_jornada(db, p, f, marcar_ausente=True)
            n += 1
            continue
        else:
            continue

        recalcular_jornada(db, p, f)
        n += 1

    db.commit()
    return RedirectResponse(
        url=f"/asistencia/carga?fecha={f.isoformat()}"
            f"{'&planta=' + planta if planta else ''}&ok={n}+filas+actualizadas",
        status_code=303)


def _render_fila(request, db, persona, fecha, current_user):
    j = (db.query(AsistenciaJornada)
         .filter(AsistenciaJornada.persona_id == persona.id,
                 AsistenciaJornada.fecha == fecha).first())
    ctx = _fila_ctx(db, persona, fecha, j)
    ctx.update({"current_user": current_user, "mt": min_a_texto,
                "turnos": TURNOS_OPERATIVO})
    return templates.TemplateResponse(request, "asistencia/_fila.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# Nómina — administración del personal propio
# ══════════════════════════════════════════════════════════════════════════════

_config = require_perm("asistencia.configurar")


def _render_persona(request, db, persona, current_user):
    return templates.TemplateResponse(request, "asistencia/_persona_fila.html", {
        "current_user": current_user,
        "p": persona,
        "plantas": PLANTAS,
        "jornadas": db.query(AsistenciaJornadaTipo).filter(
            AsistenciaJornadaTipo.activo == True).order_by(AsistenciaJornadaTipo.id).all(),  # noqa: E712
    })


@router.get("/personal", response_class=HTMLResponse)
async def personal(request: Request, db: Session = Depends(get_db),
                   current_user=Depends(_config), ver_bajas: int = 0):
    q = db.query(AsistenciaPersona)
    if not ver_bajas:
        q = q.filter(AsistenciaPersona.activo == True)  # noqa: E712
    personas = q.order_by(AsistenciaPersona.orden_planilla,
                          AsistenciaPersona.apellido).all()

    jornadas = (db.query(AsistenciaJornadaTipo)
                .filter(AsistenciaJornadaTipo.activo == True)  # noqa: E712
                .order_by(AsistenciaJornadaTipo.id).all())

    propias = [p for p in personas if p.tipo == "mtr"]
    activos = [p for p in propias if p.activo]
    return templates.TemplateResponse(request, "asistencia/personal.html", {
        "current_user": current_user,
        "personas": propias,
        "plantas": PLANTAS,
        "jornadas": jornadas,
        "ver_bajas": ver_bajas,
        "sin_planta": len([p for p in activos if not p.planta]),
        "activos": len(activos),
        "uso_planta": {cod: len([p for p in activos if p.planta == cod])
                       for cod, _ in PLANTAS},
    })


@router.post("/personal/fila", response_class=HTMLResponse)
async def personal_guardar(request: Request, db: Session = Depends(get_db),
                           current_user=Depends(_config),
                           persona_id: int = Form(...),
                           planta: str = Form(""), jornada_tipo_id: str = Form(""),
                           legajo: str = Form(""), dni: str = Form(""),
                           orden_planilla: str = Form("")):
    """Guarda una fila de la nómina. Cada cambio queda auditado con su valor
    anterior: cambiar la planta o la jornada de alguien mueve números de meses
    enteros, así que no puede pasar sin registro."""
    p = db.query(AsistenciaPersona).get(persona_id)
    if p is None:
        return HTMLResponse("", status_code=404)

    cambios = [
        ("planta",          p.planta,          (planta or "").strip() or None),
        ("jornada_tipo_id", p.jornada_tipo_id, int(jornada_tipo_id) if jornada_tipo_id else None),
        ("legajo",          p.legajo,          (legajo or "").strip() or None),
        ("dni",             p.dni,             (dni or "").strip() or None),
        ("orden_planilla",  p.orden_planilla,  int(orden_planilla) if (orden_planilla or "").strip().isdigit() else p.orden_planilla),
    ]
    for campo, viejo, nuevo in cambios:
        if viejo == nuevo:
            continue
        setattr(p, campo, nuevo)
        db.add(AsistenciaAuditLog(
            entidad="persona", entidad_id=p.id, accion="editar", campo=campo,
            valor_anterior=str(viejo) if viejo is not None else None,
            valor_nuevo=str(nuevo) if nuevo is not None else None,
            user_id=current_user.id,
        ))
    db.commit()
    db.refresh(p)
    return _render_persona(request, db, p, current_user)


@router.post("/personal/estado", response_class=HTMLResponse)
async def personal_estado(request: Request, db: Session = Depends(get_db),
                          current_user=Depends(_config),
                          persona_id: int = Form(...)):
    """Alta / baja. NUNCA se borra una persona: sale de la planilla de carga
    pero queda en todos los históricos y reportes."""
    p = db.query(AsistenciaPersona).get(persona_id)
    if p is None:
        return HTMLResponse("", status_code=404)
    p.activo = not p.activo
    p.fecha_baja = None if p.activo else date.today()
    db.add(AsistenciaAuditLog(
        entidad="persona", entidad_id=p.id,
        accion="alta" if p.activo else "baja", campo="activo",
        valor_anterior=str(not p.activo), valor_nuevo=str(p.activo),
        user_id=current_user.id,
    ))
    db.commit()
    db.refresh(p)
    return _render_persona(request, db, p, current_user)


@router.post("/personal/nueva")
async def personal_nueva(db: Session = Depends(get_db), current_user=Depends(_config),
                         apellido: str = Form(...), nombre: str = Form(...),
                         planta: str = Form(""), jornada_tipo_id: str = Form(""),
                         legajo: str = Form(""), dni: str = Form("")):
    apellido, nombre = apellido.strip(), nombre.strip()
    if not apellido or not nombre:
        return RedirectResponse("/asistencia/personal?err=Falta+apellido+o+nombre", 303)

    ya = (db.query(AsistenciaPersona)
          .filter(AsistenciaPersona.apellido == apellido,
                  AsistenciaPersona.nombre == nombre).first())
    if ya is not None:
        if not ya.activo:   # reingreso: se reactiva, no se duplica
            ya.activo, ya.fecha_baja = True, None
            db.commit()
            return RedirectResponse("/asistencia/personal?ok=Persona+reactivada", 303)
        return RedirectResponse("/asistencia/personal?err=Esa+persona+ya+está+en+la+nómina", 303)

    ultimo = (db.query(func.coalesce(func.max(AsistenciaPersona.orden_planilla), 0))
              .scalar() or 0)
    p = AsistenciaPersona(
        apellido=apellido, nombre=nombre, tipo="mtr",
        planta=(planta or "").strip() or None,
        jornada_tipo_id=int(jornada_tipo_id) if jornada_tipo_id else None,
        legajo=(legajo or "").strip() or None, dni=(dni or "").strip() or None,
        orden_planilla=ultimo + 10, activo=True, fecha_alta=date.today(),
    )
    db.add(p)
    db.flush()
    db.add(AsistenciaAuditLog(entidad="persona", entidad_id=p.id, accion="crear",
                              valor_nuevo=f"{apellido}, {nombre}", user_id=current_user.id))
    db.commit()
    return RedirectResponse(f"/asistencia/personal?ok={apellido}+agregado+a+la+nómina", 303)


# ══════════════════════════════════════════════════════════════════════════════
# Importación del Excel diario de horas
# ══════════════════════════════════════════════════════════════════════════════

_IMPORT_DIR = os.path.join(tempfile.gettempdir(), "mtr_asistencia_import")


def _planta_de_hoja(hoja: str) -> str:
    """'HS. SEPTIEMBRE. MTR I 2026' → 'MTR1'. Devuelve '' si no se puede inferir.

    El nombre de la hoja dice de qué planta es la planilla, y esa es la planta
    que vale para la jornada: Báez rota entre plantas y en la planilla de MTR1
    sus horas son de MTR1, no las del maestro.
    """
    h = (hoja or "").upper().replace(".", " ")
    if re.search(r"MTR\s*II\b|MTR\s*2\b", h):
        return "MTR2"
    if re.search(r"MTR\s*I\b|MTR\s*1\b", h):
        return "MTR1"
    return ""


def _guardar_temporal(contenido: bytes) -> str:
    """Guarda el archivo subido y devuelve un token.

    El preview y la confirmación son dos requests distintos y el navegador no
    puede reenviar el archivo solo. Se guarda en el temporal del sistema: si se
    perdió (reinicio del server, filesystem efímero de Railway), se le pide al
    usuario que lo suba de nuevo en vez de fallar con un error críptico.
    """
    os.makedirs(_IMPORT_DIR, exist_ok=True)
    token = uuid.uuid4().hex
    with open(os.path.join(_IMPORT_DIR, token + ".xlsx"), "wb") as fh:
        fh.write(contenido)
    return token


def _leer_temporal(token: str):
    ruta = os.path.join(_IMPORT_DIR, os.path.basename(token) + ".xlsx")
    if not os.path.exists(ruta):
        return None
    with open(ruta, "rb") as fh:
        return fh.read()


def _analizar(db: Session, datos: dict, tipo: str = "mtr") -> dict:
    """Cruza lo parseado contra la nómina y contra lo ya cargado.

    El match se hace SOLO contra personas del mismo tipo: la planilla de
    terceros no puede resolver un nombre contra un empleado propio.
    """
    personas = db.query(AsistenciaPersona).filter(
        AsistenciaPersona.activo == True,  # noqa: E712
        AsistenciaPersona.tipo == tipo).all()
    idx = indexar_personas(personas)
    grupos = {}

    dias, no_reconocidos = [], {}
    for d in datos["dias"]:
        existentes = {
            j.persona_id: j for j in db.query(AsistenciaJornada)
            .filter(AsistenciaJornada.fecha == d["fecha"]).all()
        }
        bloques = _bloques_por_persona(db, d["fecha"])

        filas, iguales, distintas, nuevas = [], 0, 0, 0
        for f in d["filas"]:
            grupos[f["grupo"]] = grupos.get(f["grupo"], 0) + 1
            persona, motivo = buscar_persona(idx, f["apellido"], f["nombre"])
            if persona is None:
                clave = f"{f['apellido']}, {f['nombre']}"
                no_reconocidos.setdefault(clave, {"motivo": motivo, "veces": 0,
                                                  "grupo": f["grupo"]})
                no_reconocidos[clave]["veces"] += 1
                filas.append({**f, "persona": None, "estado": "sin_match"})
                continue

            bs = bloques.get(persona.id, [])
            actual_in = bs[0].desde.strftime("%H:%M") if bs else None
            actual_out = (bs[0].hasta.strftime("%H:%M")
                          if bs and bs[0].hasta else None)
            # El Excel escribe la medianoche como 24:00 y el sistema la guarda
            # como 00:00 del día siguiente: son la misma hora.
            nuevo_out = f["egreso"]
            if nuevo_out == "24:00" and actual_out == "00:00":
                nuevo_out = "00:00"

            if persona.id not in existentes:
                estado = "nueva"; nuevas += 1
            elif f["ausente"]:
                estado = ("igual" if existentes[persona.id].estado_registro == "ausente"
                          else "distinta")
                iguales += (estado == "igual"); distintas += (estado == "distinta")
            elif actual_in == f["ingreso"] and actual_out == nuevo_out:
                estado = "igual"; iguales += 1
            else:
                estado = "distinta"; distintas += 1

            filas.append({**f, "persona": persona, "estado": estado,
                          "actual_in": actual_in, "actual_out": actual_out})

        dias.append({
            "fecha": d["fecha"],
            "filas": filas,
            "n": len(filas),
            "cargadas": len(existentes),
            "iguales": iguales, "distintas": distintas, "nuevas": nuevas,
            "cambia": distintas + nuevas,
        })

    return {"dias": dias, "no_reconocidos": no_reconocidos, "grupos": grupos,
            "avisos": datos.get("avisos", [])}


@router.get("/importar", response_class=HTMLResponse)
async def importar_form(request: Request, db: Session = Depends(get_db),
                        current_user=Depends(_cargar), tipo: str = "mtr"):
    return templates.TemplateResponse(request, "asistencia/importar.html", {
        "current_user": current_user, "paso": "subir", "tipo_sel": tipo,
    })


@router.post("/importar", response_class=HTMLResponse)
async def importar_preview(request: Request, db: Session = Depends(get_db),
                           current_user=Depends(_cargar),
                           archivo: UploadFile = File(None),
                           token: str = Form(""), hoja: str = Form(""),
                           tipo: str = Form("mtr"), planta: str = Form("")):
    """Vista previa. NO toca la base: solo muestra qué cambiaría."""
    if archivo is not None and archivo.filename:
        contenido = await archivo.read()
        token = _guardar_temporal(contenido)
    else:
        contenido = _leer_temporal(token) if token else None

    if not contenido:
        return templates.TemplateResponse(request, "asistencia/importar.html", {
            "current_user": current_user, "paso": "subir", "tipo_sel": tipo,
            "error": "Se perdió el archivo subido. Volvé a elegirlo.",
        })

    try:
        datos = parsear(contenido, hoja or None)
    except Exception as e:
        return templates.TemplateResponse(request, "asistencia/importar.html", {
            "current_user": current_user, "paso": "subir", "tipo_sel": tipo,
            "error": f"No pude leer el Excel: {e}",
        })

    analisis = _analizar(db, datos, tipo)
    return templates.TemplateResponse(request, "asistencia/importar.html", {
        "current_user": current_user, "paso": "previo",
        "token": token, "hoja": datos["hoja"], "hojas": datos["hojas"],
        "tipo": tipo, "plantas": PLANTAS,
        "planta": planta or _planta_de_hoja(datos["hoja"]),
        "nombre_archivo": archivo.filename if archivo and archivo.filename else "",
        **analisis,
    })


@router.post("/importar/aplicar")
async def importar_aplicar(db: Session = Depends(get_db), current_user=Depends(_cargar),
                           token: str = Form(...), hoja: str = Form(""),
                           fechas: list = Form([]), tipo: str = Form("mtr"),
                           planta: str = Form(""), alta_nuevos: str = Form("")):
    """Aplica los días tildados. Idempotente: reimportar un día reemplaza sus
    marcaciones (las viejas quedan anuladas, no borradas)."""
    contenido = _leer_temporal(token)
    if not contenido:
        return RedirectResponse(
            "/asistencia/importar?err=Se+perdió+el+archivo.+Subilo+de+nuevo", 303)

    datos = parsear(contenido, hoja or None)
    elegidas = {_parse_fecha(f, None) for f in fechas}
    elegidas.discard(None)
    if not elegidas:
        return RedirectResponse("/asistencia/importar?err=No+elegiste+ningún+día", 303)

    planta = planta or _planta_de_hoja(datos["hoja"])
    personas = db.query(AsistenciaPersona).filter(
        AsistenciaPersona.activo == True,  # noqa: E712
        AsistenciaPersona.tipo == tipo).all()
    idx = indexar_personas(personas)

    n_filas, n_dias, sin_match, n_altas = 0, 0, 0, 0
    for d in datos["dias"]:
        if d["fecha"] not in elegidas:
            continue
        n_dias += 1
        for f in d["filas"]:
            persona, _ = buscar_persona(idx, f["apellido"], f["nombre"])
            if persona is None and tipo == "tercero" and alta_nuevos:
                # En terceros la gente rota todo el tiempo: se dan de alta solos
                # si el usuario lo pidió. En personal propio NUNCA.
                ultimo = (db.query(func.coalesce(
                    func.max(AsistenciaPersona.orden_planilla), 0)).scalar() or 0)
                persona = AsistenciaPersona(
                    apellido=f["apellido"].strip(), nombre=f["nombre"].strip(),
                    tipo="tercero", planta=planta or None, grupo=f["grupo"] or None,
                    orden_planilla=ultimo + 10, activo=True, fecha_alta=date.today())
                db.add(persona)
                db.flush()
                idx.setdefault(normalizar(persona.apellido), []).append(persona)
                db.add(AsistenciaAuditLog(
                    entidad="persona", entidad_id=persona.id, accion="crear",
                    valor_nuevo=f"{persona.apellido}, {persona.nombre} ({f['grupo']})",
                    motivo="Alta automática desde la importación de terceros",
                    user_id=current_user.id))
                n_altas += 1
            if persona is None:
                sin_match += 1
                continue

            # El grupo de la planilla se guarda en el maestro para que la
            # nómina lo muestre sin depender de la última importación.
            if f["grupo"] and persona.grupo != f["grupo"]:
                persona.grupo = f["grupo"]

            limpiar_dia(db, persona, d["fecha"], current_user.id)
            if f["ausente"] or not f["ingreso"]:
                j = recalcular_jornada(db, persona, d["fecha"], marcar_ausente=True)
            else:
                guardar_bloque(db, persona, d["fecha"], f["ingreso"], f["egreso"] or "",
                               user_id=current_user.id, fuente="import")
                j = recalcular_jornada(db, persona, d["fecha"])
            if j is not None:
                j.nota_origen = f["nota"]
                j.grupo_snap = f["grupo"] or None
                if planta:
                    j.planta_snap = planta
            n_filas += 1

        db.add(AsistenciaAuditLog(
            entidad="jornada", accion="importar", campo="dia",
            valor_nuevo=d["fecha"].isoformat(),
            motivo=f"Importación del Excel · hoja {datos['hoja']}",
            user_id=current_user.id,
        ))

    db.commit()
    msg = f"{n_dias}+días+importados+·+{n_filas}+filas"
    if n_altas:
        msg += f"+·+{n_altas}+altas"
    if sin_match:
        msg += f"+·+{sin_match}+sin+reconocer"
    destino = "/asistencia/terceros" if tipo == "tercero" else "/asistencia"
    return RedirectResponse(f"{destino}?ok={msg}", 303)


@router.get("/terceros", response_class=HTMLResponse)
async def terceros(request: Request, db: Session = Depends(get_db),
                   current_user=Depends(_ver), mes: str = ""):
    """Personal de terceros — pantalla SEPARADA a propósito.

    Sus horas no se suman en ningún KPI junto a las del personal propio. Acá se
    ven por proveedor (el grupo de su planilla) y por persona.
    """
    hoy = date.today()
    try:
        anio, m = (int(x) for x in mes.split("-")) if mes else (hoy.year, hoy.month)
    except ValueError:
        anio, m = hoy.year, hoy.month
    desde, hasta = date(anio, m, 1), date(anio, m, monthrange(anio, m)[1])

    jornadas = (
        db.query(AsistenciaJornada)
        .options(joinedload(AsistenciaJornada.persona))
        .filter(AsistenciaJornada.fecha >= desde, AsistenciaJornada.fecha <= hasta,
                AsistenciaJornada.tipo_persona_snap == "tercero")
        .order_by(AsistenciaJornada.fecha).all()
    )

    por_grupo, por_persona = {}, {}
    for j in jornadas:
        g = j.grupo_snap or j.proveedor_snap or "Sin proveedor"
        gd = por_grupo.setdefault(g, {"minutos": 0, "dias": set(), "personas": set()})
        gd["minutos"] += j.minutos_presencia or 0
        gd["dias"].add(j.fecha)
        gd["personas"].add(j.persona_id)

        nom = f"{j.persona.apellido}, {j.persona.nombre}" if j.persona else "?"
        pd = por_persona.setdefault((g, nom), {"minutos": 0, "dias": 0})
        pd["minutos"] += j.minutos_presencia or 0
        pd["dias"] += 1 if (j.minutos_presencia or 0) else 0

    resumen = sorted(
        ({"grupo": g, "minutos": v["minutos"], "dias": len(v["dias"]),
          "personas": len(v["personas"])} for g, v in por_grupo.items()),
        key=lambda x: -x["minutos"])
    detalle = sorted(
        ({"grupo": g, "persona": n, **v} for (g, n), v in por_persona.items()),
        key=lambda x: (x["grupo"], -x["minutos"]))

    prev_m = (desde - timedelta(days=1)).replace(day=1)
    next_m = (hasta + timedelta(days=1))
    return templates.TemplateResponse(request, "asistencia/terceros.html", {
        "current_user": current_user,
        "anio": anio, "mes_num": m, "desde": desde, "hasta": hasta,
        "prev": prev_m.strftime("%Y-%m"), "next": next_m.strftime("%Y-%m"),
        "hay_next": next_m <= hoy,
        "resumen": resumen, "detalle": detalle,
        "total_min": sum(r["minutos"] for r in resumen),
        "total_personas": len({j.persona_id for j in jornadas}),
        "mt": min_a_texto,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard mensual
# ══════════════════════════════════════════════════════════════════════════════

_reportes = require_perm("asistencia.reportes")


def _rango_mes(mes: str):
    hoy = date.today()
    try:
        anio, m = (int(x) for x in mes.split("-"))
        date(anio, m, 1)
    except (ValueError, TypeError):
        anio, m = hoy.year, hoy.month
    return anio, m, date(anio, m, 1), date(anio, m, monthrange(anio, m)[1])


def _datos_mes(db: Session, desde: date, hasta: date, planta: str = "", grupo: str = ""):
    q = (db.query(AsistenciaJornada)
         .options(joinedload(AsistenciaJornada.persona),
                  joinedload(AsistenciaJornada.motivo))
         .filter(AsistenciaJornada.fecha >= desde,
                 AsistenciaJornada.fecha <= hasta,
                 AsistenciaJornada.tipo_persona_snap != "tercero"))
    if planta:
        q = q.filter(AsistenciaJornada.planta_snap == planta)
    if grupo:
        q = q.filter(AsistenciaJornada.grupo_snap == grupo)
    return q.order_by(AsistenciaJornada.fecha).all()


@router.get("/mes", response_class=HTMLResponse)
async def mes(request: Request, db: Session = Depends(get_db),
              current_user=Depends(_reportes),
              mes: str = "", planta: str = "", grupo: str = ""):
    """Parcial del mes en curso y reporte final del mes cerrado.

    Es la misma pantalla: mientras el mes corre muestra hasta dónde llegó la
    carga, y cuando está completo es el reporte. Separarlas en dos daría dos
    números distintos para la misma pregunta.
    """
    cfg = get_config(db)
    hoy = date.today()
    anio, m, desde, hasta = _rango_mes(mes)
    jornadas = _datos_mes(db, desde, hasta, planta, grupo)

    feriados = {f.fecha for f in db.query(AsistenciaFeriado).filter(
        AsistenciaFeriado.fecha >= desde, AsistenciaFeriado.fecha <= hasta).all()}

    # ── Serie diaria ─────────────────────────────────────────────────────────
    por_dia = {}
    for j in jornadas:
        d = por_dia.setdefault(j.fecha, {"e50": 0, "e100": 0, "n": 0, "personas": set()})
        d["e50"] += j.minutos_extra_50 or 0
        d["e100"] += j.minutos_extra_100 or 0
        d["n"] += 1
        if (j.minutos_extra_detectada or 0) > 0:
            d["personas"].add(j.persona_id)

    dias, laborables, cargados = [], 0, 0
    for n in range(1, monthrange(anio, m)[1] + 1):
        f = date(anio, m, n)
        dat = por_dia.get(f)
        es_laborable = f.weekday() != 6 and f not in feriados
        if es_laborable and f <= hoy:
            laborables += 1
            if dat:
                cargados += 1
        dias.append({
            "fecha": f, "dia": n, "dow": f.weekday(),
            "e50": dat["e50"] if dat else 0,
            "e100": dat["e100"] if dat else 0,
            "total": (dat["e50"] + dat["e100"]) if dat else 0,
            "personas": len(dat["personas"]) if dat else 0,
            "cargado": bool(dat),
            "futuro": f > hoy,
            "feriado": f in feriados,
            "domingo": f.weekday() == 6,
        })

    # ── Ranking de personas ──────────────────────────────────────────────────
    porp = {}
    for j in jornadas:
        k = j.persona_id
        p = porp.setdefault(k, {
            "persona": j.persona, "e50": 0, "e100": 0, "aprobada": 0,
            "trabajado": 0, "defecto": 0, "dias_extra": 0, "fechas_extra": set(),
            "planta": j.planta_snap, "grupo": j.grupo_snap, "pendientes": 0,
        })
        p["e50"] += j.minutos_extra_50 or 0
        p["e100"] += j.minutos_extra_100 or 0
        p["trabajado"] += j.minutos_trabajados or 0
        p["defecto"] += j.minutos_defecto or 0
        if j.minutos_extra_aprobada is not None:
            p["aprobada"] += j.minutos_extra_aprobada
        if (j.minutos_extra_detectada or 0) > 0:
            p["dias_extra"] += 1
            p["fechas_extra"].add(j.fecha)
            if j.estado_extra == "pendiente":
                p["pendientes"] += 1

    def _racha(fechas):
        """Días consecutivos con extra más largos del mes."""
        if not fechas:
            return 0
        ord_f = sorted(fechas)
        mejor = actual = 1
        for a, b in zip(ord_f, ord_f[1:]):
            actual = actual + 1 if (b - a).days == 1 else 1
            mejor = max(mejor, actual)
        return mejor

    ranking = []
    for v in porp.values():
        total = v["e50"] + v["e100"]
        ranking.append({**v, "total": total, "racha": _racha(v["fechas_extra"])})
    ranking.sort(key=lambda x: -x["total"])

    # ── Cortes ───────────────────────────────────────────────────────────────
    def _agrupar(campo):
        acc = {}
        for j in jornadas:
            k = getattr(j, campo) or "—"
            a = acc.setdefault(k, {"clave": k, "total": 0, "personas": set()})
            a["total"] += (j.minutos_extra_50 or 0) + (j.minutos_extra_100 or 0)
            a["personas"].add(j.persona_id)
        out = [{"clave": v["clave"], "total": v["total"], "personas": len(v["personas"])}
               for v in acc.values()]
        return sorted(out, key=lambda x: -x["total"])

    e50 = sum(j.minutos_extra_50 or 0 for j in jornadas)
    e100 = sum(j.minutos_extra_100 or 0 for j in jornadas)
    total_extra = e50 + e100
    aprobada = sum(j.minutos_extra_aprobada or 0 for j in jornadas
                   if j.minutos_extra_aprobada is not None)

    estados = {}
    for j in jornadas:
        if (j.minutos_extra_detectada or 0) > 0:
            estados[j.estado_extra] = estados.get(j.estado_extra, 0) + 1

    # ── Umbrales de acumulación ──────────────────────────────────────────────
    umbrales = [(cfg.umbral_mes_3_min, 3), (cfg.umbral_mes_2_min, 2), (cfg.umbral_mes_1_min, 1)]
    alertas = []
    for r in ranking:
        nivel = next((n for u, n in umbrales if r["total"] >= (u or 10 ** 9)), 0)
        if nivel:
            alertas.append({**r, "nivel": nivel})
        elif r["racha"] >= (cfg.dias_racha_alerta or 3):
            alertas.append({**r, "nivel": 0})

    plantas_disp = sorted({j.planta_snap for j in _datos_mes(db, desde, hasta) if j.planta_snap})
    grupos_disp = sorted({j.grupo_snap for j in _datos_mes(db, desde, hasta) if j.grupo_snap})

    prev_m = (desde - timedelta(days=1)).replace(day=1)
    next_m = hasta + timedelta(days=1)
    return templates.TemplateResponse(request, "asistencia/mes.html", {
        "current_user": current_user,
        "anio": anio, "mes_num": m, "desde": desde, "hasta": hasta,
        "prev": prev_m.strftime("%Y-%m"), "next": next_m.strftime("%Y-%m"),
        "hay_next": next_m <= hoy, "hoy": hoy,
        "en_curso": desde <= hoy <= hasta,
        "planta": planta, "grupo": grupo,
        "plantas_disp": plantas_disp, "grupos_disp": grupos_disp,
        "dias": dias, "ranking": ranking, "alertas": alertas,
        "por_grupo": _agrupar("grupo_snap"), "por_planta": _agrupar("planta_snap"),
        "estados": estados,
        "laborables": laborables, "cargados": cargados,
        "kpis": {
            "extra": total_extra, "e50": e50, "e100": e100, "aprobada": aprobada,
            "pendiente": total_extra - aprobada,
            "personas_extra": len([r for r in ranking if r["total"] > 0]),
            "personas": len(ranking),
            "dias_extra": len([d for d in dias if d["total"] > 0]),
            "trabajado": sum(j.minutos_trabajados or 0 for j in jornadas),
            "defecto": sum(j.minutos_defecto or 0 for j in jornadas),
            "max_dia": max([d["total"] for d in dias], default=0),
            # Portería y cualquier otro régimen rotativo: las horas se compensan
            # con francos, no generan extra. Se muestran igual — esconderlas
            # sin dejar rastro haría que los totales no cierren contra el papel.
            "rotativo": sum(j.minutos_presencia or 0 for j in jornadas
                            if not j.computa_extra_snap),
            "rotativo_personas": len({j.persona_id for j in jornadas
                                      if not j.computa_extra_snap}),
        },
        "mt": min_a_texto,
        "estado_extra_css": ESTADO_EXTRA_CSS,
    })


@router.get("/mes/export")
async def mes_export(db: Session = Depends(get_db), current_user=Depends(_reportes),
                     mes: str = "", planta: str = "", grupo: str = ""):
    """Excel del mes, una fila por persona y día. Respeta los filtros de pantalla."""
    import io as _io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    anio, m, desde, hasta = _rango_mes(mes)
    jornadas = _datos_mes(db, desde, hasta, planta, grupo)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{anio}-{m:02d}"
    cols = ["Fecha", "Día", "Persona", "Planta", "Grupo", "Ingreso", "Egreso",
            "Debe (h)", "Trabajado (h)", "Extra 50% (h)", "Extra 100% (h)",
            "Extra total (h)", "Aprobada (h)", "No cumplidas (h)",
            "Estado extra", "Registro", "Motivo", "Nota de la planilla"]
    ws.append(cols)
    cab = ws[1]
    for c in cab:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1E3A8A")
        c.alignment = Alignment(horizontal="center")

    dias_sem = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    h = lambda x: round((x or 0) / 60.0, 2)  # noqa: E731
    for j in sorted(jornadas, key=lambda x: (x.fecha, x.persona.apellido if x.persona else "")):
        ws.append([
            j.fecha, dias_sem[j.fecha.weekday()],
            f"{j.persona.apellido}, {j.persona.nombre}" if j.persona else "",
            j.planta_snap or "", j.grupo_snap or "",
            j.ingreso_real.strftime("%H:%M") if j.ingreso_real else "",
            j.egreso_real.strftime("%H:%M") if j.egreso_real else "",
            h(j.minutos_esperados), h(j.minutos_trabajados),
            h(j.minutos_extra_50), h(j.minutos_extra_100),
            h(j.minutos_extra_detectada),
            h(j.minutos_extra_aprobada) if j.minutos_extra_aprobada is not None else "",
            h(j.minutos_defecto),
            j.estado_extra or "", j.estado_registro or "",
            j.motivo.nombre if j.motivo else "", j.nota_origen or "",
        ])

    anchos = [11, 11, 26, 8, 16, 9, 9, 9, 12, 12, 13, 13, 12, 14, 13, 13, 20, 34]
    for i, w in enumerate(anchos, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    sufijo = ("_" + planta if planta else "") + ("_" + grupo.replace(" ", "") if grupo else "")
    nombre = f"asistencia_{anio}-{m:02d}{sufijo}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
