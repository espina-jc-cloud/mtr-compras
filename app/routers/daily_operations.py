from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.templates import templates
from app.models_daily_ops import DailyOpDay, DailyOpImport, DailyOpTrip


_DAILY_OPS_ROLES = ("admin", "superadmin")

router = APIRouter(prefix="/operations/daily", tags=["daily-operations"])


def _tn(value) -> float:
    try:
        return round(float(value or 0) / 1000, 1)
    except Exception:
        return 0.0


def _qp(request: Request, name: str, default: str = "") -> str:
    return (request.query_params.get(name) or default).strip()


def _is_operation(value, expected: str) -> bool:
    return (value or "").strip().lower() == expected.lower()


@router.get("", response_class=HTMLResponse)
async def list_daily_operations(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    q_date_from = _qp(request, "date_from")
    q_date_to = _qp(request, "date_to")
    q_client = _qp(request, "client")
    q_product = _qp(request, "product")
    q_operativo = _qp(request, "operativo")

    trips_q = db.query(DailyOpTrip).join(DailyOpDay)

    if q_date_from:
        try:
            trips_q = trips_q.filter(DailyOpDay.op_date >= datetime.strptime(q_date_from, "%Y-%m-%d").date())
        except ValueError:
            pass
    if q_date_to:
        try:
            trips_q = trips_q.filter(DailyOpDay.op_date <= datetime.strptime(q_date_to, "%Y-%m-%d").date())
        except ValueError:
            pass
    if q_client:
        trips_q = trips_q.filter(DailyOpTrip.client == q_client)
    if q_product:
        trips_q = trips_q.filter(DailyOpTrip.product == q_product)
    if q_operativo:
        trips_q = trips_q.filter(DailyOpTrip.operativo == q_operativo)

    total_trips = trips_q.count()
    sums = trips_q.with_entities(
        func.coalesce(func.sum(DailyOpTrip.neto_kg), 0),
        func.coalesce(func.sum(DailyOpTrip.origen_kg), 0),
        func.coalesce(func.sum(DailyOpTrip.diff_kg), 0),
    ).first()

    cargas_movimientos = trips_q.filter(func.lower(DailyOpTrip.operation) == "carga").count()
    cargas_neto_kg = trips_q.filter(func.lower(DailyOpTrip.operation) == "carga").with_entities(
        func.coalesce(func.sum(DailyOpTrip.neto_kg), 0)
    ).scalar() or 0

    descargas_movimientos = trips_q.filter(func.lower(DailyOpTrip.operation) == "descarga").count()
    descargas_neto_kg = trips_q.filter(func.lower(DailyOpTrip.operation) == "descarga").with_entities(
        func.coalesce(func.sum(DailyOpTrip.neto_kg), 0)
    ).scalar() or 0

    clients_count = trips_q.with_entities(func.count(func.distinct(DailyOpTrip.client))).scalar() or 0
    products_count = trips_q.with_entities(func.count(func.distinct(DailyOpTrip.product))).scalar() or 0
    days_count = trips_q.with_entities(func.count(func.distinct(DailyOpTrip.day_id))).scalar() or 0

    top_clients = (
        trips_q.with_entities(
            DailyOpTrip.client,
            func.count(DailyOpTrip.id).label("trips"),
            func.coalesce(func.sum(DailyOpTrip.neto_kg), 0).label("neto_kg"),
        )
        .filter(DailyOpTrip.client.isnot(None))
        .group_by(DailyOpTrip.client)
        .order_by(func.coalesce(func.sum(DailyOpTrip.neto_kg), 0).desc())
        .limit(5)
        .all()
    )

    all_days = (
        db.query(DailyOpDay)
        .order_by(DailyOpDay.op_date.desc())
        .all()
    )

    days_rows = []
    for day in all_days:
        day_trips_q = db.query(DailyOpTrip).filter(DailyOpTrip.day_id == day.id)

        cargas_q = day_trips_q.filter(func.lower(DailyOpTrip.operation) == "carga")
        descargas_q = day_trips_q.filter(func.lower(DailyOpTrip.operation) == "descarga")

        days_rows.append({
            "id": day.id,
            "op_date": day.op_date,
            "trips": day_trips_q.count(),
            "cargas_movimientos": cargas_q.count(),
            "cargas_neto_kg": cargas_q.with_entities(func.coalesce(func.sum(DailyOpTrip.neto_kg), 0)).scalar() or 0,
            "descargas_movimientos": descargas_q.count(),
            "descargas_neto_kg": descargas_q.with_entities(func.coalesce(func.sum(DailyOpTrip.neto_kg), 0)).scalar() or 0,
            "clients": day_trips_q.with_entities(func.count(func.distinct(DailyOpTrip.client))).scalar() or 0,
        })

    clients = [x[0] for x in db.query(DailyOpTrip.client).filter(DailyOpTrip.client.isnot(None)).distinct().order_by(DailyOpTrip.client).all()]
    products = [x[0] for x in db.query(DailyOpTrip.product).filter(DailyOpTrip.product.isnot(None)).distinct().order_by(DailyOpTrip.product).all()]
    operativos = [x[0] for x in db.query(DailyOpTrip.operativo).filter(DailyOpTrip.operativo.isnot(None)).distinct().order_by(DailyOpTrip.operativo).all()]

    stats = {
        "total_trips": total_trips,
        "neto_tn": _tn(sums[0] if sums else 0),
        "origen_tn": _tn(sums[1] if sums else 0),
        "diff_tn": _tn(sums[2] if sums else 0),
        "cargas_movimientos": cargas_movimientos,
        "cargas_tn": _tn(cargas_neto_kg),
        "descargas_movimientos": descargas_movimientos,
        "descargas_tn": _tn(descargas_neto_kg),
        "clients_count": clients_count,
        "products_count": products_count,
        "days_count": days_count,
    }

    return templates.TemplateResponse(
        request,
        "operations/daily/list.html",
        {
            "request": request,
            "current_user": current_user,
            "stats": stats,
            "top_clients": top_clients,
            "days_rows": days_rows,
            "clients": clients,
            "products": products,
            "operativos": operativos,
            "filters": {
                "date_from": q_date_from,
                "date_to": q_date_to,
                "client": q_client,
                "product": q_product,
                "operativo": q_operativo,
            },
            "tn": _tn,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_daily_operation(
    request: Request,
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    return templates.TemplateResponse(
        request,
        "operations/daily/new.html",
        {"request": request, "current_user": current_user},
    )


@router.post("/new")
async def create_daily_operation(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    from app.daily_ops_parser import parse_old_system_html

    form = await request.form()
    files = form.getlist("files")
    imported_by = getattr(current_user, "email", None)

    if not files:
        raise HTTPException(status_code=400, detail="Tenés que subir al menos un archivo.")

    imported_day_ids = []

    for uploaded in files:
        filename = getattr(uploaded, "filename", "") or ""
        if not filename:
            continue

        content = await uploaded.read()
        if not content:
            continue

        parsed = parse_old_system_html(content)
        trips = parsed.get("trips", [])
        operativo = parsed.get("operativo") or "Sin operativo"
        upload_group_id = str(uuid4())

        # Agrupar los viajes del archivo por fecha real detectada en el Excel.
        trips_by_date = defaultdict(list)

        for item in trips:
            entry_date = item.get("entry_date")
            exit_date = item.get("exit_date")
            detected_date = None

            if entry_date:
                detected_date = entry_date.date()
            elif exit_date:
                detected_date = exit_date.date()

            if not detected_date:
                continue

            trips_by_date[detected_date].append(item)

        for op_date, day_trips in trips_by_date.items():
            day = db.query(DailyOpDay).filter(DailyOpDay.op_date == op_date).first()
            if not day:
                day = DailyOpDay(
                    op_date=op_date,
                    created_by=imported_by,
                )
                db.add(day)
                db.commit()
                db.refresh(day)

            imp = DailyOpImport(
                day_id=day.id,
                filename=filename,
                upload_group_id=upload_group_id,
                operativo=operativo,
                row_count=len(day_trips),
                imported_by=imported_by,
            )
            db.add(imp)
            db.commit()
            db.refresh(imp)

            for item in day_trips:
                trip = DailyOpTrip(
                    day_id=day.id,
                    import_id=imp.id,
                    trip_code=item.get("trip_code"),
                    entry_date=item.get("entry_date"),
                    entry_time=item.get("entry_time"),
                    exit_date=item.get("exit_date"),
                    exit_time=item.get("exit_time"),
                    plate=item.get("plate"),
                    trailer_plate=item.get("trailer_plate"),
                    tara_kg=item.get("tara_kg"),
                    bruto_kg=item.get("bruto_kg"),
                    neto_kg=item.get("neto_kg"),
                    origen_kg=item.get("origen_kg"),
                    diff_kg=item.get("diff_kg"),
                    driver=item.get("driver"),
                    client=item.get("client"),
                    product=item.get("product"),
                    transporte=item.get("transporte"),
                    operation=item.get("operation"),
                    remito=item.get("remito"),
                    operativo=item.get("operativo") or operativo,
                    planta=item.get("planta"),
                    duration_min=item.get("duration_min"),
                    shift_number=item.get("shift_number"),
                )
                db.add(trip)

            db.commit()

            if day.id not in imported_day_ids:
                imported_day_ids.append(day.id)

    if len(imported_day_ids) == 1:
        return RedirectResponse(url=f"/operations/daily/{imported_day_ids[0]}", status_code=303)

    return RedirectResponse(url="/operations/daily", status_code=303)


@router.get("/imports", response_class=HTMLResponse)
async def list_daily_imports(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    imports = (
        db.query(DailyOpImport)
        .join(DailyOpDay)
        .order_by(DailyOpImport.imported_at.desc())
        .all()
    )

    grouped = {}
    for imp in imports:
        key = imp.upload_group_id or f"legacy-{imp.filename}-{imp.imported_at.strftime('%Y%m%d%H%M') if imp.imported_at else imp.id}"

        if key not in grouped:
            grouped[key] = {
                "group_id": imp.upload_group_id,
                "legacy_key": key if not imp.upload_group_id else None,
                "filename": imp.filename,
                "imported_at": imp.imported_at,
                "operativo": imp.operativo,
                "row_count": 0,
                "days_count": 0,
                "day_id": imp.day_id,
                "import_ids": [],
            }

        grouped[key]["row_count"] += imp.row_count or 0
        grouped[key]["days_count"] += 1
        grouped[key]["import_ids"].append(imp.id)

    import_groups = list(grouped.values())

    return templates.TemplateResponse(
        request,
        "operations/daily/imports.html",
        {
            "request": request,
            "current_user": current_user,
            "import_groups": import_groups,
        },
    )



@router.get("/{day_id}", response_class=HTMLResponse)
async def daily_operation_detail(
    day_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    day = db.query(DailyOpDay).filter(DailyOpDay.id == day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="Día no encontrado.")

    q_client = _qp(request, "client")
    q_product = _qp(request, "product")
    q_operativo = _qp(request, "operativo")
    q_transporte = _qp(request, "transporte")

    trips_q = db.query(DailyOpTrip).filter(DailyOpTrip.day_id == day.id)

    if q_client:
        trips_q = trips_q.filter(DailyOpTrip.client == q_client)
    if q_product:
        trips_q = trips_q.filter(DailyOpTrip.product == q_product)
    if q_operativo:
        trips_q = trips_q.filter(DailyOpTrip.operativo == q_operativo)
    if q_transporte:
        trips_q = trips_q.filter(DailyOpTrip.transporte == q_transporte)

    trips = trips_q.order_by(DailyOpTrip.client.asc(), DailyOpTrip.entry_date.asc()).all()

    sums = trips_q.with_entities(
        func.coalesce(func.sum(DailyOpTrip.neto_kg), 0),
        func.coalesce(func.sum(DailyOpTrip.origen_kg), 0),
        func.coalesce(func.sum(DailyOpTrip.diff_kg), 0),
    ).first()

    by_client = defaultdict(lambda: {
        "client": "",
        "trips": [],
        "total_trips": 0,
        "neto_kg": 0,
        "origen_kg": 0,
        "diff_kg": 0,
        "products": set(),
    })

    for trip in trips:
        key = trip.client or "Sin cliente"
        group = by_client[key]
        group["client"] = key
        group["trips"].append(trip)
        group["total_trips"] += 1
        group["neto_kg"] += trip.neto_kg or 0
        group["origen_kg"] += trip.origen_kg or 0
        group["diff_kg"] += trip.diff_kg or 0
        if trip.product:
            group["products"].add(trip.product)

    client_groups = list(by_client.values())
    client_groups.sort(key=lambda x: x["neto_kg"], reverse=True)

    clients = [x[0] for x in db.query(DailyOpTrip.client).filter(DailyOpTrip.day_id == day.id, DailyOpTrip.client.isnot(None)).distinct().order_by(DailyOpTrip.client).all()]
    products = [x[0] for x in db.query(DailyOpTrip.product).filter(DailyOpTrip.day_id == day.id, DailyOpTrip.product.isnot(None)).distinct().order_by(DailyOpTrip.product).all()]
    operativos = [x[0] for x in db.query(DailyOpTrip.operativo).filter(DailyOpTrip.day_id == day.id, DailyOpTrip.operativo.isnot(None)).distinct().order_by(DailyOpTrip.operativo).all()]
    transportes = [x[0] for x in db.query(DailyOpTrip.transporte).filter(DailyOpTrip.day_id == day.id, DailyOpTrip.transporte.isnot(None)).distinct().order_by(DailyOpTrip.transporte).all()]

    cargas_q = trips_q.filter(func.lower(DailyOpTrip.operation) == "carga")
    descargas_q = trips_q.filter(func.lower(DailyOpTrip.operation) == "descarga")

    cargas_neto_kg = cargas_q.with_entities(func.coalesce(func.sum(DailyOpTrip.neto_kg), 0)).scalar() or 0
    descargas_neto_kg = descargas_q.with_entities(func.coalesce(func.sum(DailyOpTrip.neto_kg), 0)).scalar() or 0

    stats = {
        "total_trips": trips_q.count(),
        "neto_tn": _tn(sums[0] if sums else 0),
        "origen_tn": _tn(sums[1] if sums else 0),
        "diff_tn": _tn(sums[2] if sums else 0),
        "cargas_movimientos": cargas_q.count(),
        "cargas_tn": _tn(cargas_neto_kg),
        "descargas_movimientos": descargas_q.count(),
        "descargas_tn": _tn(descargas_neto_kg),
        "clients_count": len(clients),
        "products_count": len(products),
    }

    return templates.TemplateResponse(
        request,
        "operations/daily/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "day": day,
            "stats": stats,
            "client_groups": client_groups,
            "imports": day.imports,
            "clients": clients,
            "products": products,
            "operativos": operativos,
            "transportes": transportes,
            "filters": {
                "client": q_client,
                "product": q_product,
                "operativo": q_operativo,
                "transporte": q_transporte,
            },
            "tn": _tn,
        },
    )


@router.post("/imports/legacy/{filename}/delete")
async def delete_legacy_daily_import_file(
    filename: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    imports = (
        db.query(DailyOpImport)
        .filter(
            DailyOpImport.filename == filename,
            DailyOpImport.upload_group_id.is_(None),
        )
        .all()
    )

    if not imports:
        raise HTTPException(status_code=404, detail="Archivo viejo no encontrado.")

    affected_day_ids = list({imp.day_id for imp in imports})

    for imp in imports:
        db.delete(imp)

    db.commit()

    for day_id in affected_day_ids:
        remaining_trips = db.query(DailyOpTrip).filter(DailyOpTrip.day_id == day_id).count()
        remaining_imports = db.query(DailyOpImport).filter(DailyOpImport.day_id == day_id).count()

        if remaining_trips == 0 and remaining_imports == 0:
            day = db.query(DailyOpDay).filter(DailyOpDay.id == day_id).first()
            if day:
                db.delete(day)

    db.commit()

    return RedirectResponse(url="/operations/daily/imports", status_code=303)



@router.post("/imports/{group_id}/delete")
async def delete_daily_import_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    imports = (
        db.query(DailyOpImport)
        .filter(DailyOpImport.upload_group_id == group_id)
        .all()
    )

    if not imports:
        raise HTTPException(status_code=404, detail="Archivo importado no encontrado.")

    affected_day_ids = list({imp.day_id for imp in imports})

    for imp in imports:
        db.delete(imp)

    db.commit()

    for day_id in affected_day_ids:
        remaining_trips = db.query(DailyOpTrip).filter(DailyOpTrip.day_id == day_id).count()
        remaining_imports = db.query(DailyOpImport).filter(DailyOpImport.day_id == day_id).count()

        if remaining_trips == 0 and remaining_imports == 0:
            day = db.query(DailyOpDay).filter(DailyOpDay.id == day_id).first()
            if day:
                db.delete(day)

    db.commit()

    return RedirectResponse(url="/operations/daily/imports", status_code=303)



@router.post("/{day_id}/trips/{trip_id}/delete")
async def delete_daily_trip(
    day_id: int,
    trip_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    trip = (
        db.query(DailyOpTrip)
        .filter(DailyOpTrip.id == trip_id, DailyOpTrip.day_id == day_id)
        .first()
    )

    if not trip:
        raise HTTPException(status_code=404, detail="Viaje no encontrado.")

    db.delete(trip)
    db.commit()

    return RedirectResponse(url=f"/operations/daily/{day_id}", status_code=303)



@router.post("/{day_id}/imports/{import_id}/delete")
async def delete_daily_import(
    day_id: int,
    import_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    imp = (
        db.query(DailyOpImport)
        .filter(
            DailyOpImport.id == import_id,
            DailyOpImport.day_id == day_id,
        )
        .first()
    )

    if not imp:
        raise HTTPException(status_code=404, detail="Archivo importado no encontrado.")

    db.delete(imp)
    db.commit()

    remaining_trips = db.query(DailyOpTrip).filter(DailyOpTrip.day_id == day_id).count()
    remaining_imports = db.query(DailyOpImport).filter(DailyOpImport.day_id == day_id).count()

    if remaining_trips == 0 and remaining_imports == 0:
        day = db.query(DailyOpDay).filter(DailyOpDay.id == day_id).first()
        if day:
            db.delete(day)
            db.commit()
        return RedirectResponse(url="/operations/daily", status_code=303)

    return RedirectResponse(url=f"/operations/daily/{day_id}", status_code=303)



@router.post("/{day_id}/delete")
async def delete_daily_operation(
    day_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_DAILY_OPS_ROLES)),
):
    day = db.query(DailyOpDay).filter(DailyOpDay.id == day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="Día no encontrado.")

    db.delete(day)
    db.commit()

    return RedirectResponse(url="/operations/daily", status_code=303)
