"""
Router: Tarifario — precios de venta de MTR por cliente / servicio.

Endpoints:
  GET  /tarifario                  — listado + filtros (cliente, servicio, scope, estado)
  GET  /tarifario/new              — form alta
  POST /tarifario/new              — crear tarifa
  GET  /tarifario/{id}             — detalle + historial de versiones
  GET  /tarifario/{id}/edit        — form edición
  POST /tarifario/{id}/edit        — guardar (versiona si cambia el precio)
  POST /tarifario/{id}/deactivate  — dar de baja (cierra vigencia)

  GET  /tarifario/clientes         — ABM de clientes
  POST /tarifario/clientes/new     — crear cliente
  POST /tarifario/clientes/{id}/edit — editar cliente

  GET  /tarifario/servicios        — ABM de servicios
  POST /tarifario/servicios/new    — crear servicio
  POST /tarifario/servicios/{id}/edit — editar servicio
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import require_compras_access
from app import models
from app.models_tariffs import (
    Client, TariffService, Tariff, TariffComponent, calcular_componentes,
    TARIFF_OWNERS, TARIFF_OWNER_LABELS,
    COMPONENT_TIPOS, COMPONENT_TIPO_LABELS,
    TARIFF_SCOPES, TARIFF_SCOPE_LABELS,
    TARIFF_MONEDAS, TARIFF_MONEDA_LABELS,
    TARIFF_UNIDADES, TARIFF_UNIDAD_LABELS,
    TARIFF_CATEGORIAS, SCOPE_CSS,
    TARIFF_LINE_TYPES, TARIFF_LINE_TYPE_LABELS,
    TARIFF_PRICE_TIERS, TARIFF_PRICE_TIER_LABELS,
    TARIFF_VISIBILITIES, TARIFF_VISIBILITY_LABELS,
    TARIFF_INTERNAL_ROLES, LINE_TYPE_CSS, TIER_CSS,
)
from app.templates import templates

router = APIRouter(prefix="/tarifario", tags=["tarifario"])


def _can_view_internal(user) -> bool:
    """Solo admin/superadmin pueden ver tarifas internas (piso, benchmarks)."""
    return user.role in TARIFF_INTERNAL_ROLES


def _ctx(request, current_user, **extra):
    """Contexto base con catálogos siempre disponibles en los templates."""
    base = {
        "request":          request,
        "current_user":     current_user,
        "scopes":           TARIFF_SCOPES,
        "scope_labels":     TARIFF_SCOPE_LABELS,
        "monedas":          TARIFF_MONEDAS,
        "moneda_labels":    TARIFF_MONEDA_LABELS,
        "unidades":         TARIFF_UNIDADES,
        "unidad_labels":    TARIFF_UNIDAD_LABELS,
        "categorias":       TARIFF_CATEGORIAS,
        "scope_css":        SCOPE_CSS,
        "line_types":       TARIFF_LINE_TYPES,
        "line_type_labels": TARIFF_LINE_TYPE_LABELS,
        "price_tiers":      TARIFF_PRICE_TIERS,
        "tier_labels":      TARIFF_PRICE_TIER_LABELS,
        "visibilities":     TARIFF_VISIBILITIES,
        "visibility_labels": TARIFF_VISIBILITY_LABELS,
        "line_type_css":    LINE_TYPE_CSS,
        "tier_css":         TIER_CSS,
        "owners":           TARIFF_OWNERS,
        "owner_labels":     TARIFF_OWNER_LABELS,
        "component_tipos":  COMPONENT_TIPOS,
        "component_tipo_labels": COMPONENT_TIPO_LABELS,
        "can_view_internal": _can_view_internal(current_user),
    }
    base.update(extra)
    return base


def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _parse_precio(s: str) -> Optional[float]:
    s = (s or "").strip().replace(".", "").replace(",", ".") if "," in (s or "") else (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Listado
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_class=HTMLResponse)
async def list_tariffs(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    cliente: str = "",
    servicio: str = "",
    scope: str = "",
    moneda: str = "",
    line_type: str = "",
    tier: str = "",
    historico: str = "",
    internas: str = "",
    owner: str = "propia",
):
    if owner not in ("propia", "tercero"):
        owner = "propia"
    # Tarifas de terceros: son costos internos → solo admin/superadmin
    if owner == "tercero" and not _can_view_internal(current_user):
        owner = "propia"

    q = db.query(Tariff).options(
        joinedload(Tariff.service),
        joinedload(Tariff.client),
        joinedload(Tariff.equipment),
    ).filter(Tariff.owner == owner)

    # ── Visibilidad por rol ───────────────────────────────────────────────────
    # Propias internas (piso, benchmarks): solo admin/superadmin con toggle.
    # Terceros: el tab entero ya es interno (solo admin), no se filtra de nuevo.
    if owner == "propia":
        if not _can_view_internal(current_user):
            q = q.filter(Tariff.visibility == "comercial")
            internas = ""  # un rol sin permiso jamás puede activar el toggle
        elif not internas:
            q = q.filter(Tariff.visibility == "comercial")

    if not historico:
        q = q.filter(Tariff.is_active == True)  # noqa: E712
    if cliente:
        q = q.filter(Tariff.client_id == int(cliente))
    if servicio:
        q = q.filter(Tariff.service_id == int(servicio))
    if scope:
        q = q.filter(Tariff.scope == scope)
    if moneda:
        q = q.filter(Tariff.moneda == moneda)
    if line_type:
        q = q.filter(Tariff.line_type == line_type)
    if tier:
        q = q.filter(Tariff.price_tier == tier)

    tarifas = q.order_by(
        Tariff.is_active.desc(),
        Tariff.line_type.asc(),
        Tariff.service_id.asc(),
        Tariff.price_tier.asc(),
        Tariff.valid_from.desc(),
    ).all()

    clientes  = db.query(Client).filter(Client.activo == True).order_by(Client.nombre).all()  # noqa: E712
    servicios = db.query(TariffService).filter(TariffService.activo == True).order_by(TariffService.orden).all()  # noqa: E712

    kpis = {
        "total":     len(tarifas),
        "servicios": sum(1 for t in tarifas if t.line_type == "servicio"),
        "equipos":   sum(1 for t in tarifas if t.line_type == "equipo"),
        "personal":  sum(1 for t in tarifas if t.line_type == "personal"),
        "spot":      sum(1 for t in tarifas if t.scope == "spot"),
    }

    return templates.TemplateResponse(
        request, "tarifario/list.html",
        _ctx(request, current_user,
             tarifas=tarifas, clientes=clientes, servicios=servicios, kpis=kpis,
             f_cliente=cliente, f_servicio=servicio, f_scope=scope,
             f_moneda=moneda, f_line_type=line_type, f_tier=tier,
             f_historico=historico, f_internas=internas, f_owner=owner),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Alta
# ══════════════════════════════════════════════════════════════════════════════

def _padres_candidatos(db):
    """Tarifas que pueden ser padre de un adicional (equipos y servicios vigentes)."""
    return db.query(Tariff).options(
        joinedload(Tariff.service), joinedload(Tariff.equipment),
    ).filter(
        Tariff.is_active == True,            # noqa: E712
        Tariff.line_type.in_(("equipo", "servicio")),
    ).order_by(Tariff.line_type, Tariff.service_id).all()


@router.get("/new", response_class=HTMLResponse)
async def new_tariff_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
):
    clientes  = db.query(Client).filter(Client.activo == True).order_by(Client.nombre).all()  # noqa: E712
    servicios = db.query(TariffService).filter(TariffService.activo == True).order_by(TariffService.orden).all()  # noqa: E712
    equipos   = db.query(models.Equipment).order_by(models.Equipment.name).all()
    return templates.TemplateResponse(
        request, "tarifario/form.html",
        _ctx(request, current_user, tarifa=None,
             clientes=clientes, servicios=servicios, equipos=equipos,
             padres=_padres_candidatos(db),
             today=date.today().isoformat()),
    )


def _tri_bool(v: str):
    """'si' → True, 'no' → False, '' → None (no aplica)."""
    v = (v or "").strip().lower()
    if v == "si":
        return True
    if v == "no":
        return False
    return None


def _extra_fields(line_type, price_tier, visibility, parent_id,
                  incluye_operador, incluye_combustible, recargo_pct, plaza,
                  current_user, owner="propia", tercero="") -> dict:
    """Normaliza y valida los campos extendidos del form."""
    if owner not in ("propia", "tercero"):
        owner = "propia"
    tercero = (tercero or "").strip() or None
    if owner == "tercero" and not tercero:
        raise HTTPException(status_code=400, detail="Una tarifa de tercero requiere el nombre del tercero")
    if owner == "propia":
        tercero = None
    # El precio piso, los benchmarks y TODAS las de terceros son internas.
    if price_tier == "piso" or line_type == "benchmark" or owner == "tercero":
        visibility = "interna"
    # Solo admin/superadmin pueden crear/editar tarifas internas.
    if visibility == "interna" and not _can_view_internal(current_user):
        raise HTTPException(status_code=403, detail="Sin permisos para tarifas internas")
    # Adicional con padre = cuelga de un equipo/servicio concreto.
    # Adicional SIN padre = global de categoría (ej: operador, aplica a varios equipos).
    pid = int(parent_id) if (parent_id or "").strip() else None
    if line_type != "adicional":
        pid = None
    return {
        "owner":               owner,
        "tercero":             tercero,
        "line_type":           line_type,
        "price_tier":          price_tier,
        "visibility":          visibility,
        "parent_id":           pid,
        "incluye_operador":    _tri_bool(incluye_operador),
        "incluye_combustible": _tri_bool(incluye_combustible),
        "recargo_pct":         _parse_precio(recargo_pct),
        "plaza":               (plaza or "").strip() or None,
    }


@router.post("/new")
async def create_tariff(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    scope: str = Form("base"),
    service_id: str = Form(...),
    client_id: str = Form(""),
    equipment_id: str = Form(""),
    descripcion: str = Form(""),
    precio: str = Form(...),
    moneda: str = Form("ARS"),
    unidad: str = Form("ton"),
    valid_from: str = Form(""),
    observaciones: str = Form(""),
    line_type: str = Form("servicio"),
    price_tier: str = Form("unica"),
    visibility: str = Form("comercial"),
    parent_id: str = Form(""),
    incluye_operador: str = Form(""),
    incluye_combustible: str = Form(""),
    recargo_pct: str = Form(""),
    plaza: str = Form(""),
    owner: str = Form("propia"),
    tercero: str = Form(""),
):
    precio_val = _parse_precio(precio)
    if precio_val is None:
        raise HTTPException(status_code=400, detail="Precio inválido")

    cid = int(client_id) if client_id.strip() else None
    if scope == "base":
        cid = None
    elif scope in ("cliente", "spot") and cid is None:
        raise HTTPException(status_code=400, detail="Este tipo de tarifa requiere un cliente")

    extra = _extra_fields(line_type, price_tier, visibility, parent_id,
                          incluye_operador, incluye_combustible, recargo_pct, plaza,
                          current_user, owner=owner, tercero=tercero)

    t = Tariff(
        scope=scope,
        service_id=int(service_id),
        client_id=cid,
        equipment_id=int(equipment_id) if equipment_id.strip() else None,
        descripcion=descripcion.strip() or None,
        precio=precio_val,
        moneda=moneda,
        unidad=unidad,
        valid_from=_parse_date(valid_from) or date.today(),
        is_active=True,
        observaciones=observaciones.strip() or None,
        created_by=current_user.name,
        **extra,
    )
    db.add(t)
    db.commit()
    return RedirectResponse(url=f"/tarifario/{t.id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# Detalle + historial
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{tid:int}", response_class=HTMLResponse)
async def tariff_detail(
    tid: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
):
    t = db.query(Tariff).options(
        joinedload(Tariff.service),
        joinedload(Tariff.client),
        joinedload(Tariff.equipment),
    ).filter(Tariff.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarifa no encontrada")

    # Tarifas internas: solo admin/superadmin (mismo criterio que el listado)
    if t.visibility == "interna" and not _can_view_internal(current_user):
        raise HTTPException(status_code=403, detail="Sin permisos para ver esta tarifa")

    # Reconstruir la cadena de versiones (hacia atrás por replaces_id)
    historial = []
    cur = t
    seen = set()
    while cur and cur.id not in seen:
        seen.add(cur.id)
        historial.append(cur)
        cur = cur.replaces
    # versiones que reemplazaron a esta (hacia adelante)
    posteriores = db.query(Tariff).filter(Tariff.replaces_id == t.id).all()

    # Adicionales que cuelgan de esta tarifa (operador, combustible…)
    hijos = db.query(Tariff).options(joinedload(Tariff.service)).filter(
        Tariff.parent_id == t.id,
        Tariff.is_active == True,  # noqa: E712
    ).all()
    if not _can_view_internal(current_user):
        hijos = [h for h in hijos if h.visibility == "comercial"]

    # Componentes de fórmula (tarifas compuestas) con cascada calculada
    comp_rows = calcular_componentes(t.componentes) if t.componentes else []
    comp_total = comp_rows[-1][1] if comp_rows else None

    return templates.TemplateResponse(
        request, "tarifario/detail.html",
        _ctx(request, current_user, tarifa=t,
             historial=historial, posteriores=posteriores, hijos=hijos,
             comp_rows=comp_rows, comp_total=comp_total),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Componentes de fórmula
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{tid:int}/componentes/new")
async def add_component(
    tid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    nombre: str = Form(...),
    tipo: str = Form("base"),
    valor: str = Form(...),
    orden: str = Form(""),
    observaciones: str = Form(""),
):
    t = db.query(Tariff).filter(Tariff.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarifa no encontrada")
    if t.visibility == "interna" and not _can_view_internal(current_user):
        raise HTTPException(status_code=403, detail="Sin permisos")
    v = _parse_precio(valor)
    if v is None:
        raise HTTPException(status_code=400, detail="Valor inválido")
    if tipo not in ("base", "recargo_pct", "monto_fijo"):
        tipo = "base"
    max_orden = max([c.orden for c in t.componentes], default=0)
    db.add(TariffComponent(
        tariff_id=t.id,
        orden=int(orden) if orden.strip().isdigit() else max_orden + 10,
        nombre=nombre.strip(), tipo=tipo, valor=v,
        observaciones=observaciones.strip() or None,
    ))
    db.commit()
    return RedirectResponse(url=f"/tarifario/{tid}", status_code=303)


@router.post("/{tid:int}/componentes/{cid:int}/delete")
async def delete_component(
    tid: int,
    cid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
):
    t = db.query(Tariff).filter(Tariff.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarifa no encontrada")
    if t.visibility == "interna" and not _can_view_internal(current_user):
        raise HTTPException(status_code=403, detail="Sin permisos")
    db.query(TariffComponent).filter(
        TariffComponent.id == cid, TariffComponent.tariff_id == tid).delete()
    db.commit()
    return RedirectResponse(url=f"/tarifario/{tid}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# Edición — con versionado automático
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{tid:int}/edit", response_class=HTMLResponse)
async def edit_tariff_form(
    tid: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
):
    t = db.query(Tariff).filter(Tariff.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarifa no encontrada")
    if t.visibility == "interna" and not _can_view_internal(current_user):
        raise HTTPException(status_code=403, detail="Sin permisos para editar esta tarifa")
    clientes  = db.query(Client).filter(Client.activo == True).order_by(Client.nombre).all()  # noqa: E712
    servicios = db.query(TariffService).filter(TariffService.activo == True).order_by(TariffService.orden).all()  # noqa: E712
    equipos   = db.query(models.Equipment).order_by(models.Equipment.name).all()
    return templates.TemplateResponse(
        request, "tarifario/form.html",
        _ctx(request, current_user, tarifa=t,
             clientes=clientes, servicios=servicios, equipos=equipos,
             padres=_padres_candidatos(db),
             today=date.today().isoformat()),
    )


@router.post("/{tid:int}/edit")
async def update_tariff(
    tid: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    scope: str = Form("base"),
    service_id: str = Form(...),
    client_id: str = Form(""),
    equipment_id: str = Form(""),
    descripcion: str = Form(""),
    precio: str = Form(...),
    moneda: str = Form("ARS"),
    unidad: str = Form("ton"),
    valid_from: str = Form(""),
    observaciones: str = Form(""),
    line_type: str = Form("servicio"),
    price_tier: str = Form("unica"),
    visibility: str = Form("comercial"),
    parent_id: str = Form(""),
    incluye_operador: str = Form(""),
    incluye_combustible: str = Form(""),
    recargo_pct: str = Form(""),
    plaza: str = Form(""),
    owner: str = Form("propia"),
    tercero: str = Form(""),
):
    t = db.query(Tariff).filter(Tariff.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarifa no encontrada")
    if t.visibility == "interna" and not _can_view_internal(current_user):
        raise HTTPException(status_code=403, detail="Sin permisos para editar esta tarifa")

    precio_val = _parse_precio(precio)
    if precio_val is None:
        raise HTTPException(status_code=400, detail="Precio inválido")

    cid = int(client_id) if client_id.strip() else None
    if scope == "base":
        cid = None
    elif scope in ("cliente", "spot") and cid is None:
        raise HTTPException(status_code=400, detail="Este tipo de tarifa requiere un cliente")

    eqid = int(equipment_id) if equipment_id.strip() else None
    vfrom = _parse_date(valid_from) or t.valid_from
    extra = _extra_fields(line_type, price_tier, visibility, parent_id,
                          incluye_operador, incluye_combustible, recargo_pct, plaza,
                          current_user, owner=owner, tercero=tercero)

    # ¿Cambió algo que afecte el PRECIO COMERCIAL? → versionar.
    # Cambios de texto (descripción / observaciones) → edición en el lugar.
    _rp_old = float(t.recargo_pct) if t.recargo_pct is not None else None
    precio_cambio = (
        float(t.precio) != precio_val
        or t.moneda != moneda
        or t.unidad != unidad
        or t.scope != scope
        or t.service_id != int(service_id)
        or t.client_id != cid
        or t.equipment_id != eqid
        or t.price_tier != extra["price_tier"]
        or t.line_type != extra["line_type"]
        or t.incluye_operador != extra["incluye_operador"]
        or t.incluye_combustible != extra["incluye_combustible"]
        or _rp_old != extra["recargo_pct"]
        or t.owner != extra["owner"]
        or t.tercero != extra["tercero"]
    )

    if precio_cambio and t.is_active:
        # Archivar la vigente y crear una nueva versión.
        t.valid_to = date.today()
        t.is_active = False
        nueva = Tariff(
            scope=scope,
            service_id=int(service_id),
            client_id=cid,
            equipment_id=eqid,
            descripcion=descripcion.strip() or None,
            precio=precio_val,
            moneda=moneda,
            unidad=unidad,
            valid_from=vfrom if vfrom > t.valid_from else date.today(),
            is_active=True,
            observaciones=observaciones.strip() or None,
            replaces_id=t.id,
            created_by=current_user.name,
            **extra,
        )
        db.add(nueva)
        db.commit()
        return RedirectResponse(url=f"/tarifario/{nueva.id}", status_code=303)
    else:
        # Edición simple (texto, o tarifa ya inactiva)
        t.scope = scope
        t.service_id = int(service_id)
        t.client_id = cid
        t.equipment_id = eqid
        t.descripcion = descripcion.strip() or None
        t.precio = precio_val
        t.moneda = moneda
        t.unidad = unidad
        t.valid_from = vfrom
        t.observaciones = observaciones.strip() or None
        for k, v in extra.items():
            setattr(t, k, v)
        db.commit()
        return RedirectResponse(url=f"/tarifario/{t.id}", status_code=303)


@router.post("/{tid:int}/deactivate")
async def deactivate_tariff(
    tid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
):
    t = db.query(Tariff).filter(Tariff.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tarifa no encontrada")
    t.is_active = False
    if not t.valid_to:
        t.valid_to = date.today()
    db.commit()
    return RedirectResponse(url=f"/tarifario/{t.id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# ABM Clientes
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/clientes", response_class=HTMLResponse)
async def list_clientes(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
):
    clientes = db.query(Client).order_by(Client.activo.desc(), Client.nombre).all()
    return templates.TemplateResponse(
        request, "tarifario/clientes.html",
        _ctx(request, current_user, clientes=clientes),
    )


@router.post("/clientes/new")
async def create_cliente(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    nombre: str = Form(...),
    cuit: str = Form(""),
    rubro: str = Form(""),
    contacto: str = Form(""),
    email: str = Form(""),
    telefono: str = Form(""),
    notas: str = Form(""),
):
    c = Client(
        nombre=nombre.strip(),
        cuit=cuit.strip() or None,
        rubro=rubro.strip() or None,
        contacto=contacto.strip() or None,
        email=email.strip() or None,
        telefono=telefono.strip() or None,
        notas=notas.strip() or None,
        activo=True,
    )
    db.add(c)
    db.commit()
    return RedirectResponse(url="/tarifario/clientes", status_code=303)


@router.post("/clientes/{cid:int}/edit")
async def update_cliente(
    cid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    nombre: str = Form(...),
    cuit: str = Form(""),
    rubro: str = Form(""),
    contacto: str = Form(""),
    email: str = Form(""),
    telefono: str = Form(""),
    notas: str = Form(""),
    activo: str = Form(""),
):
    c = db.query(Client).filter(Client.id == cid).first()
    if not c:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    c.nombre = nombre.strip()
    c.cuit = cuit.strip() or None
    c.rubro = rubro.strip() or None
    c.contacto = contacto.strip() or None
    c.email = email.strip() or None
    c.telefono = telefono.strip() or None
    c.notas = notas.strip() or None
    c.activo = bool(activo)
    db.commit()
    return RedirectResponse(url="/tarifario/clientes", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# ABM Servicios
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/servicios", response_class=HTMLResponse)
async def list_servicios(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
):
    servicios = db.query(TariffService).order_by(
        TariffService.activo.desc(), TariffService.orden
    ).all()
    return templates.TemplateResponse(
        request, "tarifario/servicios.html",
        _ctx(request, current_user, servicios=servicios),
    )


@router.post("/servicios/new")
async def create_servicio(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    nombre: str = Form(...),
    categoria: str = Form(""),
    unidad_default: str = Form(""),
    descripcion: str = Form(""),
    orden: str = Form("100"),
):
    s = TariffService(
        nombre=nombre.strip(),
        categoria=categoria.strip() or None,
        unidad_default=unidad_default.strip() or None,
        descripcion=descripcion.strip() or None,
        orden=int(orden) if orden.strip().isdigit() else 100,
        activo=True,
    )
    db.add(s)
    db.commit()
    return RedirectResponse(url="/tarifario/servicios", status_code=303)


@router.post("/servicios/{sid:int}/edit")
async def update_servicio(
    sid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_compras_access),
    nombre: str = Form(...),
    categoria: str = Form(""),
    unidad_default: str = Form(""),
    descripcion: str = Form(""),
    orden: str = Form("100"),
    activo: str = Form(""),
):
    s = db.query(TariffService).filter(TariffService.id == sid).first()
    if not s:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    s.nombre = nombre.strip()
    s.categoria = categoria.strip() or None
    s.unidad_default = unidad_default.strip() or None
    s.descripcion = descripcion.strip() or None
    s.orden = int(orden) if orden.strip().isdigit() else 100
    s.activo = bool(activo)
    db.commit()
    return RedirectResponse(url="/tarifario/servicios", status_code=303)
