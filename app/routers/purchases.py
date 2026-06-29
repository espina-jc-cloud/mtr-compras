import logging
from datetime import datetime, date
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, exists, func, case, nullslast
from app.database import get_db
from app.deps import get_current_user, require_role
from app.permissions import require_perm
from app import models
from app.templates import templates

_log = logging.getLogger(__name__)

# Acceso al módulo Compras → permiso "compras.compras".
require_compras_access = require_perm("compras.compras")

router = APIRouter(prefix="/purchases", dependencies=[Depends(require_compras_access)])

AREAS = ["Mantenimiento", "Producción", "Logística", "Administración", "Seguridad", "Limpieza", "Otros"]
PLANTS = ["MTR1", "MTR2"]
STATUSES = ["pendiente", "aprobada", "recibida", "facturada", "pagada", "rechazada", "cancelada"]

TODAY = date.today().isoformat()

def add_audit(db, purchase_id, user_id, action, old_status, new_status, comment=""):
    db.add(models.AuditLog(
        purchase_id=purchase_id, user_id=user_id, action=action,
        old_status=old_status, new_status=new_status, comment=comment
    ))

def _effective_date(col_purchase_date, col_created_at):
    """Fecha efectiva: purchase_date si existe, sino created_at.

    type_=DateTime: la expresión mezcla Date y DateTime; sin el tipo explícito
    hereda Date y el procesador de SQLite no puede parsear created_at con hora
    (joinedload+limit la incluye en el SELECT del subquery de orden).
    """
    from sqlalchemy import DateTime
    return func.coalesce(col_purchase_date, col_created_at, type_=DateTime)


def build_query(db, current_user, params: dict):
    """Construye query con todos los filtros y ordenamiento."""
    q = db.query(models.Purchase).options(
        joinedload(models.Purchase.supplier),
        joinedload(models.Purchase.requester),
        joinedload(models.Purchase.authorizer),
        joinedload(models.Purchase.documents),
    )

    # Ocultar eliminadas por defecto
    if params.get("include_deleted") != "1" or current_user.role not in ("admin", "superadmin"):
        q = q.filter(models.Purchase.deleted_at == None)

    # Restricción por rol
    if current_user.role == "planta":
        q = q.filter(models.Purchase.requested_by_id == current_user.id)
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        q = q.filter(models.Purchase.plant == current_user.plant)

    # Filtros estructurados
    if params.get("plant"):
        q = q.filter(models.Purchase.plant == params["plant"])
    if params.get("status"):
        q = q.filter(models.Purchase.status == params["status"])
    if params.get("supplier_id"):
        try:
            q = q.filter(models.Purchase.supplier_id == int(params["supplier_id"]))
        except ValueError:
            pass
    if params.get("requester_id"):
        try:
            q = q.filter(models.Purchase.requested_by_id == int(params["requester_id"]))
        except ValueError:
            pass

    # Filtro por fecha real de compra (purchase_date, con fallback a created_at)
    eff_date = _effective_date(models.Purchase.purchase_date, models.Purchase.created_at)
    if params.get("date_from"):
        try:
            q = q.filter(eff_date >= date.fromisoformat(params["date_from"]))
        except ValueError:
            pass
    if params.get("date_to"):
        try:
            q = q.filter(eff_date <= date.fromisoformat(params["date_to"]))
        except ValueError:
            pass

    if params.get("amount_min"):
        try:
            q = q.filter(models.Purchase.estimated_amount >= float(params["amount_min"]))
        except ValueError:
            pass
    if params.get("amount_max"):
        try:
            q = q.filter(models.Purchase.estimated_amount <= float(params["amount_max"]))
        except ValueError:
            pass
    if params.get("amount_alert") == "yes":
        q = q.filter(models.Purchase.amount_alert == True)

    # Filtros de documentos
    remito_exists = exists().where(
        and_(models.Document.purchase_id == models.Purchase.id, models.Document.doc_type == "remito")
    )
    factura_exists = exists().where(
        and_(models.Document.purchase_id == models.Purchase.id, models.Document.doc_type == "factura")
    )
    if params.get("has_remito") == "yes":
        q = q.filter(remito_exists)
    elif params.get("has_remito") == "no":
        q = q.filter(~remito_exists)
    if params.get("has_factura") == "yes":
        q = q.filter(factura_exists)
    elif params.get("has_factura") == "no":
        q = q.filter(~factura_exists)

    # Búsqueda de texto libre
    text = params.get("q", "").strip()
    if text:
        like = f"%{text}%"
        doc_match = exists().where(
            and_(
                models.Document.purchase_id == models.Purchase.id,
                or_(
                    models.Document.invoice_number.ilike(like),
                    models.Document.filename.ilike(like),
                )
            )
        )
        q = q.join(models.Supplier, models.Purchase.supplier_id == models.Supplier.id, isouter=True)
        q = q.filter(or_(
            models.Purchase.description.ilike(like),
            models.Purchase.reason.ilike(like),
            models.Purchase.purchase_order_ref.ilike(like),
            models.Supplier.name.ilike(like),
            models.Supplier.cuit.ilike(like),
            doc_match,
        ))

    # ── Ordenamiento ──────────────────────────────────────────────────────────
    sort_by  = params.get("sort_by", "purchase_date")
    sort_dir = params.get("sort_dir", "desc")

    eff = _effective_date(models.Purchase.purchase_date, models.Purchase.created_at)
    sort_map = {
        "purchase_date":  eff,
        "created_at":     models.Purchase.created_at,
        "plant":          models.Purchase.plant,
        "status":         models.Purchase.status,
        "estimated_amount": models.Purchase.estimated_amount,
    }
    col = sort_map.get(sort_by, eff)
    if sort_dir == "asc":
        q = q.order_by(col.asc())
    else:
        q = q.order_by(col.desc())

    return q


