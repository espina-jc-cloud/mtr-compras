from datetime import date
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from app.database import get_db
from app.deps import get_current_user
from app.permissions import require_perm
from app import models
from app.templates import templates
from app.cloudinary_upload import upload_factura_file, delete_factura_file


router = APIRouter(prefix="/compras/facturas", dependencies=[Depends(require_perm("compras.facturas"))])


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


def _invoice_query(db: Session, fecha_desde=None, fecha_hasta=None, proveedor_id=None, busqueda=None):
    q = db.query(models.Invoice).options(
        joinedload(models.Invoice.supplier),
        joinedload(models.Invoice.remitos).joinedload(models.Document.purchase).joinedload(models.Purchase.supplier),
    )

    if fecha_desde:
        d = _parse_date(fecha_desde)
        if d:
            q = q.filter(models.Invoice.fecha_emision >= d)

    if fecha_hasta:
        d = _parse_date(fecha_hasta)
        if d:
            q = q.filter(models.Invoice.fecha_emision <= d)

    if proveedor_id:
        try:
            q = q.filter(models.Invoice.supplier_id == int(proveedor_id))
        except ValueError:
            pass

    if busqueda:
        like = f"%{busqueda.strip()}%"
        q = q.join(models.Supplier).filter(
            or_(
                models.Invoice.numero_factura.ilike(like),
                models.Supplier.name.ilike(like),
                models.Invoice.cuit_proveedor.ilike(like),
            )
        )

    return q.order_by(models.Invoice.created_at.desc())


def _invoice_metrics(invoices):
    total_facturado = sum(float(inv.monto_total or 0) for inv in invoices)
    cantidad = len(invoices)
    promedio = total_facturado / cantidad if cantidad else 0
    total_remitos = sum(len(inv.remitos or []) for inv in invoices)

    return {
        "total_facturado": total_facturado,
        "cantidad_facturas": cantidad,
        "promedio_factura": promedio,
        "total_remitos": total_remitos,
    }


def _free_remitos_query(db: Session):
    return (
        db.query(models.Document)
        .options(joinedload(models.Document.purchase).joinedload(models.Purchase.supplier))
        .filter(
            models.Document.doc_type == "remito",
            models.Document.factura_id == None,
        )
        .order_by(models.Document.uploaded_at.desc())
    )


