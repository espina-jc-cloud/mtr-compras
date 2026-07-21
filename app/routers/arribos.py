"""
Módulo Próximos Arribos (dentro de Operaciones).

Alta manual + enriquecimiento por import del lineup PDF de San Nicolás
(solo actualiza los buques que el usuario sigue) + edición manual + historial.
"""
import json
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from app.database import get_db
from app.permissions import require_perm
from app import models
from app.models_arribos import (
    ProximoArribo, ArriboUpdate,
    ARRIBO_ESTADOS, ARRIBO_ESTADO_LABELS, ARRIBO_ESTADO_CSS,
)
from app.lineup_parser import parse_lineup_pdf, canon_vessel
from app.templates import templates

router = APIRouter(prefix="/operations/arribos")
_guard = require_perm("operaciones.arribos")

# Campos que el lineup PUEDE actualizar (los manuales —cliente, mercadería,
# comentario, observaciones, tonelaje— nunca se pisan desde el PDF).
LINEUP_FIELDS = [
    ("etb",        "ETB"),
    ("etc",        "ETC"),
    ("ready",      "Ready estimado"),
    ("muelle",     "Muelle"),
    ("posicion",   "Posición / Sector"),
    ("operacion",  "Operación"),
    ("agencia",    "Agencia"),
    ("procedencia","Procedencia"),
]


def _dec(s):
    if s is None or str(s).strip() == "":
        return None
    try:
        return Decimal(str(s).strip().replace(".", "").replace(",", ".")) if "," in str(s) else Decimal(str(s).strip())
    except InvalidOperation:
        return None


