"""
Módulo Operativos en Tiempo Real — MTR Gestión

Rutas implementadas:
  GET  /operations/live                          → lista de sesiones
  GET  /operations/live/new                      → formulario nueva sesión
  POST /operations/live/new                      → crear sesión
  GET  /operations/live/{sid}                    → detalle de sesión (dashboard live)
  GET  /operations/live/{sid}/edit               → editar sesión
  POST /operations/live/{sid}/edit               → actualizar sesión
  POST /operations/live/{sid}/finish             → finalizar sesión

  GET  /operations/live/{sid}/shift/new          → formulario nuevo turno
  POST /operations/live/{sid}/shift/new          → crear turno
  GET  /operations/live/{sid}/shift/{shid}       → detalle turno (solo lectura)
  GET  /operations/live/{sid}/shift/{shid}/edit  → editar turno
  POST /operations/live/{sid}/shift/{shid}/edit  → guardar turno (borrador o cerrar)

Decisiones de diseño explícitas:
  - Acumulados incluyen TODOS los turnos (abiertos y cerrados) — dashboard live.
  - Validación server-side: el product en bodega_data debe existir en
    session_products de esa sesión → 400 si no existe.
  - normalize_product() se aplica al guardar producto en session_products
    y en bodega_data.
  - El POST de turno maneja tanto "Guardar borrador" (save_action=draft)
    como "Cerrar turno" (save_action=close). La diferencia: close requiere
    shift_end y cambia status a 'closed'.
  - Al guardar bodega_data: delete-and-recreate para el turno completo.
    Idempotente y simple.
"""

from datetime import datetime, date as _date
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional

from app.database import get_db
from app.deps import require_role
from app.templates import templates
from app import models
from app.models_live import (
    OperationLiveSession,
    OperationLiveSessionProduct,
    OperationLiveShift,
    OperationLiveBodegaData,
    OperationLiveDelay,
    OperationLiveEquipment,
    OperationLiveStaff,
)
from sqlalchemy import asc as _asc
from app.product_normalize import normalize_product
from app.live_utils import (
    session_totals_by_product,
    session_grand_total,
    shift_summary_by_product,
    shift_totals,
    delay_total_minutes,
    delay_minutes_by_type,
    equipment_total_hours,
    equipment_hours_by_empresa,
    staff_summary,
    MOTIVO_LABELS,
    FUNCION_LABELS,
    EQUIPO_TIPOS,
    TURNO_RANGES,
    MOTIVO_TIPOS,
    fmt_kg,
    delta_badge,
    format_minutes,
)

router = APIRouter(prefix="/operations/live")

# Roles con acceso al módulo live
_LIVE_ROLES = ("admin", "superadmin", "planta")


# ── Helpers internos ──────────────────────────────────────────────────────────

def _get_session_or_404(sid: int, db: Session) -> OperationLiveSession:
    """Carga una sesión live o lanza 404."""
    session = db.query(OperationLiveSession).filter_by(id=sid).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sesión live no encontrada")
    return session


def _validate_product_in_session(
    product_normalized: str,
    session: OperationLiveSession,
) -> None:
    """
    Valida que el producto normalizado exista en session_products de esa sesión.
    Lanza 400 con mensaje claro si no existe.

    Se usa en los POST de bodega_data para garantizar consistencia.
    """
    known_products = {sp.product for sp in session.products}
    if product_normalized not in known_products:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Producto '{product_normalized}' no pertenece a esta sesión. "
                f"Productos válidos: {', '.join(sorted(known_products)) or '(ninguno)'}"
            ),
        )


