import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.deps import get_current_user, require_role, require_projects_access
from app import models
from app.templates import templates
from app.cloudinary_upload import upload_file as _cloud_upload, delete_file as _cloud_delete

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

    projects = query.order_by(models.Project.created_at.desc()).all()

    params = {
        "q": q, "status": status, "plant": plant,
        "priority": priority, "responsible": responsible,
    }

    return templates.TemplateResponse(request, "projects/list.html", {
        "user": current_user,
        "projects": projects,
        "params": params,
        "statuses": STATUSES,
        "plants": PLANTS,
        "priorities": PRIORITIES,
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

    return templates.TemplateResponse(request, "projects/detail.html", {
        "user":         current_user,
        "project":      project,
        "can_edit":     _can_edit(current_user, project),
        "statuses":     STATUSES,
        "task_statuses": TASK_STATUSES,
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
    return RedirectResponse(url="/projects", status_code=303)


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
    amount_usd:    str                  = Form(""),
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

    # ── Validación mínima: al menos un campo con contenido ───────────────────
    usd_dec  = _parse_decimal(amount_usd)
    rate_dec = _parse_decimal(exchange_rate)
    has_file     = file is not None and file.filename
    has_economic = any([
        description.strip(), supplier.strip(), usd_dec is not None, rate_dec is not None
    ])
    if not has_file:
        # Sin archivo no se crea adjunto. Evita registros vacíos o inválidos.
        return RedirectResponse(
            url=f"/projects/{project_id}/entries/{entry_id}", status_code=303
        )

    # ── Subir a Cloudinary solo si hay archivo ────────────────────────────────
    if has_file:
        contents    = await file.read()
        unique_name = f"{uuid.uuid4()}_{file.filename}"

        try:
            result = _cloud_upload(contents, unique_name, folder="mtr-compras/proyectos")
            file_url  = result["url"]
            public_id = result["public_id"]
            filename  = file.filename
        except Exception as e:
            print(f"[Cloudinary] Error subiendo adjunto: {e}")
            return RedirectResponse(
                url=f"/projects/{project_id}/entries/{entry_id}", status_code=303
            )
    else:
        file_url  = None
        public_id = None
        filename  = None

    # ── Campos económicos opcionales ──────────────────────────────────────────
    if usd_dec is not None and rate_dec is not None:
        ars_dec = (usd_dec * rate_dec).quantize(Decimal("0.01"))
    else:
        ars_dec = None

    att = models.ProjectEntryAttachment(
        entry_id       = entry_id,
        project_id     = project_id,
        file_type      = file_type,
        file_url       = file_url,
        public_id      = public_id,
        filename       = filename,
        description    = description.strip() or None,
        supplier       = supplier.strip()    or None,
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


def _apply_task_status_rules(task, new_status: str):
    """
    Reglas de negocio al cambiar estado:
    - Si pasa a 'finalizada' y actual_end_date está vacío → poner hoy.
    - Si sale de 'finalizada' → NO borrar actual_end_date.
    """
    task.status = new_status
    if new_status == "finalizada" and not task.actual_end_date:
        task.actual_end_date = date.today()


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
        status             = status,
        start_date         = _parse_date(start_date).date() if _parse_date(start_date) else None,
        estimated_end_date = _parse_date(estimated_end_date).date() if _parse_date(estimated_end_date) else None,
        actual_end_date    = _parse_date(actual_end_date).date() if _parse_date(actual_end_date) else None,
        progress_percent   = _clamp_progress(progress_percent),
        created_by_id      = current_user.id,
    )
    # Aplicar reglas de estado (ej: finalizada sin actual_end_date)
    _apply_task_status_rules(task, status)

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
    task.progress_percent   = _clamp_progress(progress_percent)

    # Fecha fin real: se respeta lo que venga del form; si está vacío se mantiene el anterior
    parsed_actual = _parse_date(actual_end_date)
    if parsed_actual:
        task.actual_end_date = parsed_actual.date()
    # (si viene vacío no tocamos actual_end_date — puede haberse seteado automáticamente antes)

    _apply_task_status_rules(task, status)

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

    if progress_percent.strip():
        task.progress_percent = _clamp_progress(progress_percent)

    _apply_task_status_rules(task, status)
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
