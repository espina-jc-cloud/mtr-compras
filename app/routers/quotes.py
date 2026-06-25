import uuid
from datetime import datetime, date
from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_
from app.database import get_db
from app.deps import get_current_user, require_role
from app.permissions import require_perm
from app import models
from app.templates import templates
from app.cloudinary_upload import upload_file

# Acceso al módulo Cotizaciones → permiso "compras.cotizaciones".
require_compras_access = require_perm("compras.cotizaciones")
require_no_operador = require_compras_access

router = APIRouter(prefix="/quotes", dependencies=[Depends(require_no_operador)])

AREAS = ["Mantenimiento", "Producción", "Logística", "Administración", "Seguridad", "Limpieza", "Otros"]
PLANTS = ["MTR1", "MTR2"]
QUOTE_STATUSES = ["borrador", "recibida", "aprobada", "rechazada", "vencida", "convertida_en_compra"]


def add_quote_audit(db: Session, quote_id: int, user_id: int, action: str, comment: str = ""):
    db.add(models.QuoteAuditLog(
        quote_id=quote_id, user_id=user_id, action=action, comment=comment
    ))


def _qp(request: Request, name: str) -> str:
    """Devuelve el primer valor no-vacío del query param (maneja duplicados desktop/mobile)."""
    values = request.query_params.getlist(name)
    return next((v for v in values if v), "")


def get_filter_params(request: Request) -> dict:
    return {
        "q":               _qp(request, "q"),
        "plant":           _qp(request, "plant"),
        "status":          _qp(request, "status"),
        "supplier_id":     _qp(request, "supplier_id"),
        "date_from":       _qp(request, "date_from"),
        "date_to":         _qp(request, "date_to"),
        "valid_from":      _qp(request, "valid_from"),
        "valid_to":        _qp(request, "valid_to"),
        "only_expired":    _qp(request, "only_expired"),
        "no_purchase":     _qp(request, "no_purchase"),
        "converted":       _qp(request, "converted"),
        "include_deleted": _qp(request, "include_deleted"),
    }


