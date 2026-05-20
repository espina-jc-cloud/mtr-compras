from datetime import timezone, timedelta
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.templating import Jinja2Templates

# ── Filtro: UTC naive → Buenos Aires (UTC-3, sin DST) ──────────────────────
_BAS_OFFSET = timedelta(hours=-3)

def _fmt_ar(dt, fmt="%d/%m/%Y %H:%M"):
    if dt is None:
        return ""
    return (dt.replace(tzinfo=timezone.utc) + _BAS_OFFSET).strftime(fmt)

# Crear el Environment con el filtro pre-registrado antes de compilar cualquier template
_env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(["html"]),
)
_env.filters["fmt_ar"] = _fmt_ar

templates = Jinja2Templates(env=_env)
