from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, exists
from app.database import get_db
from app.deps import get_current_user
from app import models

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _base_query(db, current_user):
    q = db.query(models.Purchase).options(
        joinedload(models.Purchase.supplier),
        joinedload(models.Purchase.requester),
        joinedload(models.Purchase.documents),
    )
    if current_user.role == "planta":
        q = q.filter(models.Purchase.requested_by_id == current_user.id)
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        q = q.filter(models.Purchase.plant == current_user.plant)
    return q


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    bq = _base_query(db, current_user)
    counts = {s: bq.filter(models.Purchase.status == s).count()
              for s in ["pendiente", "aprobada", "recibida", "facturada", "pagada", "rechazada"]}
    recent = bq.order_by(models.Purchase.created_at.desc()).limit(10).all()
    total = bq.count()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": current_user,
        "counts": counts, "recent": recent, "total": total,
    })


@router.get("/conciliation", response_class=HTMLResponse)
async def conciliation(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    opts = joinedload(models.Purchase.supplier), joinedload(models.Purchase.requester), joinedload(models.Purchase.documents)

    remito_ex = exists().where(and_(models.Document.purchase_id == models.Purchase.id, models.Document.doc_type == "remito"))
    factura_ex = exists().where(and_(models.Document.purchase_id == models.Purchase.id, models.Document.doc_type == "factura"))

    recibidas_sin_factura = db.query(models.Purchase).options(*opts).filter(
        models.Purchase.status == "recibida", ~factura_ex
    ).order_by(models.Purchase.created_at.desc()).all()

    facturadas_sin_pagar = db.query(models.Purchase).options(*opts).filter(
        models.Purchase.status == "facturada"
    ).order_by(models.Purchase.created_at.desc()).all()

    con_alerta_monto = db.query(models.Purchase).options(*opts).filter(
        models.Purchase.amount_alert == True
    ).order_by(models.Purchase.created_at.desc()).all()

    aprobadas_sin_remito = db.query(models.Purchase).options(*opts).filter(
        models.Purchase.status == "aprobada", ~remito_ex
    ).order_by(models.Purchase.created_at.desc()).all()

    return templates.TemplateResponse("conciliation.html", {
        "request": request, "user": current_user,
        "recibidas_sin_factura": recibidas_sin_factura,
        "facturadas_sin_pagar": facturadas_sin_pagar,
        "con_alerta_monto": con_alerta_monto,
        "aprobadas_sin_remito": aprobadas_sin_remito,
    })
