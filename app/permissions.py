"""
Sistema de permisos por usuario — MTR Gestión.

Fuente ÚNICA de verdad del acceso a módulos y submódulos. La consumen:
  - los route guards (require_perm → 403 real),
  - la navegación (base.html) y el home (home.html), vía can() / can_module().

Diseño (ver memoria project_mtr_permisos):
  - Los roles siguen siendo el DEFAULT. Encima se aplican overrides por usuario.
  - User.permissions = JSON con la lista de claves CONCEDIDAS ("compras.cotizaciones").
    NULL  → el usuario hereda los defaults de su rol (= comportamiento histórico).
    lista → control explícito; solo se concede lo que está en la lista.
  - superadmin siempre tiene todo (nunca se puede autobloquear).

Cada submódulo declara los prefijos de ruta que protege, así require_perm puede
derivar el bloqueo de forma centralizada.
"""
import json

# ── Catálogo de módulos y submódulos (el orden importa para el render) ─────────
# key del submódulo = "<modulo>.<submodulo>"
MODULES = [
    {
        "key": "compras", "label": "Compras",
        "subs": [
            {"key": "compras.compras",      "label": "Compras",      "prefixes": ["/purchases"]},
            {"key": "compras.cotizaciones", "label": "Cotizaciones", "prefixes": ["/quotes"]},
            {"key": "compras.facturas",     "label": "Facturas",     "prefixes": ["/compras/facturas"]},
            {"key": "compras.proveedores",  "label": "Proveedores",  "prefixes": ["/suppliers"]},
            {"key": "compras.conciliacion", "label": "Conciliación", "prefixes": ["/conciliation"]},
        ],
    },
    {
        "key": "mantenimiento", "label": "Mantenimiento",
        "subs": [
            {"key": "mantenimiento.mantenimiento", "label": "Mantenimiento", "prefixes": ["/maintenance"]},
            {"key": "mantenimiento.equipos",       "label": "Equipos",       "prefixes": ["/equipment"]},
            {"key": "mantenimiento.combustible",   "label": "Combustible",   "prefixes": ["/fuel"]},
        ],
    },
    {
        "key": "proyectos", "label": "Proyectos",
        "subs": [
            {"key": "proyectos.proyectos", "label": "Proyectos", "prefixes": ["/projects"]},
        ],
    },
    {
        "key": "operaciones", "label": "Operaciones",
        "subs": [
            {"key": "operaciones.arribos",          "label": "Próximos Arribos",     "prefixes": ["/operations/arribos"]},
            {"key": "operaciones.despachos",        "label": "Despachos",            "prefixes": ["/despachos"]},
            {"key": "operaciones.diarias",          "label": "Operaciones Diarias",  "prefixes": ["/operations/daily"]},
            {"key": "operaciones.live",             "label": "Operativos Live",      "prefixes": ["/operations/live"]},
            {"key": "operaciones.finalizados",      "label": "Operativos Finalizados", "prefixes": ["/operations", "/api/operations"]},
            {"key": "operaciones.tarifas_propias",  "label": "Tarifas propias",      "prefixes": ["/tarifario"]},
            {"key": "operaciones.tarifas_terceros", "label": "Tarifas de terceros",  "prefixes": []},
        ],
    },
    {
        "key": "transporte", "label": "Transporte",
        "subs": [
            {"key": "transporte.nomina",    "label": "Nómina Madre", "prefixes": ["/transporte/nomina"]},
            {"key": "transporte.historial", "label": "Historial",    "prefixes": ["/transporte/historial"]},
        ],
    },
    {
        "key": "finanzas", "label": "Finanzas",
        "subs": [
            {"key": "finanzas.tesoreria", "label": "Tesorería", "prefixes": ["/finanzas"]},
        ],
    },
    {
        "key": "usuarios", "label": "Usuarios",
        "subs": [
            {"key": "usuarios.usuarios", "label": "Usuarios", "prefixes": ["/admin/users"]},
        ],
    },
]

# Lista plana de todas las claves de submódulo (para validar y para superadmin).
ALL_KEYS = [s["key"] for m in MODULES for s in m["subs"]]
ALL_KEYS_SET = set(ALL_KEYS)

# Mapa rápido key → label (para mostrar en resúmenes).
KEY_LABELS = {s["key"]: f'{m["label"]} · {s["label"]}'
              for m in MODULES for s in m["subs"]}


