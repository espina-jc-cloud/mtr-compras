from datetime import datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.deps import get_current_user, require_role
from app.permissions import require_perm
from app import models
from app.templates import templates

# Acceso al módulo Mantenimiento → permiso "mantenimiento.mantenimiento".
require_no_operador = require_perm("mantenimiento.mantenimiento")

try:
    from app.cloudinary_upload import upload_file as cloud_upload
    CLOUDINARY_AVAILABLE = True
except Exception:
    CLOUDINARY_AVAILABLE = False

router = APIRouter(prefix="/maintenance", dependencies=[Depends(require_no_operador)])

PLANTS = ["MTR1", "MTR2"]
AREAS = [
    "Producción", "Mantenimiento", "Logística", "Administración",
    "Seguridad", "Calidad", "Infraestructura", "General",
]
MAINTENANCE_TYPES = ["correctivo", "preventivo"]
STATUSES = ["abierto", "en_progreso", "cerrado"]
WORK_TYPE_CODES = {
    261: "Equipos móviles",
    262: "Máquina de coser, mezcladora, grampas",
    263: "Tolvas",
    264: "Cintas",
    265: "Sistema",
    266: "Mantenimiento general de planta",
}


def _qp(request: Request, name: str, default: str = "") -> str:
    """Devuelve el PRIMER valor del query param (evita duplicados desktop+mobile)."""
    values = request.query_params.getlist(name)
    return values[0].strip() if values else default


def _can_edit(user, record) -> bool:
    """¿Puede este usuario editar/borrar este registro?"""
    if user.role in ("admin", "superadmin"):
        return True
    # Autorizador puede editar registros de su planta
    if user.role == "autorizador":
        return user.plant == "TODAS" or record.plant == user.plant
    # Técnico solo puede editar lo que él mismo cargó
    if user.role == "tecnico" and record.entered_by_id == user.id:
        return True
    return False