def _parse_date(value: str) -> _date | None:
    """'YYYY-MM-DD' → date. None si falla."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _parse_int(value: str | None) -> int | None:
    """String → int. None si vacío o no parseable."""
    if not value or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _build_session_context(session: OperationLiveSession, db: Session) -> dict:
    """
    Construye el contexto completo del dashboard de una sesión.
    Incluye todos los turnos (abiertos y cerrados) en los acumulados.
    Carga bodega, demoras, equipos y personal para todos los turnos en
    bulk (una query por tipo) para evitar N+1.

    Retorna un dict listo para pasar al template.
    """
    all_shift_ids = [s.id for s in session.shifts]

    if all_shift_ids:
        all_bodega_rows = (
            db.query(OperationLiveBodegaData)
            .filter(OperationLiveBodegaData.shift_id.in_(all_shift_ids))
            .all()
        )
        all_delays = (
            db.query(OperationLiveDelay)
            .filter(OperationLiveDelay.shift_id.in_(all_shift_ids))
            .all()
        )
        all_equipment = (
            db.query(OperationLiveEquipment)
            .filter(OperationLiveEquipment.shift_id.in_(all_shift_ids))
            .all()
        )
        all_staff = (
            db.query(OperationLiveStaff)
            .filter(OperationLiveStaff.shift_id.in_(all_shift_ids))
            .all()
        )
    else:
        all_bodega_rows = all_delays = all_equipment = all_staff = []

    # Acumulados por producto y total general (incluye turnos abiertos)
    product_summaries = session_totals_by_product(all_bodega_rows, session.products)
    grand_total = session_grand_total(product_summaries)

    # Sesión: demoras agregadas
    session_delay_by_type = delay_minutes_by_type(all_delays)
    session_delay_total   = sum(session_delay_by_type.values())

    # Sesión: equipos agregados
    session_equip_by_empresa = equipment_hours_by_empresa(all_equipment)
    session_equip_total      = round(sum(session_equip_by_empresa.values()), 2)

    # Turno activo (open)
    active_shift = next(
        (s for s in session.shifts if s.status == "open"), None
    )

    # Historial de turnos con resumen completo por turno
    shift_history = []
    for shift in session.shifts:
        s_bodega = [r for r in all_bodega_rows if r.shift_id == shift.id]
        s_delays = [d for d in all_delays      if d.shift_id == shift.id]
        s_equip  = [e for e in all_equipment   if e.shift_id == shift.id]
        s_staff  = [p for p in all_staff       if p.shift_id == shift.id]

        by_product      = shift_summary_by_product(s_bodega)
        totals          = shift_totals(s_bodega)
        delay_min       = delay_total_minutes(s_delays)
        delay_by_type_s = delay_minutes_by_type(s_delays)
        equip_hrs       = equipment_total_hours(s_equip)
        equip_by_emp    = equipment_hours_by_empresa(s_equip)
        staff           = staff_summary(s_staff)

        shift_history.append({
            "shift":            shift,
            "by_product":       by_product,
            "totals":           totals,
            "delay_min":        delay_min,
            "delay_fmt":        format_minutes(delay_min),
            "delay_by_type":    delay_by_type_s,
            "equip_hrs":        equip_hrs,
            "equip_by_empresa": equip_by_emp,
            "staff":            staff,
            "delta_badge":      delta_badge(totals.get("delta")),
        })

    return {
        "session":                  session,
        "product_summaries":        product_summaries,
        "grand_total":              grand_total,
        "active_shift":             active_shift,
        "shift_history":            shift_history,
        "session_delay_total":      session_delay_total,
        "session_delay_fmt":        format_minutes(session_delay_total),
        "session_delay_by_type":    session_delay_by_type,
        "session_equip_total":      session_equip_total,
        "session_equip_by_empresa": session_equip_by_empresa,
        "MOTIVO_LABELS":            MOTIVO_LABELS,
        "FUNCION_LABELS":           FUNCION_LABELS,
        # fmt_kg, delta_badge, format_minutes son Jinja2 globals (ver templates.py)
    }


# ── Vista 1: Lista de sesiones ────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_live_sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    sessions_active = (
        db.query(OperationLiveSession)
        .filter(OperationLiveSession.status.in_(["active", "paused"]))
        .order_by(desc(OperationLiveSession.created_at))
        .all()
    )
    sessions_finished = (
        db.query(OperationLiveSession)
        .filter_by(status="finished")
        .order_by(desc(OperationLiveSession.finished_at))
        .limit(30)
        .all()
    )

    # Para la lista necesitamos un resumen mínimo por sesión
    def _session_list_summary(session: OperationLiveSession) -> dict:
        all_shift_ids = [s.id for s in session.shifts]
        all_bodega_rows = (
            db.query(OperationLiveBodegaData)
            .filter(OperationLiveBodegaData.shift_id.in_(all_shift_ids))
            .all()
            if all_shift_ids else []
        )
        product_summaries = session_totals_by_product(
            all_bodega_rows, session.products
        )
        grand = session_grand_total(product_summaries)
        active_shift = next(
            (s for s in session.shifts if s.status == "open"), None
        )
        return {
            "product_summaries": product_summaries,
            "grand_total":       grand,
            "active_shift":      active_shift,
            "shift_count":       len(session.shifts),
        }

    active_summaries   = {s.id: _session_list_summary(s) for s in sessions_active}
    finished_summaries = {s.id: _session_list_summary(s) for s in sessions_finished}

    return templates.TemplateResponse(
        request,
        "operations/live/list.html",
        {
            "current_user":       current_user,
            "sessions_active":    sessions_active,
            "sessions_finished":  sessions_finished,
            "active_summaries":   active_summaries,
            "finished_summaries": finished_summaries,
        },
    )


# ── Vista 2: Crear sesión ─────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_session_form(
    request: Request,
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    return templates.TemplateResponse(
        request,
        "operations/live/new_session.html",
        {
            "current_user": current_user,
        },
    )


@router.post("/new")
async def create_session(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    """
    Crea una sesión live con uno o más productos.

    Espera form data con campos indexados:
      ship_name
      products[0][product], products[0][client], products[0][kg_contracted]
      products[1][product], products[1][client], products[1][kg_contracted]
      ...

    Alternativa: campos planos repetidos para soporte máximo en formularios simples:
      product_0, client_0, kg_contracted_0
      product_1, client_1, kg_contracted_1
    """
    form = await request.form()
    ship_name = str(form.get("ship_name", "")).strip()
    if not ship_name:
        raise HTTPException(status_code=400, detail="Nombre del barco requerido")

    created_by = getattr(current_user, "name", None) or getattr(
        current_user, "email", None
    )

    # Construir lista de productos desde form dinámico (campos product_N, client_N, etc.)
    products_data = []
    i = 0
    while True:
        raw_product = str(form.get(f"product_{i}", "")).strip()
        if not raw_product:
            break
        raw_client        = str(form.get(f"client_{i}", "")).strip() or None
        raw_kg_contracted = str(form.get(f"kg_contracted_{i}", "")).strip()
        kg_contracted     = _parse_int(raw_kg_contracted.replace(".", "").replace(",", ""))

        product_norm = normalize_product(raw_product)
        if product_norm:
            products_data.append({
                "product":       product_norm,
                "client":        raw_client,
                "kg_contracted": kg_contracted,
            })
        i += 1

    if not products_data:
        raise HTTPException(
            status_code=400,
            detail="Debe agregar al menos un producto a la sesión"
        )

    # Crear sesión
    session = OperationLiveSession(
        ship_name  = ship_name,
        status     = "active",
        created_by = created_by,
    )
    db.add(session)
    db.flush()  # obtener session.id antes de crear products

    for pd in products_data:
        db.add(OperationLiveSessionProduct(
            session_id    = session.id,
            product       = pd["product"],
            client        = pd["client"],
            kg_contracted = pd["kg_contracted"],
        ))

    db.commit()
    return RedirectResponse(url=f"/operations/live/{session.id}", status_code=303)


# ── Vista 3: Detalle de sesión (dashboard live) ───────────────────────────────

@router.get("/{sid}", response_class=HTMLResponse)
async def session_detail(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)
    ctx = _build_session_context(session, db)

    return templates.TemplateResponse(
        request,
        "operations/live/session_detail.html",
        {
            "current_user": current_user,
            **ctx,
        },
    )


# ── Vista 4: Editar sesión ────────────────────────────────────────────────────

@router.get("/{sid}/edit", response_class=HTMLResponse)
async def edit_session_form(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)
    return templates.TemplateResponse(
        request,
        "operations/live/edit_session.html",
        {
            "current_user": current_user,
            "session":      session,
        },
    )


@router.post("/{sid}/edit")
async def update_session(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)
    form = await request.form()

    new_ship = str(form.get("ship_name", "")).strip()
    if new_ship:
        session.ship_name = new_ship

    new_status = str(form.get("status", "")).strip()
    if new_status in ("active", "paused", "finished"):
        session.status = new_status

    # Actualizar productos: delete-and-recreate para simplicidad
    new_products = []
    i = 0
    while True:
        raw_product = str(form.get(f"product_{i}", "")).strip()
        if not raw_product:
            break
        raw_client = str(form.get(f"client_{i}", "")).strip() or None
        raw_kg     = str(form.get(f"kg_contracted_{i}", "")).strip()
        kg         = _parse_int(raw_kg.replace(".", "").replace(",", ""))
        norm       = normalize_product(raw_product)
        if norm:
            new_products.append({"product": norm, "client": raw_client, "kg_contracted": kg})
        i += 1

    if new_products:
        # Borrar los existentes y recrear
        db.query(OperationLiveSessionProduct).filter_by(session_id=sid).delete()
        for pd in new_products:
            db.add(OperationLiveSessionProduct(
                session_id    = sid,
                product       = pd["product"],
                client        = pd["client"],
                kg_contracted = pd["kg_contracted"],
            ))

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)


# ── Marcar sesión como finalizada ─────────────────────────────────────────────

@router.post("/{sid}/finish")
async def finish_session(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)
    form = await request.form()

    session.status        = "finished"
    session.finished_at   = datetime.utcnow()
    session.closing_notes = str(form.get("closing_notes", "")).strip() or None
    session.closed_by     = (
        getattr(current_user, "name", None)
        or getattr(current_user, "email", None)
    )

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)


# ── Parsers de demoras / equipos / personal ───────────────────────────────────

def _parse_delay_rows(form) -> list[dict]:
    """
    Lee filas de demoras con campos indexados:
      delay_{i}_desde, delay_{i}_hasta, delay_{i}_motivo_tipo,
      delay_{i}_motivo_texto, delay_{i}_bodega, delay_{i}_product

    Omite filas sin 'desde'.
    """
    rows = []
    i = 0
    while i <= 30:
        raw_desde = str(form.get(f"delay_{i}_desde", "")).strip()
        if not raw_desde:
            if i > 20:
                break
            i += 1
            continue
        raw_product = str(form.get(f"delay_{i}_product", "")).strip()
        rows.append({
            "bodega_number": _parse_int(str(form.get(f"delay_{i}_bodega", "")).strip()),
            "product":       normalize_product(raw_product) if raw_product else None,
            "desde":         raw_desde,
            "hasta":         str(form.get(f"delay_{i}_hasta", "")).strip() or None,
            "motivo_tipo":   str(form.get(f"delay_{i}_motivo_tipo", "")).strip() or None,
            "motivo_texto":  str(form.get(f"delay_{i}_motivo_texto", "")).strip() or None,
        })
        i += 1
    return rows


def _parse_equipment_rows(form) -> list[dict]:
    """
    Lee filas de equipos con campos indexados:
      equip_{i}_empresa, equip_{i}_tipo, equip_{i}_bodega,
      equip_{i}_desde, equip_{i}_hasta, equip_{i}_comentarios

    Omite filas sin empresa Y sin desde.
    """
    rows = []
    i = 0
    while i <= 30:
        raw_empresa = str(form.get(f"equip_{i}_empresa", "")).strip()
        raw_desde   = str(form.get(f"equip_{i}_desde", "")).strip()
        if not raw_empresa and not raw_desde:
            if i > 20:
                break
            i += 1
            continue
        rows.append({
            "empresa":       raw_empresa or None,
            "tipo":          str(form.get(f"equip_{i}_tipo", "")).strip() or None,
            "bodega_number": _parse_int(str(form.get(f"equip_{i}_bodega", "")).strip()),
            "desde":         raw_desde or None,
            "hasta":         str(form.get(f"equip_{i}_hasta", "")).strip() or None,
            "comentarios":   str(form.get(f"equip_{i}_comentarios", "")).strip() or None,
        })
        i += 1
    return rows


def _parse_staff_rows(form) -> list[dict]:
    """
    Lee filas de personal con campos indexados:
      staff_{i}_funcion, staff_{i}_funcion_texto, staff_{i}_cantidad,
      staff_{i}_turno_range, staff_{i}_empresa

    Omite filas sin funcion.
    """
    rows = []
    i = 0
    while i <= 30:
        raw_funcion = str(form.get(f"staff_{i}_funcion", "")).strip()
        if not raw_funcion:
            if i > 20:
                break
            i += 1
            continue
        rows.append({
            "funcion":       raw_funcion,
            "funcion_texto": str(form.get(f"staff_{i}_funcion_texto", "")).strip() or None,
            "cantidad":      _parse_int(str(form.get(f"staff_{i}_cantidad", "1")).strip()) or 1,
            "turno_range":   str(form.get(f"staff_{i}_turno_range", "")).strip() or None,
            "empresa":       str(form.get(f"staff_{i}_empresa", "coop")).strip() or "coop",
        })
        i += 1
    return rows


def _save_shift_complete(
    shift: OperationLiveShift,
    bodega_rows: list[dict],
    delay_rows: list[dict],
    equip_rows: list[dict],
    staff_rows: list[dict],
    session: OperationLiveSession,
    db: Session,
) -> None:
    """
    Persiste el turno completo: bodegas, demoras, equipos y personal.
    Patrón delete-and-recreate para todos los tipos. Idempotente.
    Valida productos de bodega_rows contra session_products (400 si falla).
    """
    # Validar productos antes de tocar la DB
    for row in bodega_rows:
        if row["product"]:
            _validate_product_in_session(row["product"], session)

    # ── Bodega data ──────────────────────────────────────────────────────────
    db.query(OperationLiveBodegaData).filter_by(shift_id=shift.id).delete()
    for row in bodega_rows:
        if not row["product"]:
            continue
        db.add(OperationLiveBodegaData(
            shift_id        = shift.id,
            bodega_number   = row["bodega_number"],
            product         = row["product"],
            measurement     = row["measurement"],
            viajes_mtr      = row["viajes_mtr"],
            kg_deposito_mtr = row["kg_deposito_mtr"],
            kg_directo_mtr  = row["kg_directo_mtr"],
            kg_cv_mtr       = row["kg_cv_mtr"],
            viajes_coop     = row["viajes_coop"],
            kg_coop         = row["kg_coop"],
        ))

    # ── Demoras ──────────────────────────────────────────────────────────────
    db.query(OperationLiveDelay).filter_by(shift_id=shift.id).delete()
    for row in delay_rows:
        db.add(OperationLiveDelay(
            shift_id      = shift.id,
            bodega_number = row["bodega_number"],
            product       = row["product"],
            desde         = row["desde"],
            hasta         = row["hasta"],
            motivo_tipo   = row["motivo_tipo"],
            motivo_texto  = row["motivo_texto"],
        ))

    # ── Equipos ──────────────────────────────────────────────────────────────
    db.query(OperationLiveEquipment).filter_by(shift_id=shift.id).delete()
    for row in equip_rows:
        db.add(OperationLiveEquipment(
            shift_id      = shift.id,
            empresa       = row["empresa"],
            tipo          = row["tipo"],
            bodega_number = row["bodega_number"],
            desde         = row["desde"],
            hasta         = row["hasta"],
            comentarios   = row["comentarios"],
        ))

    # ── Personal ─────────────────────────────────────────────────────────────
    db.query(OperationLiveStaff).filter_by(shift_id=shift.id).delete()
    for row in staff_rows:
        db.add(OperationLiveStaff(
            shift_id      = shift.id,
            funcion       = row["funcion"],
            funcion_texto = row["funcion_texto"],
            cantidad      = row["cantidad"],
            turno_range   = row["turno_range"],
            empresa       = row["empresa"],
        ))


# ── Helpers de turno ──────────────────────────────────────────────────────────

def _get_shift_or_404(
    sid: int,
    shid: int,
    db: Session,
) -> OperationLiveShift:
    """Carga un turno verificando que pertenezca a la sesión indicada."""
    shift = db.query(OperationLiveShift).filter_by(id=shid, session_id=sid).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Turno no encontrado")
    return shift


def _next_shift_number(session: OperationLiveSession) -> int:
    """Devuelve el próximo Parte Nº para una sesión (max existente + 1)."""
    if not session.shifts:
        return 1
    return max(s.shift_number for s in session.shifts) + 1


def _parse_bodega_rows(form) -> list[dict]:
    """
    Lee filas de bodega del form con campos indexados:
      bodega_{i}_number, bodega_{i}_product, bodega_{i}_measurement,
      bodega_{i}_viajes_mtr, bodega_{i}_kg_deposito_mtr,
      bodega_{i}_kg_directo_mtr, bodega_{i}_kg_cv_mtr,
      bodega_{i}_viajes_coop, bodega_{i}_kg_coop

    Omite filas donde bodega_number esté vacío.
    """
    rows = []
    i = 0
    while True:
        raw_num = str(form.get(f"bodega_{i}_number", "")).strip()
        if not raw_num and i > 20:  # stop scanning after 20 empty consecutive rows
            break
        if not raw_num:
            i += 1
            continue

        bodega_number = _parse_int(raw_num)
        if bodega_number is None:
            i += 1
            continue

        raw_product = str(form.get(f"bodega_{i}_product", "")).strip()
        product_norm = normalize_product(raw_product) if raw_product else None

        rows.append({
            "bodega_number":    bodega_number,
            "product":          product_norm,
            "measurement":      str(form.get(f"bodega_{i}_measurement", "fiscal")).strip() or "fiscal",
            "viajes_mtr":       _parse_int(str(form.get(f"bodega_{i}_viajes_mtr", "")).strip()),
            "kg_deposito_mtr":  _parse_int(str(form.get(f"bodega_{i}_kg_deposito_mtr", "0")).strip()) or 0,
            "kg_directo_mtr":   _parse_int(str(form.get(f"bodega_{i}_kg_directo_mtr", "0")).strip()) or 0,
            "kg_cv_mtr":        _parse_int(str(form.get(f"bodega_{i}_kg_cv_mtr", "0")).strip()) or 0,
            "viajes_coop":      _parse_int(str(form.get(f"bodega_{i}_viajes_coop", "")).strip()),
            "kg_coop":          _parse_int(str(form.get(f"bodega_{i}_kg_coop", "")).strip()),
        })
        i += 1

    return rows


# _save_shift_with_bodegas reemplazado por _save_shift_complete (ver arriba)


# ── Vista 6: Nuevo turno ──────────────────────────────────────────────────────

@router.get("/{sid}/shift/new", response_class=HTMLResponse)
async def new_shift_form(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)

    # Verificar que no haya ya un turno abierto
    open_shift = next((s for s in session.shifts if s.status == "open"), None)
    if open_shift:
        raise HTTPException(
            status_code=400,
            detail=f"Ya hay un turno abierto (Parte #{open_shift.shift_number}). "
                   "Cerralo antes de abrir otro."
        )

    next_num  = _next_shift_number(session)
    today_str = _date.today().isoformat()

    return templates.TemplateResponse(
        request,
        "operations/live/shift_form.html",
        {
            "current_user":   current_user,
            "session":        session,
            "shift":          None,
            "bodega_rows":    [],
            "delay_rows":     [],
            "equip_rows":     [],
            "staff_rows":     [],
            "next_num":       next_num,
            "today_str":      today_str,
            "TURNO_RANGES":   TURNO_RANGES,
            "MOTIVO_LABELS":  MOTIVO_LABELS,
            "FUNCION_LABELS": FUNCION_LABELS,
            "EQUIPO_TIPOS":   EQUIPO_TIPOS,
            "is_new":         True,
        },
    )


@router.post("/{sid}/shift/new")
async def create_shift(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)

    # Doble-check: no turno abierto
    open_shift = next((s for s in session.shifts if s.status == "open"), None)
    if open_shift:
        raise HTTPException(
            status_code=400,
            detail=f"Ya hay un turno abierto (Parte #{open_shift.shift_number})."
        )

    form         = await request.form()
    save_action  = str(form.get("save_action", "draft")).strip()  # draft | close

    shift_number = _parse_int(str(form.get("shift_number", "")).strip()) or _next_shift_number(session)
    raw_date     = str(form.get("shift_date", "")).strip()
    shift_date   = _parse_date(raw_date) or _date.today()
    shift_start  = str(form.get("shift_start", "")).strip() or None
    shift_end    = str(form.get("shift_end", "")).strip() or None

    if save_action == "close" and not shift_end:
        raise HTTPException(status_code=400, detail="Para cerrar el turno se requiere la hora de fin.")

    shift = OperationLiveShift(
        session_id      = sid,
        shift_number    = shift_number,
        shift_date      = shift_date,
        shift_start     = shift_start or "",
        shift_end       = shift_end if save_action == "close" else None,
        supervisor_mtr  = str(form.get("supervisor_mtr", "")).strip() or None,
        apuntador       = str(form.get("apuntador", "")).strip() or None,
        manos           = _parse_int(str(form.get("manos", "")).strip()),
        status          = "closed" if save_action == "close" else "open",
        notes           = str(form.get("notes", "")).strip() or None,
    )
    db.add(shift)
    db.flush()  # get shift.id

    bodega_rows = _parse_bodega_rows(form)
    delay_rows  = _parse_delay_rows(form)
    equip_rows  = _parse_equipment_rows(form)
    staff_rows  = _parse_staff_rows(form)
    _save_shift_complete(shift, bodega_rows, delay_rows, equip_rows, staff_rows, session, db)

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)


# ── Vista 7: Detalle de turno (solo lectura) ──────────────────────────────────

@router.get("/{sid}/shift/{shid}", response_class=HTMLResponse)
async def shift_detail(
    request: Request,
    sid: int,
    shid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)
    shift   = _get_shift_or_404(sid, shid, db)

    bodega_rows = (
        db.query(OperationLiveBodegaData)
        .filter_by(shift_id=shid)
        .order_by(OperationLiveBodegaData.bodega_number)
        .all()
    )
    delays = (
        db.query(OperationLiveDelay)
        .filter_by(shift_id=shid)
        .order_by(OperationLiveDelay.desde)
        .all()
    )
    equipment = (
        db.query(OperationLiveEquipment)
        .filter_by(shift_id=shid)
        .order_by(OperationLiveEquipment.desde)
        .all()
    )
    staff_rows = (
        db.query(OperationLiveStaff)
        .filter_by(shift_id=shid)
        .all()
    )

    by_product       = shift_summary_by_product(bodega_rows)
    totals           = shift_totals(bodega_rows)
    delay_min        = delay_total_minutes(delays)
    delay_by_type    = delay_minutes_by_type(delays)
    equip_hrs        = equipment_total_hours(equipment)
    equip_by_empresa = equipment_hours_by_empresa(equipment)
    staff            = staff_summary(staff_rows)

    return templates.TemplateResponse(
        request,
        "operations/live/shift_detail.html",
        {
            "current_user":    current_user,
            "session":         session,
            "shift":           shift,
            "bodega_rows":     bodega_rows,
            "by_product":      by_product,
            "totals":          totals,
            "delays":          delays,
            "delay_min":       delay_min,
            "delay_fmt":       format_minutes(delay_min),
            "delay_by_type":   delay_by_type,
            "equipment":       equipment,
            "equip_hrs":       equip_hrs,
            "equip_by_empresa": equip_by_empresa,
            "staff":           staff,
            "delta_b":         delta_badge(totals.get("delta")),
            "MOTIVO_LABELS":   MOTIVO_LABELS,
            "FUNCION_LABELS":  FUNCION_LABELS,
            "EQUIPO_TIPOS":    EQUIPO_TIPOS,
        },
    )


# ── Vista 8: Editar turno ─────────────────────────────────────────────────────

@router.get("/{sid}/shift/{shid}/edit", response_class=HTMLResponse)
async def edit_shift_form(
    request: Request,
    sid: int,
    shid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)
    shift   = _get_shift_or_404(sid, shid, db)

    bodega_rows = (
        db.query(OperationLiveBodegaData)
        .filter_by(shift_id=shid)
        .order_by(OperationLiveBodegaData.bodega_number)
        .all()
    )
    delay_rows_edit = (
        db.query(OperationLiveDelay)
        .filter_by(shift_id=shid)
        .order_by(OperationLiveDelay.desde)
        .all()
    )
    equip_rows_edit = (
        db.query(OperationLiveEquipment)
        .filter_by(shift_id=shid)
        .order_by(OperationLiveEquipment.desde)
        .all()
    )
    staff_rows_edit = (
        db.query(OperationLiveStaff)
        .filter_by(shift_id=shid)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "operations/live/shift_form.html",
        {
            "current_user":    current_user,
            "session":         session,
            "shift":           shift,
            "bodega_rows":     bodega_rows,
            "delay_rows":      delay_rows_edit,
            "equip_rows":      equip_rows_edit,
            "staff_rows":      staff_rows_edit,
            "TURNO_RANGES":    TURNO_RANGES,
            "MOTIVO_LABELS":   MOTIVO_LABELS,
            "FUNCION_LABELS":  FUNCION_LABELS,
            "EQUIPO_TIPOS":    EQUIPO_TIPOS,
            "is_new":          False,
        },
    )


@router.post("/{sid}/shift/{shid}/edit")
async def update_shift(
    request: Request,
    sid: int,
    shid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_LIVE_ROLES)),
):
    session = _get_session_or_404(sid, db)
    shift   = _get_shift_or_404(sid, shid, db)
    form    = await request.form()

    save_action = str(form.get("save_action", "draft")).strip()

    raw_date   = str(form.get("shift_date", "")).strip()
    shift_date = _parse_date(raw_date)
    if shift_date:
        shift.shift_date = shift_date

    raw_start = str(form.get("shift_start", "")).strip()
    if raw_start:
        shift.shift_start = raw_start

    raw_end = str(form.get("shift_end", "")).strip()
    if save_action == "close":
        if not raw_end:
            raise HTTPException(status_code=400, detail="Para cerrar el turno se requiere la hora de fin.")
        shift.shift_end = raw_end
        shift.status    = "closed"
    elif raw_end:
        shift.shift_end = raw_end

    raw_num = str(form.get("shift_number", "")).strip()
    if raw_num:
        n = _parse_int(raw_num)
        if n:
            shift.shift_number = n

    supervisor = str(form.get("supervisor_mtr", "")).strip()
    if supervisor:
        shift.supervisor_mtr = supervisor

    apuntador = str(form.get("apuntador", "")).strip()
    if apuntador:
        shift.apuntador = apuntador

    manos_raw = str(form.get("manos", "")).strip()
    if manos_raw:
        shift.manos = _parse_int(manos_raw)

    notes_raw = str(form.get("notes", "")).strip()
    shift.notes = notes_raw or None

    bodega_rows = _parse_bodega_rows(form)
    delay_rows  = _parse_delay_rows(form)
    equip_rows  = _parse_equipment_rows(form)
    staff_rows  = _parse_staff_rows(form)
    _save_shift_complete(shift, bodega_rows, delay_rows, equip_rows, staff_rows, session, db)

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)