@router.get("", response_class=HTMLResponse)
async def dashboard_facturas(
    request: Request,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    proveedor_id: Optional[str] = None,
    busqueda: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoices = _invoice_query(db, fecha_desde, fecha_hasta, proveedor_id, busqueda).all()
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    metrics = _invoice_metrics(invoices)

    context = {
        "request": request,
        "current_user": current_user,
        "invoices": invoices,
        "suppliers": suppliers,
        "metrics": metrics,
        "params": {
            "fecha_desde": fecha_desde or "",
            "fecha_hasta": fecha_hasta or "",
            "proveedor_id": proveedor_id or "",
            "busqueda": busqueda or "",
        },
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "facturas/_resultados.html", context)

    return templates.TemplateResponse(request, "facturas/dashboard.html", context)


@router.get("/nueva", response_class=HTMLResponse)
async def nueva_factura_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    remitos = _free_remitos_query(db).all()

    return templates.TemplateResponse(
        request,
        "facturas/cargar.html",
        {
            "request": request,
            "current_user": current_user,
            "suppliers": suppliers,
            "remitos": remitos,
            "tipos_comprobante": TIPOS_COMPROBANTE,
        },
    )


@router.post("/nueva")
async def crear_factura(
    numero_factura: str = Form(""),
    proveedor_id: int = Form(...),
    tipo_comprobante: str = Form("Factura A"),
    fecha_emision: str = Form(""),
    monto_total: str = Form(""),
    cuit_proveedor: str = Form(""),
    observaciones: str = Form(""),
    remito_ids: List[int] = Form([]),
    archivo: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    supplier = db.query(models.Supplier).filter(models.Supplier.id == proveedor_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    uploaded = await upload_factura_file(archivo)

    invoice = models.Invoice(
        numero_factura=numero_factura.strip() or None,
        supplier_id=proveedor_id,
        tipo_comprobante=tipo_comprobante or "Factura A",
        fecha_emision=_parse_date(fecha_emision),
        monto_total=_parse_amount(monto_total),
        cuit_proveedor=cuit_proveedor.strip() or supplier.cuit,
        observaciones=observaciones.strip() or None,
        archivo_url=uploaded["url"] if uploaded else None,
        archivo_nombre=uploaded["filename"] if uploaded else None,
        archivo_public_id=uploaded["public_id"] if uploaded else None,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    if remito_ids:
        remitos = (
            db.query(models.Document)
            .filter(
                models.Document.id.in_(remito_ids),
                models.Document.doc_type == "remito",
            )
            .all()
        )
        for remito in remitos:
            remito.factura_id = invoice.id

    db.commit()
    return RedirectResponse(url="/compras/facturas?ok=Factura+guardada", status_code=303)


@router.get("/{invoice_id}/editar", response_class=HTMLResponse)
async def editar_factura_form(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoice = (
        db.query(models.Invoice)
        .options(
            joinedload(models.Invoice.supplier),
            joinedload(models.Invoice.remitos).joinedload(models.Document.purchase).joinedload(models.Purchase.supplier),
        )
        .filter(models.Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")

    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    remitos_libres = _free_remitos_query(db).all()

    return templates.TemplateResponse(
        request,
        "facturas/editar.html",
        {
            "request": request,
            "current_user": current_user,
            "invoice": invoice,
            "suppliers": suppliers,
            "remitos_libres": remitos_libres,
            "tipos_comprobante": TIPOS_COMPROBANTE,
        },
    )


@router.post("/{invoice_id}/editar")
async def editar_factura(
    invoice_id: int,
    numero_factura: str = Form(""),
    proveedor_id: int = Form(...),
    tipo_comprobante: str = Form("Factura A"),
    fecha_emision: str = Form(""),
    monto_total: str = Form(""),
    cuit_proveedor: str = Form(""),
    observaciones: str = Form(""),
    remito_ids: List[int] = Form([]),
    quitar_archivo: Optional[str] = Form(None),
    archivo: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")

    supplier = db.query(models.Supplier).filter(models.Supplier.id == proveedor_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    invoice.numero_factura = numero_factura.strip() or None
    invoice.supplier_id = proveedor_id
    invoice.tipo_comprobante = tipo_comprobante or "Factura A"
    invoice.fecha_emision = _parse_date(fecha_emision)
    invoice.monto_total = _parse_amount(monto_total)
    invoice.cuit_proveedor = cuit_proveedor.strip() or supplier.cuit
    invoice.observaciones = observaciones.strip() or None

    if quitar_archivo and invoice.archivo_public_id:
        delete_factura_file(invoice.archivo_public_id)
        invoice.archivo_url = None
        invoice.archivo_nombre = None
        invoice.archivo_public_id = None

    uploaded = await upload_factura_file(archivo)
    if uploaded:
        if invoice.archivo_public_id:
            delete_factura_file(invoice.archivo_public_id)
        invoice.archivo_url = uploaded["url"]
        invoice.archivo_nombre = uploaded["filename"]
        invoice.archivo_public_id = uploaded["public_id"]

    if remito_ids:
        remitos = (
            db.query(models.Document)
            .filter(
                models.Document.id.in_(remito_ids),
                models.Document.doc_type == "remito",
            )
            .all()
        )
        for remito in remitos:
            remito.factura_id = invoice.id

    db.commit()
    return RedirectResponse(url="/compras/facturas?ok=Cambios+guardados", status_code=303)


@router.post("/{invoice_id}/eliminar")
async def eliminar_factura(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")

    db.query(models.Document).filter(models.Document.factura_id == invoice.id).update(
        {"factura_id": None},
        synchronize_session=False,
    )

    if invoice.archivo_public_id:
        delete_factura_file(invoice.archivo_public_id)

    db.delete(invoice)
    db.commit()

    return RedirectResponse(url="/compras/facturas?ok=Factura+eliminada", status_code=303)


@router.post("/{invoice_id}/desvincular-remito/{remito_id}", response_class=HTMLResponse)
async def desvincular_remito(
    invoice_id: int,
    remito_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    remito = (
        db.query(models.Document)
        .filter(
            models.Document.id == remito_id,
            models.Document.factura_id == invoice_id,
            models.Document.doc_type == "remito",
        )
        .first()
    )
    if not remito:
        raise HTTPException(status_code=404, detail="Remito no encontrado.")

    remito.factura_id = None
    db.commit()

    return HTMLResponse("")

import csv
import io
from datetime import date as _date
from fastapi.responses import StreamingResponse


@router.get("/exportar-csv")
async def exportar_facturas_csv(
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    proveedor_id: Optional[str] = None,
    busqueda: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    invoices = _invoice_query(db, fecha_desde, fecha_hasta, proveedor_id, busqueda).all()

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)

    writer.writerow([
        "Nro. Factura",
        "Proveedor",
        "CUIT",
        "Tipo Comprobante",
        "Fecha Emisión",
        "Monto Total",
        "Cant. Remitos",
        "Total Remitos",
        "Estado",
        "Observaciones",
    ])

    for inv in invoices:
        total_remitos = sum(float(r.invoice_amount or 0) for r in (inv.remitos or []))
        cant_remitos = len(inv.remitos or [])
        monto_total = float(inv.monto_total or 0)

        if not inv.monto_total or cant_remitos == 0:
            estado = "Pendiente"
        elif round(monto_total, 2) == round(total_remitos, 2):
            estado = "Conciliada"
        else:
            estado = "Diferencia"

        writer.writerow([
            inv.numero_factura or "",
            inv.supplier.name if inv.supplier else "",
            inv.cuit_proveedor or (inv.supplier.cuit if inv.supplier else ""),
            inv.tipo_comprobante or "",
            inv.fecha_emision.isoformat() if inv.fecha_emision else "",
            monto_total,
            cant_remitos,
            total_remitos,
            estado,
            inv.observaciones or "",
        ])

    output.seek(0)
    filename = f"facturas_{_date.today().isoformat()}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
