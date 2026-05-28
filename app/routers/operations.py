"""
Módulo Operativos portuarios — MTR Gestión
"""
from datetime import datetime
from collections import defaultdict

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, exists

from app.database import get_db
from app.deps import get_current_user, require_role
from app import models

_OPERATIONS_ROLES = ("admin", "superadmin")
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
    current_user=Depends(require_role(*_OPERATIONS_ROLES)),
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
    # Filter by client/product via trips (supports multi-product ops)
    if q_client:
        fq = fq.filter(
            exists().where(
                (models.OperationTrip.operation_id == models.Operation.id) &
                (models.OperationTrip.client == q_client)
            )
        )
    if q_product:
        fq = fq.filter(
            exists().where(
                (models.OperationTrip.operation_id == models.Operation.id) &
                (models.OperationTrip.product == q_product)
            )
        )

    operations = fq.order_by(models.Operation.start_date.desc()).all()

    # Totals
    total_ops    = len(operations)
    total_neto   = sum(op.total_neto_kg or 0 for op in operations)
    total_trips  = sum(op.actual_trips  or 0 for op in operations)

    # Build products_map and clients_map (one query each, avoids N+1)
    op_ids = [op.id for op in operations]
    products_map: dict[int, list[str]] = {}
    clients_map:  dict[int, list[str]] = {}
    if op_ids:
        prod_rows = (
            db.query(models.OperationTrip.operation_id, models.OperationTrip.product)
            .filter(
                models.OperationTrip.operation_id.in_(op_ids),
                models.OperationTrip.product.isnot(None),
            )
            .distinct()
            .all()
        )
        tmp: dict = defaultdict(list)
        for oid, prod in prod_rows:
            tmp[oid].append(prod)
        products_map = {oid: sorted(prods) for oid, prods in tmp.items()}

        cli_rows = (
            db.query(models.OperationTrip.operation_id, models.OperationTrip.client)
            .filter(
                models.OperationTrip.operation_id.in_(op_ids),
                models.OperationTrip.client.isnot(None),
            )
            .distinct()
            .all()
        )
        tmp2: dict = defaultdict(list)
        for oid, cli in cli_rows:
            tmp2[oid].append(cli)
        clients_map = {oid: sorted(clis) for oid, clis in tmp2.items()}

    # CV data per operation — new model first, fallback to legacy OperationProductTotal
    cv_totals = {}
    if op_ids:
        cs_rows = (
            db.query(models.OperationCargoSummary)
            .filter(models.OperationCargoSummary.operation_id.in_(op_ids))
            .all()
        )
        if cs_rows:
            for cs in cs_rows:
                oid = cs.operation_id
                if oid not in cv_totals:
                    cv_totals[oid] = {"cv_t": 0.0, "discharged_t": 0.0, "has_cv": False}
                cv_totals[oid]["cv_t"]        += float(cs.cv_kg or 0) / 1000
                cv_totals[oid]["discharged_t"] += float(cs.total_ship_kg or 0) / 1000
                if cs.cv_kg and float(cs.cv_kg) > 0:
                    cv_totals[oid]["has_cv"] = True
        else:
            # legacy fallback
            cv_rows = (
                db.query(models.OperationProductTotal)
                .filter(models.OperationProductTotal.operation_id.in_(op_ids))
                .all()
            )
            for row in cv_rows:
                oid = row.operation_id
                if oid not in cv_totals:
                    cv_totals[oid] = {"cv_t": 0.0, "discharged_t": 0.0, "has_cv": False}
                cv_totals[oid]["cv_t"]        += float(row.costado_vapor_tons or 0)
                cv_totals[oid]["discharged_t"] += float(row.total_discharged_tons or 0)
                cv_totals[oid]["has_cv"] = True

    # Unique filter options sourced from trips (catches multi-product ops)
    all_products = sorted(set(
        r[0] for r in db.query(models.OperationTrip.product)
        .filter(models.OperationTrip.product.isnot(None)).distinct().all()
    ))
    all_clients = sorted(set(
        r[0] for r in db.query(models.OperationTrip.client)
        .filter(models.OperationTrip.client.isnot(None)).distinct().all()
    ))

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
        "products_map": products_map,
        "clients_map":  clients_map,
        "cv_totals":    cv_totals,
    })