def build_query(db: Session, current_user, params: dict):
    q = db.query(models.Quote).options(
        joinedload(models.Quote.supplier),
        joinedload(models.Quote.requester),
        joinedload(models.Quote.documents),
        joinedload(models.Quote.items),
    )

    # Ocultar eliminadas por defecto
    if params.get("include_deleted") != "1" or current_user.role not in ("admin", "superadmin", "superadmin"):
        q = q.filter(models.Quote.deleted_at == None)

    # Restricción por rol
    if current_user.role == "planta":
        q = q.filter(models.Quote.requested_by_id == current_user.id)
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        q = q.filter(models.Quote.plant == current_user.plant)

    # Filtros estructurados
    if params.get("plant"):
        q = q.filter(models.Quote.plant == params["plant"])
    if params.get("status"):
        q = q.filter(models.Quote.status == params["status"])
    if params.get("supplier_id"):
        try:
            q = q.filter(models.Quote.supplier_id == int(params["supplier_id"]))
        except ValueError:
            pass

    # Filtros de fecha de cotización
    if params.get("date_from"):
        try:
            q = q.filter(models.Quote.quote_date >= datetime.strptime(params["date_from"], "%Y-%m-%d"))
        except ValueError:
            pass
    if params.get("date_to"):
        try:
            q = q.filter(models.Quote.quote_date <= datetime.strptime(params["date_to"] + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass

    # Filtros de vencimiento
    if params.get("valid_from"):
        try:
            q = q.filter(models.Quote.valid_until >= datetime.strptime(params["valid_from"], "%Y-%m-%d"))
        except ValueError:
            pass
    if params.get("valid_to"):
        try:
            q = q.filter(models.Quote.valid_until <= datetime.strptime(params["valid_to"] + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass

    # Checkboxes especiales
    now = datetime.utcnow()
    if params.get("only_expired") == "1":
        q = q.filter(models.Quote.valid_until < now, models.Quote.status != "convertida_en_compra")
    if params.get("no_purchase") == "1":
        q = q.filter(models.Quote.purchase_id == None, models.Quote.status != "rechazada")
    if params.get("converted") == "1":
        q = q.filter(models.Quote.status == "convertida_en_compra")

    # Búsqueda de texto libre
    text = params.get("q", "").strip()
    if text:
        like = f"%{text}%"
        q = q.join(models.Supplier, models.Quote.supplier_id == models.Supplier.id, isouter=True)
        q = q.filter(or_(
            models.Quote.title.ilike(like),
            models.Quote.description.ilike(like),
            models.Quote.supplier_name_text.ilike(like),
            models.Supplier.name.ilike(like),
        ))

    return q.order_by(models.Quote.quote_date.desc())


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_quotes(request: Request, db: Session = Depends(get_db), current_user=Depends(require_compras_access)):
    params = get_filter_params(request)
    quotes = build_query(db, current_user, params).limit(200).all()
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()

    now = datetime.utcnow()
    is_htmx = request.headers.get("HX-Request")
    if is_htmx:
        return templates.TemplateResponse(request, "quotes/partials/results.html", {
            "user": current_user, "quotes": quotes, "params": params, "now": now,
        })
    return templates.TemplateResponse(request, "quotes/list.html", {
        "user": current_user,
        "quotes": quotes, "suppliers": suppliers,
        "params": params, "statuses": QUOTE_STATUSES, "now": now,
    })


# ── New ──────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_quote_form(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    return templates.TemplateResponse(request, "quotes/new.html", {
        "user": current_user,
        "suppliers": suppliers, "areas": AREAS, "plants": PLANTS,
        "today": date.today().isoformat(), "error": None,
        "prefill": {}, "prefill_items": [],
    })


@router.post("/new")
async def create_quote(
    request: Request,
    plant: str = Form(...),
    area: str = Form(...),
    quote_date: str = Form(...),
    valid_until: str = Form(""),
    supplier_id: str = Form(""),
    supplier_name_text: str = Form(""),
    title: str = Form(...),
    description: str = Form(""),
    currency: str = Form("ARS"),
    estimated_total: str = Form(""),
    notes: str = Form(""),
    file: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    qdate = datetime.strptime(quote_date.strip(), "%Y-%m-%d") if quote_date.strip() else datetime.utcnow()
    vuntil = None
    if valid_until.strip():
        try:
            vuntil = datetime.strptime(valid_until.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    total = None
    if estimated_total.strip():
        try:
            total = float(estimated_total.replace(",", "."))
        except ValueError:
            pass

    sid = None
    if supplier_id.strip() and supplier_id.strip() != "otro":
        try:
            sid = int(supplier_id)
        except ValueError:
            pass

    form_data = await request.form()
    item_descs  = form_data.getlist("item_desc[]")
    item_qtys   = form_data.getlist("item_qty[]")
    item_units  = form_data.getlist("item_unit[]")
    item_prices = form_data.getlist("item_price[]")

    quote = models.Quote(
        plant=plant, area=area, quote_date=qdate, valid_until=vuntil,
        supplier_id=sid,
        supplier_name_text=supplier_name_text.strip() or None,
        title=title, description=description or None,
        currency=currency, estimated_total=total,
        notes=notes or None,
        requested_by_id=current_user.id,
        status="borrador",
    )
    db.add(quote)
    db.flush()

    # Items
    for i, desc in enumerate(item_descs):
        if not desc.strip():
            continue
        qty = None
        uprice = None
        sub = None
        try:
            qty = float(item_qtys[i].replace(",", ".")) if i < len(item_qtys) and item_qtys[i].strip() else None
        except (ValueError, IndexError):
            pass
        try:
            uprice = float(item_prices[i].replace(",", ".")) if i < len(item_prices) and item_prices[i].strip() else None
        except (ValueError, IndexError):
            pass
        if qty is not None and uprice is not None:
            sub = round(qty * uprice, 2)
        unit = item_units[i].strip() if i < len(item_units) else None
        db.add(models.QuoteItem(
            quote_id=quote.id, description=desc.strip(),
            quantity=qty, unit=unit or None, unit_price=uprice, subtotal=sub
        ))

    # File upload
    if file and file.filename:
        contents = await file.read()
        unique_name = f"{uuid.uuid4()}_{file.filename}"
        result = upload_file(contents, unique_name, folder="mtr-quotes")
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "otro"
        doc_type = "pdf" if ext == "pdf" else ("imagen" if ext in ("jpg", "jpeg", "png", "gif", "webp") else "otro")
        db.add(models.QuoteDocument(
            quote_id=quote.id, file_url=result["url"],
            filename=file.filename, doc_type=doc_type,
            uploaded_by_id=current_user.id,
        ))

    add_quote_audit(db, quote.id, current_user.id, "created", "Cotización creada")
    db.commit()
    return RedirectResponse(url=f"/quotes/{quote.id}", status_code=303)


# ── Detail ───────────────────────────────────────────────────────────────────

@router.get("/{quote_id}", response_class=HTMLResponse)
async def quote_detail(quote_id: int, request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    quote = db.query(models.Quote).options(
        joinedload(models.Quote.supplier),
        joinedload(models.Quote.requester),
        joinedload(models.Quote.documents).joinedload(models.QuoteDocument.uploader),
        joinedload(models.Quote.items),
        joinedload(models.Quote.audit_logs).joinedload(models.QuoteAuditLog.user),
        joinedload(models.Quote.purchase),
    ).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)

    # Restricción por rol
    if current_user.role == "planta" and quote.requested_by_id != current_user.id:
        raise HTTPException(status_code=403)
    if current_user.role == "autorizador" and current_user.plant != "TODAS" and quote.plant != current_user.plant:
        raise HTTPException(status_code=403)

    purchases_list = []
    if current_user.role in ("admin", "superadmin"):
        purchases_list = db.query(models.Purchase).filter(
            models.Purchase.deleted_at == None
        ).order_by(models.Purchase.id.desc()).limit(100).all()

    return templates.TemplateResponse(request, "quotes/detail.html", {
        "user": current_user, "quote": quote,
        "purchases_list": purchases_list,
        "now": datetime.utcnow(),
    })


# ── Edit ─────────────────────────────────────────────────────────────────────

@router.get("/{quote_id}/edit", response_class=HTMLResponse)
async def edit_quote_form(
    quote_id: int, request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    quote = db.query(models.Quote).options(
        joinedload(models.Quote.items),
        joinedload(models.Quote.documents),
    ).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    return templates.TemplateResponse(request, "quotes/edit.html", {
        "user": current_user, "quote": quote,
        "suppliers": suppliers, "areas": AREAS, "plants": PLANTS,
        "statuses": QUOTE_STATUSES, "error": None,
    })


@router.post("/{quote_id}/edit")
async def edit_quote(
    quote_id: int,
    request: Request,
    plant: str = Form(...),
    area: str = Form(...),
    quote_date: str = Form(...),
    valid_until: str = Form(""),
    supplier_id: str = Form(""),
    supplier_name_text: str = Form(""),
    title: str = Form(...),
    description: str = Form(""),
    currency: str = Form("ARS"),
    estimated_total: str = Form(""),
    notes: str = Form(""),
    status: str = Form(...),
    edit_reason: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)

    quote.plant = plant
    quote.area = area
    quote.title = title
    quote.description = description or None
    quote.currency = currency
    quote.notes = notes or None
    quote.status = status
    quote.supplier_name_text = supplier_name_text.strip() or None

    sid = None
    if supplier_id.strip() and supplier_id.strip() != "otro":
        try:
            sid = int(supplier_id)
        except ValueError:
            pass
    quote.supplier_id = sid

    if quote_date.strip():
        try:
            quote.quote_date = datetime.strptime(quote_date.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    quote.valid_until = None
    if valid_until.strip():
        try:
            quote.valid_until = datetime.strptime(valid_until.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    if estimated_total.strip():
        try:
            quote.estimated_total = float(estimated_total.replace(",", "."))
        except ValueError:
            quote.estimated_total = None
    else:
        quote.estimated_total = None

    # Replace items
    for item in list(quote.items):
        db.delete(item)
    db.flush()

    form_data = await request.form()
    item_descs  = form_data.getlist("item_desc[]")
    item_qtys   = form_data.getlist("item_qty[]")
    item_units  = form_data.getlist("item_unit[]")
    item_prices = form_data.getlist("item_price[]")

    for i, desc in enumerate(item_descs):
        if not desc.strip():
            continue
        qty = None
        uprice = None
        sub = None
        try:
            qty = float(item_qtys[i].replace(",", ".")) if i < len(item_qtys) and item_qtys[i].strip() else None
        except (ValueError, IndexError):
            pass
        try:
            uprice = float(item_prices[i].replace(",", ".")) if i < len(item_prices) and item_prices[i].strip() else None
        except (ValueError, IndexError):
            pass
        if qty is not None and uprice is not None:
            sub = round(qty * uprice, 2)
        unit = item_units[i].strip() if i < len(item_units) else None
        db.add(models.QuoteItem(
            quote_id=quote.id, description=desc.strip(),
            quantity=qty, unit=unit or None, unit_price=uprice, subtotal=sub
        ))

    add_quote_audit(db, quote.id, current_user.id, "edited", edit_reason)
    db.commit()
    return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)


# ── Status change ─────────────────────────────────────────────────────────────

@router.post("/{quote_id}/status")
async def change_quote_status(
    quote_id: int,
    new_status: str = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)

    # Permission check
    if new_status in ("aprobada", "rechazada"):
        if current_user.role not in ("autorizador", "admin", "superadmin"):
            raise HTTPException(status_code=403)
    elif new_status == "vencida":
        if current_user.role not in ("admin", "superadmin"):
            raise HTTPException(status_code=403)

    old = quote.status
    quote.status = new_status
    add_quote_audit(db, quote.id, current_user.id, f"status:{old}->{new_status}", comment)
    db.commit()
    return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)


# ── Soft delete ───────────────────────────────────────────────────────────────

@router.post("/{quote_id}/delete")
async def delete_quote(
    quote_id: int,
    deleted_reason: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("superadmin")),
):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)
    quote.deleted_at = datetime.utcnow()
    quote.deleted_reason = deleted_reason or "Sin motivo indicado"
    add_quote_audit(db, quote.id, current_user.id, "deleted", quote.deleted_reason)
    db.commit()
    return RedirectResponse(url="/quotes", status_code=303)


# ── Upload document ───────────────────────────────────────────────────────────

@router.post("/{quote_id}/documents")
async def upload_quote_document(
    quote_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)

    contents = await file.read()
    unique_name = f"{uuid.uuid4()}_{file.filename}"
    result = upload_file(contents, unique_name, folder="mtr-quotes")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "otro"
    doc_type = "pdf" if ext == "pdf" else ("imagen" if ext in ("jpg", "jpeg", "png", "gif", "webp") else "otro")

    db.add(models.QuoteDocument(
        quote_id=quote.id, file_url=result["url"],
        filename=file.filename, doc_type=doc_type,
        uploaded_by_id=current_user.id,
    ))
    add_quote_audit(db, quote.id, current_user.id, "document_uploaded", file.filename)
    db.commit()
    return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)


# ── Link existing purchase ────────────────────────────────────────────────────

@router.post("/{quote_id}/link-purchase")
async def link_purchase_to_quote(
    quote_id: int,
    purchase_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not purchase:
        raise HTTPException(status_code=404, detail="Compra no encontrada")
    quote.purchase_id = purchase_id
    add_quote_audit(db, quote.id, current_user.id, "linked_purchase", f"Vinculada a compra #{purchase_id}")
    db.commit()
    return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)


# ── Convert to purchase ───────────────────────────────────────────────────────

@router.get("/{quote_id}/to-purchase", response_class=HTMLResponse)
async def to_purchase_form(
    quote_id: int, request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    quote = db.query(models.Quote).options(
        joinedload(models.Quote.supplier),
        joinedload(models.Quote.items),
    ).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)
    if quote.purchase_id:
        return RedirectResponse(url=f"/purchases/{quote.purchase_id}", status_code=303)

    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()

    # Build prefill description from items
    if quote.items:
        desc_lines = []
        for item in quote.items:
            line = item.description
            if item.quantity:
                line = f"{item.quantity} {item.unit or 'u'} — {line}"
            desc_lines.append(line)
        prefill_desc = "\n".join(desc_lines)
    else:
        prefill_desc = quote.description or quote.title

    prefill = {
        "supplier_id": quote.supplier_id or "",
        "plant": quote.plant,
        "area": quote.area,
        "description": prefill_desc,
        "reason": f"Cotización #{quote.id}: {quote.title}",
        "estimated_amount": str(quote.estimated_total) if quote.estimated_total else "",
        "notes": quote.notes or "",
    }

    return templates.TemplateResponse(request, "quotes/to_purchase.html", {
        "user": current_user,
        "quote": quote,
        "suppliers": suppliers,
        "areas": AREAS,
        "plants": PLANTS,
        "prefill": prefill,
        "today": date.today().isoformat(),
        "error": None,
    })


@router.post("/{quote_id}/to-purchase")
async def to_purchase_create(
    quote_id: int,
    plant: str = Form(...),
    area: str = Form(...),
    supplier_id: int = Form(...),
    description: str = Form(...),
    reason: str = Form(...),
    purchase_date: str = Form(""),
    estimated_amount: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404)

    amount = None
    if estimated_amount.strip():
        try:
            amount = float(estimated_amount.replace(",", "."))
        except ValueError:
            pass

    pd = None
    if purchase_date.strip():
        try:
            pd = datetime.strptime(purchase_date.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    purchase = models.Purchase(
        plant=plant, area=area, supplier_id=supplier_id,
        description=description, reason=reason,
        estimated_amount=amount, notes=notes or None,
        requested_by_id=current_user.id,
        status="pendiente",
        purchase_date=pd,
    )
    db.add(purchase)
    db.flush()

    # Audit on purchase side
    db.add(models.AuditLog(
        purchase_id=purchase.id, user_id=current_user.id,
        action="created", old_status=None, new_status="pendiente",
        comment=f"Creada desde cotización #{quote_id}",
    ))

    # Link quote
    quote.purchase_id = purchase.id
    quote.status = "convertida_en_compra"
    add_quote_audit(db, quote.id, current_user.id, "converted_to_purchase",
                    f"Convertida en compra #{purchase.id}")

    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase.id}", status_code=303)
