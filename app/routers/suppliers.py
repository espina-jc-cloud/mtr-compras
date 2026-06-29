from datetime import datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, exists
from app.database import get_db
from app.deps import get_current_user, require_role
from app.permissions import require_perm
from app import models
from app.templates import templates

# Acceso al módulo Proveedores → permiso "compras.proveedores".
require_compras_access = require_perm("compras.proveedores")
require_no_operador = require_compras_access

router = APIRouter(prefix="/suppliers", dependencies=[Depends(require_no_operador)])

STATUSES = ["pendiente", "aprobada", "recibida", "facturada", "pagada", "rechazada", "cancelada"]


@router.get("", response_class=HTMLResponse)
async def list_suppliers(request: Request, db: Session = Depends(get_db), current_user=Depends(require_compras_access)):
    suppliers = db.query(models.Supplier).order_by(models.Supplier.name).all()
    return templates.TemplateResponse(request, "suppliers/list.html", {"user": current_user, "suppliers": suppliers})


@router.get("/new", response_class=HTMLResponse)
async def new_supplier_form(request: Request, current_user=Depends(require_role("admin", "superadmin"))):
    return templates.TemplateResponse(request, "suppliers/form.html", {"user": current_user, "supplier": None, "error": None})


@router.post("/new")
async def create_supplier(
    request: Request,
    name: str = Form(...),
    cuit: str = Form(""),
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    supplier = models.Supplier(name=name, cuit=cuit, contact_name=contact_name, contact_phone=contact_phone, email=email)
    db.add(supplier)
    db.commit()
    return RedirectResponse(url="/suppliers", status_code=303)


@router.get("/{supplier_id}", response_class=HTMLResponse)
async def supplier_account(
    supplier_id: int, request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    supplier = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404)

    date_from = request.query_params.get("date_from", "")
    date_to   = request.query_params.get("date_to", "")
    status_f  = request.query_params.get("status", "")
    q         = request.query_params.get("q", "").strip()

    pq = db.query(models.Purchase).options(
        joinedload(models.Purchase.requester),
        joinedload(models.Purchase.authorizer),
        joinedload(models.Purchase.documents),
    ).filter(models.Purchase.supplier_id == supplier_id)

    # Misma restricción por rol que la lista de compras
    if current_user.role == "planta":
        pq = pq.filter(models.Purchase.requested_by_id == current_user.id)
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        pq = pq.filter(models.Purchase.plant == current_user.plant)

    if date_from:
        try:
            pq = pq.filter(models.Purchase.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            pq = pq.filter(models.Purchase.created_at <= datetime.strptime(date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass
    if status_f:
        pq = pq.filter(models.Purchase.status == status_f)
    if q:
        like = f"%{q}%"
        doc_match = exists().where(and_(
            models.Document.purchase_id == models.Purchase.id,
            or_(
                models.Document.invoice_number.ilike(like),
                models.Document.filename.ilike(like),
            )
        ))
        pq = pq.filter(or_(
            models.Purchase.description.ilike(like),
            models.Purchase.reason.ilike(like),
            doc_match,
        ))

    purchases = pq.order_by(models.Purchase.created_at.desc()).all()

    # ── Cálculos financieros ─────────────────────────────────
    active = [p for p in purchases if p.status not in ("rechazada", "cancelada")]

    total_estimado  = sum(float(p.estimated_amount or 0) for p in active)
    total_facturado = 0.0
    total_pagado    = 0.0

    for p in purchases:
        facturas = [d for d in p.documents if d.doc_type == "factura"]
        inv = sum(float(d.invoice_amount or 0) for d in facturas)
        total_facturado += inv
        if p.status == "pagada":
            total_pagado += inv

    saldo_pendiente    = total_facturado - total_pagado
    compras_abiertas   = sum(1 for p in purchases if p.status in ("pendiente", "aprobada", "recibida", "facturada"))
    facturas_pendientes = sum(1 for p in purchases if p.status == "facturada")

    # ── Alertas ──────────────────────────────────────────────
    n_alerta_monto    = sum(1 for p in purchases if p.amount_alert)
    n_sin_remito      = sum(1 for p in purchases if p.status == "aprobada"
                            and not any(d.doc_type == "remito" for d in p.documents))
    n_sin_factura     = sum(1 for p in purchases if p.status == "recibida"
                            and not any(d.doc_type == "factura" for d in p.documents))

    params = {"date_from": date_from, "date_to": date_to, "status": status_f, "q": q}

    # Cotizaciones del proveedor (últimas 20)
    supplier_quotes = (
        db.query(models.Quote)
        .options(joinedload(models.Quote.supplier))
        .filter(models.Quote.supplier_id == supplier_id, models.Quote.deleted_at.is_(None))
        .order_by(models.Quote.quote_date.desc())
        .limit(20)
        .all()
    )

    now = datetime.utcnow()

    return templates.TemplateResponse(request, "suppliers/account.html", {
        "user": current_user,
        "supplier": supplier,
        "purchases": purchases,
        "params": params,
        "statuses": STATUSES,
        "total_estimado": total_estimado,
        "total_facturado": total_facturado,
        "total_pagado": total_pagado,
        "saldo_pendiente": saldo_pendiente,
        "compras_abiertas": compras_abiertas,
        "facturas_pendientes": facturas_pendientes,
        "n_alerta_monto": n_alerta_monto,
        "n_sin_remito": n_sin_remito,
        "n_sin_factura": n_sin_factura,
        "quotes": supplier_quotes,
        "now": now,
    })


@router.get("/{supplier_id}/edit", response_class=HTMLResponse)
async def edit_supplier_form(supplier_id: int, request: Request, db: Session = Depends(get_db), current_user=Depends(require_role("admin", "superadmin"))):
    supplier = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    return templates.TemplateResponse(request, "suppliers/form.html", {"user": current_user, "supplier": supplier, "error": None})


@router.post("/{supplier_id}/edit")
async def update_supplier(
    supplier_id: int,
    request: Request,
    name: str = Form(...),
    cuit: str = Form(""),
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
    email: str = Form(""),
    active: str = Form("on"),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    supplier = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    supplier.name = name
    supplier.cuit = cuit
    supplier.contact_name = contact_name
    supplier.contact_phone = contact_phone
    supplier.email = email
    supplier.active = (active == "on")
    db.commit()
    return RedirectResponse(url="/suppliers", status_code=303)
