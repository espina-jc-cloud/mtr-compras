"""
Servicios de equipo — trabajos esporádicos de alquiler (autoelevador, etc.).
Documenta los operativos que el supervisor pasa por WhatsApp.
"""
import re
from datetime import date, datetime

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.permissions import require_perm
from app.templates import templates
from app.models_servicios import ServicioEquipo, ESTADOS, ESTADO_CSS

router = APIRouter(prefix="/servicios")
_guard = require_perm("operaciones.servicios")


def _num(s):
    try:
        return float(str(s).replace(",", ".")) if str(s).strip() else None
    except ValueError:
        return None


def _to_date(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


# ── Listado ────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def listado(request: Request, db: Session = Depends(get_db),
                  current_user=Depends(_guard), estado: str = "", q: str = ""):
    query = db.query(ServicioEquipo)
    if estado:
        query = query.filter(ServicioEquipo.estado == estado)
    if q:
        like = f"%{q}%"
        query = query.filter(func.lower(ServicioEquipo.cliente).like(func.lower(like))
                             | func.lower(ServicioEquipo.conductor).like(func.lower(like))
                             | func.lower(ServicioEquipo.descripcion).like(func.lower(like)))
    servicios = query.order_by(ServicioEquipo.fecha.desc().nullslast(),
                               ServicioEquipo.id.desc()).limit(500).all()

    total_horas = sum(float(s.horas_facturadas or 0) for s in servicios)
    por_facturar = sum(1 for s in servicios if s.estado == "por_facturar")
    a_cobrar = sum(float(s.importe or 0) for s in servicios if s.estado != "cobrado")

    return templates.TemplateResponse(request, "servicios/list.html", {
        "current_user": current_user, "servicios": servicios,
        "estados": ESTADOS, "estado_css": ESTADO_CSS,
        "f_estado": estado, "f_q": q,
        "kpis": {"total": len(servicios), "horas": round(total_horas, 1),
                 "por_facturar": por_facturar, "a_cobrar": round(a_cobrar)},
    })


# ── Nuevo / editar ─────────────────────────────────────────────────────────
@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo_form(request: Request, current_user=Depends(_guard)):
    return templates.TemplateResponse(request, "servicios/form.html", {
        "current_user": current_user, "s": None, "estados": ESTADOS,
        "form_action": "/servicios/nuevo", "error": None,
    })


def _read_form(form):
    g = lambda k: str(form.get(k, "") or "").strip()
    horas = _num(g("horas_facturadas"))
    tarifa = _num(g("tarifa_hora"))
    importe = _num(g("importe"))
    if importe is None and horas is not None and tarifa is not None:
        importe = round(horas * tarifa, 2)
    return dict(
        fecha=_to_date(g("fecha")), cliente=g("cliente") or None,
        equipo=g("equipo") or None, conductor=g("conductor") or None,
        hora_inicio=g("hora_inicio") or None, hora_fin=g("hora_fin") or None,
        horas_facturadas=horas, origen=g("origen") or None, destino=g("destino") or None,
        descripcion=g("descripcion") or None, tarifa_hora=tarifa, importe=importe,
        estado=g("estado") or "por_facturar",
        mensaje_original=g("mensaje_original") or None, notas=g("notas") or None,
    )


@router.post("/nuevo")
async def crear(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    form = await request.form()
    data = _read_form(form)
    if not data["cliente"] and not data["descripcion"]:
        return templates.TemplateResponse(request, "servicios/form.html", {
            "current_user": current_user, "s": data, "estados": ESTADOS,
            "form_action": "/servicios/nuevo",
            "error": "Cargá al menos el cliente o una descripción del trabajo.",
        }, status_code=422)
    s = ServicioEquipo(created_by=current_user.name, **data)
    db.add(s)
    db.commit()
    return RedirectResponse(url="/servicios?ok=Servicio+cargado", status_code=303)


@router.get("/{sid}", response_class=HTMLResponse)
async def detalle(sid: int, request: Request, db: Session = Depends(get_db),
                  current_user=Depends(_guard)):
    s = db.query(ServicioEquipo).filter_by(id=sid).first()
    if not s:
        raise HTTPException(404, "Servicio no encontrado")
    return templates.TemplateResponse(request, "servicios/form.html", {
        "current_user": current_user, "s": s, "estados": ESTADOS,
        "form_action": f"/servicios/{sid}/editar", "error": None,
    })


@router.post("/{sid}/editar")
async def editar(sid: int, request: Request, db: Session = Depends(get_db),
                 current_user=Depends(_guard)):
    s = db.query(ServicioEquipo).filter_by(id=sid).first()
    if not s:
        raise HTTPException(404, "Servicio no encontrado")
    form = await request.form()
    for k, v in _read_form(form).items():
        setattr(s, k, v)
    db.commit()
    return RedirectResponse(url="/servicios?ok=Servicio+actualizado", status_code=303)


@router.post("/{sid}/borrar")
async def borrar(sid: int, db: Session = Depends(get_db), current_user=Depends(_guard)):
    s = db.query(ServicioEquipo).filter_by(id=sid).first()
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse(url="/servicios?ok=Servicio+eliminado", status_code=303)