def _qp(request: Request, name: str) -> str:
    """Devuelve el primer valor no-vacío del query param (maneja duplicados desktop/mobile)."""
    values = request.query_params.getlist(name)
    return next((v for v in values if v), "")

def get_filter_params(request: Request) -> dict:
    return {
        "q":              _qp(request, "q"),
        "plant":          _qp(request, "plant"),
        "status":         _qp(request, "status"),
        "supplier_id":    _qp(request, "supplier_id"),
        "requester_id":   _qp(request, "requester_id"),
        "date_from":      _qp(request, "date_from"),
        "date_to":        _qp(request, "date_to"),
        "amount_min":     _qp(request, "amount_min"),
        "amount_max":     _qp(request, "amount_max"),
        "has_remito":     _qp(request, "has_remito"),
        "has_factura":    _qp(request, "has_factura"),
        "amount_alert":   _qp(request, "amount_alert"),
        "sort_by":        _qp(request, "sort_by") or "purchase_date",
        "sort_dir":       _qp(request, "sort_dir") or "desc",
        "include_deleted": _qp(request, "include_deleted"),
    }


@router.get("", response_class=HTMLResponse)
async def list_purchases(request: Request, db: Session = Depends(get_db), current_user=Depends(require_compras_access)):
    params = get_filter_params(request)
    purchases = build_query(db, current_user, params).limit(200).all()
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    requesters = db.query(models.User).filter(models.User.active == True).order_by(models.User.name).all()

    is_htmx = request.headers.get("HX-Request")
    if is_htmx:
        return templates.TemplateResponse(request, "purchases/partials/results.html", {
            "user": current_user, "purchases": purchases, "params": params, "statuses": STATUSES,
        })
    return templates.TemplateResponse(request, "purchases/list.html", {
        "user": current_user,
        "purchases": purchases, "suppliers": suppliers, "requesters": requesters,
        "params": params, "statuses": STATUSES,
    })


