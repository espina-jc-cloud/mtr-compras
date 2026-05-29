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

_env.filters["fmt_ar"]   = _fmt_ar
_env.filters["fmt_num"]  = _fmt_num
_env.filters["fmt_date"] = _fmt_date

templates = Jinja2Templates(env=_env)
