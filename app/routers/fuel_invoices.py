from datetime import date
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, subqueryload
from sqlalchemy import or_

from app.database import get_db
from app.deps import get_current_user
from app import models
from app.templates import templates

try:
    from app.cloudinary_upload import upload_file as cloud_upload
    CLOUDINARY_AVAILABLE = True
except Exception:
    CLOUDINARY_AVAILABLE = False


router = APIRouter(prefix="/fuel/invoices")

TIPOS_COMPROBANTE = [
    "Factura A",
    "Factura B",
    "Factura C",
    "Nota de crédito A",
    "Nota de crédito B",
    "Nota de débito",
]


def _parse_date(value: Optional[str]):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_amount(value: Optional[str]):
    if not value:
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return None


def _fuel_invoice_query(db: Session, fecha_desde=None, fecha_hasta=None, company=None, busqueda=None):
    q = db.query(models.FuelInvoice).options(subqueryload(models.FuelInvoice.cargas))

    if fecha_desde:
        d = _parse_date(fecha_desde)
        if d:
            q = q.filter(models.FuelInvoice.fecha_emision >= d)

    if fecha_hasta:
        d = _parse_date(fecha_hasta)
        if d:
            q = q.filter(models.FuelInvoice.fecha_emision <= d)

    if company:
        q = q.filter(models.FuelInvoice.company == company)

    if busqueda:
        like = f"%{busqueda.strip()}%"
        q = q.filter(
            or_(
                models.FuelInvoice.numero_factura.ilike(like),
                models.FuelInvoice.supplier_name.ilike(like),
                models.FuelInvoice.cuit_proveedor.ilike(like),
            )
        )

    return q.order_by(models.FuelInvoice.created_at.desc())


def _free_fuel_loads_query(db: Session):
    return (
        db.query(models.FuelLoad)
        .filter(
            models.FuelLoad.deleted_at.is_(None),
            models.FuelLoad.fuel_invoice_id == None,
        )
        .order_by(models.FuelLoad.fuel_date.desc())
    )


def _invoice_metrics(invoices):
    total_facturado = sum(float(inv.monto_total or 0) for inv in invoices)
    cantidad = len(invoices)
    promedio = total_facturado / cantidad if cantidad else 0
    total_cargas = sum(len(inv.cargas or []) for inv in invoices)

    return {
        "total_facturado": total_facturado,
        "cantidad_facturas": cantidad,
        "promedio_factura": promedio,
        "total_cargas": total_cargas,
    }


def _loads_total(cargas):
    return sum(float(c.amount or 0) for c in cargas or [])


def _invoice_status(invoice):
    if not invoice.monto_total or not invoice.cargas:
        return "Pendiente"

    total_cargas = _loads_total(invoice.cargas)
    if round(float(invoice.monto_total or 0), 2) == round(total_cargas, 2):
        return "Conciliada"

    return "Diferencia"


