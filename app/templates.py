from datetime import timezone, timedelta
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

# ── Filtro: UTC naive → Buenos Aires (UTC-3, sin DST) ──────────────────────
_BAS_OFFSET = timedelta(hours=-3)

def _fmt_ar(dt, fmt="%d/%m/%Y %H:%M"):
    if dt is None:
        return ""
    return (dt.replace(tzinfo=timezone.utc) + _BAS_OFFSET).strftime(fmt)

templates.env.filters["fmt_ar"] = _fmt_ar
