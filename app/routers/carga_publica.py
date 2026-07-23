"""
Carga pública de combustible (sin login).

Link libre para que los operarios carguen su combustible desde el celular,
sacándole una foto al remito. No expone datos: solo permite crear una carga
que aparece en el módulo Combustible. Se atribuye a un usuario de sistema
inactivo (no puede loguearse).
"""
import re
import secrets
from datetime import datetime

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from fastapi.responses import JSONResponse

from app.database import get_db
from app import models
from app.auth import hash_password
from app.templates import templates
from app import fuel_ocr
from app.routers.fuel import (
    COMPANIES, PLANTS, FUEL_TYPES, _infer_vehicle_type,
    CLOUDINARY_AVAILABLE, cloud_upload, _parse_decimal,
)

router = APIRouter(prefix="/carga")

_SISTEMA_EMAIL = "carga-web@sistema.local"


def _sistema_user(db: Session) -> models.User:
    """Usuario de sistema (inactivo) al que se atribuyen las cargas públicas."""
    u = db.query(models.User).filter(models.User.email == _SISTEMA_EMAIL).first()
    if not u:
        u = models.User(
            name="Carga Web (operarios)", email=_SISTEMA_EMAIL,
            hashed_password=hash_password(secrets.token_hex(24)),
            role="planta", plant="TODAS", active=False,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
    return u


@router.get("", response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse(request, "carga_publica.html", {
        "companies": COMPANIES, "plants": PLANTS, "fuel_types": FUEL_TYPES,
        "error": None, "prefill": {}, "ocr_on": fuel_ocr.available(),
    })


@router.post("/leer-remito")
async def leer_remito(receipt: UploadFile = File(None)):
    """Lee la foto del remito y devuelve los campos detectados (JSON).
    Público: no guarda nada, solo pre-llena el form del operario."""
    if not receipt or not receipt.filename:
        return JSONResponse({"ok": False, "fields": {}})
    content = await receipt.read()
    if not content or len(content) > 12_000_000:   # límite 12MB
        return JSONResponse({"ok": False, "fields": {}})
    fields = fuel_ocr.parse_remito(content, receipt.filename)
    return JSONResponse({"ok": bool(fields), "fields": fields})


@router.post("")
async def submit(
    request: Request,
    responsible_text: str = Form(""),
    company:          str = Form(""),
    plant:            str = Form(""),
    vehicle_plate:    str = Form(""),
    fuel_type:        str = Form("gasoil_comun"),
    liters:           str = Form(""),
    amount:           str = Form(""),
    notes:            str = Form(""),
    website:          str = Form(""),      # honeypot anti-bots
    receipt:  UploadFile  = File(None),
    db: Session = Depends(get_db),
):
    # Bot: si el honeypot viene lleno, fingir éxito sin guardar.
    if website.strip():
        return RedirectResponse(url="/carga/listo", status_code=303)

    def _err(msg):
        return templates.TemplateResponse(request, "carga_publica.html", {
            "companies": COMPANIES, "plants": PLANTS, "fuel_types": FUEL_TYPES,
            "error": msg,
            "prefill": {
                "responsible_text": responsible_text, "company": company,
                "plant": plant, "vehicle_plate": vehicle_plate,
                "fuel_type": fuel_type, "liters": liters, "amount": amount,
                "notes": notes,
            },
        }, status_code=422)

    nombre = responsible_text.strip()
    plate  = vehicle_plate.strip().upper()
    if not nombre:
        return _err("Poné tu nombre.")
    if not plate:
        return _err("Poné la patente o equipo.")
    if company not in COMPANIES:
        return _err("Elegí la empresa.")
    if fuel_type not in FUEL_TYPES:
        fuel_type = "gasoil_comun"

    liters_dec = _parse_decimal(liters)
    if not liters_dec or liters_dec <= 0:
        return _err("Poné los litros cargados (número mayor a 0).")
    if liters_dec > 2000:
        return _err("Los litros superan 2000. Revisá el valor.")

    amount_dec = _parse_decimal(amount)
    if amount_dec is not None and amount_dec < 0:
        amount_dec = None

    # Foto del remito → Cloudinary
    receipt_url = receipt_filename = None
    if receipt and receipt.filename and CLOUDINARY_AVAILABLE:
        content = await receipt.read()
        if content:
            try:
                res = cloud_upload(content, receipt.filename, folder="mtr-combustible")
                receipt_url      = res["url"]
                receipt_filename = receipt.filename
            except Exception:
                pass  # la foto no bloquea la carga

    plant_val = plant.strip() if company == "MTR SA" and plant.strip() in PLANTS else None

    load = models.FuelLoad(
        fuel_date        = datetime.utcnow(),
        responsible_text = nombre,
        entered_by_id    = _sistema_user(db).id,
        vehicle_plate    = plate,
        vehicle_type     = _infer_vehicle_type(plate),
        fuel_type        = fuel_type,
        liters           = liters_dec,
        station          = "—",
        amount           = amount_dec,
        company          = company,
        plant            = plant_val,
        receipt_url      = receipt_url,
        receipt_filename = receipt_filename,
        notes            = (notes.strip() or None),
    )
    db.add(load)
    db.commit()
    return RedirectResponse(url="/carga/listo", status_code=303)


@router.get("/listo", response_class=HTMLResponse)
async def listo(request: Request):
    return templates.TemplateResponse(request, "carga_publica_ok.html", {})