@router.get("", response_class=HTMLResponse)
async def dashboard_fuel_facturas(
    request: Request,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    company: Optional[str] = None,
    busqueda: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoices = _fuel_invoice_query(db, fecha_desde, fecha_hasta, company, busqueda).all()
    metrics = _invoice_metrics(invoices)

    context = {
        "request": request,
        "user": current_user,
        "current_user": current_user,
        "invoices": invoices,
        "metrics": metrics,
        "params": {
            "fecha_desde": fecha_desde or "",
            "fecha_hasta": fecha_hasta or "",
            "company": company or "",
            "busqueda": busqueda or "",
        },
        "companies": ["MTR SA", "INGEE"],
        "loads_total": _loads_total,
        "invoice_status": _invoice_status,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "fuel/facturas/_resultados.html", context)

    return templates.TemplateResponse(request, "fuel/facturas/dashboard.html", context)


@router.get("/nueva", response_class=HTMLResponse)
async def nueva_fuel_factura_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    cargas = _free_fuel_loads_query(db).limit(300).all()

    return templates.TemplateResponse(request, "fuel/facturas/cargar.html", {
        "request": request,
        "user": current_user,
        "current_user": current_user,
        "cargas": cargas,
        "companies": ["MTR SA", "INGEE"],
        "tipos_comprobante": TIPOS_COMPROBANTE,
    })


@router.post("/nueva")
async def crear_fuel_factura(
    numero_factura: str = Form(""),
    company: str = Form(...),
    supplier_name: str = Form(""),
    tipo_comprobante: str = Form("Factura A"),
    fecha_emision: str = Form(""),
    monto_total: str = Form(""),
    cuit_proveedor: str = Form(""),
    observaciones: str = Form(""),
    carga_ids: List[int] = Form([]),
    archivo: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Upload archivo a Cloudinary
    archivo_url = archivo_nombre = archivo_public_id = None
    if archivo and archivo.filename and CLOUDINARY_AVAILABLE:
        content = await archivo.read()
        if content:
            try:
                res = cloud_upload(content, archivo.filename, folder="mtr-facturas-combustible")
                archivo_url = res["url"]
                archivo_public_id = res["public_id"]
                archivo_nombre = archivo.filename
            except Exception:
                pass

    invoice = models.FuelInvoice(
        numero_factura=numero_factura.strip() or None,
        company=company,
        supplier_name=supplier_name.strip() or None,
        tipo_comprobante=tipo_comprobante or "Factura A",
        fecha_emision=_parse_date(fecha_emision),
        monto_total=_parse_amount(monto_total),
        cuit_proveedor=cuit_proveedor.strip() or None,
        observaciones=observaciones.strip() or None,
        archivo_url=archivo_url,
        archivo_nombre=archivo_nombre,
        archivo_public_id=archivo_public_id,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    if carga_ids:
        cargas = (
            db.query(models.FuelLoad)
            .filter(
                models.FuelLoad.id.in_(carga_ids),
                models.FuelLoad.deleted_at.is_(None),
            )
            .all()
        )
        for carga in cargas:
            carga.fuel_invoice_id = invoice.id

    db.commit()
    return RedirectResponse(url="/fuel/invoices?ok=Factura+guardada", status_code=303)


@router.post("/{invoice_id}/eliminar")
async def eliminar_fuel_factura(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoice = db.query(models.FuelInvoice).filter(models.FuelInvoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura de combustible no encontrada.")

    db.query(models.FuelLoad).filter(models.FuelLoad.fuel_invoice_id == invoice.id).update(
        {"fuel_invoice_id": None},
        synchronize_session=False,
    )

    db.delete(invoice)
    db.commit()

    return RedirectResponse(url="/fuel/invoices?ok=Factura+eliminada", status_code=303)


@router.get("/{invoice_id}/editar", response_class=HTMLResponse)
async def editar_fuel_factura_form(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoice = db.query(models.FuelInvoice).filter(models.FuelInvoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura de combustible no encontrada.")

    # Cargas ya vinculadas a ESTA factura + cargas libres (para vincular).
    vinculadas = (
        db.query(models.FuelLoad)
        .filter(models.FuelLoad.fuel_invoice_id == invoice_id,
                models.FuelLoad.deleted_at.is_(None))
        .order_by(models.FuelLoad.fuel_date.desc())
        .all()
    )
    libres = _free_fuel_loads_query(db).limit(400).all()

    return templates.TemplateResponse(request, "fuel/facturas/editar.html", {
        "request": request, "user": current_user, "current_user": current_user,
        "invoice": invoice, "vinculadas": vinculadas, "libres": libres,
        "companies": ["MTR SA", "INGEE"], "tipos_comprobante": TIPOS_COMPROBANTE,
    })


@router.post("/{invoice_id}/editar")
async def editar_fuel_factura(
    invoice_id: int,
    numero_factura: str = Form(""),
    company: str = Form(...),
    supplier_name: str = Form(""),
    tipo_comprobante: str = Form("Factura A"),
    fecha_emision: str = Form(""),
    monto_total: str = Form(""),
    cuit_proveedor: str = Form(""),
    observaciones: str = Form(""),
    carga_ids: List[int] = Form([]),
    archivo: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoice = db.query(models.FuelInvoice).filter(models.FuelInvoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura de combustible no encontrada.")

    invoice.numero_factura   = numero_factura.strip() or None
    invoice.company          = company
    invoice.supplier_name    = supplier_name.strip() or None
    invoice.tipo_comprobante = tipo_comprobante or "Factura A"
    invoice.fecha_emision    = _parse_date(fecha_emision)
    invoice.monto_total      = _parse_amount(monto_total)
    invoice.cuit_proveedor   = cuit_proveedor.strip() or None
    invoice.observaciones    = observaciones.strip() or None

    # Reemplazar archivo si se sube uno nuevo.
    if archivo and archivo.filename and CLOUDINARY_AVAILABLE:
        content = await archivo.read()
        if content:
            try:
                res = cloud_upload(content, archivo.filename, folder="mtr-facturas-combustible")
                invoice.archivo_url = res["url"]
                invoice.archivo_public_id = res["public_id"]
                invoice.archivo_nombre = archivo.filename
            except Exception:
                pass

    # Reconciliar vínculos: las tildadas quedan en esta factura; las que estaban
    # y se destildaron se liberan. Se pueden sumar cargas libres o ya vinculadas.
    seleccion = set(carga_ids or [])
    # Desvincular las que estaban en esta factura y ya no están tildadas.
    for carga in db.query(models.FuelLoad).filter(
            models.FuelLoad.fuel_invoice_id == invoice_id).all():
        if carga.id not in seleccion:
            carga.fuel_invoice_id = None
    # Vincular las tildadas (libres o de otra factura → se mueven a ésta).
    if seleccion:
        for carga in db.query(models.FuelLoad).filter(
                models.FuelLoad.id.in_(seleccion),
                models.FuelLoad.deleted_at.is_(None)).all():
            carga.fuel_invoice_id = invoice_id

    db.commit()
    return RedirectResponse(url="/fuel/invoices?ok=Factura+actualizada", status_code=303)


@router.post("/{invoice_id}/desvincular-carga/{load_id}", response_class=HTMLResponse)
async def desvincular_carga(
    invoice_id: int,
    load_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    carga = (
        db.query(models.FuelLoad)
        .filter(
            models.FuelLoad.id == load_id,
            models.FuelLoad.fuel_invoice_id == invoice_id,
            models.FuelLoad.deleted_at.is_(None),
        )
        .first()
    )
    if not carga:
        raise HTTPException(status_code=404, detail="Carga no encontrada.")

    carga.fuel_invoice_id = None
    db.commit()

    return HTMLResponse("")
