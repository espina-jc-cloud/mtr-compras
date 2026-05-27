"""
Módulo Combustible — MTR Gestión
Reemplaza el Google Form de carga de combustible.
"""
import re
import calendar
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.deps import get_current_user, require_role
from app import models
from app.templates import templates

try:
    from app.cloudinary_upload import upload_file as cloud_upload
    CLOUDINARY_AVAILABLE = True
except Exception:
    CLOUDINARY_AVAILABLE = False

router = APIRouter(prefix="/fuel")

FUEL_TYPES = {
    "gasoil_premium": "Gasoil Premium",
    "nafta":          "Nafta",
    "nafta_premium":  "Nafta Premium",
}
COMPANIES = ["MTR SA", "INGEE"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_vehicle_type(plate: str) -> str:
    p = plate.strip().upper()
    if p in ("BIDÓN", "BIDON", "BIDO", "BIDON 200L"):
        return "bidon"
    if re.match(r'^[A-Z]{2,3}\d{3}[A-Z]{0,2}$', p):
        return "vehiculo"
    return "equipo"


def _recent_plates(db: Session, limit: int = 12):
    cutoff = datetime.utcnow() - timedelta(days=90)
    rows = (
        db.query(models.FuelLoad.vehicle_plate, func.count().label("cnt"))
        .filter(models.FuelLoad.deleted_at.is_(None),
                models.FuelLoad.fuel_date >= cutoff)
        .group_by(models.FuelLoad.vehicle_plate)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )
    return [r.vehicle_plate for r in rows]


def _month_stats(db: Session, year: int, month: int) -> dict:
    first = datetime(year, month, 1)
    last  = datetime(year, month, calendar.monthrange(year, month)[1], 23, 59, 59)
    rows = (
        db.query(
            models.FuelLoad.company,
            func.sum(models.FuelLoad.liters).label("liters"),
            func.sum(models.FuelLoad.amount).label("amount"),
            func.count().label("count"),
        )
        .filter(models.FuelLoad.deleted_at.is_(None),
                models.FuelLoad.fuel_date.between(first, last))
        .group_by(models.FuelLoad.company)
        .all()
    )
    by_co = {c: {"liters": 0.0, "amount": 0.0, "count": 0} for c in COMPANIES}
    for r in rows:
        if r.company in by_co:
            by_co[r.company] = {
                "liters": float(r.liters or 0),
                "amount": float(r.amount or 0),
                "count":  r.count,
            }
    return {
        "by_company":    by_co,
        "total_liters":  sum(v["liters"] for v in by_co.values()),
        "total_amount":  sum(v["amount"] for v in by_co.values()),
        "total_count":   sum(v["count"]  for v in by_co.values()),
    }


def _parse_decimal(s: str):
    """Parses '50', '50.5', '50,5' → Decimal or None."""
    if not s or not s.strip():
        return None
    try:
        return Decimal(s.strip().replace(",", "."))
    except InvalidOperation:
        return None


def _find_duplicate(db: Session, plate: str, fuel_date: datetime, exclude_id: int = None):
    q = (
        db.query(models.FuelLoad)
        .filter(
            models.FuelLoad.vehicle_plate.ilike(plate.strip()),
            func.date(models.FuelLoad.fuel_date) == fuel_date.date(),
            models.FuelLoad.deleted_at.is_(None),
        )
    )
    if exclude_id:
        q = q.filter(models.FuelLoad.id != exclude_id)
    return q.first()


# ── LIST ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_fuel(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    def qp(name, default=""):
        vals = request.query_params.getlist(name)
        return vals[0].strip() if vals else default

    q_company   = qp("company")
    q_plate     = qp("plate")
    q_fuel_type = qp("fuel_type")
    q_date_from = qp("date_from")
    q_date_to   = qp("date_to")

    fq = db.query(models.FuelLoad).filter(models.FuelLoad.deleted_at.is_(None))

    if q_company:
        fq = fq.filter(models.FuelLoad.company == q_company)
    if q_plate:
        fq = fq.filter(models.FuelLoad.vehicle_plate.ilike(f"%{q_plate}%"))
    if q_fuel_type:
        fq = fq.filter(models.FuelLoad.fuel_type == q_fuel_type)
    if q_date_from:
        try:
            fq = fq.filter(models.FuelLoad.fuel_date >= datetime.strptime(q_date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if q_date_to:
        try:
            fq = fq.filter(models.FuelLoad.fuel_date <= datetime.strptime(q_date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass

    loads = fq.order_by(models.FuelLoad.fuel_date.desc()).limit(300).all()

    # Totales del filtro actual
    total_liters = sum(float(r.liters or 0) for r in loads)
    total_amount = sum(float(r.amount or 0) for r in loads)

    # Stats del mes actual
    now = datetime.utcnow()
    this_month = _month_stats(db, now.year, now.month)

    params = {
        "company": q_company, "plate": q_plate,
        "fuel_type": q_fuel_type, "date_from": q_date_from, "date_to": q_date_to,
    }

    return templates.TemplateResponse(request, "fuel/list.html", {
        "user":         current_user,
        "loads":        loads,
        "params":       params,
        "companies":    COMPANIES,
        "fuel_types":   FUEL_TYPES,
        "total_liters": total_liters,
        "total_amount": total_amount,
        "this_month":   this_month,
        "now":          now,
    })


# ── NEW FORM ──────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_fuel_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    today  = datetime.utcnow().strftime("%Y-%m-%d")
    plates = _recent_plates(db)

    return templates.TemplateResponse(request, "fuel/new.html", {
        "user":         current_user,
        "today":        today,
        "recent_plates": plates,
        "companies":    COMPANIES,
        "fuel_types":   FUEL_TYPES,
        "error":        None,
        "warning":      None,
        "prefill":      {},
    })


@router.post("/new")
async def create_fuel(
    request: Request,
    fuel_date:        str  = Form(...),
    company:          str  = Form(...),
    vehicle_plate:    str  = Form(...),
    fuel_type:        str  = Form(...),
    liters:           str  = Form(...),
    amount:           str  = Form(""),
    station:          str  = Form("Hipolito"),
    order_number:     str  = Form(""),
    responsible_text: str  = Form(""),
    odometer_km:      str  = Form(""),
    hourmeter:        str  = Form(""),
    notes:            str  = Form(""),
    force_save:       str  = Form(""),       # "1" si el usuario confirmó duplicado
    receipt:  UploadFile   = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plates = _recent_plates(db)

    def _err(msg, warn=None, prefill=None):
        return templates.TemplateResponse(request, "fuel/new.html", {
            "user": current_user,
            "today": fuel_date,
            "recent_plates": plates,
            "companies": COMPANIES,
            "fuel_types": FUEL_TYPES,
            "error": msg, "warning": warn,
            "prefill": prefill or {},
        })

    # Validaciones básicas
    vehicle_plate = vehicle_plate.strip().upper()
    if not vehicle_plate:
        return _err("La patente es obligatoria.")
    if company not in COMPANIES:
        return _err("Empresa inválida.")
    if fuel_type not in FUEL_TYPES:
        return _err("Tipo de combustible inválido.")

    liters_dec = _parse_decimal(liters)
    if not liters_dec or liters_dec <= 0:
        return _err("Litros debe ser un número positivo.")
    if liters_dec > 2000:
        return _err("Litros supera el límite de 2000L. Verificar el valor.")

    amount_dec = _parse_decimal(amount)
    if amount_dec is not None and amount_dec < 0:
        return _err("El monto no puede ser negativo.")

    try:
        fuel_dt = datetime.strptime(fuel_date, "%Y-%m-%d")
    except ValueError:
        return _err("Fecha inválida.")

    # Detección de duplicado
    if not force_save:
        dup = _find_duplicate(db, vehicle_plate, fuel_dt)
        if dup:
            prefill = {
                "fuel_date": fuel_date, "company": company,
                "vehicle_plate": vehicle_plate, "fuel_type": fuel_type,
                "liters": liters, "amount": amount, "station": station,
                "order_number": order_number, "responsible_text": responsible_text,
                "odometer_km": odometer_km, "hourmeter": hourmeter, "notes": notes,
            }
            warn = (
                f"Ya existe una carga para {vehicle_plate} el "
                f"{fuel_dt.strftime('%d/%m/%Y')} ({float(dup.liters):.1f}L). "
                f"Marcá la casilla para guardar de todas formas."
            )
            return _err(None, warn=warn, prefill=prefill)

    # Upload comprobante
    receipt_url = receipt_filename = None
    if receipt and receipt.filename and CLOUDINARY_AVAILABLE:
        content = await receipt.read()
        if content:
            try:
                res = cloud_upload(content, receipt.filename, folder="mtr-combustible")
                receipt_url      = res["url"]
                receipt_filename = receipt.filename
            except Exception:
                pass  # Comprobante falla silenciosamente — no bloquea el guardado

    resp_text = responsible_text.strip() or current_user.name

    load = models.FuelLoad(
        fuel_date        = fuel_dt,
        responsible_text = resp_text,
        entered_by_id    = current_user.id,
        vehicle_plate    = vehicle_plate,
        vehicle_type     = _infer_vehicle_type(vehicle_plate),
        fuel_type        = fuel_type,
        liters           = liters_dec,
        station          = station.strip() or "Hipolito",
        amount           = amount_dec,
        company          = company,
        order_number     = order_number.strip() or None,
        receipt_url      = receipt_url,
        receipt_filename = receipt_filename,
        odometer_km      = int(odometer_km) if odometer_km.strip().isdigit() else None,
        hourmeter        = _parse_decimal(hourmeter),
        notes            = notes.strip() or None,
    )
    db.add(load)
    db.commit()
    return RedirectResponse(url=f"/fuel/{load.id}?saved=1", status_code=303)


# ── DETAIL ────────────────────────────────────────────────────────────────────

@router.get("/{load_id}", response_class=HTMLResponse)
async def fuel_detail(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    load = db.query(models.FuelLoad).filter(
        models.FuelLoad.id == load_id,
        models.FuelLoad.deleted_at.is_(None),
    ).first()
    if not load:
        raise HTTPException(status_code=404)

    can_edit = current_user.role in ("admin", "superadmin") or load.entered_by_id == current_user.id
    saved    = request.query_params.get("saved") == "1"

    return templates.TemplateResponse(request, "fuel/detail.html", {
        "user":       current_user,
        "load":       load,
        "can_edit":   can_edit,
        "fuel_types": FUEL_TYPES,
        "saved":      saved,
    })


# ── EDIT ──────────────────────────────────────────────────────────────────────

@router.get("/{load_id}/edit", response_class=HTMLResponse)
async def edit_fuel_form(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    load = db.query(models.FuelLoad).filter(
        models.FuelLoad.id == load_id,
        models.FuelLoad.deleted_at.is_(None),
    ).first()
    if not load:
        raise HTTPException(status_code=404)

    if current_user.role not in ("admin", "superadmin") and load.entered_by_id != current_user.id:
        raise HTTPException(status_code=403)

    plates = _recent_plates(db)
    return templates.TemplateResponse(request, "fuel/edit.html", {
        "user":          current_user,
        "load":          load,
        "recent_plates": plates,
        "companies":     COMPANIES,
        "fuel_types":    FUEL_TYPES,
        "error":         None,
    })


@router.post("/{load_id}/edit")
async def update_fuel(
    load_id: int,
    request: Request,
    fuel_date:        str  = Form(...),
    company:          str  = Form(...),
    vehicle_plate:    str  = Form(...),
    fuel_type:        str  = Form(...),
    liters:           str  = Form(...),
    amount:           str  = Form(""),
    station:          str  = Form("Hipolito"),
    order_number:     str  = Form(""),
    responsible_text: str  = Form(""),
    odometer_km:      str  = Form(""),
    hourmeter:        str  = Form(""),
    notes:            str  = Form(""),
    receipt:  UploadFile   = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    load = db.query(models.FuelLoad).filter(
        models.FuelLoad.id == load_id,
        models.FuelLoad.deleted_at.is_(None),
    ).first()
    if not load:
        raise HTTPException(status_code=404)
    if current_user.role not in ("admin", "superadmin") and load.entered_by_id != current_user.id:
        raise HTTPException(status_code=403)

    vehicle_plate = vehicle_plate.strip().upper()
    liters_dec    = _parse_decimal(liters)
    amount_dec    = _parse_decimal(amount)

    if not liters_dec or liters_dec <= 0:
        raise HTTPException(status_code=422, detail="Litros inválidos")

    try:
        fuel_dt = datetime.strptime(fuel_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Fecha inválida")

    # Upload nuevo comprobante si se adjunta
    if receipt and receipt.filename and CLOUDINARY_AVAILABLE:
        content = await receipt.read()
        if content:
            try:
                res = cloud_upload(content, receipt.filename, folder="mtr-combustible")
                load.receipt_url      = res["url"]
                load.receipt_filename = receipt.filename
            except Exception:
                pass

    load.fuel_date        = fuel_dt
    load.responsible_text = responsible_text.strip() or current_user.name
    load.vehicle_plate    = vehicle_plate
    load.vehicle_type     = _infer_vehicle_type(vehicle_plate)
    load.fuel_type        = fuel_type
    load.liters           = liters_dec
    load.station          = station.strip() or "Hipolito"
    load.amount           = amount_dec
    load.company          = company
    load.order_number     = order_number.strip() or None
    load.odometer_km      = int(odometer_km) if odometer_km.strip().isdigit() else None
    load.hourmeter        = _parse_decimal(hourmeter)
    load.notes            = notes.strip() or None

    db.commit()
    return RedirectResponse(url=f"/fuel/{load_id}", status_code=303)


# ── DELETE (soft) ─────────────────────────────────────────────────────────────

@router.post("/{load_id}/delete")
async def delete_fuel(
    load_id: int,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("superadmin", "admin")),
):
    load = db.query(models.FuelLoad).filter(models.FuelLoad.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404)
    load.deleted_at     = datetime.utcnow()
    load.deleted_reason = reason.strip() or "Sin motivo"
    db.commit()
    return RedirectResponse(url="/fuel", status_code=303)
