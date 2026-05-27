"""
Módulo Operativos portuarios — MTR Gestión
"""
from datetime import datetime
from collections import defaultdict

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.deps import get_current_user
from app import models
from app.templates import templates

router     = APIRouter(prefix="/operations")
api_router = APIRouter(prefix="/api/operations")

SHIFT_LABELS = {
    1: "Turno 1 (00-06)",
    2: "Turno 2 (06-12)",
    3: "Turno 3 (12-18)",
    4: "Turno 4 (18-24)",
}


def _float(v):
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _op_to_dict(op: models.Operation) -> dict:
    return {
        "id":               op.id,
        "raw_name":         op.raw_name,
        "ship_name":        op.ship_name,
        "operation_type":   op.operation_type,
        "client":           op.client,
        "product":          op.product,
        "start_date":       op.start_date.isoformat() if op.start_date else None,
        "end_date":         op.end_date.isoformat()   if op.end_date   else None,
        "actual_trips":     op.actual_trips,
        "total_neto_kg":    op.total_neto_kg,
        "total_origen_kg":  op.total_origen_kg,
        "total_diff_kg":    op.total_diff_kg,
        "avg_duration_min": _float(op.avg_duration_min),
        "avg_tons_per_trip": _float(op.avg_tons_per_trip),
        "avg_tons_per_hour": _float(op.avg_tons_per_hour),
        "source_file":      op.source_file,
    }


def _trip_to_dict(t: models.OperationTrip) -> dict:
    return {
        "id":           t.id,
        "operation_id": t.operation_id,
        "trip_code":    t.trip_code,
        "entry_date":   t.entry_date.isoformat() if t.entry_date else None,
        "entry_time":   t.entry_time,
        "exit_date":    t.exit_date.isoformat()  if t.exit_date  else None,
        "exit_time":    t.exit_time,
        "plate":        t.plate,
        "tara_kg":      t.tara_kg,
        "bruto_kg":     t.bruto_kg,
        "neto_kg":      t.neto_kg,
        "origen_kg":    t.origen_kg,
        "diff_kg":      t.diff_kg,
        "shift_number": t.shift_number,
        "duration_min": _float(t.duration_min),
        "client":       t.client,
        "product":      t.product,
    }


def _compute_shift_stats(trips):
    shifts = {
        k: {"label": SHIFT_LABELS[k], "trips": 0, "neto_kg": 0, "diff_kg": 0, "dur_sum": 0.0, "dur_count": 0}
        for k in (1, 2, 3, 4)
    }
    for t in trips:
        sn = t.shift_number or 1
        if sn not in shifts:
            sn = 1
        shifts[sn]["trips"]   += 1
        shifts[sn]["neto_kg"] += t.neto_kg or 0
        shifts[sn]["diff_kg"] += t.diff_kg or 0
        if t.duration_min is not None and _float(t.duration_min) > 0:
            shifts[sn]["dur_sum"]   += _float(t.duration_min)
            shifts[sn]["dur_count"] += 1

    for sn, s in shifts.items():
        avg_dur = s["dur_sum"] / s["dur_count"] if s["dur_count"] > 0 else None
        # t/h estimate per shift
        if avg_dur and avg_dur > 0 and s["trips"] > 0:
            avg_neto_per_trip_ton = s["neto_kg"] / 1000 / s["trips"]
            t_per_h = avg_neto_per_trip_ton / (avg_dur / 60) if avg_dur > 0 else None
        else:
            t_per_h = None
        s["avg_duration_min"] = round(avg_dur, 2) if avg_dur else None
        s["t_per_h"]          = round(t_per_h, 3) if t_per_h else None

    return shifts