@router.get("/new", response_class=HTMLResponse)
async def new_purchase_form(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    return templates.TemplateResponse(request, "purchases/new.html", {
        "user": current_user,
        "suppliers": suppliers, "areas": AREAS, "plants": PLANTS, "error": None,
        "today": date.today().isoformat(),
    })


@router.post("/new")
async def create_purchase(
    request: Request,
    plant: str = Form(...), area: str = Form(...), supplier_id: int = Form(...),
    description: str = Form(...), reason: str = Form(...),
    estimated_amount: str = Form(""), notes: str = Form(""),
    purchase_date: str = Form(""),
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    amount = None
    if estimated_amount.strip():
        try:
            amount = float(estimated_amount.replace(",", "."))
        except ValueError:
            pass
    pd = None
    if purchase_date.strip():
        try:
            pd = date.fromisoformat(purchase_date.strip())
        except ValueError:
            pass
    purchase = models.Purchase(
        plant=plant, area=area, supplier_id=supplier_id,
        description=description, reason=reason, estimated_amount=amount, notes=notes,
        requested_by_id=current_user.id, status="pendiente",
        purchase_date=pd,
    )
    db.add(purchase)
    db.flush()
    add_audit(db, purchase.id, current_user.id, "created", None, "pendiente")
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase.id}", status_code=303)


@router.get("/{purchase_id}", response_class=HTMLResponse)
async def purchase_detail(purchase_id: int, request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not purchase:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "purchases/detail.html", {
        "user": current_user, "purchase": purchase
    })


@router.post("/{purchase_id}/approve")
async def approve_purchase(purchase_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in ("autorizador", "admin", "superadmin"):
        raise HTTPException(status_code=403)
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404)
    # Plant isolation: autorizador can only approve purchases from their own plant
    if current_user.role == "autorizador" and current_user.plant != "TODAS":
        if p.plant != current_user.plant:
            _log.warning("SECURITY DENY approve purchase=%d user=%d role=%s plant=%s purchase_plant=%s",
                         purchase_id, current_user.id, current_user.role, current_user.plant, p.plant)
            raise HTTPException(status_code=403, detail="No podés aprobar compras de otra planta")
    if p.status != "pendiente":
        raise HTTPException(status_code=400)
    old = p.status
    p.status = "aprobada"
    p.authorized_by_id = current_user.id
    p.authorized_at = datetime.utcnow()
    add_audit(db, purchase_id, current_user.id, "approved", old, "aprobada")
    db.commit()
    _log.info("SECURITY approve purchase=%d user=%d role=%s plant=%s",
              purchase_id, current_user.id, current_user.role, current_user.plant)
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)


@router.post("/{purchase_id}/reject")
async def reject_purchase(purchase_id: int, rejection_reason: str = Form(...), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in ("autorizador", "admin", "superadmin"):
        raise HTTPException(status_code=403)
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404)
    if p.status != "pendiente":
        raise HTTPException(status_code=400)
    old = p.status
    p.status = "rechazada"
    p.rejection_reason = rejection_reason
    add_audit(db, purchase_id, current_user.id, "rejected", old, "rechazada", rejection_reason)
    db.commit()
    _log.info("SECURITY reject purchase=%d user=%d role=%s reason=%s",
              purchase_id, current_user.id, current_user.role, rejection_reason[:80])
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)


@router.post("/{purchase_id}/receive")
async def receive_purchase(purchase_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in ("autorizador", "admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Sin permiso para marcar como recibida")
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404)
    if p.status != "aprobada":
        raise HTTPException(status_code=400)
    if not any(d.doc_type == "remito" for d in p.documents):
        raise HTTPException(status_code=400, detail="Subí el remito antes de marcar como recibida")
    old = p.status
    p.status = "recibida"
    add_audit(db, purchase_id, current_user.id, "received", old, "recibida")
    db.commit()
    _log.info("SECURITY receive purchase=%d user=%d role=%s",
              purchase_id, current_user.id, current_user.role)
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)


@router.post("/{purchase_id}/invoice")
async def invoice_purchase(purchase_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if p.status != "recibida":
        raise HTTPException(status_code=400)
    if not any(d.doc_type == "factura" for d in p.documents):
        raise HTTPException(status_code=400, detail="Cargá la factura antes de marcar como facturada")
    old = p.status
    p.status = "facturada"
    add_audit(db, purchase_id, current_user.id, "invoiced", old, "facturada")
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)