# ── Defaults por rol — replican EXACTAMENTE el acceso actual (cero regresión) ───
# Derivado de los guards existentes en los routers (require_role / require_*).
_DEF = {
    # admin: todo salvo administrar usuarios.
    "admin": set(ALL_KEYS) - {"usuarios.usuarios"},

    # autorizador: compras (sin conciliación), mantenimiento, proyectos,
    # transporte, tarifas propias. Sin operativos ni admin-only.
    "autorizador": {
        "compras.compras", "compras.cotizaciones", "compras.facturas", "compras.proveedores",
        "mantenimiento.mantenimiento", "mantenimiento.equipos", "mantenimiento.combustible",
        "proyectos.proyectos",
        "operaciones.tarifas_propias",
        "transporte.nomina", "transporte.historial",
    },

    # planta: como autorizador + despachos + operativos live.
    "planta": {
        "compras.compras", "compras.cotizaciones", "compras.facturas", "compras.proveedores",
        "mantenimiento.mantenimiento", "mantenimiento.equipos", "mantenimiento.combustible",
        "proyectos.proyectos",
        "operaciones.arribos", "operaciones.despachos", "operaciones.live", "operaciones.tarifas_propias",
        "transporte.nomina", "transporte.historial",
    },

    # tecnico: mantenimiento, proyectos, transporte. Sin compras ni operativos.
    "tecnico": {
        "mantenimiento.mantenimiento", "mantenimiento.equipos", "mantenimiento.combustible",
        "proyectos.proyectos",
        "transporte.nomina", "transporte.historial",
    },

    # operador: solo carga de combustible + despachos (su tarea de campo).
    "operador": {
        "mantenimiento.combustible",
        "operaciones.despachos",
    },
}


def role_defaults(role: str) -> set:
    """Claves concedidas por defecto a un rol (cuando el usuario no tiene override)."""
    if role == "superadmin":
        return set(ALL_KEYS)
    return set(_DEF.get(role, set()))


def user_grants(user) -> set:
    """Set de claves concedidas a un usuario, resolviendo override → default de rol."""
    if user is None:
        return set()
    if user.role == "superadmin":
        return set(ALL_KEYS)
    raw = getattr(user, "permissions", None)
    if raw:
        try:
            return {k for k in json.loads(raw) if k in ALL_KEYS_SET}
        except (ValueError, TypeError):
            pass
    return role_defaults(user.role)


def can(user, key: str) -> bool:
    """¿El usuario puede acceder a este submódulo?"""
    if user is None:
        return False
    if user.role == "superadmin":
        return True
    return key in user_grants(user)


def can_module(user, module_key: str) -> bool:
    """¿El usuario ve el módulo? (= tiene al menos un submódulo concedido)."""
    if user is None:
        return False
    if user.role == "superadmin":
        return True
    grants = user_grants(user)
    return any(s["key"] in grants for m in MODULES if m["key"] == module_key for s in m["subs"])


def has_custom_permissions(user) -> bool:
    """True si el usuario tiene permisos explícitos (no hereda del rol)."""
    return bool(getattr(user, "permissions", None))


# ── Guard de rutas ─────────────────────────────────────────────────────────────
# Import perezoso de FastAPI/deps para no crear ciclos al importar el catálogo.

def require_perm(*keys):
    """Dependency: permite el acceso si el usuario tiene CUALQUIERA de las claves.

    Uso en routers:  current_user = Depends(require_perm("compras.cotizaciones"))
    """
    from fastapi import Depends, HTTPException
    from app.deps import get_current_user

    def checker(current_user=Depends(get_current_user)):
        if not any(can(current_user, k) for k in keys):
            raise HTTPException(status_code=403, detail="Sin acceso a este módulo.")
        return current_user

    return checker


def _key_for_path(path: str):
    """Devuelve la clave de submódulo cuyo prefijo de ruta calza mejor (más largo)."""
    best, best_len = None, -1
    for m in MODULES:
        for s in m["subs"]:
            for p in s["prefixes"]:
                if p and (path == p or path.startswith(p + "/")) and len(p) > best_len:
                    best, best_len = s["key"], len(p)
    return best


def require_path_perm():
    """Factory de guard que deriva el submódulo desde la ruta pedida (para routers
    con submódulos distintos según el path, ej: /transporte/nomina vs /historial).

    Uso a nivel de router:  dependencies=[Depends(require_path_perm())]
    """
    from fastapi import Depends, HTTPException, Request
    from app.deps import get_current_user

    def checker(request: Request, current_user=Depends(get_current_user)):
        key = _key_for_path(request.url.path)
        if key and not can(current_user, key):
            raise HTTPException(status_code=403, detail="Sin acceso a este módulo.")
        return current_user

    return checker
