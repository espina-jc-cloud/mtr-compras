from __future__ import annotations
"""
Módulo Operativos en Tiempo Real — MTR Gestión

Rutas implementadas:
  GET  /operations/live                          → lista de sesiones
  GET  /operations/live/new                      → formulario nueva sesión
  POST /operations/live/new                      → crear sesión
  GET  /operations/live/{sid}                    → detalle de sesión (dashboard live)
  GET  /operations/live/{sid}/edit               → editar sesión
  POST /operations/live/{sid}/edit               → actualizar sesión
  POST /operations/live/{sid}/finish             → finalizar sesión (legacy)
  GET  /operations/live/{sid}/close              → confirmación cierre formal (Fase 2)
  POST /operations/live/{sid}/close              → ejecutar cierre formal (Fase 2)

  GET  /operations/live/{sid}/invoice/new        → formulario nueva factura cooperativa
  POST /operations/live/{sid}/invoice/new        → crear factura cooperativa
  GET  /operations/live/{sid}/invoice/{iid}      → detalle factura (solo lectura)
  GET  /operations/live/{sid}/invoice/{iid}/edit → editar factura
  POST /operations/live/{sid}/invoice/{iid}/edit → guardar factura

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
import uuid
from fastapi import APIRouter, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional

from app.database import get_db
from app.deps import require_role
from app.permissions import require_perm
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
    # Fase 2
    OperationLiveInvoice,
    OperationLiveInvoiceTonnageLine,
    OperationLiveInvoiceLaborLine,
    OperationLiveInvoiceCargoLine,
    OperationLiveInvoiceTotals,
    # Fase 3
    OperationLivePhoto,
)
from app.invoice_utils import calculate_invoice_totals
from app.cloudinary_upload import upload_file as _cloudinary_upload, delete_file as _cloudinary_delete
from decimal import Decimal as _Decimal
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
    all_shifts_closed,
    open_shifts_count,
    tns_by_product_from_session,
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


def _parse_decimal(value: str | None) -> _Decimal | None:
    """String → Decimal. None si vacío o no parseable. Reemplaza coma por punto."""
    if not value or not str(value).strip():
        return None
    try:
        return _Decimal(str(value).strip().replace(",", "."))
    except Exception:
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

    # Turnos en borrador (status="open") — puede haber más de uno (multi-borrador)
    open_shifts = [s for s in session.shifts if s.status == "open"]
    # active_shift: primer borrador (retrocompatibilidad con cualquier template que lo use)
    active_shift = open_shifts[0] if open_shifts else None

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

    # Fase 2: factura activa (draft o reviewed) para Bloque F
    active_invoice = (
        db.query(OperationLiveInvoice)
        .filter(
            OperationLiveInvoice.session_id == session.id,
            OperationLiveInvoice.status.in_(["draft", "reviewed", "approved_for_payment"]),
        )
        .order_by(OperationLiveInvoice.id.desc())
        .first()
    )

    # Fase 3: Fotos del operativo
    all_photos = (
        db.query(OperationLivePhoto)
        .filter(OperationLivePhoto.session_id == session.id)
        .order_by(OperationLivePhoto.created_at.desc())
        .all()
    )
    session_photos = [p for p in all_photos if p.shift_id is None]  # fotos del operativo general

    # Fotos por shift (dict: shift_id → lista)
    shift_photos_map: dict[int, list] = {}
    for p in all_photos:
        if p.shift_id is not None:
            shift_photos_map.setdefault(p.shift_id, []).append(p)

    # Inyectar fotos en cada entrada de shift_history
    for sh in shift_history:
        sh["photos"] = shift_photos_map.get(sh["shift"].id, [])

    return {
        "session":                  session,
        "product_summaries":        product_summaries,
        "grand_total":              grand_total,
        "active_shift":             active_shift,    # retrocompat: primer borrador o None
        "open_shifts":              open_shifts,     # lista completa de borradores
        "shift_history":            shift_history,
        "session_delay_total":      session_delay_total,
        "session_delay_fmt":        format_minutes(session_delay_total),
        "session_delay_by_type":    session_delay_by_type,
        "session_equip_total":      session_equip_total,
        "session_equip_by_empresa": session_equip_by_empresa,
        "MOTIVO_LABELS":            MOTIVO_LABELS,
        "FUNCION_LABELS":           FUNCION_LABELS,
        # Fase 2: estado de cierre + factura para Bloque F
        "all_shifts_closed_flag":   all_shifts_closed(session),
        "open_shifts_count":        open_shifts_count(session),
        "active_invoice":           active_invoice,
        # Fase 3: Fotos
        "session_photos":           session_photos,
        "all_photos_count":         len(all_photos),
        # fmt_kg, delta_badge, format_minutes son Jinja2 globals (ver templates.py)
    }


# ── Vista 1: Lista de sesiones ────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_live_sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
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
        open_shifts_list = [s for s in session.shifts if s.status == "open"]
        return {
            "product_summaries":    product_summaries,
            "grand_total":          grand,
            "active_shift":         open_shifts_list[0] if open_shifts_list else None,
            "open_shifts_count":    len(open_shifts_list),
            "shift_count":          len(session.shifts),
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
    current_user=Depends(require_perm("operaciones.live")),
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
    current_user=Depends(require_perm("operaciones.live")),
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
    current_user=Depends(require_perm("operaciones.live")),
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
    current_user=Depends(require_perm("operaciones.live")),
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
    current_user=Depends(require_perm("operaciones.live")),
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
    current_user=Depends(require_perm("operaciones.live")),
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


# ── Vista: Cierre formal del operativo (Fase 2) ───────────────────────────────

@router.get("/{sid}/close", response_class=HTMLResponse)
async def close_session_form(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Pantalla de confirmación antes de cerrar formalmente el operativo.

    Muestra el resumen de turnos, toneladas por producto y validaciones.
    Solo disponible para sesiones en status='active'.
    Si los turnos no están todos cerrados, muestra el warning pero no bloquea
    la visualización — el POST sí bloquea.
    """
    session = _get_session_or_404(sid, db)

    if session.status != "active":
        return RedirectResponse(
            url=f"/operations/live/{sid}", status_code=303
        )

    all_shift_ids = [s.id for s in session.shifts]
    all_bodega_rows = (
        db.query(OperationLiveBodegaData)
        .filter(OperationLiveBodegaData.shift_id.in_(all_shift_ids))
        .all()
        if all_shift_ids else []
    )

    product_summaries = session_totals_by_product(all_bodega_rows, session.products)
    grand_total       = session_grand_total(product_summaries)
    can_close         = all_shifts_closed(session)
    open_count        = open_shifts_count(session)

    return templates.TemplateResponse(
        request,
        "operations/live/session_close.html",
        {
            "current_user":      current_user,
            "session":           session,
            "product_summaries": product_summaries,
            "grand_total":       grand_total,
            "can_close":         can_close,
            "open_count":        open_count,
        },
    )


