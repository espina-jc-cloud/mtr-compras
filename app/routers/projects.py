from __future__ import annotations
import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from app.database import get_db
from app.deps import get_current_user, require_role
from app.permissions import require_perm
from app import models
from app.templates import templates
from app.cloudinary_upload import upload_file as _cloud_upload, delete_file as _cloud_delete

# Acceso al módulo Proyectos → permiso "proyectos.proyectos".
require_projects_access = require_perm("proyectos.proyectos")

router = APIRouter(prefix="/projects")

PLANTS = ["MTR1", "MTR2", "ROSARIO", "TODAS"]
STATUSES = ["pendiente", "en_progreso", "pausado", "finalizado", "cancelado"]
PRIORITIES = ["baja", "media", "alta", "urgente"]
TASK_STATUSES = ["pendiente", "en_progreso", "bloqueada", "finalizada", "cancelada"]


def _add_audit(db, project_id, user_id, action, old_status=None, new_status=None, comment=""):
    db.add(models.ProjectAuditLog(
        project_id=project_id, user_id=user_id, action=action,
        old_status=old_status, new_status=new_status, comment=comment or None,
    ))


def _can_edit(user, project) -> bool:
    if user.role in ("admin", "superadmin"):
        return True
    if user.role == "autorizador":
        return user.plant == "TODAS" or project.plant == user.plant
    if user.role == "planta":
        return project.created_by_id == user.id
    return False  # tecnico: solo lectura


def _parse_date(s: str):
    if not s or not s.strip():
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _parse_decimal(s: str):
    if not s or not s.strip():
        return None
    try:
        return Decimal(s.strip().replace(",", "."))
    except InvalidOperation:
        return None


def _qp(request: Request, name: str, default: str = "") -> str:
    values = request.query_params.getlist(name)
    return values[0].strip() if values else default


# ── Lista ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_projects(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    q           = _qp(request, "q")
    status      = _qp(request, "status")
    plant       = _qp(request, "plant")
    priority    = _qp(request, "priority")
    responsible = _qp(request, "responsible")

    query = db.query(models.Project).filter(models.Project.deleted_at == None)

    # Restricción por rol
    if current_user.role in ("planta", "tecnico") and current_user.plant != "TODAS":
        query = query.filter(
            or_(
                models.Project.plant == current_user.plant,
                models.Project.plant == "TODAS",
            )
        )
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        query = query.filter(
            or_(
                models.Project.plant == current_user.plant,
                models.Project.plant == "TODAS",
            )
        )

    # Filtros
    if plant:
        query = query.filter(models.Project.plant == plant)
    if status:
        query = query.filter(models.Project.status == status)
    if priority:
        query = query.filter(models.Project.priority == priority)
    if responsible:
        query = query.filter(models.Project.responsible.ilike(f"%{responsible}%"))
    if q:
        query = query.filter(
            or_(
                models.Project.name.ilike(f"%{q}%"),
                models.Project.description.ilike(f"%{q}%"),
                models.Project.area.ilike(f"%{q}%"),
                models.Project.responsible.ilike(f"%{q}%"),
            )
        )

    projects = (
        query
        .options(joinedload(models.Project.tasks))
        .order_by(models.Project.created_at.desc())
        .all()
    )

    # Resumen de tareas por proyecto (evita N+1 — tasks ya cargadas por joinedload)
    task_summaries = {}
    for p in projects:
        active = [t for t in p.tasks if not t.deleted_at]
        total  = len(active)
        fin    = sum(1 for t in active if t.status == "finalizada")
        avance = round(sum(t.progress_percent for t in active) / total) if total else 0
        task_summaries[p.id] = {"total": total, "finalizadas": fin, "avance_pct": avance}

    params = {
        "q": q, "status": status, "plant": plant,
        "priority": priority, "responsible": responsible,
    }

    return templates.TemplateResponse(request, "projects/list.html", {
        "user":           current_user,
        "projects":       projects,
        "params":         params,
        "statuses":       STATUSES,
        "plants":         PLANTS,
        "priorities":     PRIORITIES,
        "task_summaries": task_summaries,
    })


