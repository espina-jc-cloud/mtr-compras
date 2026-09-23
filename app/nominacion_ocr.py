"""
Lee la captura de pantalla que viene pegada en la nominación de Nutrien.

POR QUÉ HACE FALTA UN MODELO DE VISIÓN
    El resto del sistema evita la IA a propósito: el parser del line-up y el de
    los partes de turno son determinísticos y por eso se puede confiar en
    ellos. Acá no hay alternativa. Javier pega una captura de su planilla —no
    una tabla HTML, no un adjunto— así que el ETB y las toneladas existen
    únicamente como píxeles.

    La captura siempre tiene la misma forma:
        SPLIT BUQUES ARGENTINA - ARRIBOS <año>
        BUQUE | Proveedor | Origen | Producto | MT TOTAL | Demurrage |
        <puerto>: ETB | MT ...
    con una fila por buque nominado.

    OJO CON LOS PUERTOS
        Un mismo buque puede descargar en varios puertos, y la planilla trae un
        par de columnas ETB/MT por cada uno: Necochea, Bahía Blanca, San
        Nicolás MTR. Sólo importa el nuestro. Leer el ETB de la columna de al
        lado sería peor que no leer nada, porque parecería un dato válido.

LO QUE LEE DE ACÁ NUNCA SE DA POR CIERTO
    Todo lo que salga de la imagen entra al sistema marcado "a confirmar". Una
    fecha mal leída en un ETB mueve camiones y personal: que el dato exista
    rápido vale menos que saber si alguien lo miró.

SIN API KEY SIGUE SIRVIENDO
    Si no hay clave, devuelve [] y el arribo se da de alta igual con lo que sí
    está en texto (buque, producto, servicios). El ETB queda vacío para
    completarlo a mano, con la imagen al lado.

Env vars:
    ANTHROPIC_API_KEY / OPENAI_API_KEY   cuál se usa (OpenAI tiene prioridad)
    NOMINACION_OCR_MODEL                 override del modelo (opcional)
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import date, datetime

_PROMPT = (
    "Las imágenes son capturas de pantalla de un correo. Una o más pueden ser "
    "un logo o una firma: ignoralas por completo.\n"
    "Buscá la tabla titulada 'SPLIT BUQUES ARGENTINA' y devolvé UNA fila por "
    "cada buque listado.\n"
    "Devolvé EXCLUSIVAMENTE un JSON con esta forma, sin texto alrededor:\n"
    '{"filas": [{"buque": string, "proveedor": string|null, '
    '"origen": string|null, "producto": string|null, '
    '"mt_total": number|null, "demurrage": number|null, '
    '"etb": "YYYY-MM-DD"|null, "mt_mtr": number|null}]}\n'
    "· La tabla puede tener varios puertos, cada uno con su par de columnas "
    "ETB y MT (Necochea, Bahía Blanca, San Nicolás MTR). 'etb' y 'mt_mtr' "
    "salen ÚNICAMENTE del par que está bajo el encabezado 'San Nicolás MTR'. "
    "Si ese par está vacío para un buque, poné null en los dos, aunque otro "
    "puerto sí tenga datos.\n"
    "· 'mt_total' es la columna 'MT TOTAL' del buque entero, que puede ser "
    "mayor que 'mt_mtr' cuando el buque descarga en más de un puerto.\n"
    "· Los números van sin separador de miles y con punto decimal: 4400 o 4400.5.\n"
    "· Las fechas de la planilla están en dd/mm/aaaa; convertilas a aaaa-mm-dd.\n"
    "· Si una celda está vacía o no la leés con claridad, poné null. "
    "No adivines: un ETB inventado es peor que un ETB vacío.\n"
    "· Si no encontrás esa tabla en ninguna imagen, devolvé {\"filas\": []}."
)

_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
          "gif": "image/gif", "webp": "image/webp"}


def _media_type(nombre: str, tipo: str = "") -> str:
    if tipo in _MEDIA.values():
        return tipo
    return _MEDIA.get((nombre or "").lower().rsplit(".", 1)[-1], "image/png")


def _proveedor() -> str:
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return ""


def disponible() -> bool:
    return bool(_proveedor())


def _preguntar_anthropic(imagenes: list[dict]) -> str:
    import anthropic
    cliente = anthropic.Anthropic()
    modelo = os.getenv("NOMINACION_OCR_MODEL", "claude-haiku-4-5-20251001")
    contenido = [{"type": "image", "source": {
        "type": "base64", "media_type": i["media"], "data": i["b64"]}}
        for i in imagenes]
    contenido.append({"type": "text", "text": _PROMPT})
    msg = cliente.messages.create(model=modelo, max_tokens=1200,
                                  messages=[{"role": "user", "content": contenido}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _preguntar_openai(imagenes: list[dict]) -> str:
    from openai import OpenAI
    cliente = OpenAI()
    modelo = os.getenv("NOMINACION_OCR_MODEL", "gpt-4o-mini")
    contenido = [{"type": "text", "text": _PROMPT}]
    contenido += [{"type": "image_url",
                   "image_url": {"url": f"data:{i['media']};base64,{i['b64']}"}}
                  for i in imagenes]
    r = cliente.chat.completions.create(
        model=modelo, max_tokens=1200,
        messages=[{"role": "user", "content": contenido}])
    return r.choices[0].message.content or ""


def _numero(v):
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, str) and v.strip():
        limpio = re.sub(r"[^\d,.\-]", "", v)
        # "4,400.00" es inglés y "4.400,00" es castellano: manda el último signo.
        if "," in limpio and "." in limpio:
            limpio = (limpio.replace(",", "") if limpio.rfind(".") > limpio.rfind(",")
                      else limpio.replace(".", "").replace(",", "."))
        elif "," in limpio:
            limpio = limpio.replace(",", ".")
        try:
            n = float(limpio)
            return n if n > 0 else None
        except ValueError:
            return None
    return None


def _fecha(v) -> date | None:
    if not isinstance(v, str) or not v.strip():
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(v.strip(), formato).date()
        except ValueError:
            continue
    return None


def leer(imagenes: list[dict]) -> list[dict]:
    """Las filas de la tabla de la nominación.

    `imagenes` es [{"nombre", "tipo", "datos"}] tal como las devuelve
    app/arribos_mail.py. Van todas: el modelo descarta los logos.

    Devuelve [] cuando no hay API key, cuando falla la llamada o cuando no
    encontró la tabla. Nunca levanta: que no se pueda leer una captura no
    puede dejar sin cargar el resto de las nominaciones.
    """
    if not imagenes or not disponible():
        return []

    payload = [{"media": _media_type(i.get("nombre", ""), i.get("tipo", "")),
                "b64": base64.standard_b64encode(i["datos"]).decode("ascii")}
               for i in imagenes if i.get("datos")]
    if not payload:
        return []

    try:
        texto = (_preguntar_openai(payload) if _proveedor() == "openai"
                 else _preguntar_anthropic(payload))
    except Exception:
        return []

    m = re.search(r"\{.*\}", texto or "", re.S)
    if not m:
        return []
    try:
        crudo = json.loads(m.group(0))
    except Exception:
        return []

    out = []
    for f in (crudo.get("filas") or []):
        buque = (f.get("buque") or "").strip()
        if not buque:
            continue
        out.append({
            "buque": re.sub(r"\s+", " ", buque).upper(),
            "proveedor": (f.get("proveedor") or "").strip() or None,
            "origen": (f.get("origen") or "").strip() or None,
            "producto": (f.get("producto") or "").strip() or None,
            "mt_total": _numero(f.get("mt_total")),
            "demurrage": _numero(f.get("demurrage")),
            "etb": _fecha(f.get("etb")),
            "mt_mtr": _numero(f.get("mt_mtr")),
        })
    return out
