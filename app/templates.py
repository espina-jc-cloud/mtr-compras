from datetime import timezone, timedelta
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.templating import Jinja2Templates

# ── Filtro: UTC naive → Buenos Aires (UTC-3, sin DST) ──────────────────────
_BAS_OFFSET = timedelta(hours=-3)

def _fmt_ar(dt, fmt="%d/%m/%Y %H:%M"):
    if dt is None:
        return ""
    return (dt.replace(tzinfo=timezone.utc) + _BAS_OFFSET).strftime(fmt)

def _fmt_date(dt, fmt="%d/%m/%Y"):
    """Render a date/datetime as a pure local date (no timezone shift).
    Use this for date-only fields (fuel_date, work_date, purchase_date) to
    avoid the UTC-3 off-by-one that fmt_ar produces on midnight timestamps."""
    if dt is None:
        return ""
    if hasattr(dt, "date"):
        return dt.date().strftime(fmt)
    return dt.strftime(fmt)

# Crear el Environment con el filtro pre-registrado antes de compilar cualquier template
_env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(["html"]),
)
def _fmt_num(value, decimals=0):
    """Formatea un número como moneda argentina: 1234567.5 → '1.234.568'"""
    if value is None:
        return "—"
    try:
        n = float(value)
        if decimals == 0:
            return f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{n:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)

def _cloudinary_thumb(url: str, width: int = 400) -> str:
    """Genera URL de thumbnail Cloudinary insertando la transformación w_{width},c_limit."""
    if not url or "/upload/" not in url:
        return url
    return url.replace("/upload/", f"/upload/w_{width},c_limit/", 1)

import json as _json
_env.filters["fmt_ar"]        = _fmt_ar
_env.filters["fmt_num"]       = _fmt_num
_env.filters["fmt_date"]      = _fmt_date
_env.filters["cl_thumb"]      = _cloudinary_thumb
_env.filters["fromjson"]      = lambda s: _json.loads(s) if s else []

# ── Globals para el módulo Live (disponibles en todos los templates sin pasarlos) ─
from app.live_utils import fmt_kg as _fmt_kg, delta_badge as _delta_badge, format_minutes as _format_minutes
_env.globals["fmt_kg"]          = _fmt_kg
_env.globals["delta_badge"]     = _delta_badge
_env.globals["format_minutes"]  = _format_minutes

# ── Permisos: can() / can_module() disponibles en todos los templates ──────────
from app.permissions import can as _can, can_module as _can_module
_env.globals["can"]        = _can
_env.globals["can_module"] = _can_module


# ── Badges de pendientes en el sidebar (Etapa 4) ───────────────────────────────
# Cuentas ligeras (compras por autorizar + turnos abiertos) cacheadas 60s por
# usuario para no golpear la DB en cada render del shell.
import time as _time
_badge_cache: dict = {}      # user_id -> (timestamp, {"compras": n, "turnos": n})
_BADGE_TTL = 60

def _pending_badges(user):
    if user is None:
        return {"compras": 0, "turnos": 0}
    now = _time.time()
    hit = _badge_cache.get(user.id)
    if hit and now - hit[0] < _BADGE_TTL:
        return hit[1]

    from app.database import SessionLocal
    from app import models
    from app.models_live import OperationLiveShift, OperationLiveSession

    out = {"compras": 0, "turnos": 0}
    db = SessionLocal()
    try:
        if _can(user, "compras.compras"):
            q = db.query(models.Purchase).filter(
                models.Purchase.deleted_at == None,
                models.Purchase.status == "pendiente",
            )
            # Mismo aislamiento por rol/planta que el listado de Compras.
            if user.role == "planta":
                q = q.filter(models.Purchase.requested_by_id == user.id)
            elif user.role == "autorizador" and user.plant != "TODAS":
                q = q.filter(models.Purchase.plant == user.plant)
            out["compras"] = q.count()

        if _can(user, "operaciones.live"):
            out["turnos"] = (
                db.query(OperationLiveShift)
                .join(OperationLiveSession,
                      OperationLiveShift.session_id == OperationLiveSession.id)
                .filter(OperationLiveShift.status == "open",
                        OperationLiveSession.status.in_(("active", "paused")))
                .count()
            )
    except Exception:
        # Nunca romper el shell por un badge.
        out = {"compras": 0, "turnos": 0}
    finally:
        db.close()

    _badge_cache[user.id] = (now, out)
    return out

_env.globals["pending_badges"] = _pending_badges


# ── Breadcrumb ligero (Etapa 4): módulo › sección › hoja ───────────────────────
_BREADCRUMB_MAP = [
    ("/operations/arribos",   "Operación",      "Próximos Arribos"),
    ("/operations/live",      "Operación",      "Operativos Live"),
    ("/operations/daily",     "Operación",      "Operaciones Diarias"),
    ("/operations",           "Operación",      "Operativos"),
    ("/despachos",            "Operación",      "Despachos"),
    ("/tarifario",            "Operación",      "Tarifas"),
    ("/polinomica",           "Operación",      "Polinómica CNA"),
    ("/transporte/nomina",    "Transporte",     "Nómina Madre"),
    ("/transporte/historial", "Transporte",     "Historial"),
    ("/purchases",            "Comercial",      "Compras"),
    ("/quotes",               "Comercial",      "Cotizaciones"),
    ("/suppliers",            "Comercial",      "Proveedores"),
    ("/conciliation",         "Comercial",      "Conciliación"),
    ("/maintenance",          "Mantenimiento",  "Mantenimiento"),
    ("/equipment",            "Mantenimiento",  "Equipos"),
    ("/fuel",                 "Mantenimiento",  "Combustible"),
    ("/projects",             "Proyectos",      "Proyectos"),
    ("/admin/users",          "Administración", "Usuarios"),
]

def _breadcrumb(path):
    if not path:
        return []
    best = None
    for prefix, module, section in _BREADCRUMB_MAP:
        if path == prefix or path.startswith(prefix + "/"):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, module, section)
    if not best:
        return []
    prefix, module, section = best
    crumbs = [{"label": module, "href": None},
              {"label": section, "href": prefix}]
    tail = path[len(prefix):].strip("/")
    if tail:
        low = tail.lower()
        if low == "new" or low.startswith("new/"):
            leaf = "Nuevo"
        elif "edit" in low:
            leaf = "Editar"
        elif low in ("import", "imports"):
            leaf = "Importar"
        elif low == "board":
            leaf = "Vista compartible"
        elif low == "reconciliation":
            leaf = "Conciliación"
        else:
            leaf = "Detalle"
        crumbs.append({"label": leaf, "href": None})
    return crumbs

_env.globals["breadcrumb"] = _breadcrumb

templates = Jinja2Templates(env=_env)