def _parse_fecha(s):
    """Date desde texto libre: 'dd/mm/yyyy', 'dd-mm-yyyy', 'dd/mm' (asume año) o ISO.

    Tolerante: ignora sufijos como 'AM/PM/estimado'. Para 'dd/mm' sin año usa el
    año actual; si la fecha quedó muy en el pasado (>180 días), pasa al siguiente.
    """
    if not s:
        return None
    import re
    s = str(s).strip()
    # ISO 'yyyy-mm-dd'
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    # dd/mm/yyyy o dd-mm-yyyy
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    # dd/mm (sin año) → asumir año actual, ajustar si quedó muy en el pasado
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", s)
    if m:
        from datetime import timedelta
        today = date.today()
        try:
            d = date(today.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        if d < today - timedelta(days=180):
            try:
                d = d.replace(year=today.year + 1)
            except ValueError:
                pass
        return d
    return None


def _sort_date(arribo_or_etb, ready=None):
    """Fecha de orden: ETB primero, si no Ready estimado."""
    etb = arribo_or_etb
    return _parse_fecha(etb) or _parse_fecha(ready)


def _lineup_value(vessel, field):
    """Valor que aporta el lineup para un campo del arribo (o '' si no trae)."""
    if field == "procedencia":
        return vessel.get("origen") or ""
    return vessel.get(field) or ""


# ── LISTADO ─────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_arribos(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    def qp(n, d=""):
        v = request.query_params.getlist(n)
        return v[0].strip() if v else d

    q_estado = qp("estado")
    q_cli    = qp("cliente")
    q_texto  = qp("q")

    aq = db.query(ProximoArribo).filter(ProximoArribo.deleted_at.is_(None))
    if q_estado in ARRIBO_ESTADO_LABELS:
        aq = aq.filter(ProximoArribo.estado == q_estado)
    if q_cli:
        aq = aq.filter(ProximoArribo.cliente.ilike(f"%{q_cli}%"))
    if q_texto:
        aq = aq.filter(or_(
            ProximoArribo.buque.ilike(f"%{q_texto}%"),
            ProximoArribo.mercaderia.ilike(f"%{q_texto}%"),
        ))

    # Orden: por fecha estimada (ETB→Ready) ascendente; los sin fecha al final.
    arribos = aq.order_by(
        func.coalesce(ProximoArribo.fecha_estimada, "9999-12-31").asc(),
        ProximoArribo.updated_at.desc(),
    ).all()

    return templates.TemplateResponse(request, "operations/arribos/list.html", {
        "user": current_user, "arribos": arribos,
        "estados": ARRIBO_ESTADOS, "estado_css": ARRIBO_ESTADO_CSS,
        "params": {"estado": q_estado, "cliente": q_cli, "q": q_texto},
        "saved": request.query_params.get("saved"),
    })


# ── VISTA COMPARTIBLE (board) ─────────────────────────────────────────────────

@router.get("/board", response_class=HTMLResponse)
async def board(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    arribos = (db.query(ProximoArribo)
               .filter(ProximoArribo.deleted_at.is_(None),
                       ProximoArribo.estado != "cancelado",
                       ProximoArribo.estado != "finalizado")
               .order_by(func.coalesce(ProximoArribo.fecha_estimada, "9999-12-31").asc(),
                         ProximoArribo.updated_at.desc())
               .all())
    return templates.TemplateResponse(request, "operations/arribos/board.html", {
        "user": current_user, "arribos": arribos,
        "estado_css": ARRIBO_ESTADO_CSS, "estado_labels": ARRIBO_ESTADO_LABELS,
        "now": datetime.utcnow(),
    })


# ── ALTA MANUAL ───────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_arribo_form(request: Request, current_user=Depends(_guard)):
    return templates.TemplateResponse(request, "operations/arribos/form.html", {
        "user": current_user, "arribo": None, "estados": ARRIBO_ESTADOS, "error": None,
    })


@router.post("/new")
async def create_arribo(
    request: Request,
    buque: str = Form(...),
    cliente: str = Form(""),
    mercaderia: str = Form(""),
    tonelaje_estimado: str = Form(""),
    procedencia: str = Form(""),
    agencia: str = Form(""),
    operacion: str = Form(""),
    estado: str = Form("esperado"),
    fecha_estimada: str = Form(""),
    etb: str = Form(""),
    etc: str = Form(""),
    ready: str = Form(""),
    muelle: str = Form(""),
    posicion: str = Form(""),
    amarre: str = Form(""),
    observaciones: str = Form(""),
    comentario_operativo: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_guard),
):
    if not buque.strip():
        raise HTTPException(status_code=422, detail="El nombre del buque es obligatorio.")
    a = ProximoArribo(
        buque=buque.strip(), buque_canon=canon_vessel(buque),
        cliente=cliente.strip() or None, mercaderia=mercaderia.strip() or None,
        tonelaje_estimado=_dec(tonelaje_estimado), procedencia=procedencia.strip() or None,
        agencia=agencia.strip() or None, operacion=operacion.strip() or None,
        estado=estado if estado in ARRIBO_ESTADO_LABELS else "esperado",
        fecha_estimada=_parse_fecha(fecha_estimada) or _sort_date(etb, ready),
        etb=etb.strip() or None, etc=etc.strip() or None, ready=ready.strip() or None,
        muelle=muelle.strip() or None, posicion=posicion.strip() or None,
        amarre=amarre.strip() or None, observaciones=observaciones.strip() or None,
        comentario_operativo=comentario_operativo.strip() or None,
        last_update_source="manual", last_update_at=datetime.utcnow(),
        created_by_id=current_user.id,
    )
    db.add(a)
    db.flush()
    db.add(ArriboUpdate(arribo_id=a.id, source="manual",
                        resumen="Alta manual del arribo.", created_by_id=current_user.id))
    db.commit()
    return RedirectResponse(url=f"/operations/arribos/{a.id}", status_code=303)


# ── IMPORTAR LINEUP PDF ────────────────────────────────────────────────────────

@router.get("/import", response_class=HTMLResponse)
async def import_form(request: Request, current_user=Depends(_guard)):
    return templates.TemplateResponse(request, "operations/arribos/import.html", {
        "user": current_user, "preview": None, "error": None,
    })


@router.post("/import", response_class=HTMLResponse)
async def import_preview(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(_guard),
):
    def _err(msg):
        return templates.TemplateResponse(request, "operations/arribos/import.html", {
            "user": current_user, "preview": None, "error": msg,
        })

    if not file or not file.filename:
        return _err("Tenés que seleccionar un PDF del lineup.")
    content = await file.read()
    try:
        pdf_date, vessels = parse_lineup_pdf(content)
    except ValueError as e:
        return _err(str(e))
    if not vessels:
        return _err("No se detectaron buques en el PDF.")

    # Matchear contra MIS arribos activos.
    arribos = (db.query(ProximoArribo)
               .filter(ProximoArribo.deleted_at.is_(None),
                       ProximoArribo.estado.notin_(("finalizado", "cancelado")))
               .all())
    by_canon = {v["buque_canon"]: v for v in vessels}

    matched = []
    matched_canons = set()
    for a in arribos:
        v = by_canon.get(a.buque_canon)
        if not v:  # fallback: contains
            for vc, vv in by_canon.items():
                if vc and (vc in a.buque_canon or a.buque_canon in vc) and len(vc) >= 4:
                    v = vv
                    break
        if not v:
            continue
        diffs = []
        for field, label in LINEUP_FIELDS:
            new = _lineup_value(v, field).strip()
            cur = (getattr(a, field) or "")
            cur = str(cur).strip()
            if new and new != cur:
                diffs.append({"field": field, "label": label, "old": cur, "new": new})
        matched_canons.add(v["buque_canon"])
        matched.append({"arribo_id": a.id, "buque": a.buque, "estado": a.estado,
                        "vessel": v, "diffs": diffs})

    unmatched = [v for v in vessels if v["buque_canon"] not in matched_canons]

    return templates.TemplateResponse(request, "operations/arribos/import.html", {
        "user": current_user,
        "preview": matched,
        "vessels_json": json.dumps({m["arribo_id"]: m["vessel"] for m in matched}),
        "filename": file.filename,
        "pdf_date": pdf_date,
        "total_vessels": len(vessels),
        "matched_count": len(matched),
        "with_changes": sum(1 for m in matched if m["diffs"]),
        "unmatched": unmatched,
        "error": None,
    })


@router.post("/import/confirm")
async def import_confirm(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_guard),
):
    form = await request.form()
    filename = str(form.get("filename", "lineup.pdf"))
    try:
        vessels_by_arribo = json.loads(form.get("vessels_json", "{}"))
    except Exception:
        raise HTTPException(400, "Datos de importación inválidos.")

    updated = 0
    for arribo_id_str, v in vessels_by_arribo.items():
        a = db.query(ProximoArribo).filter(
            ProximoArribo.id == int(arribo_id_str),
            ProximoArribo.deleted_at.is_(None),
        ).first()
        if not a:
            continue
        changed = []
        for field, label in LINEUP_FIELDS:
            new = _lineup_value(v, field).strip()
            cur = str(getattr(a, field) or "").strip()
            if new and new != cur:
                setattr(a, field, new)
                changed.append(label)
        # fecha_estimada (orden): ETB y, si no hay, Ready estimado.
        f = _sort_date(v.get("etb"), v.get("ready"))
        if f and a.fecha_estimada != f:
            a.fecha_estimada = f
            changed.append("Fecha estimada")
        if changed:
            a.last_update_source = "lineup"
            a.last_update_at = datetime.utcnow()
            a.last_lineup_file = filename
            db.add(ArriboUpdate(
                arribo_id=a.id, source="lineup", lineup_file=filename,
                resumen="Actualizado desde lineup: " + ", ".join(changed) + ".",
                created_by_id=current_user.id,
            ))
            updated += 1
    db.commit()
    from urllib.parse import quote as _q
    return RedirectResponse(
        url=f"/operations/arribos?saved={_q(f'{updated} arribo(s) actualizados desde el lineup')}",
        status_code=303)