@router.get("/dashboard", response_class=HTMLResponse)
async def operations_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_OPERATIONS_ROLES)),
):
    def qp(name, default=""):
        vals = request.query_params.getlist(name)
        return vals[0].strip() if vals else default

    q_client    = qp("client")
    q_product   = qp("product")
    q_op_type   = qp("op_type")
    q_date_from = qp("date_from")
    q_date_to   = qp("date_to")

    fq = db.query(models.Operation)
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
    if q_client:
        fq = fq.filter(
            exists().where(
                (models.OperationTrip.operation_id == models.Operation.id) &
                (models.OperationTrip.client == q_client)
            )
        )
    if q_product:
        fq = fq.filter(
            exists().where(
                (models.OperationTrip.operation_id == models.Operation.id) &
                (models.OperationTrip.product == q_product)
            )
        )

    all_ops = fq.order_by(models.Operation.start_date).all()

    # Trips only from filtered operations
    op_ids = [op.id for op in all_ops]
    if op_ids:
        all_trips = db.query(models.OperationTrip).filter(
            models.OperationTrip.operation_id.in_(op_ids)
        ).all()
    else:
        all_trips = []

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

    # Product distribution (from trips — correct for multi-product ops)
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

    # Shift distribution
    shift_stats = {k: {"label": SHIFT_LABELS[k], "trips": 0, "neto_kg": 0} for k in (1, 2, 3, 4)}
    for t in all_trips:
        sn = t.shift_number or 1
        if sn not in shift_stats:
            sn = 1
        shift_stats[sn]["trips"]   += 1
        shift_stats[sn]["neto_kg"] += t.neto_kg or 0

    period_start = min((o.start_date for o in all_ops if o.start_date), default=None)
    period_end   = max((o.start_date for o in all_ops if o.start_date), default=None)
    total_diff   = sum(op.total_diff_kg or 0 for op in all_ops)

    total_product_kg = sum(v["neto_kg"] for v in prod_stats.values())
    total_client_kg  = sum(v["neto_kg"] for v in client_stats.values())

    # CV totals for filtered operations — new model first, fallback to legacy
    from collections import defaultdict as _dd
    _cv_norm = []   # list of {operation_id, cv_t, product}
    if op_ids:
        _cs_all = db.query(models.OperationCargoSummary).filter(
            models.OperationCargoSummary.operation_id.in_(op_ids)
        ).all()
        if _cs_all:
            _cv_norm = [
                {"operation_id": r.operation_id, "cv_t": float(r.cv_kg or 0) / 1000, "product": r.product}
                for r in _cs_all
            ]
        else:
            _legacy = db.query(models.OperationProductTotal).filter(
                models.OperationProductTotal.operation_id.in_(op_ids)
            ).all()
            _cv_norm = [
                {"operation_id": r.operation_id, "cv_t": float(r.costado_vapor_tons or 0), "product": r.product}
                for r in _legacy
            ]

    total_cv_t = sum(d["cv_t"] for d in _cv_norm)
    total_neto_t_value = total_neto / 1000 if total_neto else 0
    total_discharged_t = total_neto_t_value + total_cv_t
    pct_cv = total_cv_t / total_discharged_t * 100 if total_discharged_t > 0 else 0

    # Top 5 by costado vapor
    cv_by_op = _dd(float)
    for d in _cv_norm:
        if d["operation_id"]:
            cv_by_op[d["operation_id"]] += d["cv_t"]
    top_by_cv = sorted(
        [o for o in all_ops if cv_by_op.get(o.id, 0) > 0],
        key=lambda o: cv_by_op[o.id],
        reverse=True
    )[:5]
    for o in top_by_cv:
        o._cv_tons = cv_by_op[o.id]
        o._discharged_t = (o.total_neto_kg or 0) / 1000 + cv_by_op[o.id]

    # Product breakdown including CV
    prod_cv = _dd(float)
    for d in _cv_norm:
        prod_cv[d["product"]] += d["cv_t"]

    # Filter options for dropdowns (always full list)
    all_products = sorted(set(
        r[0] for r in db.query(models.OperationTrip.product)
        .filter(models.OperationTrip.product.isnot(None)).distinct().all()
    ))
    all_clients = sorted(set(
        r[0] for r in db.query(models.OperationTrip.client)
        .filter(models.OperationTrip.client.isnot(None)).distinct().all()
    ))

    params = {
        "client": q_client, "product": q_product, "op_type": q_op_type,
        "date_from": q_date_from, "date_to": q_date_to,
    }
    has_filters = any(v for v in params.values())

    return templates.TemplateResponse(request, "operations/dashboard.html", {
        "user":             current_user,
        "total_ops":        total_ops,
        "total_neto":       total_neto,
        "total_trips":      total_trips,
        "avg_per_op":       avg_per_op,
        "top_by_tons":      top_by_tons,
        "top_by_th":        top_by_th,
        "prod_list":        prod_list,
        "client_list":      client_list,
        "shift_stats":      shift_stats,
        "total_neto_t":     total_neto / 1000 if total_neto else 0,
        "period_start":     period_start,
        "period_end":       period_end,
        "total_diff_t":     total_diff / 1000 if total_diff else 0,
        "total_product_kg": total_product_kg,
        "total_client_kg":  total_client_kg,
        "params":           params,
        "has_filters":      has_filters,
        "all_products":     all_products,
        "all_clients":      all_clients,
        "total_cv_t":       total_cv_t,
        "total_discharged_t": total_discharged_t,
        "pct_cv":           pct_cv,
        "top_by_cv":        top_by_cv,
        "prod_cv":          dict(prod_cv),
    })


