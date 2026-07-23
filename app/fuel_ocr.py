"""
Lectura automática del remito de combustible con IA de visión.

Extrae los datos visibles del remito/ticket (patente, litros, monto, fecha,
estación, tipo de combustible) para pre-llenar el form. Soporta OpenAI o
Anthropic según qué API key esté seteada (OpenAI tiene prioridad). Degradación
elegante: si no hay ninguna key o falla, devuelve {} y el operario carga a mano.

Env vars:
  OPENAI_API_KEY      → usa OpenAI      (modelo por defecto gpt-4o-mini)
  ANTHROPIC_API_KEY   → usa Anthropic   (modelo por defecto claude-haiku-4-5)
  FUEL_OCR_MODEL      → override del modelo (opcional)
"""
import os
import re
import json
import base64
from datetime import datetime

_VALID_FUEL = ("gasoil_comun", "gasoil_premium", "nafta", "nafta_premium")

_PROMPT = (
    "Sos un asistente que lee un remito o ticket de carga de combustible "
    "(estación de servicio) en español. Extraé SOLO lo que veas con claridad y "
    "devolvé EXCLUSIVAMENTE un JSON con estas claves (usá null si no aparece):\n"
    '{"vehicle_plate": string|null,   // patente del vehículo\n'
    ' "liters": number|null,           // litros cargados\n'
    ' "amount": number|null,           // importe total en pesos\n'
    ' "fuel_date": string|null,        // fecha YYYY-MM-DD\n'
    ' "station": string|null,          // nombre de la estación\n'
    ' "fuel_type": string|null}        // uno de: gasoil_comun, gasoil_premium, nafta, nafta_premium\n'
    "No inventes datos. No agregues texto fuera del JSON."
)


def _media_type(filename: str, fallback: str = "image/jpeg") -> str:
    ext = (filename or "").lower().rsplit(".", 1)[-1]
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "webp": "image/webp", "gif": "image/gif",
    }.get(ext, fallback)


def _provider() -> str:
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return ""


def available() -> bool:
    return bool(_provider())


def _ask_openai(b64: str, media: str) -> str:
    from openai import OpenAI
    client = OpenAI()  # toma OPENAI_API_KEY del entorno
    model = os.getenv("FUEL_OCR_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:{media};base64,{b64}"}},
            ],
        }],
    )
    return resp.choices[0].message.content or ""


def _ask_anthropic(b64: str, media: str) -> str:
    import anthropic
    client = anthropic.Anthropic()  # toma ANTHROPIC_API_KEY del entorno
    model = os.getenv("FUEL_OCR_MODEL", "claude-haiku-4-5-20251001")
    msg = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media, "data": b64}},
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def parse_remito(image_bytes: bytes, filename: str = "") -> dict:
    """Devuelve un dict con los campos detectados (o {} si no se pudo)."""
    prov = _provider()
    if not image_bytes or not prov:
        return {}

    media = _media_type(filename)
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    try:
        text = _ask_openai(b64, media) if prov == "openai" else _ask_anthropic(b64, media)
    except Exception:
        return {}

    m = re.search(r"\{.*\}", text or "", re.S)
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
        elif isinstance(v, str) and v.strip():
            try:
                out[k] = float(v.replace(".", "").replace(",", "."))
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
    if raw.get("fuel_type") in _VALID_FUEL:
        out["fuel_type"] = raw["fuel_type"]
    return {k: v for k, v in out.items() if v not in (None, "")}
