from __future__ import annotations

import re
from datetime import datetime
from bs4 import BeautifulSoup


HEADERS = {
    "fecha ent.": "entry_date",
    "h. ent.": "entry_time",
    "fecha sal.": "exit_date",
    "h. sal.": "exit_time",
    "nro.": "trip_code",
    "pat. cam.": "plate",
    "tara": "tara_kg",
    "bruto": "bruto_kg",
    "neto": "neto_kg",
    "neto origen": "origen_kg",
    "chofer": "driver",
    "transporte": "transporte",
    "producto": "product",
    "cliente": "client",
    "operacion": "operation",
    "operativo": "operativo",
}


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _norm(value) -> str:
    return _clean(value).lower()


def _to_int(value):
    value = _clean(value)
    if not value:
        return None
    value = value.replace(".", "").replace(",", ".")
    try:
        return int(float(value))
    except Exception:
        return None


def _parse_date(value):
    value = _clean(value)
    if not value:
        return None

    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _shift_from_time(time_str):
    time_str = _clean(time_str)
    if not time_str:
        return None
    try:
        hour = int(time_str.split(":")[0])
    except Exception:
        return None

    if 0 <= hour < 6:
        return 1
    if 6 <= hour < 12:
        return 2
    if 12 <= hour < 18:
        return 3
    return 4


def _duration_min(entry_date, entry_time, exit_date, exit_time):
    if not entry_date or not exit_date or not entry_time or not exit_time:
        return None

    try:
        e_parts = [int(float(x)) for x in entry_time.split(":")]
        x_parts = [int(float(x)) for x in exit_time.split(":")]

        e_dt = entry_date.replace(
            hour=e_parts[0],
            minute=e_parts[1] if len(e_parts) > 1 else 0,
            second=e_parts[2] if len(e_parts) > 2 else 0,
        )
        x_dt = exit_date.replace(
            hour=x_parts[0],
            minute=x_parts[1] if len(x_parts) > 1 else 0,
            second=x_parts[2] if len(x_parts) > 2 else 0,
        )

        minutes = (x_dt - e_dt).total_seconds() / 60
        return round(minutes, 2) if minutes >= 0 else None
    except Exception:
        return None


def parse_old_system_html(file_content: bytes) -> dict:
    soup = BeautifulSoup(file_content, "lxml")

    cells = [_clean(td.get_text(" ", strip=True)) for td in soup.find_all("td")]

    operativo_header = ""
    for cell in cells:
        if _norm(cell).startswith("operativo:"):
            operativo_header = _clean(cell.split(":", 1)[1])
            break

    rows = []
    for tr in soup.find_all("tr"):
        row = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        if any(row):
            rows.append(row)

    header_idx = None
    header_map = {}

    for idx, row in enumerate(rows):
        normalized = [_norm(x) for x in row]
        if "fecha ent." in normalized and "nro." in normalized and "neto" in normalized:
            header_idx = idx
            for col_idx, label in enumerate(normalized):
                if label in HEADERS:
                    header_map[col_idx] = HEADERS[label]
            break

    if header_idx is None:
        return {"operativo": operativo_header or "Sin operativo", "trips": []}

    trips = []

    for row in rows[header_idx + 1:]:
        if not row:
            continue

        first = _norm(row[0])
        if first.startswith("total") or first.startswith("ingresos") or first.startswith("despachos"):
            continue

        data = {}
        for col_idx, field in header_map.items():
            data[field] = row[col_idx] if col_idx < len(row) else ""

        if not _clean(data.get("trip_code")):
            continue

        trip_code = _to_int(data.get("trip_code"))
        if trip_code is None:
            continue

        entry_date = _parse_date(data.get("entry_date"))
        exit_date = _parse_date(data.get("exit_date"))
        entry_time = _clean(data.get("entry_time"))
        exit_time = _clean(data.get("exit_time"))

        operativo = _clean(data.get("operativo")) or operativo_header or "Sin operativo"
        client = _clean(data.get("client"))
        product = _clean(data.get("product"))
        transporte = _clean(data.get("transporte"))

        neto_kg = _to_int(data.get("neto_kg")) or 0
        origen_kg = _to_int(data.get("origen_kg")) or 0
        diff_kg = neto_kg - origen_kg

        # Descartar filas basura/resúmenes: sin toneladas reales y sin identidad útil.
        # Ejemplo: archivos de Terminales y Servicios con secciones en cero.
        if neto_kg == 0 and origen_kg == 0 and diff_kg == 0:
            continue

        if not client and not product and not transporte and operativo in ("", "Sin operativo", "Ninguno"):
            continue

        trips.append({
            "trip_code": trip_code,
            "entry_date": entry_date,
            "entry_time": entry_time,
            "exit_date": exit_date,
            "exit_time": exit_time,
            "plate": _clean(data.get("plate")),
            "tara_kg": _to_int(data.get("tara_kg")),
            "bruto_kg": _to_int(data.get("bruto_kg")),
            "neto_kg": neto_kg,
            "origen_kg": origen_kg,
            "diff_kg": diff_kg,
            "client": client,
            "product": product,
            "transporte": transporte,
            "operativo": operativo,
            "duration_min": _duration_min(entry_date, entry_time, exit_date, exit_time),
            "shift_number": _shift_from_time(entry_time),
        })

    return {
        "operativo": operativo_header or (trips[0]["operativo"] if trips else "Sin operativo"),
        "trips": trips,
    }