@router.post("/{purchase_id}/pay")
async def pay_purchase(purchase_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404)
    if p.status != "facturada":
        raise HTTPException(status_code=400)
    old = p.status
    p.status = "pagada"
    add_audit(db, purchase_id, current_user.id, "paid", old, "pagada")
    db.commit()
    _log.info("SECURITY pay purchase=%d user=%d role=%s amount=%s",
              purchase_id, current_user.id, current_user.role, p.actual_amount)
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)


@router.post("/{purchase_id}/cancel")
async def cancel_purchase(purchase_id: int, reason: str = Form(""), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403)
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    old = p.status
    p.status = "cancelada"
    add_audit(db, purchase_id, current_user.id, "cancelled", old, "cancelada", reason)
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)


@router.post("/{purchase_id}/delete")
async def delete_purchase(
    purchase_id: int,
    deleted_reason: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404)
    p.deleted_at = datetime.utcnow()
    p.deleted_reason = deleted_reason or "Sin motivo indicado"
    add_audit(db, purchase_id, current_user.id, "deleted", p.status, p.status, p.deleted_reason)
    db.commit()
    _log.warning("SECURITY delete purchase=%d user=%d role=%s reason=%s",
                 purchase_id, current_user.id, current_user.role, p.deleted_reason[:80])
    return RedirectResponse(url="/purchases", status_code=303)


# ── Edit purchase (admin/superadmin) ─────────────────────────────────────────

@router.post("/{purchase_id}/link-quote")
async def link_quote_to_purchase(
    purchase_id: int,
    quote_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin")),
):
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not purchase:
        raise HTTPException(status_code=404)
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    quote.purchase_id = purchase_id
    add_audit(db, purchase_id, current_user.id, "linked_quote", purchase.status, purchase.status,
              f"Vinculada cotización #{quote_id}")
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)


@router.get("/{purchase_id}/edit", response_class=HTMLResponse)
async def edit_purchase_form(
    purchase_id: int, request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    p = db.query(models.Purchase).options(
        joinedload(models.Purchase.documents),
        joinedload(models.Purchase.audit_logs),
    ).filter(models.Purchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404)
    suppliers = db.query(models.Supplier).order_by(models.Supplier.name).all()
    users = db.query(models.User).filter(models.User.active == True).order_by(models.User.name).all()
    return templates.TemplateResponse(request, "purchases/edit.html", {
        "user": current_user, "purchase": p,
        "suppliers": suppliers, "users": users,
        "areas": AREAS, "plants": PLANTS, "statuses": STATUSES, "error": None,
    })


@router.post("/{purchase_id}/edit")
async def edit_purchase(
    purchase_id: int,
    request: Request,
    plant: str = Form(...),
    area: str = Form(...),
    supplier_id: int = Form(...),
    description: str = Form(...),
    reason: str = Form(...),
    purchase_date: str = Form(""),
    status: str = Form(...),
    requested_by_id: int = Form(...),
    authorized_by_id: str = Form(""),
    estimated_amount: str = Form(""),
    actual_amount: str = Form(""),
    purchase_order_ref: str = Form(""),
    notes: str = Form(""),
    edit_reason: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    p = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404)

    old_status = p.status

    p.plant = plant
    p.area = area
    p.supplier_id = supplier_id
    p.description = description
    p.reason = reason
    p.purchase_order_ref = purchase_order_ref or None
    p.notes = notes or None
    p.status = status
    p.requested_by_id = requested_by_id
    p.authorized_by_id = int(authorized_by_id) if authorized_by_id.strip() else None

    if purchase_date.strip():
        try:
            p.purchase_date = date.fromisoformat(purchase_date.strip())
        except ValueError:
            pass

    if estimated_amount.strip():
        try:
            p.estimated_amount = float(estimated_amount.replace(",", "."))
        except ValueError:
            pass
    else:
        p.estimated_amount = None

    if actual_amount.strip():
        try:
            p.actual_amount = float(actual_amount.replace(",", "."))
        except ValueError:
            pass
    else:
        p.actual_amount = None

    add_audit(db, purchase_id, current_user.id, "edited", old_status, status, edit_reason)
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)
