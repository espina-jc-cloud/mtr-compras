from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, exists
from datetime import datetime
from app.database import get_db
from app.deps import get_current_user, require_role
from app import models
from app.templates import templates

router = APIRouter()


def _base_purchase_query(db, current_user):
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


def _base_maintenance_query(db, current_user):
    q = db.query(models.MaintenanceRecord).filter(
        models.MaintenanceRecord.deleted_at.is_(None)
    )
    if current_user.role in ("tecnico", "planta") and current_user.plant != "TODAS":
        q = q.filter(models.MaintenanceRecord.plant == current_user.plant)
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        q = q.filter(models.MaintenanceRecord.plant == current_user.plant)
    return q


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # Operador: redirect to primary action
    if current_user.role == "operador":
        return RedirectResponse(url="/fuel/new", status_code=302)
    # ── Compras ──
    bq = _base_purchase_query(db, current_user)
    counts = {s: bq.filter(models.Purchase.status == s).count()
              for s in ["pendiente", "aprobada", "recibida", "facturada", "pagada", "rechazada"]}
    recent = bq.order_by(models.Purchase.created_at.desc()).limit(10).all()
    total = bq.count()

    # ── Combustible este mes ──
    from app.routers.fuel import _month_stats as _fuel_month_stats
    now = datetime.utcnow()
    fuel_stats = _fuel_month_stats(db, now.year, now.month)

    # ── Mantenimiento (no para rol planta) ──
    maint_counts = {"abierto": 0, "en_progreso": 0, "cerrado": 0, "total": 0}
    maint_recent = []
    if current_user.role not in ("planta",):
        mq = _base_maintenance_query(db, current_user)
        for s in ["abierto", "en_progreso", "cerrado"]:
            maint_counts[s] = mq.filter(models.MaintenanceRecord.status == s).count()
        maint_counts["total"] = sum(maint_counts[s] for s in ["abierto", "en_progreso", "cerrado"])

        # Últimos trabajos para rol técnico (en lugar de compras)
        if current_user.role == "tecnico":
            maint_recent = (
                mq.options(joinedload(models.MaintenanceRecord.equipment))
                .order_by(models.MaintenanceRecord.work_date.desc())
                .limit(10)
                .all()
            )

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": current_user,
        "counts": counts,
        "recent": recent,
        "total": total,
        "maint_counts": maint_counts,
        "maint_recent": maint_recent,
        "fuel_stats": fuel_stats,
    })


@router.get("/conciliation", response_class=HTMLResponse)
async def conciliation(request: Request, db: Session = Depends(get_db), current_user=Depends(require_role("admin", "superadmin"))):
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

    return templates.TemplateResponse(request, "conciliation.html", {
        "user": current_user,
        "recibidas_sin_factura": recibidas_sin_factura,
        "facturadas_sin_pagar": facturadas_sin_pagar,
        "con_alerta_monto": con_alerta_monto,
        "aprobadas_sin_remito": aprobadas_sin_remito,
    })
