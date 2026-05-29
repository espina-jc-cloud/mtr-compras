"""
Módulo Operativos portuarios — MTR Gestión
"""
from datetime import datetime, date as _date, time as _time, timedelta
from collections import defaultdict
from urllib.parse import quote as _url_quote

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

def _build_datetime(d, t_str: str | None) -> datetime | None:
    """Combine a date/datetime column value with a 'HH:MM:SS' time string."""
    if d is None:
        return None
    if hasattr(d, "date"):
        d = d.date()
    elif isinstance(d, datetime):
        d = d.date()
    try:
        if t_str:
            parts = t_str.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            s = int(float(parts[2])) if len(parts) > 2 else 0
            return datetime.combine(d, _time(h, m, s))
        return datetime.combine(d, _time(0, 0, 0))
    except Exception:
        return datetime.combine(d, _time(0, 0, 0))


def _calc_rhythm_from_trips(trips) -> dict:
    """
    Compute real depot discharge rhythm from trip entry/exit times.
    Combines entry_date (date part) + entry_time ('HH:MM:SS' string) for precision.
    Returns dict: first_dt, last_dt, duration_h, total_depot_t, t_per_h (all None if insufficient).
    """
    result: dict = {"first_dt": None, "last_dt": None,
                    "duration_h": None, "total_depot_t": None, "t_per_h": None}
    entry_dts = []
    exit_dts  = []
    total_kg  = 0
    for t in trips:
        ed = _build_datetime(getattr(t, "entry_date", None), getattr(t, "entry_time", None))
        if ed:
            entry_dts.append(ed)
        xd = _build_datetime(getattr(t, "exit_date", None), getattr(t, "exit_time", None))
        if xd:
            exit_dts.append(xd)
        total_kg += getattr(t, "neto_kg", None) or 0
    if not entry_dts or not exit_dts or total_kg <= 0:
        return result
    first_dt   = min(entry_dts)
    last_dt    = max(exit_dts)
    duration_h = (last_dt - first_dt).total_seconds() / 3600
    result["first_dt"]      = first_dt
    result["last_dt"]       = last_dt
    result["duration_h"]    = round(duration_h, 2)
    result["total_depot_t"] = round(total_kg / 1000, 3)
    if duration_h > 0:
        result["t_per_h"] = round(total_kg / 1000 / duration_h, 3)
    return result


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
    operations = fq.order_by(models.Operation.start_date.desc()).all()
    op_ids    = [op.id for op in operations]
    ops_by_id = {op.id: op for op in operations}

    # ── list_rows: one row per OperationCargoSummary (multiproduct = multiple rows) ──
    list_rows: list[dict] = []
    op_ids_with_cs: set   = set()

    if op_ids:
        cs_q = (
            db.query(models.OperationCargoSummary)
            .filter(models.OperationCargoSummary.operation_id.in_(op_ids))
        )
        if q_client:
            cs_q = cs_q.filter(models.OperationCargoSummary.client == q_client)
        if q_product:
            cs_q = cs_q.filter(models.OperationCargoSummary.product == q_product)
        cs_rows = cs_q.order_by(
            models.OperationCargoSummary.operation_id,
            models.OperationCargoSummary.total_ship_kg.desc(),
        ).all()

        for cs in cs_rows:
            op = ops_by_id.get(cs.operation_id)
            if not op:
                continue
            op_ids_with_cs.add(cs.operation_id)
            total_t = float(cs.total_ship_kg or 0) / 1000
            depot_t = float(cs.depot_kg or 0) / 1000
            cv_t    = float(cs.cv_kg or 0) / 1000
            list_rows.append({
                "op":          op,
                "product":     cs.product,
                "client":      cs.client,
                "total_t":     total_t,
                "depot_t":     depot_t,
                "cv_t":        cv_t,
                "pct_cv":      cv_t / total_t * 100 if total_t > 0 else 0.0,
                "has_cv":      cv_t > 0,
                "trip_count":  cs.trip_count,
                "has_summary": True,
            })

    # ── Fallback for ops without cargo_summaries (skipped if client/product filter) ──
    if not q_client and not q_product:
        ops_without_cs = [op for op in operations if op.id not in op_ids_with_cs]
        if ops_without_cs:
            _leg_ids = [op.id for op in ops_without_cs]
            _leg_rows = (
                db.query(models.OperationProductTotal)
                .filter(models.OperationProductTotal.operation_id.in_(_leg_ids))
                .all()
            )
            _leg_by_op: dict = defaultdict(list)
            for r in _leg_rows:
                _leg_by_op[r.operation_id].append(r)

            for op in ops_without_cs:
                if op.id in _leg_by_op:
                    for r in _leg_by_op[op.id]:
                        total_t = float(r.total_discharged_tons or 0)
                        depot_t = float(r.depot_tons or 0)
                        cv_t    = float(r.costado_vapor_tons or 0)
                        list_rows.append({
                            "op":          op,
                            "product":     r.product or "(sin producto)",
                            "client":      None,
                            "total_t":     total_t,
                            "depot_t":     depot_t,
                            "cv_t":        cv_t,
                            "pct_cv":      cv_t / total_t * 100 if total_t > 0 else 0.0,
                            "has_cv":      cv_t > 0,
                            "trip_count":  op.actual_trips,
                            "has_summary": True,
                        })
                else:
                    depot_t = (op.total_neto_kg or 0) / 1000
                    list_rows.append({
                        "op":          op,
                        "product":     op.product or "(sin producto)",
                        "client":      op.client,
                        "total_t":     depot_t,
                        "depot_t":     depot_t,
                        "cv_t":        0.0,
                        "pct_cv":      0.0,
                        "has_cv":      False,
                        "trip_count":  op.actual_trips,
                        "has_summary": False,
                    })

    # Sort: op.start_date desc, then total_t desc within same op
    list_rows.sort(
        key=lambda r: (r["op"].start_date or datetime.min, r["total_t"]),
        reverse=True,
    )

    # Mark multiproduct rows and build detail URLs
    _op_row_counts: dict = defaultdict(int)
    for r in list_rows:
        _op_row_counts[r["op"].id] += 1
    for r in list_rows:
        r["is_multiproduct"] = _op_row_counts[r["op"].id] > 1
        if r["is_multiproduct"]:
            r["detail_url"] = f"/operations/{r['op'].id}?product={_url_quote(r['product'] or '')}"
        else:
            r["detail_url"] = f"/operations/{r['op'].id}"

    # Compute correct rhythm from actual trip timestamps (not stored avg_tons_per_hour)
    _op_rhythm_list: dict = {}
    if op_ids:
        _trip_thin = (
            db.query(
                models.OperationTrip.operation_id,
                models.OperationTrip.entry_date,
                models.OperationTrip.entry_time,
                models.OperationTrip.exit_date,
                models.OperationTrip.exit_time,
                models.OperationTrip.neto_kg,
            )
            .filter(models.OperationTrip.operation_id.in_(op_ids))
            .all()
        )
        _tbo: dict = defaultdict(list)
        for row in _trip_thin:
            _tbo[row.operation_id].append(row)
        for oid, rows in _tbo.items():
            _op_rhythm_list[oid] = _calc_rhythm_from_trips(rows).get("t_per_h")
    for r in list_rows:
        r["rhythm_t_h"] = None if r["has_cv"] else _op_rhythm_list.get(r["op"].id)

    # Grand totals
    grand_total_t = sum(r["total_t"] for r in list_rows)
    grand_depot_t = sum(r["depot_t"] for r in list_rows)
    grand_cv_t    = sum(r["cv_t"]    for r in list_rows)
    grand_pct_cv  = grand_cv_t / grand_total_t * 100 if grand_total_t > 0 else 0.0
    total_trips   = sum(r["trip_count"] or 0 for r in list_rows)
    total_ops     = len({r["op"].id for r in list_rows})

    # Filter dropdown options from cargo_summaries (not trips)
    all_products = sorted({
        r[0] for r in db.query(models.OperationCargoSummary.product)
        .filter(models.OperationCargoSummary.product.isnot(None)).distinct().all()
    })
    all_clients = sorted({
        r[0] for r in db.query(models.OperationCargoSummary.client)
        .filter(models.OperationCargoSummary.client.isnot(None)).distinct().all()
    })

    params = {
        "ship": q_ship, "client": q_client, "product": q_product,
        "op_type": q_op_type, "date_from": q_date_from, "date_to": q_date_to,
    }

    return templates.TemplateResponse(request, "operations/list.html", {
        "user":          current_user,
        "list_rows":     list_rows,
        "params":        params,
        "total_ops":     total_ops,
        "total_trips":   total_trips,
        "all_clients":   all_clients,
        "all_products":  all_products,
        "grand_total_t": grand_total_t,
        "grand_depot_t": grand_depot_t,
        "grand_cv_t":    grand_cv_t,
        "grand_pct_cv":  grand_pct_cv,
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

    # top_by_th is built later (after _op_cs) to exclude ops with CV

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

    # ── Discharge data (cargo_summaries → legacy → trip fallback) ────────────
    from collections import defaultdict as _dd
    _cs_norm: list = []   # {operation_id, total_t, depot_t, cv_t, product, client}
    if op_ids:
        _cs_all = db.query(models.OperationCargoSummary).filter(
            models.OperationCargoSummary.operation_id.in_(op_ids)
        ).all()
        if _cs_all:
            _cs_norm = [
                {
                    "operation_id": r.operation_id,
                    "total_t": float(r.total_ship_kg or 0) / 1000,
                    "depot_t": float(r.depot_kg      or 0) / 1000,
                    "cv_t":    float(r.cv_kg          or 0) / 1000,
                    "product": r.product,
                    "client":  r.client,
                }
                for r in _cs_all
            ]
        else:
            _legacy_all = db.query(models.OperationProductTotal).filter(
                models.OperationProductTotal.operation_id.in_(op_ids)
            ).all()
            _cs_norm = [
                {
                    "operation_id": r.operation_id,
                    "total_t": float(r.total_discharged_tons or 0),
                    "depot_t": float(r.depot_tons            or 0),
                    "cv_t":    float(r.costado_vapor_tons    or 0),
                    "product": r.product,
                    "client":  None,
                }
                for r in _legacy_all
            ]

    # Trip-based fallback for ops without any summary
    _ops_with_cs = {d["operation_id"] for d in _cs_norm}
    for op in all_ops:
        if op.id not in _ops_with_cs:
            _cs_norm.append({
                "operation_id": op.id,
                "total_t": (op.total_neto_kg or 0) / 1000,
                "depot_t": (op.total_neto_kg or 0) / 1000,
                "cv_t":    0.0,
                "product": op.product,
                "client":  op.client,
            })

    # Apply client/product filter at cargo-summary level (not trip level)
    if q_client:
        _cs_norm = [d for d in _cs_norm if d["client"] == q_client]
    if q_product:
        _cs_norm = [d for d in _cs_norm if d["product"] == q_product]
    # Restrict trip-based stats to operations present in the filtered cs_norm
    if (q_client or q_product) and all_trips:
        _filt_op_ids = {d["operation_id"] for d in _cs_norm}
        all_trips = [t for t in all_trips if t.operation_id in _filt_op_ids]

    # Per-op aggregates
    _op_cs: dict = _dd(lambda: {"total_t": 0.0, "depot_t": 0.0, "cv_t": 0.0})
    for d in _cs_norm:
        _op_cs[d["operation_id"]]["total_t"] += d["total_t"]
        _op_cs[d["operation_id"]]["depot_t"] += d["depot_t"]
        _op_cs[d["operation_id"]]["cv_t"]    += d["cv_t"]

    # Compute correct rhythm per op from actual trip timestamps
    _trips_by_op_d: dict = defaultdict(list)
    for t in all_trips:
        _trips_by_op_d[t.operation_id].append(t)
    _op_rhythm_dash: dict = {}
    for oid, tlist in _trips_by_op_d.items():
        _op_rhythm_dash[oid] = _calc_rhythm_from_trips(tlist).get("t_per_h")

    # Top 5 by t/h — exclude ops that have any CV (rate is misleading / partial)
    top_by_th = sorted(
        [o for o in all_ops if _op_rhythm_dash.get(o.id) and _op_cs[o.id]["cv_t"] == 0],
        key=lambda o: _op_rhythm_dash.get(o.id, 0),
        reverse=True
    )[:5]
    for o in top_by_th:
        o._rhythm_t_h = _op_rhythm_dash.get(o.id, 0)

    # Grand totals
    total_discharged_t = sum(v["total_t"] for v in _op_cs.values())
    total_depot_t      = sum(v["depot_t"] for v in _op_cs.values())
    total_cv_t         = sum(v["cv_t"]    for v in _op_cs.values())
    pct_cv    = total_cv_t    / total_discharged_t * 100 if total_discharged_t > 0 else 0.0
    pct_depot = total_depot_t / total_discharged_t * 100 if total_discharged_t > 0 else 0.0
    avg_per_op_t = round(total_discharged_t / total_ops, 1) if total_ops > 0 else 0

    # Top 5 by total desestibado
    top_by_tons = sorted(all_ops, key=lambda o: _op_cs[o.id]["total_t"], reverse=True)[:5]
    for o in top_by_tons:
        o._total_t = _op_cs[o.id]["total_t"]
        o._depot_t = _op_cs[o.id]["depot_t"]
        o._cv_t    = _op_cs[o.id]["cv_t"]

    # Top 5 by costado vapor
    top_by_cv = sorted(
        [o for o in all_ops if _op_cs[o.id]["cv_t"] > 0],
        key=lambda o: _op_cs[o.id]["cv_t"],
        reverse=True
    )[:5]
    for o in top_by_cv:
        o._cv_tons      = _op_cs[o.id]["cv_t"]
        o._discharged_t = _op_cs[o.id]["total_t"]

    # Product and client distribution (from cargo summaries, not trips)
    _cs_prod_stats:   dict = _dd(lambda: {"total_t": 0.0, "depot_t": 0.0, "cv_t": 0.0})
    _cs_client_stats: dict = _dd(lambda: {"total_t": 0.0, "depot_t": 0.0, "cv_t": 0.0})
    for d in _cs_norm:
        prod   = d["product"] or "(sin producto)"
        client = d["client"]  or "(sin cliente)"
        _cs_prod_stats[prod]["total_t"]     += d["total_t"]
        _cs_prod_stats[prod]["depot_t"]     += d["depot_t"]
        _cs_prod_stats[prod]["cv_t"]        += d["cv_t"]
        _cs_client_stats[client]["total_t"] += d["total_t"]
        _cs_client_stats[client]["depot_t"] += d["depot_t"]
        _cs_client_stats[client]["cv_t"]    += d["cv_t"]

    prod_list   = sorted(_cs_prod_stats.items(),   key=lambda x: -x[1]["total_t"])
    client_list = sorted(_cs_client_stats.items(), key=lambda x: -x[1]["total_t"])
    total_product_t = sum(v["total_t"] for v in _cs_prod_stats.values())
    total_client_t  = sum(v["total_t"] for v in _cs_client_stats.values())

    # Filter options for dropdowns (from cargo_summaries, not trips)
    all_products = sorted({
        r[0] for r in db.query(models.OperationCargoSummary.product)
        .filter(models.OperationCargoSummary.product.isnot(None)).distinct().all()
    })
    all_clients = sorted({
        r[0] for r in db.query(models.OperationCargoSummary.client)
        .filter(models.OperationCargoSummary.client.isnot(None)).distinct().all()
    })

    params = {
        "client": q_client, "product": q_product, "op_type": q_op_type,
        "date_from": q_date_from, "date_to": q_date_to,
    }
    has_filters = any(v for v in params.values())

    return templates.TemplateResponse(request, "operations/dashboard.html", {
        "user":               current_user,
        "total_ops":          total_ops,
        "total_trips":        total_trips,
        "total_neto":         total_neto,          # depot from trips, secondary
        "total_neto_t":       total_neto / 1000 if total_neto else 0,
        "total_discharged_t": total_discharged_t,  # PRIMARY
        "total_depot_t":      total_depot_t,
        "total_cv_t":         total_cv_t,
        "pct_cv":             pct_cv,
        "pct_depot":          pct_depot,
        "avg_per_op":         avg_per_op_t,
        "top_by_tons":        top_by_tons,
        "top_by_th":          top_by_th,
        "top_by_cv":          top_by_cv,
        "prod_list":          prod_list,
        "client_list":        client_list,
        "total_product_t":    total_product_t,
        "total_client_t":     total_client_t,
        "shift_stats":        shift_stats,
        "period_start":       period_start,
        "period_end":         period_end,
        "total_diff_t":       total_diff / 1000 if total_diff else 0,
        "params":             params,
        "has_filters":        has_filters,
        "all_products":       all_products,
        "all_clients":        all_clients,
    })


@router.get("/{op_id}", response_class=HTMLResponse)
async def operation_detail(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_OPERATIONS_ROLES)),
):
    filter_product: str = request.query_params.get("product", "").strip()

    op = db.query(models.Operation).filter(models.Operation.id == op_id).first()
    if not op:
        raise HTTPException(status_code=404)

    trips_q = (
        db.query(models.OperationTrip)
        .filter(models.OperationTrip.operation_id == op_id)
        .order_by(models.OperationTrip.entry_date)
    )
    if filter_product:
        trips_q = trips_q.filter(models.OperationTrip.product == filter_product)
    trips = trips_q.all()

    # Pre-compute rhythm (may be overridden to None if has_cv, done later in context)
    _rhythm = _calc_rhythm_from_trips(trips)

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

    cs_q = (
        db.query(models.OperationCargoSummary)
        .filter(models.OperationCargoSummary.operation_id == op_id)
        .order_by(models.OperationCargoSummary.total_ship_kg.desc())
    )
    if filter_product:
        cs_q = cs_q.filter(models.OperationCargoSummary.product == filter_product)
    cargo_summaries = cs_q.all()

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

    # Split cargo notes: informational (100% CV) vs anomaly warnings
    cargo_notes_info = [
        (cs.product, cs.notes) for cs in cargo_summaries
        if cs.notes and cs.notes.startswith("100% Costado Vapor")
    ]
    cargo_notes_warnings = [
        (cs.product, cs.notes) for cs in cargo_summaries
        if cs.notes and not cs.notes.startswith("100% Costado Vapor")
    ]

    # Rhythm: only for depot-only ops (no CV)
    rhythm_t_h        = _rhythm.get("t_per_h")   if not has_cv else None
    rhythm_duration_h = _rhythm.get("duration_h") if not has_cv else None
    rhythm_first_dt   = _rhythm.get("first_dt")   if not has_cv else None
    rhythm_last_dt    = _rhythm.get("last_dt")    if not has_cv else None

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
        "cargo_notes_info":      cargo_notes_info,
        "cargo_notes_warnings":  cargo_notes_warnings,
        "filter_product":        filter_product,
        "rhythm_t_h":            rhythm_t_h,
        "rhythm_duration_h":     rhythm_duration_h,
        "rhythm_first_dt":       rhythm_first_dt,
        "rhythm_last_dt":        rhythm_last_dt,
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
