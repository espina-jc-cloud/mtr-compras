"""
Lectura automática del remito de combustible con Claude (visión).

Extrae los datos visibles del remito/ticket (patente, litros, monto, fecha,
estación, tipo de combustible) para pre-llenar el form. Degradación elegante:
si no hay ANTHROPIC_API_KEY o falla, devuelve {} y el operario carga a mano.
"""
import os
import re
import json
import base64
from datetime import datetime

_MODEL = os.getenv("FUEL_OCR_MODEL", "claude-haiku-4-5-20251001")

_PROMPT = (
    "Sos un asistente que lee un remito o ticket de carga de combustible "
    "(estación de servicio) en español. Extraé SOLO lo que veas con claridad y "
    "devolvé EXCLUSIVAMENTE un JSON con estas claves (usá null si no aparece):\n"
    '{"vehicle_plate": string|null,  // patente del vehículo\n'
    ' "liters": number|null,          // litros cargados\n'
    ' "amount": number|null,          // importe total en pesos\n'
    ' "fuel_date": string|null,       // fecha en formato YYYY-MM-DD\n'
    ' "station": string|null,         // nombre de la estación\n'
    ' "fuel_type": string|null}       // uno de: gasoil_comun, gasoil_premium, nafta, nafta_premium\n'
    "No inventes datos. No agregues texto fuera del JSON."
)


def _media_type(filename: str, fallback: str = "image/jpeg") -> str:
    ext = (filename or "").lower().rsplit(".", 1)[-1]
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "webp": "image/webp", "gif": "image/gif",
    }.get(ext, fallback)


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def parse_remito(image_bytes: bytes, filename: str = "") -> dict:
    """Devuelve un dict con los campos detectados (o {} si no se pudo)."""
    if not image_bytes or not available():
        return {}
    try:
        import anthropic
    except Exception:
        return {}

    try:
        client = anthropic.Anthropic()  # toma ANTHROPIC_API_KEY del entorno
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": _media_type(filename),
                        "data": b64,
                    }},
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception:
        return {}

    # Extraer el JSON de la respuesta.
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(0))
    except Exception:
        return {}

    out = {}
    plate = raw.get("vehicle_plate")
    if plate:
        out["vehicle_plate"] = str(plate).strip().upper()
    for k in ("liters", "amount"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and v > 0:
            out[k] = v
        elif isinstance(v, str):
            try:
                out[k] = float(v.replace(".", "").replace(",", ".")) if v.strip() else None
            except ValueError:
                pass
    fd = raw.get("fuel_date")
    if fd:
        try:
            out["fuel_date"] = datetime.fromisoformat(str(fd)[:10]).date().isoformat()
        except ValueError:
            pass
    if raw.get("station"):
        out["station"] = str(raw["station"]).strip()[:60]
    ft = raw.get("fuel_type")
    if ft in ("gasoil_comun", "gasoil_premium", "nafta", "nafta_premium"):
        out["fuel_type"] = ft
    return {k: v for k, v in out.items() if v not in (None, "")}
