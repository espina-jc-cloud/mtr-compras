from datetime import datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.deps import get_current_user, require_role
from app import models
from app.templates import templates

router = APIRouter(prefix="/equipment")

PLANTS = ["MTR1", "MTR2"]
CATEGORIES = ["fijo", "flota", "infraestructura"]
WORK_TYPE_CODES = {
    261: "Equipos móviles",
    262: "Máquina de coser, mezcladora, grampas",
    263: "Tolvas",
    264: "Cintas",
    265: "Sistema",
    266: "Mantenimiento general de planta",
}


@router.get("", response_class=HTMLResponse)
async def list_equipment(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    q = request.query_params.get("q", "").strip()
    plant_f = request.query_params.get("plant", "")
    cat_f = request.query_params.get("category", "")

    eq_q = db.query(models.Equipment).filter(models.Equipment.active == True)

    # Técnico ve sólo su planta
    if current_user.role == "tecnico" and current_user.plant != "TODAS":
        eq_q = eq_q.filter(models.Equipment.plant == current_user.plant)
    elif plant_f:
        eq_q = eq_q.filter(models.Equipment.plant == plant_f)

    if cat_f:
        eq_q = eq_q.filter(models.Equipment.category == cat_f)
    if q:
        like = f"%{q}%"
        eq_q = eq_q.filter(
            models.Equipment.code.ilike(like) | models.Equipment.name.ilike(like)
        )

    equipment = eq_q.order_by(models.Equipment.plant, models.Equipment.code).all()

    params = {"q": q, "plant": plant_f, "category": cat_f}
    return templates.TemplateResponse(request, "equipment/list.html", {
        "user": current_user,
        "equipment": equipment,
        "params": params,
        "plants": PLANTS,
        "categories": CATEGORIES,
        "work_type_codes": WORK_TYPE_CODES,
    })


@router.get("/new", response_class=HTMLResponse)
async def new_equipment_form(
    request: Request,
    current_user=Depends(require_role("admin", "superadmin")),
):
    return templates.TemplateResponse(request, "equipment/new.html", {
        "user": current_user,
        "plants": PLANTS,
        "categories": CATEGORIES,
        "work_type_codes": WORK_TYPE_CODES,
        "error": None,
    })


@router.post("/new")
async def create_equipment(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    plant: str = Form(...),
    category: str = Form(""),
    work_type_code: str = Form(""),
    brand: str = Form(""),
    model_name: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    code = code.strip().upper()
    existing = db.query(models.Equipment).filter(models.Equipment.code == code).first()
    if existing:
        return templates.TemplateResponse(request, "equipment/new.html", {
            "user": current_user,
            "plants": PLANTS,
            "categories": CATEGORIES,
            "work_type_codes": WORK_TYPE_CODES,
            "error": f"Ya existe un equipo con código {code}",
        })

    wtc = None
    if work_type_code.strip():
        try:
            wtc = int(work_type_code.strip())
        except ValueError:
            pass

    eq = models.Equipment(
        code=code,
        name=name.strip(),
        plant=plant,
        category=category.strip() or None,
        work_type_code=wtc,
        brand=brand.strip() or None,
        model_name=model_name.strip() or None,
        notes=notes.strip() or None,
    )
    db.add(eq)
    db.commit()
    return RedirectResponse(url=f"/equipment/{eq.id}", status_code=303)


@router.get("/{equipment_id}", response_class=HTMLResponse)
async def equipment_detail(
    equipment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    eq = db.query(models.Equipment).filter(models.Equipment.id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404)

    records = (
        db.query(models.MaintenanceRecord)
        .filter(
            models.MaintenanceRecord.equipment_id == equipment_id,
            models.MaintenanceRecord.deleted_at.is_(None),
        )
        .order_by(models.MaintenanceRecord.work_date.desc())
        .limit(50)
        .all()
    )

    return templates.TemplateResponse(request, "equipment/detail.html", {
        "user": current_user,
        "equipment": eq,
        "records": records,
        "work_type_codes": WORK_TYPE_CODES,
    })


@router.get("/{equipment_id}/edit", response_class=HTMLResponse)
async def edit_equipment_form(
    equipment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    eq = db.query(models.Equipment).filter(models.Equipment.id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "equipment/edit.html", {
        "user": current_user,
        "equipment": eq,
        "plants": PLANTS,
        "categories": CATEGORIES,
        "work_type_codes": WORK_TYPE_CODES,
        "error": None,
    })


@router.post("/{equipment_id}/edit")
async def update_equipment(
    equipment_id: int,
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    plant: str = Form(...),
    category: str = Form(""),
    work_type_code: str = Form(""),
    brand: str = Form(""),
    model_name: str = Form(""),
    notes: str = Form(""),
    active: str = Form("on"),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    eq = db.query(models.Equipment).filter(models.Equipment.id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404)

    code = code.strip().upper()
    clash = db.query(models.Equipment).filter(
        models.Equipment.code == code,
        models.Equipment.id != equipment_id,
    ).first()
    if clash:
        return templates.TemplateResponse(request, "equipment/edit.html", {
            "user": current_user,
            "equipment": eq,
            "plants": PLANTS,
            "categories": CATEGORIES,
            "work_type_codes": WORK_TYPE_CODES,
            "error": f"Ya existe otro equipo con código {code}",
        })

    wtc = None
    if work_type_code.strip():
        try:
            wtc = int(work_type_code.strip())
        except ValueError:
            pass

    eq.code = code
    eq.name = name.strip()
    eq.plant = plant
    eq.category = category.strip() or None
    eq.work_type_code = wtc
    eq.brand = brand.strip() or None
    eq.model_name = model_name.strip() or None
    eq.notes = notes.strip() or None
    eq.active = (active == "on")
    db.commit()
    return RedirectResponse(url=f"/equipment/{equipment_id}", status_code=303)