@router.post("/{sid}/close")
async def close_session(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Ejecuta el cierre formal del operativo.

    Precondiciones (ambas validadas):
      1. session.status == 'active'
      2. Todos los turnos tienen status == 'closed'

    Resultado: session.status = 'closed', session.closed_at = now.
    El cierre habilita la carga de la factura cooperativa (Paso 4).
    """
    session = _get_session_or_404(sid, db)

    if session.status != "active":
        raise HTTPException(
            status_code=400,
            detail="Solo se puede cerrar un operativo en estado 'activo'.",
        )

    if not all_shifts_closed(session):
        n = open_shifts_count(session)
        raise HTTPException(
            status_code=400,
            detail=(
                f"No se puede cerrar el operativo: "
                f"{n} turno(s) aún no están cerrados. "
                f"Cerrá todos los turnos antes de continuar."
            ),
        )

    form = await request.form()
    session.status        = "closed"
    session.closed_at     = datetime.utcnow()
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
            tipo_guinche    = row["tipo_guinche"],
            tipo_grampa     = row["tipo_grampa"],
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

        raw_guinche = str(form.get(f"bodega_{i}_tipo_guinche", "")).strip()
        raw_grampa  = str(form.get(f"bodega_{i}_tipo_grampa",  "")).strip()
        rows.append({
            "bodega_number":    bodega_number,
            "product":          product_norm,
            "measurement":      str(form.get(f"bodega_{i}_measurement", "fiscal")).strip() or "fiscal",
            "tipo_guinche":     raw_guinche if raw_guinche in ("fiscal", "abordo") else None,
            "tipo_grampa":      raw_grampa  if raw_grampa  in ("fiscal", "abordo") else None,
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


# ── Helper: acumulado de sesión para el form de turno ────────────────────────

def _build_shift_form_cumulative(
    session: OperationLiveSession,
    exclude_shift_id: int | None,
    db: Session,
) -> dict | None:
    """
    Calcula el acumulado de la sesión excluyendo el turno indicado.
    Usado para mostrar 'Acumulado anterior' en el formulario de turno.
    Devuelve None si no hay datos relevantes.
    """
    include_ids = [
        s.id for s in session.shifts
        if s.id != exclude_shift_id
    ]
    if not include_ids:
        return None

    rows = (
        db.query(OperationLiveBodegaData)
        .filter(OperationLiveBodegaData.shift_id.in_(include_ids))
        .all()
    )
    product_summaries = session_totals_by_product(rows, session.products)
    grand = session_grand_total(product_summaries)

    # No mostrar el bloque si no hay nada cargado
    if grand["kg_total_mtr"] == 0 and grand["viajes_total"] is None:
        return None

    return {
        "grand_total": grand,
        "shift_count": len(include_ids),
        "product_summaries": product_summaries,
    }


# ── Vista 6: Nuevo turno ──────────────────────────────────────────────────────

@router.get("/{sid}/shift/new", response_class=HTMLResponse)
async def new_shift_form(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    session = _get_session_or_404(sid, db)

    # Multi-borrador: se permite crear un nuevo turno aunque haya otros en borrador.
    # La única restricción que se mantiene es que la sesión esté activa/pausada.
    if session.status not in ("active", "paused"):
        raise HTTPException(
            status_code=400,
            detail="No se puede agregar turnos a un operativo que no está activo."
        )

    next_num  = _next_shift_number(session)
    today_str = _date.today().isoformat()

    return templates.TemplateResponse(
        request,
        "operations/live/shift_form.html",
        {
            "current_user":       current_user,
            "session":            session,
            "shift":              None,
            "bodega_rows":        [],
            "delay_rows":         [],
            "equip_rows":         [],
            "staff_rows":         [],
            "next_num":           next_num,
            "today_str":          today_str,
            "TURNO_RANGES":       TURNO_RANGES,
            "MOTIVO_LABELS":      MOTIVO_LABELS,
            "FUNCION_LABELS":     FUNCION_LABELS,
            "EQUIPO_TIPOS":       EQUIPO_TIPOS,
            "is_new":             True,
            "session_cumulative": _build_shift_form_cumulative(session, None, db),
        },
    )


@router.post("/{sid}/shift/new")
async def create_shift(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    session = _get_session_or_404(sid, db)

    # Multi-borrador: no se bloquea por turnos abiertos existentes.
    if session.status not in ("active", "paused"):
        raise HTTPException(
            status_code=400,
            detail="No se puede agregar turnos a un operativo que no está activo."
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

    raw_turno_tipo = str(form.get("turno_tipo", "habil")).strip()
    turno_tipo = raw_turno_tipo if raw_turno_tipo in ("habil", "inhabil", "extraordinario") else "habil"

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
        turno_tipo      = turno_tipo,
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
    current_user=Depends(require_perm("operaciones.live")),
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

    # Fase 3: Fotos del turno
    shift_photos = (
        db.query(OperationLivePhoto)
        .filter_by(shift_id=shid)
        .order_by(OperationLivePhoto.created_at.desc())
        .all()
    )

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
            # Fase 3
            "shift_photos":    shift_photos,
        },
    )


# ── Vista 8: Editar turno ─────────────────────────────────────────────────────

@router.get("/{sid}/shift/{shid}/edit", response_class=HTMLResponse)
async def edit_shift_form(
    request: Request,
    sid: int,
    shid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
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
            "current_user":       current_user,
            "session":            session,
            "shift":              shift,
            "bodega_rows":        bodega_rows,
            "delay_rows":         delay_rows_edit,
            "equip_rows":         equip_rows_edit,
            "staff_rows":         staff_rows_edit,
            "TURNO_RANGES":       TURNO_RANGES,
            "MOTIVO_LABELS":      MOTIVO_LABELS,
            "FUNCION_LABELS":     FUNCION_LABELS,
            "EQUIPO_TIPOS":       EQUIPO_TIPOS,
            "is_new":             False,
            "session_cumulative": _build_shift_form_cumulative(session, shid, db),
        },
    )


@router.post("/{sid}/shift/{shid}/edit")
async def update_shift(
    request: Request,
    sid: int,
    shid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
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

    # turno_tipo: el usuario lo confirma explícitamente — sin derivación automática
    raw_tipo = str(form.get("turno_tipo", "")).strip()
    if raw_tipo in ("habil", "inhabil", "extraordinario"):
        shift.turno_tipo = raw_tipo

    notes_raw = str(form.get("notes", "")).strip()
    shift.notes = notes_raw or None

    bodega_rows = _parse_bodega_rows(form)
    delay_rows  = _parse_delay_rows(form)
    equip_rows  = _parse_equipment_rows(form)
    staff_rows  = _parse_staff_rows(form)
    _save_shift_complete(shift, bodega_rows, delay_rows, equip_rows, staff_rows, session, db)

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# Paso 4 — Factura Cooperativa (Fase 2)
# ══════════════════════════════════════════════════════════════════════════════

# ── Parsers de factura ────────────────────────────────────────────────────────

def _parse_tonnage_rows(form) -> list[dict]:
    """
    Lee líneas de tonelaje del form con campos indexados:
      ton_{i}_shift_date, ton_{i}_turno_range, ton_{i}_guinche_tipo,
      ton_{i}_guinche_descripcion, ton_{i}_product,
      ton_{i}_tns_habiles, ton_{i}_tns_inhabiles, ton_{i}_tns_extraordinarias,
      ton_{i}_manos,
      ton_{i}_tarifa_habiles, ton_{i}_tarifa_inhabiles, ton_{i}_tarifa_extraordinarias

    Sentinel: guinche_tipo vacío (en i > 20 para ahorrar ciclos).
    """
    rows = []
    i = 0
    while i <= 50:
        raw_guinche = str(form.get(f"ton_{i}_guinche_tipo", "")).strip()
        if not raw_guinche:
            if i > 20:
                break
            i += 1
            continue
        raw_product = str(form.get(f"ton_{i}_product", "")).strip()
        rows.append({
            "shift_date":           _parse_date(str(form.get(f"ton_{i}_shift_date", "")).strip()),
            "turno_range":          str(form.get(f"ton_{i}_turno_range", "")).strip() or "N/A",
            "guinche_tipo":         raw_guinche,
            "guinche_descripcion":  str(form.get(f"ton_{i}_guinche_descripcion", "")).strip() or None,
            "product":              normalize_product(raw_product) if raw_product else None,
            "tns_habiles":          _parse_decimal(str(form.get(f"ton_{i}_tns_habiles",         "")).strip()),
            "tns_inhabiles":        _parse_decimal(str(form.get(f"ton_{i}_tns_inhabiles",       "")).strip()),
            "tns_extraordinarias":  _parse_decimal(str(form.get(f"ton_{i}_tns_extraordinarias", "")).strip()),
            "manos":                _parse_int(str(form.get(f"ton_{i}_manos", "")).strip()),
            "tarifa_habiles":       _parse_decimal(str(form.get(f"ton_{i}_tarifa_habiles",         "")).strip()),
            "tarifa_inhabiles":     _parse_decimal(str(form.get(f"ton_{i}_tarifa_inhabiles",       "")).strip()),
            "tarifa_extraordinarias": _parse_decimal(str(form.get(f"ton_{i}_tarifa_extraordinarias", "")).strip()),
        })
        i += 1
    return rows


def _parse_labor_rows(form) -> list[dict]:
    """
    Lee líneas de jornales del form con campos indexados:
      lab_{i}_shift_date, lab_{i}_turno_range, lab_{i}_turno_tipo,
      lab_{i}_funcion, lab_{i}_funcion_texto,
      lab_{i}_cantidad, lab_{i}_precio_unitario

    Sentinel: funcion vacío.
    """
    rows = []
    i = 0
    while i <= 50:
        raw_funcion = str(form.get(f"lab_{i}_funcion", "")).strip()
        if not raw_funcion:
            if i > 20:
                break
            i += 1
            continue
        rows.append({
            "shift_date":    _parse_date(str(form.get(f"lab_{i}_shift_date", "")).strip()),
            "turno_range":   str(form.get(f"lab_{i}_turno_range", "")).strip() or "N/A",
            "turno_tipo":    str(form.get(f"lab_{i}_turno_tipo",  "habil")).strip() or "habil",
            "funcion":       raw_funcion,
            "funcion_texto": str(form.get(f"lab_{i}_funcion_texto", "")).strip() or None,
            "cantidad":      _parse_int(str(form.get(f"lab_{i}_cantidad", "")).strip()),
            "precio_unitario": _parse_decimal(str(form.get(f"lab_{i}_precio_unitario", "")).strip()),
        })
        i += 1
    return rows


def _parse_cargo_rows(form) -> list[dict]:
    """
    Lee ítems especiales del form con campos indexados:
      cargo_{i}_tipo, cargo_{i}_descripcion, cargo_{i}_cantidad,
      cargo_{i}_unidad, cargo_{i}_precio_unitario, cargo_{i}_subtotal

    Sentinel: descripcion vacía.
    """
    rows = []
    i = 0
    while i <= 30:
        raw_desc = str(form.get(f"cargo_{i}_descripcion", "")).strip()
        if not raw_desc:
            if i > 20:
                break
            i += 1
            continue
        cant  = _parse_decimal(str(form.get(f"cargo_{i}_cantidad",        "")).strip())
        price = _parse_decimal(str(form.get(f"cargo_{i}_precio_unitario", "")).strip())
        sub   = _parse_decimal(str(form.get(f"cargo_{i}_subtotal",        "")).strip())
        if sub is None and cant is not None and price is not None:
            sub = cant * price
        rows.append({
            "tipo":            str(form.get(f"cargo_{i}_tipo", "otro")).strip() or "otro",
            "descripcion":     raw_desc,
            "cantidad":        cant,
            "unidad":          str(form.get(f"cargo_{i}_unidad", "")).strip() or None,
            "precio_unitario": price,
            "subtotal":        sub,
        })
        i += 1
    return rows


# ── Save de factura completa ──────────────────────────────────────────────────

def _save_invoice_complete(
    invoice: OperationLiveInvoice,
    tonnage_rows: list[dict],
    labor_rows:   list[dict],
    cargo_rows:   list[dict],
    supa_pct:         _Decimal,
    contrib_coop_pct: _Decimal,
    iva_pct:          _Decimal,
    iibb_pct:         _Decimal | None,
    total_declarado:  _Decimal | None,
    db: Session,
) -> None:
    """
    Persiste el cuerpo completo de la factura. Patrón delete-and-recreate.
    Llama a calculate_invoice_totals() y persiste los totales calculados.
    Idempotente: se puede llamar en POST /new y en POST /{iid}/edit.

    NOTA: solo guarda _recibido. El flujo de revisión (_revisado) es Paso 5.
    """
    iid = invoice.id

    # ── Líneas de tonelaje ──────────────────────────────────────────────────
    db.query(OperationLiveInvoiceTonnageLine).filter_by(invoice_id=iid).delete()
    orm_ton = []
    for row in tonnage_rows:
        if not row["shift_date"]:
            continue  # fila incompleta — omitir silenciosamente
        obj = OperationLiveInvoiceTonnageLine(
            invoice_id          = iid,
            shift_date          = row["shift_date"],
            turno_range         = row["turno_range"],
            guinche_tipo        = row["guinche_tipo"],
            guinche_descripcion = row["guinche_descripcion"],
            product             = row["product"],
            # DATO RECIBIDO
            tns_habiles_recibido          = row["tns_habiles"],
            tns_inhabiles_recibido        = row["tns_inhabiles"],
            tns_extraordinarias_recibido  = row["tns_extraordinarias"],
            manos_recibido                = row["manos"],
            # Tarifas
            tarifa_habiles          = row["tarifa_habiles"],
            tarifa_inhabiles        = row["tarifa_inhabiles"],
            tarifa_extraordinarias  = row["tarifa_extraordinarias"],
        )
        db.add(obj)
        orm_ton.append(obj)

    # ── Líneas de jornales ──────────────────────────────────────────────────
    db.query(OperationLiveInvoiceLaborLine).filter_by(invoice_id=iid).delete()
    orm_lab = []
    for row in labor_rows:
        if not row["shift_date"]:
            continue
        turno_tipo_val = row["turno_tipo"]
        if turno_tipo_val not in ("habil", "inhabil", "extraordinario"):
            turno_tipo_val = "habil"
        obj = OperationLiveInvoiceLaborLine(
            invoice_id    = iid,
            shift_date    = row["shift_date"],
            turno_range   = row["turno_range"],
            turno_tipo    = turno_tipo_val,
            funcion       = row["funcion"],
            funcion_texto = row["funcion_texto"],
            # DATO RECIBIDO
            cantidad_recibido        = row["cantidad"],
            precio_unitario_recibido = row["precio_unitario"],
        )
        db.add(obj)
        orm_lab.append(obj)

    # ── Ítems especiales (cargo) ────────────────────────────────────────────
    db.query(OperationLiveInvoiceCargoLine).filter_by(invoice_id=iid).delete()
    orm_cargo = []
    for row in cargo_rows:
        obj = OperationLiveInvoiceCargoLine(
            invoice_id      = iid,
            tipo            = row["tipo"],
            descripcion     = row["descripcion"],
            cantidad        = row["cantidad"],
            unidad          = row["unidad"],
            precio_unitario = row["precio_unitario"],
            subtotal        = row["subtotal"],
        )
        db.add(obj)
        orm_cargo.append(obj)

    # ── Totales calculados ──────────────────────────────────────────────────
    db.flush()  # necesitamos IDs para que el ORM acceda a los atributos

    totals_data = calculate_invoice_totals(
        tonnage_lines    = orm_ton,
        labor_lines      = orm_lab,
        cargo_lines      = orm_cargo,
        supa_pct         = supa_pct,
        contrib_coop_pct = contrib_coop_pct,
        iva_pct          = iva_pct,
        iibb_pct         = iibb_pct,
    )

    existing_totals = db.query(OperationLiveInvoiceTotals).filter_by(invoice_id=iid).first()
    if existing_totals:
        db.delete(existing_totals)
        db.flush()

    db.add(OperationLiveInvoiceTotals(
        invoice_id              = iid,
        base_tonelaje_recibido  = totals_data["base_tonelaje_recibido"],
        base_jornales_recibido  = totals_data["base_jornales_recibido"],
        base_tonelaje_revisado  = totals_data["base_tonelaje_revisado"],
        base_jornales_revisado  = totals_data["base_jornales_revisado"],
        supa_pct                = supa_pct,
        contrib_coop_pct        = contrib_coop_pct,
        iva_pct                 = iva_pct,
        iibb_pct                = iibb_pct,
        supa_monto              = totals_data["supa_monto"],
        contrib_coop_monto      = totals_data["contrib_coop_monto"],
        adm_total               = totals_data["adm_total"],
        iva_monto               = totals_data["iva_monto"],
        iibb_monto              = totals_data["iibb_monto"],
        total_factura_declarado = total_declarado,
        total_factura_calculado = totals_data["total_factura_calculado"],
    ))


# ── Helper de factura ─────────────────────────────────────────────────────────

def _get_invoice_or_404(sid: int, iid: int, db: Session) -> OperationLiveInvoice:
    """Carga una factura verificando que pertenezca a la sesión indicada."""
    inv = db.query(OperationLiveInvoice).filter_by(id=iid, session_id=sid).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return inv


# ── Rutas: Nueva factura ──────────────────────────────────────────────────────

@router.get("/{sid}/invoice/new", response_class=HTMLResponse)
async def invoice_new_form(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Formulario de carga de la factura cooperativa.
    Solo disponible cuando session.status == 'closed'.
    Redirige al detalle si ya existe una factura activa.
    """
    session = _get_session_or_404(sid, db)

    if session.status != "closed":
        return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)

    existing = (
        db.query(OperationLiveInvoice)
        .filter(
            OperationLiveInvoice.session_id == sid,
            OperationLiveInvoice.status.in_(["draft", "reviewed", "approved_for_payment"]),
        )
        .first()
    )
    if existing:
        return RedirectResponse(
            url=f"/operations/live/{sid}/invoice/{existing.id}",
            status_code=303,
        )

    return templates.TemplateResponse(
        request,
        "operations/live/invoice_form.html",
        {
            "current_user": current_user,
            "session":      session,
            "invoice":      None,
            "prefill":      {},
        },
    )


