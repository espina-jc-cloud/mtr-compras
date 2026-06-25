"""
Módulo Finanzas — Tesorería (caja/banco), movimientos y centros de costo.

Núcleo administrativo del ERP. Solo admin/superadmin acceden (datos sensibles).
El saldo de cada cuenta se calcula desde los movimientos (ver finanzas_utils).
"""
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.permissions import require_perm
from app import models
from app.models_finanzas import (
    CentroCosto, CuentaTesoreria, MovimientoTesoreria,
    TIPOS_CUENTA, MONEDAS, TIPOS_MOVIMIENTO, MEDIOS_PAGO, CONTRAPARTE_TIPOS,
    TIPO_CUENTA_LABELS, MONEDA_LABELS, TIPO_MOVIMIENTO_LABELS,
    MEDIO_PAGO_LABELS, CONTRAPARTE_TIPO_LABELS,
)
from app.models_tariffs import Client
from app.finanzas_utils import saldo_cuenta, saldos_por_moneda
from app.templates import templates

router = APIRouter(prefix="/finanzas")

# Acceso a Finanzas → permiso "finanzas.tesoreria".
_admin = require_perm("finanzas.tesoreria")


def _parse_decimal(s):
    if s is None:
        return None
    s = str(s).strip().replace(".", "").replace(",", ".") if "," in str(s) else str(s).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_date(s, default=None):
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return default


# ── DASHBOARD ───────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    cuentas = db.query(CuentaTesoreria).filter(CuentaTesoreria.activo.is_(True)).all()
    cuentas_con_saldo = [(c, saldo_cuenta(db, c)) for c in cuentas]
    por_moneda = saldos_por_moneda(db)

    # Últimos movimientos (todas las cuentas)
    ultimos = (
        db.query(MovimientoTesoreria)
        .filter(MovimientoTesoreria.deleted_at.is_(None))
        .order_by(MovimientoTesoreria.fecha.desc(), MovimientoTesoreria.id.desc())
        .limit(15)
        .all()
    )

    return templates.TemplateResponse(request, "finanzas/dashboard.html", {
        "user":              current_user,
        "cuentas_con_saldo": cuentas_con_saldo,
        "por_moneda":        por_moneda,
        "ultimos":           ultimos,
        "tipo_cuenta_labels": TIPO_CUENTA_LABELS,
        "moneda_labels":     MONEDA_LABELS,
    })


# ── CUENTAS ─────────────────────────────────────────────────────────────────

@router.get("/cuentas", response_class=HTMLResponse)
async def cuentas_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    cuentas = db.query(CuentaTesoreria).order_by(CuentaTesoreria.activo.desc(), CuentaTesoreria.nombre).all()
    cuentas_con_saldo = [(c, saldo_cuenta(db, c)) for c in cuentas]
    return templates.TemplateResponse(request, "finanzas/cuentas.html", {
        "user":               current_user,
        "cuentas_con_saldo":  cuentas_con_saldo,
        "tipos_cuenta":       TIPOS_CUENTA,
        "monedas":            MONEDAS,
        "tipo_cuenta_labels": TIPO_CUENTA_LABELS,
        "moneda_labels":      MONEDA_LABELS,
    })