# ── DETALLE ─────────────────────────────────────────────────────────────────

@router.get("/{arribo_id}", response_class=HTMLResponse)
async def arribo_detail(arribo_id: int, request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    a = db.query(ProximoArribo).filter(ProximoArribo.id == arribo_id, ProximoArribo.deleted_at.is_(None)).first()
    if not a:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "operations/arribos/detail.html", {
        "user": current_user, "a": a, "estado_css": ARRIBO_ESTADO_CSS,
        "estado_labels": ARRIBO_ESTADO_LABELS, "lineup_fields": LINEUP_FIELDS,
    })


# ── EDICIÓN ─────────────────────────────────────────────────────────────────

@router.get("/{arribo_id}/edit", response_class=HTMLResponse)
async def edit_arribo_form(arribo_id: int, request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    a = db.query(ProximoArribo).filter(ProximoArribo.id == arribo_id, ProximoArribo.deleted_at.is_(None)).first()
    if not a:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "operations/arribos/form.html", {
        "user": current_user, "arribo": a, "estados": ARRIBO_ESTADOS, "error": None,
    })


@router.post("/{arribo_id}/edit")
async def update_arribo(
    arribo_id: int,
    request: Request,
    buque: str = Form(...),
    cliente: str = Form(""),
    mercaderia: str = Form(""),
    tonelaje_estimado: str = Form(""),
    procedencia: str = Form(""),
    agencia: str = Form(""),
    operacion: str = Form(""),
    estado: str = Form("esperado"),
    fecha_estimada: str = Form(""),
    etb: str = Form(""),
    etc: str = Form(""),
    ready: str = Form(""),
    muelle: str = Form(""),
    posicion: str = Form(""),
    amarre: str = Form(""),
    observaciones: str = Form(""),
    comentario_operativo: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_guard),
):
    a = db.query(ProximoArribo).filter(ProximoArribo.id == arribo_id, ProximoArribo.deleted_at.is_(None)).first()
    if not a:
        raise HTTPException(status_code=404)
    if not buque.strip():
        raise HTTPException(status_code=422, detail="El nombre del buque es obligatorio.")

    a.buque = buque.strip()
    a.buque_canon = canon_vessel(buque)
    a.cliente = cliente.strip() or None
    a.mercaderia = mercaderia.strip() or None
    a.tonelaje_estimado = _dec(tonelaje_estimado)
    a.procedencia = procedencia.strip() or None
    a.agencia = agencia.strip() or None
    a.operacion = operacion.strip() or None
    a.estado = estado if estado in ARRIBO_ESTADO_LABELS else a.estado
    a.fecha_estimada = _parse_fecha(fecha_estimada) or _sort_date(etb, ready)
    a.etb = etb.strip() or None
    a.etc = etc.strip() or None
    a.ready = ready.strip() or None
    a.muelle = muelle.strip() or None
    a.posicion = posicion.strip() or None
    a.amarre = amarre.strip() or None
    a.observaciones = observaciones.strip() or None
    a.comentario_operativo = comentario_operativo.strip() or None
    a.last_update_source = "manual"
    a.last_update_at = datetime.utcnow()
    db.add(ArriboUpdate(arribo_id=a.id, source="manual",
                        resumen="Edición manual.", created_by_id=current_user.id))
    db.commit()
    return RedirectResponse(url=f"/operations/arribos/{a.id}", status_code=303)


@router.post("/{arribo_id}/delete")
async def delete_arribo(arribo_id: int, db: Session = Depends(get_db), current_user=Depends(_guard)):
    a = db.query(ProximoArribo).filter(ProximoArribo.id == arribo_id).first()
    if not a:
        raise HTTPException(status_code=404)
    a.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/operations/arribos?ok=Arribo+eliminado", status_code=303)