@router.post("/{sid}/invoice/new")
async def invoice_new_submit(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """Crea la factura cooperativa en estado 'draft'."""
    session = _get_session_or_404(sid, db)

    if session.status != "closed":
        raise HTTPException(status_code=400, detail="Solo se puede cargar una factura en un operativo cerrado.")

    existing = (
        db.query(OperationLiveInvoice)
        .filter(
            OperationLiveInvoice.session_id == sid,
            OperationLiveInvoice.status.in_(["draft", "reviewed", "approved_for_payment"]),
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe una factura activa para este operativo.")

    form = await request.form()

    invoice = OperationLiveInvoice(
        session_id     = sid,
        invoice_number = str(form.get("invoice_number", "")).strip() or None,
        invoice_date   = _parse_date(str(form.get("invoice_date", "")).strip()),
        upload_method  = "manual",
        status         = "draft",
        origen_nota    = str(form.get("origen_nota", "")).strip() or None,
    )
    db.add(invoice)
    db.flush()

    supa_pct         = _parse_decimal(str(form.get("supa_pct",         "9.5" )).strip()) or _Decimal("9.5")
    contrib_coop_pct = _parse_decimal(str(form.get("contrib_coop_pct", "85.0")).strip()) or _Decimal("85.0")
    iva_pct          = _parse_decimal(str(form.get("iva_pct",          "21.0")).strip()) or _Decimal("21.0")
    iibb_pct         = _parse_decimal(str(form.get("iibb_pct",         ""    )).strip())
    total_declarado  = _parse_decimal(str(form.get("total_factura_declarado", "")).strip())

    _save_invoice_complete(
        invoice,
        _parse_tonnage_rows(form),
        _parse_labor_rows(form),
        _parse_cargo_rows(form),
        supa_pct, contrib_coop_pct, iva_pct, iibb_pct, total_declarado, db,
    )

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}/invoice/{invoice.id}", status_code=303)


# ── Rutas: Detalle factura ────────────────────────────────────────────────────

@router.get("/{sid}/invoice/{iid}", response_class=HTMLResponse)
async def invoice_detail_view(
    request: Request,
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """Vista de solo lectura de la factura cooperativa."""
    session = _get_session_or_404(sid, db)
    invoice = _get_invoice_or_404(sid, iid, db)

    return templates.TemplateResponse(
        request,
        "operations/live/invoice_detail.html",
        {
            "current_user": current_user,
            "session":      session,
            "invoice":      invoice,
            "totals":       invoice.totals,
        },
    )


# ── Rutas: Editar factura ─────────────────────────────────────────────────────

@router.get("/{sid}/invoice/{iid}/edit", response_class=HTMLResponse)
async def invoice_edit_form(
    request: Request,
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Formulario de edición de la factura cooperativa.
    Solo disponible cuando invoice.status == 'draft'.
    """
    session = _get_session_or_404(sid, db)
    invoice = _get_invoice_or_404(sid, iid, db)

    if invoice.status != "draft":
        return RedirectResponse(url=f"/operations/live/{sid}/invoice/{iid}", status_code=303)

    return templates.TemplateResponse(
        request,
        "operations/live/invoice_form.html",
        {
            "current_user": current_user,
            "session":      session,
            "invoice":      invoice,
            "prefill":      {},
        },
    )


@router.post("/{sid}/invoice/{iid}/edit")
async def invoice_edit_submit(
    request: Request,
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """Guarda los cambios de la factura (solo si status == 'draft')."""
    _get_session_or_404(sid, db)
    invoice = _get_invoice_or_404(sid, iid, db)

    if invoice.status != "draft":
        raise HTTPException(status_code=400, detail="Solo se puede editar una factura en estado 'borrador'.")

    form = await request.form()

    invoice.invoice_number = str(form.get("invoice_number", "")).strip() or None
    invoice.invoice_date   = _parse_date(str(form.get("invoice_date", "")).strip())
    invoice.origen_nota    = str(form.get("origen_nota",   "")).strip() or None

    supa_pct         = _parse_decimal(str(form.get("supa_pct",         "9.5" )).strip()) or _Decimal("9.5")
    contrib_coop_pct = _parse_decimal(str(form.get("contrib_coop_pct", "85.0")).strip()) or _Decimal("85.0")
    iva_pct          = _parse_decimal(str(form.get("iva_pct",          "21.0")).strip()) or _Decimal("21.0")
    iibb_pct         = _parse_decimal(str(form.get("iibb_pct",         ""    )).strip())
    total_declarado  = _parse_decimal(str(form.get("total_factura_declarado", "")).strip())

    _save_invoice_complete(
        invoice,
        _parse_tonnage_rows(form),
        _parse_labor_rows(form),
        _parse_cargo_rows(form),
        supa_pct, contrib_coop_pct, iva_pct, iibb_pct, total_declarado, db,
    )

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}/invoice/{iid}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# Paso 5 — Revisión MTR, estados de factura, conciliación final
# ══════════════════════════════════════════════════════════════════════════════

from app.invoice_utils import reconciliation_data as _reconciliation_data
from app.models_live import OperationLiveReconciliation

# Tolerancia para aprobar: si abs(declarado - calculado) / calculado > 5% → bloquear
_APPROVE_TOL_PCT = _Decimal("5")


# ── Helpers Paso 5 ────────────────────────────────────────────────────────────

def _recalc_totals(invoice: OperationLiveInvoice, db: Session) -> None:
    """
    Recalcula y persiste los totales de la factura.
    Llama después de guardar _revisado para que los totales reflejen la revisión.
    """
    t = invoice.totals
    supa_pct         = _Decimal(str(t.supa_pct))         if t else _Decimal("9.5")
    contrib_coop_pct = _Decimal(str(t.contrib_coop_pct)) if t else _Decimal("85.0")
    iva_pct          = _Decimal(str(t.iva_pct))          if t else _Decimal("21.0")
    iibb_pct         = _Decimal(str(t.iibb_pct)) if (t and t.iibb_pct is not None) else None
    total_declarado  = _Decimal(str(t.total_factura_declarado)) if (t and t.total_factura_declarado is not None) else None

    totals_data = calculate_invoice_totals(
        tonnage_lines    = invoice.tonnage_lines,
        labor_lines      = invoice.labor_lines,
        cargo_lines      = invoice.cargo_lines,
        supa_pct         = supa_pct,
        contrib_coop_pct = contrib_coop_pct,
        iva_pct          = iva_pct,
        iibb_pct         = iibb_pct,
    )
    if t:
        t.base_tonelaje_recibido = totals_data["base_tonelaje_recibido"]
        t.base_jornales_recibido = totals_data["base_jornales_recibido"]
        t.base_tonelaje_revisado = totals_data["base_tonelaje_revisado"]
        t.base_jornales_revisado = totals_data["base_jornales_revisado"]
        t.supa_monto             = totals_data["supa_monto"]
        t.contrib_coop_monto     = totals_data["contrib_coop_monto"]
        t.adm_total              = totals_data["adm_total"]
        t.iva_monto              = totals_data["iva_monto"]
        t.iibb_monto             = totals_data["iibb_monto"]
        t.total_factura_calculado = totals_data["total_factura_calculado"]


def _count_unreviewed(invoice: OperationLiveInvoice) -> int:
    """Cuenta líneas de tonelaje y jornales sin revisión (_revisado = None en todos los campos)."""
    n = 0
    for line in (invoice.tonnage_lines or []):
        if (line.tns_habiles_revisado is None
                and line.tns_inhabiles_revisado is None
                and line.tns_extraordinarias_revisado is None):
            n += 1
    for line in (invoice.labor_lines or []):
        if line.cantidad_revisado is None and line.precio_unitario_revisado is None:
            n += 1
    return n


# ── Rutas: Revisión MTR ───────────────────────────────────────────────────────

@router.get("/{sid}/invoice/{iid}/review", response_class=HTMLResponse)
async def invoice_review_form(
    request: Request,
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Pantalla de revisión MTR. Muestra recibido vs revisado línea por línea.
    Solo disponible para facturas en status='draft' o 'disputed'.
    """
    session = _get_session_or_404(sid, db)
    invoice = _get_invoice_or_404(sid, iid, db)

    if invoice.status not in ("draft", "disputed"):
        return RedirectResponse(
            url=f"/operations/live/{sid}/invoice/{iid}", status_code=303
        )

    return templates.TemplateResponse(
        request,
        "operations/live/invoice_review.html",
        {
            "current_user": current_user,
            "session":      session,
            "invoice":      invoice,
        },
    )


@router.post("/{sid}/invoice/{iid}/review")
async def invoice_review_submit(
    request: Request,
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Guarda los datos revisados (_revisado) de todas las líneas.
    Puede marcar como reviewed si el usuario lo solicita.

    Acciones (campo 'review_action'):
      'save'    → guarda _revisado, mantiene status (draft/disputed)
      'approve' → guarda _revisado, transiciona a 'reviewed' si todas revisadas
    """
    session = _get_session_or_404(sid, db)      # noqa: F841
    invoice = _get_invoice_or_404(sid, iid, db)

    if invoice.status not in ("draft", "disputed"):
        raise HTTPException(status_code=400, detail="Solo se puede revisar una factura en estado borrador o disputada.")

    form = await request.form()
    review_action = str(form.get("review_action", "save")).strip()
    reviewer = str(form.get("reviewer_name", "")).strip()

    if review_action == "approve" and not reviewer:
        raise HTTPException(status_code=400, detail="El nombre del revisor es obligatorio para confirmar la revisión.")

    now = datetime.utcnow()

    # ── Líneas de tonelaje ──────────────────────────────────────────────────
    for i, line in enumerate(invoice.tonnage_lines):
        h_rev = _parse_decimal(str(form.get(f"ton_{i}_habiles_rev",         "")).strip())
        i_rev = _parse_decimal(str(form.get(f"ton_{i}_inhabiles_rev",       "")).strip())
        e_rev = _parse_decimal(str(form.get(f"ton_{i}_extraordinarias_rev", "")).strip())
        m_rev = _parse_int(str(form.get(f"ton_{i}_manos_rev", "")).strip())
        nota  = str(form.get(f"ton_{i}_diferencia_nota", "")).strip() or None

        line.tns_habiles_revisado          = h_rev
        line.tns_inhabiles_revisado        = i_rev
        line.tns_extraordinarias_revisado  = e_rev
        line.manos_revisado                = m_rev
        line.diferencia_nota               = nota
        if reviewer:
            line.revisado_por = reviewer
            line.revisado_at  = now

    # ── Líneas de jornales ──────────────────────────────────────────────────
    for i, line in enumerate(invoice.labor_lines):
        c_rev = _parse_int(str(form.get(f"lab_{i}_cantidad_rev",        "")).strip())
        p_rev = _parse_decimal(str(form.get(f"lab_{i}_precio_rev",      "")).strip())
        nota  = str(form.get(f"lab_{i}_diferencia_nota", "")).strip() or None

        line.cantidad_revisado        = c_rev
        line.precio_unitario_revisado = p_rev
        line.diferencia_nota          = nota
        if reviewer:
            line.revisado_por = reviewer
            line.revisado_at  = now

    # ── Recalcular totales con los nuevos _revisado ─────────────────────────
    db.flush()
    _recalc_totals(invoice, db)

    # ── Transición de estado ────────────────────────────────────────────────
    if review_action == "approve":
        unreviewed = _count_unreviewed(invoice)
        if unreviewed > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No se puede confirmar la revisión: {unreviewed} línea(s) "
                    f"sin datos revisados. Completá todos los campos revisados primero."
                ),
            )
        invoice.status      = "reviewed"
        invoice.reviewed_by = reviewer
        invoice.reviewed_at = now

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}/invoice/{iid}", status_code=303)


# ── Rutas: Aprobar para pago ──────────────────────────────────────────────────

@router.post("/{sid}/invoice/{iid}/approve")
async def invoice_approve(
    request: Request,
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Transiciona la factura a 'approved_for_payment'.

    Precondiciones:
      1. status == 'reviewed'
      2. Nombre de validador obligatorio
      3. Cero líneas sin revisar
      4. Diferencia declarado/calculado ≤ 5% (si total_declarado está cargado)
    """
    session = _get_session_or_404(sid, db)      # noqa: F841
    invoice = _get_invoice_or_404(sid, iid, db)

    if invoice.status != "reviewed":
        raise HTTPException(status_code=400, detail="Solo se puede aprobar una factura en estado 'revisado'.")

    form = await request.form()
    validator = str(form.get("validator_name", "")).strip()

    if not validator:
        raise HTTPException(status_code=400, detail="El nombre del validador es obligatorio para aprobar la factura.")

    unreviewed = _count_unreviewed(invoice)
    if unreviewed > 0:
        raise HTTPException(
            status_code=400,
            detail=f"No se puede aprobar: {unreviewed} línea(s) sin datos revisados.",
        )

    t = invoice.totals
    if t and t.total_factura_declarado is not None and t.total_factura_calculado:
        calc  = _Decimal(str(t.total_factura_calculado))
        decl  = _Decimal(str(t.total_factura_declarado))
        if calc > 0:
            diff_pct = abs(decl - calc) / calc * _Decimal("100")
            if diff_pct > _APPROVE_TOL_PCT:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Diferencia entre total declarado (${float(decl):,.2f}) y calculado "
                        f"(${float(calc):,.2f}) es {float(diff_pct):.1f}%, supera el "
                        f"{float(_APPROVE_TOL_PCT):.0f}% de tolerancia. "
                        f"Revisá los datos antes de aprobar."
                    ),
                )

    invoice.status       = "approved_for_payment"
    invoice.validated_by = validator
    invoice.validated_at = datetime.utcnow()

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}/invoice/{iid}", status_code=303)


# ── Rutas: Disputar factura ───────────────────────────────────────────────────

@router.post("/{sid}/invoice/{iid}/dispute")
async def invoice_dispute(
    request: Request,
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Transiciona la factura a 'disputed'.
    Disponible desde cualquier estado activo. Requiere nota obligatoria.
    """
    session = _get_session_or_404(sid, db)      # noqa: F841
    invoice = _get_invoice_or_404(sid, iid, db)

    form = await request.form()
    dispute_note = str(form.get("dispute_note", "")).strip()

    if not dispute_note:
        raise HTTPException(status_code=400, detail="La nota de disputa es obligatoria.")

    invoice.status      = "disputed"
    invoice.origen_nota = (
        (invoice.origen_nota or "") + f"\n[DISPUTA] {dispute_note}"
    ).strip()

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}/invoice/{iid}", status_code=303)


# ── Rutas: Conciliación final ─────────────────────────────────────────────────

@router.get("/{sid}/reconciliation", response_class=HTMLResponse)
async def reconciliation_view(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Pantalla de conciliación final por producto.
    Usa EXCLUSIVAMENTE datos _revisado.
    SIN_PRODUCTO → siempre excluded, nunca se infiere producto.
    """
    session = _get_session_or_404(sid, db)

    # Factura más reciente (puede ser reviewed o approved)
    invoice = (
        db.query(OperationLiveInvoice)
        .filter(
            OperationLiveInvoice.session_id == sid,
            OperationLiveInvoice.status.in_(["reviewed", "approved_for_payment"]),
        )
        .order_by(OperationLiveInvoice.id.desc())
        .first()
    )

    # Bodega rows para lado MTR
    all_shift_ids = [s.id for s in session.shifts]
    all_bodega_rows = (
        db.query(OperationLiveBodegaData)
        .filter(OperationLiveBodegaData.shift_id.in_(all_shift_ids))
        .all()
        if all_shift_ids else []
    )

    # Calcular reconciliación (SIEMPRE con revisado, NUNCA infiere producto)
    recon_rows = _reconciliation_data(
        bodega_rows      = all_bodega_rows,
        tonnage_lines    = invoice.tonnage_lines if invoice else [],
        session_products = session.products,
    ) if invoice else []

    # Dictámenes ya guardados
    existing_recon = {
        r.product: r
        for r in db.query(OperationLiveReconciliation).filter_by(session_id=sid).all()
    }

    return templates.TemplateResponse(
        request,
        "operations/live/reconciliation.html",
        {
            "current_user":    current_user,
            "session":         session,
            "invoice":         invoice,
            "recon_rows":      recon_rows,
            "existing_recon":  existing_recon,
            "can_close_recon": invoice is not None and invoice.status == "approved_for_payment",
        },
    )


@router.post("/{sid}/reconciliation")
async def reconciliation_save(
    request: Request,
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Guarda el dictamen de conciliación por producto.
    Solo actualiza filas explícitamente enviadas.
    Si save_action == 'close_reconciliation': transiciona session a 'reconciled'.
    """
    session = _get_session_or_404(sid, db)

    invoice = (
        db.query(OperationLiveInvoice)
        .filter(
            OperationLiveInvoice.session_id == sid,
            OperationLiveInvoice.status.in_(["reviewed", "approved_for_payment"]),
        )
        .order_by(OperationLiveInvoice.id.desc())
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=400, detail="No hay factura revisada para conciliar.")

    all_shift_ids = [s.id for s in session.shifts]
    all_bodega_rows = (
        db.query(OperationLiveBodegaData)
        .filter(OperationLiveBodegaData.shift_id.in_(all_shift_ids))
        .all()
        if all_shift_ids else []
    )

    recon_rows = _reconciliation_data(
        bodega_rows      = all_bodega_rows,
        tonnage_lines    = invoice.tonnage_lines,
        session_products = session.products,
    )

    form = await request.form()
    now  = datetime.utcnow()
    dictamen_by = str(form.get("dictamen_by", "")).strip() or None

    for row in recon_rows:
        product = row["product"]
        status_key  = f"recon_{product}_status"
        obs_key     = f"recon_{product}_observacion"
        ajuste_key  = f"recon_{product}_ajuste_final"

        raw_status  = str(form.get(status_key,  "")).strip() or None
        raw_obs     = str(form.get(obs_key,     "")).strip() or None
        raw_ajuste  = _parse_decimal(str(form.get(ajuste_key, "")).strip())

        if not raw_status:
            continue  # no enviado / no modificado

        # Upsert por (session_id, product)
        existing = (
            db.query(OperationLiveReconciliation)
            .filter_by(session_id=sid, product=product)
            .first()
        )
        if existing:
            rec = existing
        else:
            rec = OperationLiveReconciliation(session_id=sid, product=product)
            db.add(rec)

        rec.tns_mtr                    = row["tns_mtr"]
        rec.tns_coop_abordo_revisado   = row["tns_coop_abordo_revisado"]
        rec.tns_coop_fiscal_revisado   = row["tns_coop_fiscal_revisado"]
        rec.tns_coop_total_revisado    = row["tns_coop_total_revisado"]
        rec.diferencia_abordo          = row["diferencia_abordo"]
        rec.diferencia_fiscal          = row["diferencia_fiscal"]
        rec.status                     = raw_status
        rec.observacion                = raw_obs
        rec.ajuste_final               = raw_ajuste
        rec.dictamen_by                = dictamen_by
        rec.dictamen_at                = now

    # Cerrar conciliación (opcional)
    save_action = str(form.get("save_action", "save")).strip()
    if save_action == "close_reconciliation":
        if invoice.status != "approved_for_payment":
            raise HTTPException(
                status_code=400,
                detail="Se necesita factura aprobada para cerrar la conciliación.",
            )
        session.status        = "reconciled"
        session.reconciled_at = now

    db.commit()
    return RedirectResponse(url=f"/operations/live/{sid}/reconciliation", status_code=303)


# ── Fase 3: Fotos ─────────────────────────────────────────────────────────────

_PHOTO_CATEGORIES = ["barco", "tapas", "mercaderia", "equipos", "demora", "otro"]


@router.post("/{sid}/photos")
async def upload_photo(
    request: Request,
    sid: int,
    files: list[UploadFile] = File(...),
    caption: str = Form(""),
    category: str = Form(""),
    shift_id: Optional[int] = Form(None),
    bodega_number: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """
    Sube una o más fotos al operativo.
    shift_id optional: si se envía, las fotos quedan asociadas a ese turno.
    bodega_number optional: si se envía, indica la bodega específica.
    """
    session = _get_session_or_404(sid, db)

    # Validar shift_id si se provee
    shift = None
    if shift_id:
        shift = db.query(OperationLiveShift).filter_by(
            id=shift_id, session_id=sid
        ).first()
        if not shift:
            raise HTTPException(status_code=404, detail="Turno no encontrado en esta sesión")

    uploaded_count = 0
    for f in files:
        if not f.filename:
            continue
        contents = await f.read()
        if not contents:
            continue
        unique_name = f"{uuid.uuid4()}_{f.filename}"
        result = _cloudinary_upload(contents, unique_name, folder="mtr-compras/live")

        photo = OperationLivePhoto(
            session_id    = session.id,
            shift_id      = shift_id,
            bodega_number = bodega_number,
            file_url      = result["url"],
            public_id     = result["public_id"],
            caption       = caption.strip() or None,
            uploaded_by   = getattr(current_user, "name", None) or getattr(current_user, "email", None),
            category      = category.strip() or None,
        )
        db.add(photo)
        uploaded_count += 1

    if uploaded_count > 0:
        db.commit()

    # Redirige al origen correcto
    if shift_id:
        return RedirectResponse(
            url=f"/operations/live/{sid}/shift/{shift_id}",
            status_code=303,
        )
    return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)


@router.post("/{sid}/photos/{pid}/delete")
async def delete_photo(
    request: Request,
    sid: int,
    pid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_perm("operaciones.live")),
):
    """Borra una foto del operativo (DB + Cloudinary)."""
    session = _get_session_or_404(sid, db)

    photo = db.query(OperationLivePhoto).filter_by(
        id=pid, session_id=session.id
    ).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Foto no encontrada")

    shift_id = photo.shift_id

    # Borra de Cloudinary (best-effort: si falla no bloquea el borrado de DB)
    try:
        _cloudinary_delete(photo.public_id)
    except Exception:
        pass

    db.delete(photo)
    db.commit()

    if shift_id:
        return RedirectResponse(
            url=f"/operations/live/{sid}/shift/{shift_id}",
            status_code=303,
        )
    return RedirectResponse(url=f"/operations/live/{sid}", status_code=303)