@router.post("/cuentas/new")
async def cuenta_new(
    nombre:        str = Form(...),
    tipo:          str = Form("caja"),
    banco:         str = Form(""),
    numero_cuenta: str = Form(""),
    cbu:           str = Form(""),
    alias_cbu:     str = Form(""),
    moneda:        str = Form("ARS"),
    saldo_inicial: str = Form("0"),
    notas:         str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    if not nombre.strip():
        raise HTTPException(status_code=422, detail="Nombre obligatorio")
    if tipo not in TIPO_CUENTA_LABELS:
        raise HTTPException(status_code=422, detail="Tipo inválido")
    cuenta = CuentaTesoreria(
        nombre        = nombre.strip(),
        tipo          = tipo,
        banco         = banco.strip() or None,
        numero_cuenta = numero_cuenta.strip() or None,
        cbu           = cbu.strip() or None,
        alias_cbu     = alias_cbu.strip() or None,
        moneda        = moneda if moneda in MONEDA_LABELS else "ARS",
        saldo_inicial = _parse_decimal(saldo_inicial) or Decimal(0),
        notas         = notas.strip() or None,
    )
    db.add(cuenta)
    db.commit()
    return RedirectResponse(url="/finanzas/cuentas", status_code=303)


@router.post("/cuentas/{cuenta_id}/toggle")
async def cuenta_toggle(
    cuenta_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    cuenta = db.query(CuentaTesoreria).filter(CuentaTesoreria.id == cuenta_id).first()
    if not cuenta:
        raise HTTPException(status_code=404)
    cuenta.activo = not cuenta.activo
    db.commit()
    return RedirectResponse(url="/finanzas/cuentas", status_code=303)


# ── MOVIMIENTOS ─────────────────────────────────────────────────────────────

@router.get("/movimientos", response_class=HTMLResponse)
async def movimientos_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    def qp(name, default=""):
        vals = request.query_params.getlist(name)
        return vals[0].strip() if vals else default

    q_cuenta = qp("cuenta")
    q_tipo   = qp("tipo")
    q_desde  = qp("desde")
    q_hasta  = qp("hasta")

    mq = db.query(MovimientoTesoreria).filter(MovimientoTesoreria.deleted_at.is_(None))
    if q_cuenta.isdigit():
        mq = mq.filter(MovimientoTesoreria.cuenta_id == int(q_cuenta))
    if q_tipo in TIPO_MOVIMIENTO_LABELS:
        mq = mq.filter(MovimientoTesoreria.tipo == q_tipo)
    d_desde = _parse_date(q_desde)
    d_hasta = _parse_date(q_hasta)
    if d_desde:
        mq = mq.filter(MovimientoTesoreria.fecha >= d_desde)
    if d_hasta:
        mq = mq.filter(MovimientoTesoreria.fecha <= d_hasta)

    movimientos = mq.order_by(
        MovimientoTesoreria.fecha.desc(), MovimientoTesoreria.id.desc()
    ).limit(500).all()

    total_ing = sum(float(m.monto) for m in movimientos if m.tipo == "ingreso")
    total_egr = sum(float(m.monto) for m in movimientos if m.tipo == "egreso")

    cuentas       = db.query(CuentaTesoreria).order_by(CuentaTesoreria.nombre).all()
    clientes      = db.query(Client).filter(Client.activo.is_(True)).order_by(Client.nombre).all()
    proveedores   = db.query(models.Supplier).filter(models.Supplier.active.is_(True)).order_by(models.Supplier.name).all()
    centros       = db.query(CentroCosto).filter(CentroCosto.activo.is_(True)).order_by(CentroCosto.nombre).all()

    return templates.TemplateResponse(request, "finanzas/movimientos.html", {
        "user":            current_user,
        "movimientos":     movimientos,
        "cuentas":         cuentas,
        "clientes":        clientes,
        "proveedores":     proveedores,
        "centros":         centros,
        "tipos_movimiento": TIPOS_MOVIMIENTO,
        "medios_pago":     MEDIOS_PAGO,
        "contraparte_tipos": CONTRAPARTE_TIPOS,
        "tipo_movimiento_labels": TIPO_MOVIMIENTO_LABELS,
        "medio_pago_labels": MEDIO_PAGO_LABELS,
        "total_ing":       total_ing,
        "total_egr":       total_egr,
        "params":          {"cuenta": q_cuenta, "tipo": q_tipo, "desde": q_desde, "hasta": q_hasta},
        "today":           date.today().isoformat(),
    })


@router.post("/movimientos/new")
async def movimiento_new(
    cuenta_id:        int = Form(...),
    fecha:            str = Form(...),
    tipo:             str = Form(...),
    concepto:         str = Form(...),
    monto:            str = Form(...),
    medio:            str = Form(""),
    referencia:       str = Form(""),
    contraparte_tipo: str = Form(""),
    client_id:        str = Form(""),
    supplier_id:      str = Form(""),
    contraparte_text: str = Form(""),
    centro_costo_id:  str = Form(""),
    notas:            str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    cuenta = db.query(CuentaTesoreria).filter(CuentaTesoreria.id == cuenta_id).first()
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta inexistente")
    if tipo not in TIPO_MOVIMIENTO_LABELS:
        raise HTTPException(status_code=422, detail="Tipo inválido")

    monto_dec = _parse_decimal(monto)
    if not monto_dec or monto_dec <= 0:
        raise HTTPException(status_code=422, detail="Monto debe ser positivo")

    mov = MovimientoTesoreria(
        cuenta_id        = cuenta_id,
        fecha            = _parse_date(fecha, date.today()),
        tipo             = tipo,
        concepto         = concepto.strip(),
        monto            = monto_dec,
        moneda           = cuenta.moneda,
        medio            = medio.strip() or None,
        referencia       = referencia.strip() or None,
        contraparte_tipo = contraparte_tipo.strip() or None,
        client_id        = int(client_id) if client_id.isdigit() else None,
        supplier_id      = int(supplier_id) if supplier_id.isdigit() else None,
        contraparte_text = contraparte_text.strip() or None,
        centro_costo_id  = int(centro_costo_id) if centro_costo_id.isdigit() else None,
        notas            = notas.strip() or None,
        created_by_id    = current_user.id,
    )
    db.add(mov)
    db.commit()
    return RedirectResponse(url="/finanzas/movimientos?saved=1", status_code=303)


@router.post("/movimientos/{mov_id}/delete")
async def movimiento_delete(
    mov_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    mov = db.query(MovimientoTesoreria).filter(MovimientoTesoreria.id == mov_id).first()
    if not mov:
        raise HTTPException(status_code=404)
    mov.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/finanzas/movimientos", status_code=303)


# ── CENTROS DE COSTO ──────────────────────────────────────────────────────────

@router.get("/centros", response_class=HTMLResponse)
async def centros_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    centros = db.query(CentroCosto).order_by(CentroCosto.activo.desc(), CentroCosto.codigo).all()
    # Total movido por centro (egresos − ingresos no anulados)
    return templates.TemplateResponse(request, "finanzas/centros.html", {
        "user":    current_user,
        "centros": centros,
    })


@router.post("/centros/new")
async def centro_new(
    codigo:      str = Form(...),
    nombre:      str = Form(...),
    plant:       str = Form(""),
    descripcion: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    codigo = codigo.strip().upper()
    if not codigo or not nombre.strip():
        raise HTTPException(status_code=422, detail="Código y nombre obligatorios")
    if db.query(CentroCosto).filter(CentroCosto.codigo == codigo).first():
        raise HTTPException(status_code=422, detail="Ya existe un centro con ese código")
    db.add(CentroCosto(
        codigo      = codigo,
        nombre      = nombre.strip(),
        plant       = plant.strip() or None,
        descripcion = descripcion.strip() or None,
    ))
    db.commit()
    return RedirectResponse(url="/finanzas/centros", status_code=303)


@router.post("/centros/{centro_id}/toggle")
async def centro_toggle(
    centro_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_admin),
):
    centro = db.query(CentroCosto).filter(CentroCosto.id == centro_id).first()
    if not centro:
        raise HTTPException(status_code=404)
    centro.activo = not centro.activo
    db.commit()
    return RedirectResponse(url="/finanzas/centros", status_code=303)