# ── HTML routes ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_operations(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    def qp(name, default=""):
        vals = request.query_params.getlist(name)
        return vals[0].strip() if vals else default

    q_ship      = qp("ship")
    q_client    = qp("client")
    q_product   = qp("product")
    q_op_type   = qp("op_type")
    q_date_from = qp("date_from")
    q_date_to   = qp("date_to")

    fq = db.query(models.Operation)

    if q_ship:
        fq = fq.filter(models.Operation.ship_name.ilike(f"%{q_ship}%"))
    if q_client:
        fq = fq.filter(models.Operation.client == q_client)
    if q_product:
        fq = fq.filter(models.Operation.product == q_product)
    if q_op_type:
        fq = fq.filter(models.Operation.operation_type == q_op_type)
    if q_date_from:
        try:
            fq = fq.filter(models.Operation.start_date >= datetime.strptime(q_date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if q_date_to:
        try:
            fq = fq.filter(models.Operation.start_date <= datetime.strptime(q_date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass

    operations = fq.order_by(models.Operation.start_date.desc()).all()

    # Totals
    total_ops    = len(operations)
    total_neto   = sum(op.total_neto_kg or 0 for op in operations)
    total_trips  = sum(op.actual_trips  or 0 for op in operations)

    # Unique filter options
    all_clients  = sorted(set(r[0] for r in db.query(models.Operation.client).filter(
        models.Operation.client.isnot(None)).distinct().all()))
    all_products = sorted(set(r[0] for r in db.query(models.Operation.product).filter(
        models.Operation.product.isnot(None)).distinct().all()))

    params = {
        "ship": q_ship, "client": q_client, "product": q_product,
        "op_type": q_op_type, "date_from": q_date_from, "date_to": q_date_to,
    }

    return templates.TemplateResponse(request, "operations/list.html", {
        "user":         current_user,
        "operations":   operations,
        "params":       params,
        "total_ops":    total_ops,
        "total_neto":   total_neto,
        "total_trips":  total_trips,
        "all_clients":  all_clients,
        "all_products": all_products,
    })


@router.get("/dashboard", response_class=HTMLResponse)
async def operations_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    all_ops   = db.query(models.Operation).all()
    all_trips = db.query(models.OperationTrip).all()

    total_ops    = len(all_ops)
    total_neto   = sum(op.total_neto_kg or 0 for op in all_ops)
    total_trips  = sum(op.actual_trips  or 0 for op in all_ops)
    avg_per_op   = round(total_neto / 1000 / total_ops, 1) if total_ops > 0 else 0

    # Top 5 by toneladas
    top_by_tons = sorted(all_ops, key=lambda o: o.total_neto_kg or 0, reverse=True)[:5]

    # Top 5 by avg t/h
    top_by_th = sorted(
        [o for o in all_ops if o.avg_tons_per_hour],
        key=lambda o: _float(o.avg_tons_per_hour),
        reverse=True
    )[:5]

    # Product distribution
    prod_stats = defaultdict(lambda: {"trips": 0, "neto_kg": 0})
    for t in all_trips:
        key = t.product or "(sin producto)"
        prod_stats[key]["trips"]   += 1
        prod_stats[key]["neto_kg"] += t.neto_kg or 0
    prod_list = sorted(prod_stats.items(), key=lambda x: x[1]["neto_kg"], reverse=True)

    # Client distribution
    client_stats = defaultdict(lambda: {"trips": 0, "neto_kg": 0})
    for t in all_trips:
        key = t.client or "(sin cliente)"
        client_stats[key]["trips"]   += 1
        client_stats[key]["neto_kg"] += t.neto_kg or 0
    client_list = sorted(client_stats.items(), key=lambda x: x[1]["neto_kg"], reverse=True)

    # Shift distribution (global)
    shift_stats = {k: {"label": SHIFT_LABELS[k], "trips": 0, "neto_kg": 0} for k in (1, 2, 3, 4)}
    for t in all_trips:
        sn = t.shift_number or 1
        if sn not in shift_stats:
            sn = 1
        shift_stats[sn]["trips"]   += 1
        shift_stats[sn]["neto_kg"] += t.neto_kg or 0

    return templates.TemplateResponse(request, "operations/dashboard.html", {
        "user":          current_user,
        "total_ops":     total_ops,
        "total_neto":    total_neto,
        "total_trips":   total_trips,
        "avg_per_op":    avg_per_op,
        "top_by_tons":   top_by_tons,
        "top_by_th":     top_by_th,
        "prod_list":     prod_list,
        "client_list":   client_list,
        "shift_stats":   shift_stats,
        "total_neto_t":  total_neto / 1000 if total_neto else 0,
    })


@router.get("/{op_id}", response_class=HTMLResponse)
async def operation_detail(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    op = db.query(models.Operation).filter(models.Operation.id == op_id).first()
    if not op:
        raise HTTPException(status_code=404)

    trips = (
        db.query(models.OperationTrip)
        .filter(models.OperationTrip.operation_id == op_id)
        .order_by(models.OperationTrip.entry_date)
        .all()
    )

    shifts = _compute_shift_stats(trips)

    return templates.TemplateResponse(request, "operations/detail.html", {
        "user":   current_user,
        "op":     op,
        "trips":  trips,
        "shifts": shifts,
    })


# ── JSON API routes ────────────────────────────────────────────────────────────

@api_router.get("")
async def api_list_operations(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ops = db.query(models.Operation).order_by(models.Operation.start_date.desc()).all()
    return JSONResponse([_op_to_dict(o) for o in ops])


@api_router.get("/{op_id}")
async def api_operation_detail(
    op_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    op = db.query(models.Operation).filter(models.Operation.id == op_id).first()
    if not op:
        raise HTTPException(status_code=404)
    trips = (
        db.query(models.OperationTrip)
        .filter(models.OperationTrip.operation_id == op_id)
        .order_by(models.OperationTrip.entry_date)
        .all()
    )
    result = _op_to_dict(op)
    result["trips"] = [_trip_to_dict(t) for t in trips]
    return JSONResponse(result)
