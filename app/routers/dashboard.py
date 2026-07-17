from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, exists
from datetime import datetime
from app.database import get_db
from app.deps import get_current_user, require_role
from app.permissions import require_perm
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


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Centro de Operaciones: qué pasa ahora, qué viene, qué necesita atención.

    Solo LECTURAS agregadas por permiso — no toca ninguna lógica de negocio.
    """
    # Operador: su única acción es cargar combustible, va directo
    if current_user.role == "operador":
        return RedirectResponse(url="/fuel/new", status_code=302)

    from datetime import date as _date_cls
    from sqlalchemy import func
    from app.permissions import can

    today = _date_cls.today()
    ctx = {"user": current_user, "today": today, "now": datetime.utcnow()}

    # ── AHORA: operativos live activos ────────────────────────────────────────
    live_rows = []
    if can(current_user, "operaciones.live"):
        from app.models_live import OperationLiveSession, OperationLiveBodegaData
        from app.live_utils import session_totals_by_product, session_grand_total
        sessions = (db.query(OperationLiveSession)
                    .filter(OperationLiveSession.status.in_(["active", "paused"]))
                    .order_by(OperationLiveSession.created_at.desc())
                    .limit(6).all())
        for s in sessions:
            shift_ids = [sh.id for sh in s.shifts]
            bodega = (db.query(OperationLiveBodegaData)
                      .filter(OperationLiveBodegaData.shift_id.in_(shift_ids)).all()
                      if shift_ids else [])
            grand = session_grand_total(session_totals_by_product(bodega, s.products))
            live_rows.append({
                "s": s,
                "grand": grand,
                "open_shifts": sum(1 for sh in s.shifts if sh.status == "open"),
                "shift_count": len(s.shifts),
            })
    ctx["live_rows"] = live_rows

    # ── PRÓXIMO: arribos + despachos de hoy ───────────────────────────────────
    arribos = []
    if can(current_user, "operaciones.arribos"):
        from app.models_arribos import ProximoArribo
        arribos = (db.query(ProximoArribo)
                   .filter(ProximoArribo.deleted_at.is_(None),
                           ProximoArribo.estado.notin_(("finalizado", "cancelado")))
                   .order_by(func.coalesce(ProximoArribo.fecha_estimada, "9999-12-31").asc())
                   .limit(5).all())
    ctx["arribos"] = arribos

    despachos_hoy = None
    if can(current_user, "operaciones.despachos"):
        from app.models_cupos import CupoDespacho
        base = db.query(CupoDespacho).filter(CupoDespacho.scheduled_date == today)
        despachos_hoy = {
            "total":      base.count(),
            "programado": base.filter(CupoDespacho.status == "programado").count(),
            "cargado":    base.filter(CupoDespacho.status == "cargado").count(),
        }
    ctx["despachos_hoy"] = despachos_hoy

    # ── ATENCIÓN: pendientes accionables (solo si > 0) ────────────────────────
    atencion = []
    if can(current_user, "compras.compras"):
        n = _base_purchase_query(db, current_user).filter(models.Purchase.status == "pendiente").count()
        if n:
            atencion.append({"n": n, "label": "compras pendientes de autorizar", "href": "/purchases?status=pendiente"})
    if can(current_user, "compras.conciliacion"):
        factura_ex = exists().where(and_(models.Document.purchase_id == models.Purchase.id,
                                         models.Document.doc_type == "factura"))
        n = db.query(models.Purchase).filter(models.Purchase.status == "recibida", ~factura_ex).count()
        if n:
            atencion.append({"n": n, "label": "recibidas sin factura", "href": "/conciliation"})
    if can(current_user, "mantenimiento.mantenimiento"):
        n = _base_maintenance_query(db, current_user).filter(
            models.MaintenanceRecord.status.in_(["abierto", "en_progreso"])).count()
        if n:
            atencion.append({"n": n, "label": "trabajos de mantenimiento abiertos", "href": "/maintenance"})
    open_shifts_total = sum(r["open_shifts"] for r in live_rows)
    if open_shifts_total:
        atencion.append({"n": open_shifts_total, "label": "turnos live sin cerrar", "href": "/operations/live"})
    ctx["atencion"] = atencion

    # ── Acciones rápidas por permiso ──────────────────────────────────────────
    quick = []
    if can(current_user, "compras.compras"):        quick.append(("Nueva compra", "/purchases/new"))
    if can(current_user, "operaciones.arribos"):     quick.append(("Nuevo arribo", "/operations/arribos/new"))
    if can(current_user, "operaciones.arribos"):     quick.append(("Importar lineup", "/operations/arribos/import"))
    if can(current_user, "operaciones.live"):        quick.append(("Nuevo operativo live", "/operations/live/new"))
    if can(current_user, "mantenimiento.combustible"): quick.append(("Cargar combustible", "/fuel/new"))
    ctx["quick"] = quick

    return templates.TemplateResponse(request, "home.html", ctx)


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
async def conciliation(request: Request, db: Session = Depends(get_db), current_user=Depends(require_perm("compras.conciliacion"))):
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
