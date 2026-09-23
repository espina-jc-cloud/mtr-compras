"""
Lector del buzón para Próximos Arribos: nominaciones de Nutrien y line-ups.

DOS CORREOS, DOS FORMAS
    NOMINACIÓN — la manda Javier Schuck (Nutrien) con asunto
    "NOMINACIÓN MV <buque> - <producto>". El cuerpo dice el buque y los
    servicios contratados; el ETB, las toneladas, el proveedor y el origen
    vienen en una CAPTURA DE PANTALLA de su Excel, pegada en el mail. No hay
    tabla HTML ni adjunto: es una imagen. Por eso las imágenes se devuelven
    tal cual y las lee app/nominacion_ocr.py.

    LINE UP — llega reenviado con asunto "LINE UP PUERTO SAN NICOLAS dd-mm-aa"
    y el PDF adjunto, que app/lineup_parser.py ya sabe leer entero.

POR QUÉ ESTE MÓDULO NO DECIDE NADA
    Devuelve lo que encontró y ahí termina. Quién se da de alta, qué se
    actualiza y qué queda pendiente de confirmar lo resuelve el router, que es
    donde está la regla de negocio y donde se puede mirar antes de guardar.

EL BUZÓN SE ABRE SIEMPRE EN SOLO LECTURA
    Igual que el de las horas: no marca leídos, no mueve, no borra.

CONFIGURACIÓN
    Usa las mismas variables ASISTENCIA_MAIL_* que el buzón de horas, porque es
    la misma casilla. Las propias:
      ARRIBOS_MAIL_DIAS_NOMINACION   default 90
      ARRIBOS_MAIL_DIAS_LINEUP       default 20
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from app.asistencia_mail import TIMEOUT_SEG, _texto, config, configurado

# Tope de mails a mirar por pasada, para que una casilla con mucho movimiento
# no convierta esto en una tarea eterna.
MAX_CANDIDATOS = 300

_IMG_OK = ("image/png", "image/jpeg", "image/jpg", "image/gif")


def _sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def _cuerpo_texto(msg) -> str:
    for parte in msg.walk():
        if parte.get_content_type() != "text/plain":
            continue
        if "attachment" in str(parte.get("Content-Disposition") or ""):
            continue
        try:
            return (parte.get_payload(decode=True) or b"").decode(
                parte.get_content_charset() or "utf-8", "replace")
        except Exception:
            pass
    return ""


def _fecha(msg):
    try:
        return parsedate_to_datetime(msg.get("Date"))
    except Exception:
        return None


# ── Nominaciones ──────────────────────────────────────────────────────────────

def buques_del_asunto(asunto: str) -> list[str]:
    """Los buques nombrados en el asunto de una nominación.

    "NOMINACIÓN MV PAIWAN DIAMOND y KYVELI GS - UREA" → los dos buques. El
    guión separa el producto, y la "y" separa buques cuando nomina más de uno
    en el mismo correo.
    """
    s = _sin_acentos(asunto)
    s = re.sub(r"^\s*(RE|RV|FW|FWD)\s*:\s*", "", s).strip()
    m = re.search(r"NOMINACION\s*(?:DE\s*)?(?:BUQUE\s*)?(.+)", s)
    if not m:
        return []
    cuerpo = re.split(r"\s+[-–]\s+", m.group(1))[0]
    partes = re.split(r"\s+Y\s+|\s*,\s*|\s*/\s*", cuerpo)
    return [re.sub(r"\s+", " ", p).strip() for p in partes if p.strip()]


def producto_del_asunto(asunto: str) -> str:
    """El producto que dice el asunto, después del guión.

    El asunto se reenvía con agregados. Javier manda
    "NOMINACIÓN MV OCEAN INNOVATION - MAP" y en el reenvío llegó
    "RV: NOMINACIÓN MV  OCEAN INNOVATION - MAP 17/18 SEP 2026": quedarse con
    todo lo que sigue al guión dejaba la mercadería como
    "MAP 17/18 SEP 2026". El producto es lo que viene antes del primer pedazo
    con números — MAP, DAP, UREA, AMSUL, MOP no los tienen.
    """
    partes = re.split(r"\s+[-–]\s+", _sin_acentos(asunto))
    if len(partes) < 2:
        return ""
    palabras = []
    for palabra in re.sub(r"\s+", " ", partes[-1]).strip().split():
        if any(c.isdigit() for c in palabra):
            break
        palabras.append(palabra)
    return " ".join(palabras).strip()


def servicios_del_cuerpo(texto: str) -> list[str]:
    """La lista de servicios contratados, que viene como viñetas."""
    out = []
    for linea in (texto or "").splitlines():
        m = re.match(r"\s*\*\s+(.{3,60})\s*$", linea)
        if m:
            out.append(re.sub(r"\s+", " ", m.group(1)).strip())
    return out


def _es_nominacion(asunto: str) -> bool:
    return "NOMINACION" in _sin_acentos(asunto)


def _es_lineup(asunto: str) -> bool:
    return bool(re.search(r"LINE\s*-?\s*UP", _sin_acentos(asunto)))


# La captura de la planilla es una tira: una o dos filas de una tabla ancha.
# Medida en los correos reales va de 1003x119 a 1217x94 — relación de 8 a 13—,
# mientras que el logo de la firma de Nutrien es siempre 512x182 (2,8) y las
# fotos que se cuelan en los hilos respondidos no pasan de 5.
TABLA_RATIO_MINIMO = 6.0
TABLA_ANCHO_MINIMO = 700


def imagen_de_la_tabla(imagenes: list[dict]) -> dict | None:
    """Cuál de las imágenes del mail es la captura de la planilla.

    Sirve para guardar junto al arribo la que hay que mirar para confirmar el
    ETB. Sin esto se guardaba la primera del mail, que en un hilo respondido
    puede ser cualquier cosa — en la nominación del MV KOCIEWIE era una foto
    del buque.

    Si Pillow no está o ninguna imagen tiene forma de tira, devuelve la
    primera: es mejor mostrar algo que no mostrar nada.
    """
    if not imagenes:
        return None
    try:
        from io import BytesIO

        from PIL import Image
    except Exception:
        return imagenes[0]

    candidatas = []
    for i in imagenes:
        try:
            ancho, alto = Image.open(BytesIO(i["datos"])).size
        except Exception:
            continue
        if alto and ancho >= TABLA_ANCHO_MINIMO and ancho / alto >= TABLA_RATIO_MINIMO:
            candidatas.append((ancho / alto, ancho, i))
    if not candidatas:
        return imagenes[0]
    return max(candidatas, key=lambda x: (x[0], x[1]))[2]


def _imagenes(msg) -> list[dict]:
    """Las imágenes embebidas del mail, en el orden en que aparecen.

    Van todas, el logo de la firma incluido: distinguir la captura del logo por
    tamaño o por nombre es adivinar, y si se elige mal se pierde el ETB. Quien
    las lee ya descarta las que no son una tabla.
    """
    out = []
    for parte in msg.walk():
        tipo = (parte.get_content_type() or "").lower()
        if tipo not in _IMG_OK:
            continue
        datos = parte.get_payload(decode=True) or b""
        if len(datos) < 800:                      # espaciadores de Outlook
            continue
        out.append({"nombre": parte.get_filename() or "imagen",
                    "tipo": tipo, "datos": datos})
    return out


def _abrir(c):
    m = imaplib.IMAP4_SSL(c["host"], c["port"], timeout=TIMEOUT_SEG)
    m.login(c["user"], c["password"])
    estado, _ = m.select(c["carpeta"], readonly=True)   # jamás escribe en el buzón
    if estado != "OK":
        m.logout()
        raise RuntimeError(f'No pude abrir la carpeta "{c["carpeta"]}".')
    return m


def _buscar(criterio_extra: str, dias: int, quedarse, limite: int) -> dict:
    """Recorre el buzón y devuelve lo que `quedarse(msg, asunto)` acepte."""
    c = config()
    if not configurado():
        return {"ok": False, "error": "El buzón no está configurado.", "mensajes": []}

    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%d-%b-%Y")
    try:
        m = _abrir(c)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "mensajes": []}

    try:
        estado, datos = m.search(None, f'(SINCE {desde} {criterio_extra})')
        if estado != "OK":
            return {"ok": False, "error": "La búsqueda IMAP falló.", "mensajes": []}
        ids = (datos[0] or b"").split()[-MAX_CANDIDATOS:]
        out = []
        for uid in reversed(ids):                 # del más nuevo al más viejo
            if len(out) >= limite:
                break
            # BODY.PEEK no marca el mail como leído; BODY[] sí lo haría.
            estado, bruto = m.fetch(uid, "(BODY.PEEK[])")
            if estado != "OK" or not bruto or not isinstance(bruto[0], tuple):
                continue
            msg = email.message_from_bytes(bruto[0][1])
            item = quedarse(msg, _texto(msg.get("Subject")))
            if item:
                item.update(message_id=(msg.get("Message-ID") or f"uid:{uid.decode()}").strip(),
                            remitente=_texto(msg.get("From")), fecha=_fecha(msg))
                out.append(item)
        return {"ok": True, "mensajes": out, "error": None}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "mensajes": []}
    finally:
        try:
            m.logout()
        except Exception:
            pass


def buscar_nominaciones(limite: int = 40, dias: int | None = None) -> dict:
    """Las nominaciones del buzón, con sus imágenes sin interpretar."""
    dias = dias or int(os.getenv("ARRIBOS_MAIL_DIAS_NOMINACION", "90") or 90)

    def quedarse(msg, asunto):
        if not _es_nominacion(asunto):
            return None
        buques = buques_del_asunto(asunto)
        if not buques:
            return None
        texto = _cuerpo_texto(msg)
        return {"asunto": asunto, "buques": buques,
                "producto": producto_del_asunto(asunto),
                "servicios": servicios_del_cuerpo(texto),
                "imagenes": _imagenes(msg), "texto": texto}

    return _buscar('SUBJECT "NOMINAC"', dias, quedarse, limite)


def buscar_lineups(limite: int = 6, dias: int | None = None) -> dict:
    """Los line-up recientes, con el PDF adjunto listo para el parser."""
    dias = dias or int(os.getenv("ARRIBOS_MAIL_DIAS_LINEUP", "20") or 20)

    def quedarse(msg, asunto):
        if not _es_lineup(asunto):
            return None
        for parte in msg.walk():
            nombre = parte.get_filename() or ""
            if not nombre.lower().endswith(".pdf"):
                continue
            datos = parte.get_payload(decode=True) or b""
            if datos:
                return {"asunto": asunto, "archivo": _texto(nombre), "contenido": datos}
        return None

    return _buscar('SUBJECT "LINE"', dias, quedarse, limite)
