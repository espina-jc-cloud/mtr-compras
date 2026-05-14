from datetime import datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.deps import get_current_user
from app import models

router = APIRouter(prefix="/purchases")
templates = Jinja2Templates(directory="templates")

AREAS = ["Mantenimiento", "Producción", "Logística", "Administración", "Seguridad", "Limpieza", "Otros"]
PLANTS = ["MTR1", "MTR2"]

def add_audit(db: Session, purchase_id: int, user_id: int, action: str, old_status: str, new_status: str, comment: str = ""):
    log = models.AuditLog(
        purchase_id=purchase_id,
        user_id=user_id,
        action=action,
        old_status=old_status,
        new_status=new_status,
        comment=comment
    )
    db.add(log)

@router.get("", response_class=HTMLResponse)
async def list_purchases(
    request: Request,
    plant: str = "",
    status: str = "",
    supplier_id: str = "",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    query = db.query(models.Purchase)
    if current_user.role == "planta":
        query = query.filter(models.Purchase.requested_by_id == current_user.id)
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        query = query.filter(models.Purchase.plant == current_user.plant)
    if plant:
        query = query.filter(models.Purchase.plant == plant)
    if status:
        query = query.filter(models.Purchase.status == status)
    if supplier_id:
        query = query.filter(models.Purchase.supplier_id == int(supplier_id))
    purchases = query.order_by(models.Purchase.created_at.desc()).all()
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).all()
    return templates.TemplateResponse("purchases/list.html", {
        "request": request, "user": current_user,
        "purchases": purchases, "suppliers": suppliers,
        "filters": {"plant": plant, "status": status, "supplier_id": supplier_id}
    })

@router.get("/new", response_class=HTMLResponse)
async def new_purchase_form(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).order_by(models.Supplier.name).all()
    return templates.TemplateResponse("purchases/new.html", {
        "request": request, "user": current_user,
        "suppliers": suppliers, "areas": AREAS, "plants": PLANTS, "error": None
    })

@router.post("/new")
async def create_purchase(
    request: Request,
    plant: str = Form(...),
    area: str = Form(...),
    supplier_id: int = Form(...),
    description: str = Form(...),
    reason: str = Form(...),
    estimated_amount: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    amount = None
    if estimated_amount.strip():
        try:
            amount = float(estimated_amount.replace(",", "."))
        except ValueError:
            pass
    purchase = models.Purchase(
        plant=plant, area=area, supplier_id=supplier_id,
        description=description, reason=reason,
        estimated_amount=amount, notes=notes,
        requested_by_id=current_user.id, status="pendiente"
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
    return templates.TemplateResponse("purchases/detail.html", {
        "request": request, "user": current_user, "purchase": purchase
    })

@router.post("/{purchase_id}/approve")
async def approve_purchase(
    purchase_id: int, request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    if current_user.role not in ("autorizador", "admin", "superadmin"):
        raise HTTPException(status_code=403)
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if purchase.status != "pendiente":
        raise HTTPException(status_code=400, detail="Solo se pueden aprobar compras pendientes")
    old = purchase.status
    purchase.status = "aprobada"
    purchase.authorized_by_id = current_user.id
    purchase.authorized_at = datetime.utcnow()
    add_audit(db, purchase_id, current_user.id, "approved", old, "aprobada")
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)

@router.post("/{purchase_id}/reject")
async def reject_purchase(
    purchase_id: int, request: Request,
    rejection_reason: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    if current_user.role not in ("autorizador", "admin", "superadmin"):
        raise HTTPException(status_code=403)
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if purchase.status != "pendiente":
        raise HTTPException(status_code=400)
    old = purchase.status
    purchase.status = "rechazada"
    purchase.rejection_reason = rejection_reason
    add_audit(db, purchase_id, current_user.id, "rejected", old, "rechazada", rejection_reason)
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)

@router.post("/{purchase_id}/receive")
async def receive_purchase(
    purchase_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if purchase.status != "aprobada":
        raise HTTPException(status_code=400, detail="Solo se puede recibir una compra aprobada")
    has_remito = any(d.doc_type == "remito" for d in purchase.documents)
    if not has_remito:
        raise HTTPException(status_code=400, detail="Debe subir el remito antes de marcar como recibida")
    old = purchase.status
    purchase.status = "recibida"
    add_audit(db, purchase_id, current_user.id, "received", old, "recibida")
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)

@router.post("/{purchase_id}/invoice")
async def invoice_purchase(
    purchase_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if purchase.status != "recibida":
        raise HTTPException(status_code=400)
    has_factura = any(d.doc_type == "factura" for d in purchase.documents)
    if not has_factura:
        raise HTTPException(status_code=400, detail="Debe cargar la factura antes de marcar como facturada")
    old = purchase.status
    purchase.status = "facturada"
    add_audit(db, purchase_id, current_user.id, "invoiced", old, "facturada")
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)

@router.post("/{purchase_id}/pay")
async def pay_purchase(
    purchase_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403)
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if purchase.status != "facturada":
        raise HTTPException(status_code=400)
    old = purchase.status
    purchase.status = "pagada"
    add_audit(db, purchase_id, current_user.id, "paid", old, "pagada")
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)

@router.post("/{purchase_id}/cancel")
async def cancel_purchase(
    purchase_id: int,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403)
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    old = purchase.status
    purchase.status = "cancelada"
    add_audit(db, purchase_id, current_user.id, "cancelled", old, "cancelada", reason)
    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)