@router.get("", response_class=HTMLResponse)
async def list_maintenance(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    q = _qp(request, "q")
    plant_f = _qp(request, "plant")
    status_f = _qp(request, "status")
    mtype_f = _qp(request, "mtype")
    date_from = _qp(request, "date_from")
    date_to = _qp(request, "date_to")

    rq = (
        db.query(models.MaintenanceRecord)
        .options(
            joinedload(models.MaintenanceRecord.equipment),
            joinedload(models.MaintenanceRecord.entered_by),
            joinedload(models.MaintenanceRecord.performer),
        )
        .filter(models.MaintenanceRecord.deleted_at.is_(None))
    )

    # Filtro por rol
    if current_user.role == "tecnico" and current_user.plant != "TODAS":
        rq = rq.filter(models.MaintenanceRecord.plant == current_user.plant)
    elif current_user.role == "planta":
        # planta puede ver registros de su planta
        rq = rq.filter(models.MaintenanceRecord.plant == current_user.plant)
    elif plant_f:
        rq = rq.filter(models.MaintenanceRecord.plant == plant_f)

    if status_f:
        rq = rq.filter(models.MaintenanceRecord.status == status_f)
    if mtype_f:
        rq = rq.filter(models.MaintenanceRecord.maintenance_type == mtype_f)
    if date_from:
        try:
            rq = rq.filter(models.MaintenanceRecord.work_date >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            rq = rq.filter(models.MaintenanceRecord.work_date <= datetime.strptime(date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass
    if q:
        like = f"%{q}%"
        rq = rq.filter(
            models.MaintenanceRecord.title.ilike(like)
            | models.MaintenanceRecord.description.ilike(like)
            | models.MaintenanceRecord.performed_by_text.ilike(like)
            | models.MaintenanceRecord.equipment_text.ilike(like)
            | models.MaintenanceRecord.location_text.ilike(like)
        )

    records = rq.order_by(models.MaintenanceRecord.work_date.desc()).limit(200).all()

    params = {
        "q": q, "plant": plant_f, "status": status_f,
        "mtype": mtype_f, "date_from": date_from, "date_to": date_to,
    }
    return templates.TemplateResponse(request, "maintenance/list.html", {
        "user": current_user,
        "records": records,
        "params": params,
        "plants": PLANTS,
        "statuses": STATUSES,
        "maintenance_types": MAINTENANCE_TYPES,
    })


@router.get("/new", response_class=HTMLResponse)
async def new_maintenance_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Técnico, admin, superadmin pueden crear
    if current_user.role == "planta":
        raise HTTPException(status_code=403, detail="Sin permisos para crear registros de mantenimiento")

    equipment = db.query(models.Equipment).filter(
        models.Equipment.active == True
    ).order_by(models.Equipment.plant, models.Equipment.code).all()

    users_list = db.query(models.User).filter(models.User.active == True).order_by(models.User.name).all()
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()

    today = datetime.utcnow().strftime("%Y-%m-%d")

    return templates.TemplateResponse(request, "maintenance/new.html", {
        "user": current_user,
        "equipment_list": equipment,
        "users_list": users_list,
        "suppliers": suppliers,
        "plants": PLANTS,
        "maintenance_types": MAINTENANCE_TYPES,
        "statuses": STATUSES,
        "work_type_codes": WORK_TYPE_CODES,
        "today": today,
        "error": None,
    })


@router.post("/new")
async def create_maintenance(
    request: Request,
    # Campos principales
    plant: str = Form(...),
    title: str = Form(...),
    work_date: str = Form(...),
    maintenance_type: str = Form("correctivo"),
    status: str = Form("cerrado"),
    # Equipo
    equipment_id: str = Form(""),
    equipment_text: str = Form(""),
    location_text: str = Form(""),
    work_type_code: str = Form(""),
    # Quién hizo el trabajo
    is_contractor: str = Form(""),
    performed_by_id: str = Form(""),
    performed_by_text: str = Form(""),
    contractor_company: str = Form(""),
    # Supervisión
    supervised_by_id: str = Form(""),
    # Descripción
    description: str = Form(""),
    did_lubrication: str = Form(""),
    did_cleaning: str = Form(""),
    # Costos
    hours_worked: str = Form(""),
    workers_count: str = Form(""),
    hourly_rate: str = Form(""),
    labor_cost: str = Form(""),
    parts_cost: str = Form(""),
    total_cost: str = Form(""),
    # Vinculación
    supplier_id: str = Form(""),
    # Archivo adjunto
    file: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.role == "planta":
        raise HTTPException(status_code=403)

    def _int_or_none(v):
        try:
            return int(str(v).strip()) if v and str(v).strip() else None
        except (ValueError, TypeError):
            return None

    def _dec_or_none(v):
        try:
            return float(str(v).strip().replace(",", ".")) if v and str(v).strip() else None
        except (ValueError, TypeError):
            return None

    try:
        wd = datetime.strptime(work_date.strip(), "%Y-%m-%d")
    except ValueError:
        wd = datetime.utcnow()

    record = models.MaintenanceRecord(
        plant=plant,
        title=title.strip(),
        work_date=wd,
        maintenance_type=maintenance_type,
        status=status,
        equipment_id=_int_or_none(equipment_id),
        equipment_text=equipment_text.strip() or None,
        location_text=location_text.strip() or None,
        work_type_code=_int_or_none(work_type_code),
        is_contractor=(is_contractor == "1"),
        performed_by_id=_int_or_none(performed_by_id),
        performed_by_text=performed_by_text.strip() or None,
        contractor_company=contractor_company.strip() or None,
        supervised_by_id=_int_or_none(supervised_by_id),
        description=description.strip() or None,
        did_lubrication=(did_lubrication == "1") if did_lubrication else None,
        did_cleaning=(did_cleaning == "1") if did_cleaning else None,
        hours_worked=_dec_or_none(hours_worked),
        workers_count=_int_or_none(workers_count) or 1,
        hourly_rate=_dec_or_none(hourly_rate),
        labor_cost=_dec_or_none(labor_cost),
        parts_cost=_dec_or_none(parts_cost),
        total_cost=_dec_or_none(total_cost),
        supplier_id=_int_or_none(supplier_id),
        entered_by_id=current_user.id,
    )
    db.add(record)
    db.flush()  # get record.id

    # Adjunto
    if file and file.filename:
        file_bytes = await file.read()
        if file_bytes:
            try:
                file_url = cloud_upload(file_bytes, file.filename, folder="mtr-mantenimiento")
            except Exception:
                file_url = ""  # no fallar por Cloudinary
            if file_url:
                doc = models.MaintenanceDocument(
                    record_id=record.id,
                    file_url=file_url,
                    filename=file.filename,
                    doc_type="foto",
                    uploaded_by_id=current_user.id,
                )
                db.add(doc)

    # Audit log
    log = models.MaintenanceAuditLog(
        record_id=record.id,
        user_id=current_user.id,
        action="created",
        comment=f"Registro creado con estado '{status}'",
    )
    db.add(log)
    db.commit()

    return RedirectResponse(url=f"/maintenance/{record.id}", status_code=303)


@router.get("/{record_id}", response_class=HTMLResponse)
async def maintenance_detail(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = (
        db.query(models.MaintenanceRecord)
        .options(
            joinedload(models.MaintenanceRecord.equipment),
            joinedload(models.MaintenanceRecord.entered_by),
            joinedload(models.MaintenanceRecord.performer),
            joinedload(models.MaintenanceRecord.supervised_by),
            joinedload(models.MaintenanceRecord.supplier),
            joinedload(models.MaintenanceRecord.purchase),
            joinedload(models.MaintenanceRecord.documents).joinedload(models.MaintenanceDocument.uploader),
            joinedload(models.MaintenanceRecord.audit_logs).joinedload(models.MaintenanceAuditLog.user),
        )
        .filter(models.MaintenanceRecord.id == record_id)
        .first()
    )
    if not record or record.deleted_at:
        raise HTTPException(status_code=404)

    # Restricción de visibilidad por rol
    if current_user.role in ("planta", "tecnico") and current_user.plant != "TODAS":
        if record.plant != current_user.plant:
            raise HTTPException(status_code=403)

    can_edit = _can_edit(current_user, record)

    return templates.TemplateResponse(request, "maintenance/detail.html", {
        "user": current_user,
        "record": record,
        "can_edit": can_edit,
        "work_type_codes": WORK_TYPE_CODES,
    })


@router.get("/{record_id}/edit", response_class=HTMLResponse)
async def edit_maintenance_form(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = db.query(models.MaintenanceRecord).filter(
        models.MaintenanceRecord.id == record_id,
        models.MaintenanceRecord.deleted_at.is_(None),
    ).first()
    if not record:
        raise HTTPException(status_code=404)
    if not _can_edit(current_user, record):
        raise HTTPException(status_code=403)

    equipment = db.query(models.Equipment).filter(models.Equipment.active == True).order_by(models.Equipment.code).all()
    users_list = db.query(models.User).filter(models.User.active == True).order_by(models.User.name).all()
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()

    return templates.TemplateResponse(request, "maintenance/edit.html", {
        "user": current_user,
        "record": record,
        "equipment_list": equipment,
        "users_list": users_list,
        "suppliers": suppliers,
        "plants": PLANTS,
        "maintenance_types": MAINTENANCE_TYPES,
        "statuses": STATUSES,
        "work_type_codes": WORK_TYPE_CODES,
        "error": None,
    })


@router.post("/{record_id}/edit")
async def update_maintenance(
    record_id: int,
    request: Request,
    plant: str = Form(...),
    title: str = Form(...),
    work_date: str = Form(...),
    maintenance_type: str = Form("correctivo"),
    status: str = Form("cerrado"),
    equipment_id: str = Form(""),
    equipment_text: str = Form(""),
    location_text: str = Form(""),
    work_type_code: str = Form(""),
    is_contractor: str = Form(""),
    performed_by_id: str = Form(""),
    performed_by_text: str = Form(""),
    contractor_company: str = Form(""),
    supervised_by_id: str = Form(""),
    description: str = Form(""),
    did_lubrication: str = Form(""),
    did_cleaning: str = Form(""),
    hours_worked: str = Form(""),
    workers_count: str = Form(""),
    hourly_rate: str = Form(""),
    labor_cost: str = Form(""),
    parts_cost: str = Form(""),
    total_cost: str = Form(""),
    supplier_id: str = Form(""),
    edit_reason: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = db.query(models.MaintenanceRecord).filter(
        models.MaintenanceRecord.id == record_id,
        models.MaintenanceRecord.deleted_at.is_(None),
    ).first()
    if not record:
        raise HTTPException(status_code=404)
    if not _can_edit(current_user, record):
        raise HTTPException(status_code=403)

    def _int_or_none(v):
        try:
            return int(str(v).strip()) if v and str(v).strip() else None
        except (ValueError, TypeError):
            return None

    def _dec_or_none(v):
        try:
            return float(str(v).strip().replace(",", ".")) if v and str(v).strip() else None
        except (ValueError, TypeError):
            return None

    try:
        wd = datetime.strptime(work_date.strip(), "%Y-%m-%d")
    except ValueError:
        wd = record.work_date

    old_status = record.status

    record.plant = plant
    record.title = title.strip()
    record.work_date = wd
    record.maintenance_type = maintenance_type
    record.status = status
    record.equipment_id = _int_or_none(equipment_id)
    record.equipment_text = equipment_text.strip() or None
    record.location_text = location_text.strip() or None
    record.work_type_code = _int_or_none(work_type_code)
    record.is_contractor = (is_contractor == "1")
    record.performed_by_id = _int_or_none(performed_by_id)
    record.performed_by_text = performed_by_text.strip() or None
    record.contractor_company = contractor_company.strip() or None
    record.supervised_by_id = _int_or_none(supervised_by_id)
    record.description = description.strip() or None
    record.did_lubrication = (did_lubrication == "1") if did_lubrication else None
    record.did_cleaning = (did_cleaning == "1") if did_cleaning else None
    record.hours_worked = _dec_or_none(hours_worked)
    record.workers_count = _int_or_none(workers_count) or 1
    record.hourly_rate = _dec_or_none(hourly_rate)
    record.labor_cost = _dec_or_none(labor_cost)
    record.parts_cost = _dec_or_none(parts_cost)
    record.total_cost = _dec_or_none(total_cost)
    record.supplier_id = _int_or_none(supplier_id)
    record.updated_at = datetime.utcnow()

    log = models.MaintenanceAuditLog(
        record_id=record.id,
        user_id=current_user.id,
        action="edited",
        comment=edit_reason.strip() or f"Estado: {old_status} → {status}",
    )
    db.add(log)
    db.commit()

    return RedirectResponse(url=f"/maintenance/{record_id}", status_code=303)


@router.post("/{record_id}/close")
async def close_maintenance(
    record_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = db.query(models.MaintenanceRecord).filter(
        models.MaintenanceRecord.id == record_id,
        models.MaintenanceRecord.deleted_at.is_(None),
    ).first()
    if not record:
        raise HTTPException(status_code=404)
    if not _can_edit(current_user, record):
        raise HTTPException(status_code=403)

    record.status = "cerrado"
    record.updated_at = datetime.utcnow()
    log = models.MaintenanceAuditLog(
        record_id=record.id,
        user_id=current_user.id,
        action="closed",
        comment="Registro cerrado",
    )
    db.add(log)
    db.commit()
    return RedirectResponse(url=f"/maintenance/{record_id}", status_code=303)


@router.post("/{record_id}/delete")
async def delete_maintenance(
    record_id: int,
    deleted_reason: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("superadmin")),
):
    record = db.query(models.MaintenanceRecord).filter(
        models.MaintenanceRecord.id == record_id
    ).first()
    if not record:
        raise HTTPException(status_code=404)

    record.deleted_at = datetime.utcnow()
    record.deleted_reason = deleted_reason.strip() or "Sin motivo indicado"
    log = models.MaintenanceAuditLog(
        record_id=record.id,
        user_id=current_user.id,
        action="deleted",
        comment=record.deleted_reason,
    )
    db.add(log)
    db.commit()
    return RedirectResponse(url="/maintenance", status_code=303)


@router.post("/{record_id}/documents")
async def upload_document(
    record_id: int,
    doc_type: str = Form("foto"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = db.query(models.MaintenanceRecord).filter(
        models.MaintenanceRecord.id == record_id,
        models.MaintenanceRecord.deleted_at.is_(None),
    ).first()
    if not record:
        raise HTTPException(status_code=404)

    if current_user.role in ("planta", "tecnico") and current_user.plant != "TODAS":
        if record.plant != current_user.plant:
            raise HTTPException(status_code=403)

    file_bytes = await file.read()
    if not file_bytes:
        return RedirectResponse(url=f"/maintenance/{record_id}", status_code=303)

    file_url = cloud_upload(file_bytes, file.filename, folder="mtr-mantenimiento")

    doc = models.MaintenanceDocument(
        record_id=record_id,
        file_url=file_url,
        filename=file.filename,
        doc_type=doc_type,
        uploaded_by_id=current_user.id,
    )
    db.add(doc)
    db.commit()
    return RedirectResponse(url=f"/maintenance/{record_id}", status_code=303)