@router.get("/{op_id}", response_class=HTMLResponse)
async def operation_detail(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_OPERATIONS_ROLES)),
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

    # Product breakdown (only shown when > 1 product)
    prod_bk: dict = defaultdict(lambda: {"trips": 0, "neto_kg": 0, "diff_kg": 0})
    for t in trips:
        key = t.product or "(sin producto)"
        prod_bk[key]["trips"]   += 1
        prod_bk[key]["neto_kg"] += t.neto_kg or 0
        prod_bk[key]["diff_kg"] += t.diff_kg or 0
    total_neto_bk = sum(v["neto_kg"] for v in prod_bk.values())
    for v in prod_bk.values():
        v["pct"] = round(v["neto_kg"] / total_neto_bk * 100, 1) if total_neto_bk else 0
    product_breakdown = sorted(prod_bk.items(), key=lambda x: x[1]["neto_kg"], reverse=True)

    # CV breakdown — new model first, fallback to legacy OperationProductTotal
    _DEPOT_ALIASES = {"MOP": "CLORURO DE POTASIO", "AMSUL": "SULFATO DE AMONIO"}

    cargo_summaries = (
        db.query(models.OperationCargoSummary)
        .filter(models.OperationCargoSummary.operation_id == op_id)
        .order_by(models.OperationCargoSummary.total_ship_kg.desc())
        .all()
    )

    if cargo_summaries:
        has_cv = any(cs.cv_kg and float(cs.cv_kg) > 0 for cs in cargo_summaries)
        discharge_bk = {}
        for cs in cargo_summaries:
            p = cs.product
            discharge_bk[p] = {
                "depot":  float(cs.depot_kg or 0) / 1000,
                "cv":     float(cs.cv_kg or 0) / 1000,
                "total":  float(cs.total_ship_kg or 0) / 1000,
                "status": cs.match_status,
            }
        for v in discharge_bk.values():
            v["pct_cv"] = v["cv"] / v["total"] * 100 if v["total"] > 0 else 0
        discharge_list = sorted(discharge_bk.items(), key=lambda x: -x[1]["total"])
        total_discharge_depot = sum(v["depot"] for v in discharge_bk.values())
        total_discharge_cv    = sum(v["cv"]    for v in discharge_bk.values())
        total_discharged      = total_discharge_depot + total_discharge_cv
        pct_cv_op = total_discharge_cv / total_discharged * 100 if total_discharged > 0 else 0
    else:
        # Legacy fallback: OperationProductTotal
        cv_rows = (
            db.query(models.OperationProductTotal)
            .filter(models.OperationProductTotal.operation_id == op_id)
            .order_by(models.OperationProductTotal.costado_vapor_tons.desc())
            .all()
        )
        has_cv = len(cv_rows) > 0

        if has_cv:
            depot_by_prod = {}
            for t in trips:
                pnorm = _DEPOT_ALIASES.get(t.product, t.product) if t.product else "(sin producto)"
                depot_by_prod[pnorm] = depot_by_prod.get(pnorm, 0.0) + (t.neto_kg or 0) / 1000

            discharge_bk = {}
            for row in cv_rows:
                p = row.product
                discharge_bk[p] = {
                    "depot":  float(row.depot_tons or 0),
                    "cv":     float(row.costado_vapor_tons or 0),
                    "total":  float(row.total_discharged_tons or 0),
                    "status": row.match_status,
                }
            for p, dt in depot_by_prod.items():
                if p not in discharge_bk:
                    discharge_bk[p] = {"depot": dt, "cv": 0.0, "total": dt, "status": "depot_only"}

            for v in discharge_bk.values():
                v["pct_cv"] = v["cv"] / v["total"] * 100 if v["total"] > 0 else 0

            discharge_list = sorted(discharge_bk.items(), key=lambda x: -x[1]["total"])
            total_discharge_depot = sum(v["depot"] for v in discharge_bk.values())
            total_discharge_cv    = sum(v["cv"]    for v in discharge_bk.values())
            total_discharged      = total_discharge_depot + total_discharge_cv
            pct_cv_op = total_discharge_cv / total_discharged * 100 if total_discharged > 0 else 0
        else:
            discharge_list = []
            total_discharge_depot = (op.total_neto_kg or 0) / 1000
            total_discharge_cv = 0.0
            total_discharged = total_discharge_depot
            pct_cv_op = 0.0

    # Collect notes/warnings from cargo summaries (e.g. STAR HELSINKI anomaly)
    cargo_notes = [(cs.product, cs.notes) for cs in cargo_summaries if cs.notes]

    return templates.TemplateResponse(request, "operations/detail.html", {
        "user":                  current_user,
        "op":                    op,
        "trips":                 trips,
        "shifts":                shifts,
        "product_breakdown":     product_breakdown,
        "has_cv":                has_cv,
        "discharge_list":        discharge_list,
        "total_discharge_depot": total_discharge_depot,
        "total_discharge_cv":    total_discharge_cv,
        "total_discharged":      total_discharged,
        "pct_cv_op":             pct_cv_op,
        "cargo_summaries":       cargo_summaries,
        "cargo_notes":           cargo_notes,
    })


# ── JSON API routes ────────────────────────────────────────────────────────────

@api_router.get("")
async def api_list_operations(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_OPERATIONS_ROLES)),
):
    q = db.query(models.Operation)
    op_type = request.query_params.get("op_type", "")
    ship    = request.query_params.get("ship", "")
    client  = request.query_params.get("client", "")
    product = request.query_params.get("product", "")
    if op_type:
        q = q.filter(models.Operation.operation_type == op_type)
    if ship:
        q = q.filter(models.Operation.ship_name.ilike(f"%{ship}%"))
    if client:
        q = q.filter(
            exists().where(
                (models.OperationTrip.operation_id == models.Operation.id) &
                (models.OperationTrip.client == client)
            )
        )
    if product:
        q = q.filter(
            exists().where(
                (models.OperationTrip.operation_id == models.Operation.id) &
                (models.OperationTrip.product == product)
            )
        )
    ops = q.order_by(models.Operation.start_date.desc()).all()
    return JSONResponse([_op_to_dict(o) for o in ops])


@api_router.get("/{op_id}")
async def api_operation_detail(
    op_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_OPERATIONS_ROLES)),
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