# ── Nuevo ─────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_project_form(
    request: Request,
    current_user=Depends(require_projects_access),
):
    if current_user.role == "tecnico":
        raise HTTPException(status_code=403, detail="Sin permisos para crear proyectos.")
    return templates.TemplateResponse(request, "projects/new.html", {
        "user": current_user,
        "plants": PLANTS,
        "statuses": STATUSES,
        "priorities": PRIORITIES,
        "today": date.today().isoformat(),
        "error": None,
    })


@router.post("/new")
async def create_project(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    area: str = Form(""),
    responsible: str = Form(""),
    plant: str = Form(""),
    status: str = Form("pendiente"),
    priority: str = Form("media"),
    start_date: str = Form(""),
    estimated_end_date: str = Form(""),
    estimated_budget: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    if current_user.role == "tecnico":
        raise HTTPException(status_code=403, detail="Sin permisos para crear proyectos.")

    if not name.strip():
        return templates.TemplateResponse(request, "projects/new.html", {
            "user": current_user, "plants": PLANTS, "statuses": STATUSES,
            "priorities": PRIORITIES, "today": date.today().isoformat(),
            "error": "El nombre del proyecto es obligatorio.",
        })

    project = models.Project(
        name=name.strip(),
        description=description.strip() or None,
        area=area.strip() or None,
        responsible=responsible.strip() or "Sin asignar",
        plant=plant or None,
        status=status,
        priority=priority,
        start_date=_parse_date(start_date),
        estimated_end_date=_parse_date(estimated_end_date),
        estimated_budget=_parse_decimal(estimated_budget),
        notes=notes.strip() or None,
        created_by_id=current_user.id,
    )
    db.add(project)
    db.flush()
    _add_audit(db, project.id, current_user.id, "creado", new_status=status)
    db.commit()
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


# ── Detalle ───────────────────────────────────────────────────────────────────

@router.get("/{project_id}", response_class=HTMLResponse)
async def project_detail(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = db.query(models.Project).filter(
        models.Project.id == project_id,
        models.Project.deleted_at == None,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")

    active_tasks = [t for t in project.tasks if not t.deleted_at]
    total  = len(active_tasks)
    task_summary = {
        "total":       total,
        "finalizadas": sum(1 for t in active_tasks if t.status == "finalizada"),
        "en_progreso": sum(1 for t in active_tasks if t.status == "en_progreso"),
        "bloqueadas":  sum(1 for t in active_tasks if t.status == "bloqueada"),
        "pendientes":  sum(1 for t in active_tasks if t.status == "pendiente"),
        "canceladas":  sum(1 for t in active_tasks if t.status == "cancelada"),
        "avance_pct":  round(sum(t.progress_percent for t in active_tasks) / total)
                       if total else 0,
    }

    return templates.TemplateResponse(request, "projects/detail.html", {
        "user":          current_user,
        "project":       project,
        "can_edit":      _can_edit(current_user, project),
        "statuses":      STATUSES,
        "task_statuses": TASK_STATUSES,
        "task_summary":  task_summary,
    })


# ── Gantt ─────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/gantt", response_class=HTMLResponse)
async def project_gantt(
    project_id:   int,
    request:      Request,
    db:           Session = Depends(get_db),
    current_user           = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)

    all_tasks = [t for t in project.tasks if not t.deleted_at]
    dated     = [t for t in all_tasks if t.start_date and t.estimated_end_date]
    undated   = [t for t in all_tasks if not (t.start_date and t.estimated_end_date)]

    range_start = None
    range_end   = None
    total_days  = None
    today_pct   = None
    tick_marks  = []
    gantt_tasks = []

    if dated:
        range_start = min(t.start_date for t in dated)

        # range_end: si actual_end_date es posterior a estimated_end_date, usarla
        # para el rango — pero NO para la barra (requisito #3).
        end_candidates = [t.estimated_end_date for t in dated]
        for t in dated:
            if t.actual_end_date and t.actual_end_date > t.estimated_end_date:
                end_candidates.append(t.actual_end_date)
        range_end  = max(max(end_candidates), date.today())
        total_days = max((range_end - range_start).days, 1)

        # Posición de "hoy" en el timeline
        today = date.today()
        if range_start <= today <= range_end:
            today_pct = round((today - range_start).days / total_days * 100, 2)

        # Marcas de escala: máximo 6, distribuidas uniformemente
        num_ticks = min(6, total_days)
        step = total_days / num_ticks if num_ticks else total_days
        for i in range(num_ticks + 1):
            delta     = round(i * step)
            tick_date = range_start + timedelta(days=delta)
            tick_pct  = round(delta / total_days * 100, 1)
            tick_marks.append({"date": tick_date, "pct": tick_pct})

        # Posición y ancho de cada barra
        for t in dated:
            offset_pct = round((t.start_date - range_start).days / total_days * 100, 2)

            # Fecha de fin visual de la barra:
            # - Finalizada o 100 %: usar actual_end_date si existe, si no estimated_end_date.
            # - Cualquier otro estado: usar estimated_end_date.
            if (t.status == "finalizada" or t.progress_percent == 100) and t.actual_end_date:
                bar_end = t.actual_end_date
            else:
                bar_end = t.estimated_end_date

            # Duración mínima 1 día (cubre bar_end < start_date o iguales)
            duration  = max((bar_end - t.start_date).days, 1)
            width_pct = round(duration / total_days * 100, 2)

            # Clamp: la barra nunca sale del 100 %
            offset_pct = max(0.0, min(offset_pct, 99.0))
            width_pct  = max(0.5, min(width_pct, 100.0 - offset_pct))

            gantt_tasks.append({
                "task":       t,
                "offset_pct": offset_pct,
                "width_pct":  width_pct,
                "bar_end":    bar_end,   # disponible en template para tooltip futuro
            })

    return templates.TemplateResponse(request, "projects/gantt.html", {
        "user":         current_user,
        "project":      project,
        "can_edit":     _can_edit(current_user, project),
        "gantt_tasks":  gantt_tasks,
        "undated_tasks": undated,
        "range_start":  range_start,
        "range_end":    range_end,
        "total_days":   total_days,
        "today_pct":    today_pct,
        "tick_marks":   tick_marks,
        "all_count":    len(all_tasks),
    })


# ── Editar ────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/edit", response_class=HTMLResponse)
async def edit_project_form(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = db.query(models.Project).filter(
        models.Project.id == project_id,
        models.Project.deleted_at == None,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para editar este proyecto.")

    return templates.TemplateResponse(request, "projects/edit.html", {
        "user": current_user,
        "project": project,
        "plants": PLANTS,
        "statuses": STATUSES,
        "priorities": PRIORITIES,
        "error": None,
    })


@router.post("/{project_id}/edit")
async def edit_project(
    project_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    area: str = Form(""),
    responsible: str = Form(""),
    plant: str = Form(""),
    status: str = Form(...),
    priority: str = Form(...),
    start_date: str = Form(""),
    estimated_end_date: str = Form(""),
    actual_end_date: str = Form(""),
    estimated_budget: str = Form(""),
    actual_cost: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = db.query(models.Project).filter(
        models.Project.id == project_id,
        models.Project.deleted_at == None,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para editar este proyecto.")

    old_status = project.status

    project.name               = name.strip()
    project.description        = description.strip() or None
    project.area               = area.strip() or None
    project.responsible        = responsible.strip() or None
    project.plant              = plant or None
    project.status             = status
    project.priority           = priority
    project.start_date         = _parse_date(start_date)
    project.estimated_end_date = _parse_date(estimated_end_date)
    project.actual_end_date    = _parse_date(actual_end_date)
    project.estimated_budget   = _parse_decimal(estimated_budget)
    project.actual_cost        = _parse_decimal(actual_cost)
    project.notes              = notes.strip() or None

    if old_status != status:
        _add_audit(db, project.id, current_user.id, "estado_cambiado",
                   old_status=old_status, new_status=status)
    else:
        _add_audit(db, project.id, current_user.id, "editado")

    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


# ── Cambiar estado (rápido, desde detalle) ────────────────────────────────────

@router.post("/{project_id}/status")
async def change_status(
    project_id: int,
    status: str = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = db.query(models.Project).filter(
        models.Project.id == project_id,
        models.Project.deleted_at == None,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos.")
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="Estado inválido.")

    old_status = project.status
    project.status = status
    if status == "finalizado" and not project.actual_end_date:
        project.actual_end_date = datetime.utcnow()

    _add_audit(db, project.id, current_user.id, "estado_cambiado",
               old_status=old_status, new_status=status, comment=comment)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


# ── Eliminar (soft delete) ────────────────────────────────────────────────────

@router.post("/{project_id}/delete")
async def delete_project(
    project_id: int,
    deleted_reason: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    project = db.query(models.Project).filter(
        models.Project.id == project_id,
        models.Project.deleted_at == None,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")

    project.deleted_at     = datetime.utcnow()
    project.deleted_reason = deleted_reason.strip()
    _add_audit(db, project.id, current_user.id, "eliminado", comment=deleted_reason)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}/entries/{entry.id}", status_code=303)


# ── Bitácora: helpers ─────────────────────────────────────────────────────────

def _get_project_or_404(db, project_id):
    p = db.query(models.Project).filter(
        models.Project.id == project_id,
        models.Project.deleted_at == None,
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    return p


def _get_entry_or_404(db, project_id, entry_id):
    e = db.query(models.ProjectEntry).filter(
        models.ProjectEntry.id == entry_id,
        models.ProjectEntry.project_id == project_id,
        models.ProjectEntry.deleted_at == None,
    ).first()
    if not e:
        raise HTTPException(status_code=404, detail="Entrada no encontrada.")
    return e


def _entry_form_ctx(project, entry=None, error=None, prefill_date=None):
    """Contexto común para el formulario de bitácora (nueva y edición)."""
    return {
        "project": project,
        "entry": entry,
        "error": error,
        "today": date.today().isoformat(),
        "prefill_date": prefill_date or (entry.entry_date.isoformat() if entry else date.today().isoformat()),
    }


# ── Bitácora: nueva entrada ───────────────────────────────────────────────────

@router.get("/{project_id}/entries/new", response_class=HTMLResponse)
async def new_entry_form(
    project_id: int,
    request: Request,
    prefill_date: str = "",
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para agregar entradas.")
    ctx = _entry_form_ctx(project, prefill_date=prefill_date or date.today().isoformat())
    return templates.TemplateResponse(request, "projects/entry_form.html",
                                      {"user": current_user, **ctx})


@router.post("/{project_id}/entries/new")
async def create_entry(
    project_id: int,
    request: Request,
    entry_date: str = Form(...),
    avances: str = Form(""),
    observaciones: str = Form(""),
    problemas: str = Form(""),
    tareas_realizadas: str = Form(""),
    proximos_pasos: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para agregar entradas.")

    parsed_date = _parse_date(entry_date)
    if not parsed_date:
        ctx = _entry_form_ctx(project, prefill_date=entry_date,
                              error="La fecha es obligatoria y debe tener formato válido.")
        return templates.TemplateResponse(request, "projects/entry_form.html",
                                          {"user": current_user, **ctx})

    # Una sola entrada activa por fecha por proyecto
    existing = db.query(models.ProjectEntry).filter(
        models.ProjectEntry.project_id == project_id,
        models.ProjectEntry.entry_date == parsed_date.date(),
        models.ProjectEntry.deleted_at == None,
    ).first()
    if existing:
        ctx = _entry_form_ctx(project, prefill_date=entry_date,
                              error=f"Ya existe una entrada para el {parsed_date.strftime('%d/%m/%Y')}. "
                                    f"Editá la entrada existente.")
        return templates.TemplateResponse(request, "projects/entry_form.html",
                                          {"user": current_user, **ctx})

    entry = models.ProjectEntry(
        project_id=project_id,
        entry_date=parsed_date.date(),
        avances=avances.strip() or None,
        observaciones=observaciones.strip() or None,
        problemas=problemas.strip() or None,
        tareas_realizadas=tareas_realizadas.strip() or None,
        proximos_pasos=proximos_pasos.strip() or None,
        created_by_id=current_user.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


# ── Bitácora: detalle de una entrada ─────────────────────────────────────────

@router.get("/{project_id}/entries/{entry_id}", response_class=HTMLResponse)
async def entry_detail(
    project_id: int,
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    entry   = _get_entry_or_404(db, project_id, entry_id)
    return templates.TemplateResponse(request, "projects/entry_detail.html", {
        "user":     current_user,
        "project":  project,
        "entry":    entry,
        "can_edit": _can_edit(current_user, project),
    })


# ── Bitácora: editar entrada ──────────────────────────────────────────────────

@router.get("/{project_id}/entries/{entry_id}/edit", response_class=HTMLResponse)
async def edit_entry_form(
    project_id: int,
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    entry   = _get_entry_or_404(db, project_id, entry_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para editar entradas.")
    ctx = _entry_form_ctx(project, entry=entry)
    return templates.TemplateResponse(request, "projects/entry_form.html",
                                      {"user": current_user, **ctx})


@router.post("/{project_id}/entries/{entry_id}/edit")
async def update_entry(
    project_id: int,
    entry_id: int,
    request: Request,
    entry_date: str = Form(...),
    avances: str = Form(""),
    observaciones: str = Form(""),
    problemas: str = Form(""),
    tareas_realizadas: str = Form(""),
    proximos_pasos: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    entry   = _get_entry_or_404(db, project_id, entry_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para editar entradas.")

    parsed_date = _parse_date(entry_date)
    if not parsed_date:
        ctx = _entry_form_ctx(project, entry=entry, error="Fecha inválida.")
        return templates.TemplateResponse(request, "projects/entry_form.html",
                                          {"user": current_user, **ctx})

    # Si cambió la fecha, verificar que no exista otra entrada en esa fecha
    if parsed_date.date() != entry.entry_date:
        conflict = db.query(models.ProjectEntry).filter(
            models.ProjectEntry.project_id == project_id,
            models.ProjectEntry.entry_date == parsed_date.date(),
            models.ProjectEntry.id != entry_id,
            models.ProjectEntry.deleted_at == None,
        ).first()
        if conflict:
            ctx = _entry_form_ctx(project, entry=entry,
                                  error=f"Ya existe una entrada para el {parsed_date.strftime('%d/%m/%Y')}.")
            return templates.TemplateResponse(request, "projects/entry_form.html",
                                              {"user": current_user, **ctx})

    entry.entry_date        = parsed_date.date()
    entry.avances           = avances.strip() or None
    entry.observaciones     = observaciones.strip() or None
    entry.problemas         = problemas.strip() or None
    entry.tareas_realizadas = tareas_realizadas.strip() or None
    entry.proximos_pasos    = proximos_pasos.strip() or None
    db.commit()
    return RedirectResponse(url="/projects?ok=Entrada+guardada", status_code=303)


# ── Bitácora: eliminar entrada (soft delete) ──────────────────────────────────

@router.post("/{project_id}/entries/{entry_id}/delete")
async def delete_entry(
    project_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    entry   = _get_entry_or_404(db, project_id, entry_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para eliminar entradas.")
    entry.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


# ── Adjuntos: subir ───────────────────────────────────────────────────────────

FILE_TYPES = ["foto", "factura", "remito", "cotizacion", "documento", "otro"]


@router.post("/{project_id}/entries/{entry_id}/attachments")
async def upload_attachment(
    project_id:    int,
    entry_id:      int,
    request:       Request,
    file_type:     str                  = Form("otro"),
    description:   str                  = Form(""),
    supplier:      str                  = Form(""),
    currency:      str                  = Form("USD"),   # "USD" | "ARS"
    amount:        str                  = Form(""),      # monto en la moneda elegida
    exchange_rate: str                  = Form(""),
    file:          UploadFile           = File(None),
    db:            Session              = Depends(get_db),
    current_user                        = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    entry   = _get_entry_or_404(db, project_id, entry_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para subir archivos.")
    if file_type not in FILE_TYPES:
        file_type = "otro"

    has_file = file is not None and file.filename

    # ── Determinar si hay datos económicos o descriptivos con contenido ───────
    amount_dec_check = _parse_decimal(amount)
    has_economic = any([
        description.strip(),
        supplier.strip(),
        amount_dec_check is not None,
    ])

    # ── Sin archivo y sin datos → nada que guardar, redirigir ─────────────────
    if not has_file and not has_economic:
        return RedirectResponse(
            url=f"/projects/{project_id}/entries/{entry_id}", status_code=303
        )

    # ── Subir a Cloudinary solo si hay archivo ────────────────────────────────
    if has_file:
        contents    = await file.read()
        unique_name = f"{uuid.uuid4()}_{file.filename}"
        try:
            result    = _cloud_upload(contents, unique_name, folder="mtr-compras/proyectos")
            file_url  = result["url"]
            public_id = result["public_id"]
            filename  = file.filename
        except Exception as e:
            print(f"[Cloudinary] Error subiendo adjunto: {e}")
            return RedirectResponse(
                url=f"/projects/{project_id}/entries/{entry_id}", status_code=303
            )
    else:
        # Registro económico sin archivo
        file_url  = None
        public_id = None
        filename  = None

    # ── Campos económicos opcionales ──────────────────────────────────────────
    amount_dec = _parse_decimal(amount)
    rate_dec   = _parse_decimal(exchange_rate)

    # exchange_rate válido: debe ser > 0
    if rate_dec is not None and rate_dec <= 0:
        rate_dec = None

    usd_dec  = None
    ars_dec  = None
    curr_out = None   # currency que se guarda en DB

    if amount_dec is not None:
        if currency == "USD":
            curr_out = "USD"
            usd_dec  = amount_dec
            if rate_dec is not None:
                ars_dec = (usd_dec * rate_dec).quantize(Decimal("0.01"))
        elif currency == "ARS":
            curr_out = "ARS"
            ars_dec  = amount_dec
            if rate_dec is not None:
                try:
                    usd_dec = (ars_dec / rate_dec).quantize(Decimal("0.01"))
                except Exception:
                    usd_dec = None
        # currency desconocido: se ignora el monto
    # Si amount_dec es None: currency, usd_dec, ars_dec, rate_dec quedan None

    # Si no hubo monto válido, no guardar exchange_rate ni currency
    if curr_out is None:
        rate_dec = None

    att = models.ProjectEntryAttachment(
        entry_id       = entry_id,
        project_id     = project_id,
        file_type      = file_type,
        file_url       = file_url,
        public_id      = public_id,
        filename       = filename,
        description    = description.strip() or None,
        supplier       = supplier.strip()    or None,
        currency       = curr_out,
        amount_usd     = usd_dec,
        exchange_rate  = rate_dec,
        amount_ars     = ars_dec,
        uploaded_by_id = current_user.id,
    )
    db.add(att)
    db.commit()
    return RedirectResponse(
        url=f"/projects/{project_id}/entries/{entry_id}", status_code=303
    )


# ── Adjuntos: eliminar ────────────────────────────────────────────────────────

@router.post("/{project_id}/entries/{entry_id}/attachments/{att_id}/delete")
async def delete_attachment(
    project_id: int,
    entry_id:   int,
    att_id:     int,
    db:         Session = Depends(get_db),
    current_user        = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para eliminar archivos.")

    att = db.query(models.ProjectEntryAttachment).filter(
        models.ProjectEntryAttachment.id       == att_id,
        models.ProjectEntryAttachment.entry_id == entry_id,
        models.ProjectEntryAttachment.deleted_at == None,
    ).first()
    if not att:
        raise HTTPException(status_code=404, detail="Adjunto no encontrado.")

    # Borrar de Cloudinary primero; si falla, no modificamos la DB
    try:
        _cloud_delete(att.public_id)
    except Exception:
        pass  # si Cloudinary falla, igual soft-deleteamos el registro

    att.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(
        url=f"/projects/{project_id}/entries/{entry_id}", status_code=303
    )


# ── Tareas: helpers ───────────────────────────────────────────────────────────

def _get_task_or_404(db, project_id, task_id):
    t = db.query(models.ProjectTask).filter(
        models.ProjectTask.id         == task_id,
        models.ProjectTask.project_id == project_id,
        models.ProjectTask.deleted_at == None,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return t


def _clamp_progress(value: str) -> int:
    """Convierte string a int 0–100. Devuelve 0 si inválido."""
    try:
        n = int(value)
        return max(0, min(100, n))
    except (ValueError, TypeError):
        return 0


def _apply_task_status_rules(task, new_status: str, new_progress: int | None = None):
    """
    Reglas de negocio de estado <-> progreso (sincronizacion bidireccional, Opcion A).

    Orden de evaluacion:
      1. Si new_progress no es None -> asignarlo (ya debe venir clampado 0-100).
      2. Si new_status == "finalizada" O progress == 100 -> ambos se sincronizan:
         status = "finalizada", progress = 100, actual_end_date = hoy si estaba vacio.
      3. En cualquier otro caso -> status = new_status, actual_end_date se preserva.

    Invariante: no puede existir progress=100 con status!="finalizada" ni viceversa.
    """
    if new_progress is not None:
        task.progress_percent = new_progress

    if new_status == "finalizada" or task.progress_percent == 100:
        task.status           = "finalizada"
        task.progress_percent = 100
        if not task.actual_end_date:
            task.actual_end_date = date.today()
    else:
        task.status = new_status


def _task_form_ctx(project, task=None, error=None):
    return {
        "project":          project,
        "task":             task,
        "error":            error,
        "task_statuses":    TASK_STATUSES,
        "priorities":       PRIORITIES,
    }


# ── Tareas: nueva ─────────────────────────────────────────────────────────────

@router.get("/{project_id}/tasks/new", response_class=HTMLResponse)
async def new_task_form(
    project_id:   int,
    request:      Request,
    db:           Session = Depends(get_db),
    current_user           = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para crear tareas.")
    return templates.TemplateResponse(request, "projects/task_form.html",
                                      {"user": current_user,
                                       **_task_form_ctx(project)})


@router.post("/{project_id}/tasks/new")
async def create_task(
    project_id:        int,
    request:           Request,
    title:             str = Form(...),
    description:       str = Form(""),
    responsible:       str = Form(""),
    priority:          str = Form("media"),
    status:            str = Form("pendiente"),
    start_date:        str = Form(""),
    estimated_end_date:str = Form(""),
    actual_end_date:   str = Form(""),
    progress_percent:  str = Form("0"),
    db:                Session = Depends(get_db),
    current_user               = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para crear tareas.")

    if not title.strip():
        return templates.TemplateResponse(request, "projects/task_form.html", {
            "user": current_user,
            **_task_form_ctx(project, error="El título de la tarea es obligatorio."),
        })

    if priority not in PRIORITIES:
        priority = "media"
    if status not in TASK_STATUSES:
        status = "pendiente"

    task = models.ProjectTask(
        project_id         = project_id,
        title              = title.strip(),
        description        = description.strip() or None,
        responsible        = responsible.strip() or None,
        priority           = priority,
        status             = "pendiente",   # el helper fija el status definitivo
        start_date         = _parse_date(start_date).date() if _parse_date(start_date) else None,
        estimated_end_date = _parse_date(estimated_end_date).date() if _parse_date(estimated_end_date) else None,
        actual_end_date    = _parse_date(actual_end_date).date() if _parse_date(actual_end_date) else None,
        progress_percent   = 0,             # el helper fija el progreso definitivo
        created_by_id      = current_user.id,
    )
    _apply_task_status_rules(task, status, new_progress=_clamp_progress(progress_percent))

    db.add(task)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


# ── Tareas: editar ────────────────────────────────────────────────────────────

@router.get("/{project_id}/tasks/{task_id}/edit", response_class=HTMLResponse)
async def edit_task_form(
    project_id: int,
    task_id:    int,
    request:    Request,
    db:         Session = Depends(get_db),
    current_user         = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    task    = _get_task_or_404(db, project_id, task_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para editar tareas.")
    return templates.TemplateResponse(request, "projects/task_form.html",
                                      {"user": current_user,
                                       **_task_form_ctx(project, task=task)})


@router.post("/{project_id}/tasks/{task_id}/edit")
async def update_task(
    project_id:        int,
    task_id:           int,
    request:           Request,
    title:             str = Form(...),
    description:       str = Form(""),
    responsible:       str = Form(""),
    priority:          str = Form("media"),
    status:            str = Form("pendiente"),
    start_date:        str = Form(""),
    estimated_end_date:str = Form(""),
    actual_end_date:   str = Form(""),
    progress_percent:  str = Form("0"),
    db:                Session = Depends(get_db),
    current_user               = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    task    = _get_task_or_404(db, project_id, task_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para editar tareas.")

    if not title.strip():
        return templates.TemplateResponse(request, "projects/task_form.html", {
            "user": current_user,
            **_task_form_ctx(project, task=task,
                             error="El título de la tarea es obligatorio."),
        })

    if priority not in PRIORITIES:
        priority = "media"
    if status not in TASK_STATUSES:
        status = "pendiente"

    task.title              = title.strip()
    task.description        = description.strip() or None
    task.responsible        = responsible.strip() or None
    task.priority           = priority
    task.start_date         = _parse_date(start_date).date() if _parse_date(start_date) else None
    task.estimated_end_date = _parse_date(estimated_end_date).date() if _parse_date(estimated_end_date) else None

    # Fecha fin real: se respeta lo que venga del form; si viene vacío se mantiene la anterior
    parsed_actual = _parse_date(actual_end_date)
    if parsed_actual:
        task.actual_end_date = parsed_actual.date()

    _apply_task_status_rules(task, status, new_progress=_clamp_progress(progress_percent))

    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


# ── Tareas: cambio rápido de estado + progreso (desde detail.html) ────────────

@router.post("/{project_id}/tasks/{task_id}/status")
async def change_task_status(
    project_id:       int,
    task_id:          int,
    status:           str = Form(...),
    progress_percent: str = Form(""),
    db:               Session = Depends(get_db),
    current_user               = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    task    = _get_task_or_404(db, project_id, task_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos.")
    if status not in TASK_STATUSES:
        raise HTTPException(status_code=400, detail="Estado de tarea inválido.")

    # new_progress: None si el campo viene vacío (se preserva el actual antes de las reglas)
    new_prog = _clamp_progress(progress_percent) if progress_percent.strip() else None
    _apply_task_status_rules(task, status, new_progress=new_prog)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


# ── Tareas: eliminar (soft delete) ────────────────────────────────────────────

@router.post("/{project_id}/tasks/{task_id}/delete")
async def delete_task(
    project_id:  int,
    task_id:     int,
    db:          Session = Depends(get_db),
    current_user          = Depends(require_projects_access),
):
    project = _get_project_or_404(db, project_id)
    task    = _get_task_or_404(db, project_id, task_id)
    if not _can_edit(current_user, project):
        raise HTTPException(status_code=403, detail="Sin permisos para eliminar tareas.")
    task.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)
