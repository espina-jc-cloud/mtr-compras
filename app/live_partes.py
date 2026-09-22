"""
Lector de los partes de turno que el jefe de turno manda por WhatsApp.

QUÉ RESUELVE
    El parte ya se escribe: cada seis horas alguien lo manda al grupo. Cargarlo
    de nuevo a mano en el sistema es escribir dos veces lo mismo, y es donde se
    cuelan los errores. Acá se pega el mensaje tal cual y el sistema lo lee.

DOS FORMATOS CONVIVEN EN EL MISMO GRUPO
    Uno escribe en mayúsculas y con asteriscos, una sección por bodega:
        *BODEGA 7*
        *VIAJES A MANUCHAR 2 VIAJES - 57560 KG 56 BIG BAG*
    Otro escribe corrido, con la bodega como "#4" y el destino como palabra
    suelta, a veces con el peso en el renglón de abajo:
        #4
        Manuchar 14 viajes (388 BB) 395880 kg
    Por eso el parser guarda el estado (bodega y destino en curso) en vez de
    exigir que cada renglón venga completo.

LO QUE NO HACE
    No decide nada: devuelve lo que leyó para que una persona lo confirme antes
    de guardar. Un parte mal leído que entra solo es peor que uno no leído.

VALIDADO CONTRA
    Los 19 partes del MV Macuru Arrow y los 4 del MV Ocean Innovation
    (17 al 22/09/2026), en los dos formatos y con sus correcciones.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

# Destinos tal como se escriben en el grupo → cómo los guarda el modelo.
#   MTR      → kg_deposito_mtr   (depósito propio)
#   TERCERO  → kg_tercero        (depósito de otro: Manuchar)
#   CV       → kg_cv_mtr         (costado de vapor, directo a camión)
#   DIRECTO  → kg_directo_mtr
DESTINOS = {
    "MTR": "MTR", "DEPOSITO": "MTR",
    "MANUCHAR": "TERCERO", "TERCERO": "TERCERO",
    "CV": "CV", "COSTADO": "CV",
    "DIRECTO": "DIRECTO",
}
CAMPO = {"MTR": "kg_deposito_mtr", "TERCERO": "kg_tercero",
         "CV": "kg_cv_mtr", "DIRECTO": "kg_directo_mtr"}

ETIQUETA = {"MTR": "Depósito MTR", "TERCERO": "Depósito de tercero",
            "CV": "Costado de vapor", "DIRECTO": "Directo"}

_TURNO  = re.compile(r"TURNO\s+DE\s+(\d{1,2})\s*A\s*(\d{1,2})", re.I)
_FECHA  = re.compile(r"\b(\d{2})-(\d{2})-(\d{2,4})\b")       # 18-09-26 y 20-09-2026
_BODEGA = re.compile(r"^(?:BODEGA\s*#?\s*|#\s*)(\d{1,2})\b")
_DEST   = re.compile(r"\b(MANUCHAR|DEPOSITO|TERCERO|COSTADO|DIRECTO|CV|MTR)\b")
_VIAJES = re.compile(r"(\d{1,3})\s*VIAJES?\b")
_KG     = re.compile(r"(\d{4,8})\s*KG\b")
_BULTOS = re.compile(r"\((\d{1,4})\s*(?:BB|BIG\s*BAG|PERFILES)\)|(\d{1,4})\s*BIG\s*BAG")


def _sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def parsear_parte(texto: str, fecha_por_defecto: date | None = None) -> dict:
    """Lee un parte pegado y devuelve qué entendió.

    {"ok": True, "fecha": date|None, "turno": "06-12", "desde": 6, "hasta": 12,
     "movimientos": [{"bodega": 7, "destino": "TERCERO", "viajes": 2,
                      "kg": 57560, "bultos": 56}],
     "avisos": [str]}
    o {"ok": False, "error": str} si el texto no es un parte.
    """
    if not (texto or "").strip():
        return {"ok": False, "error": "No pegaste nada."}

    t = _TURNO.search(texto)
    if not t:
        return {"ok": False, "error": 'No encontré el turno. El parte tiene que '
                                      'decir algo como "TURNO DE 12 A 18".'}
    desde, hasta = int(t.group(1)), int(t.group(2))
    turno = f"{desde:02d}-{hasta:02d}"

    avisos = []
    fecha = fecha_por_defecto
    m = _FECHA.search(texto)
    if m:
        anio = m.group(3)
        try:
            fecha = date(int(anio) if len(anio) == 4 else 2000 + int(anio),
                         int(m.group(2)), int(m.group(1)))
        except ValueError:
            avisos.append(f"La fecha {m.group(0)} no es válida: confirmá cuál va.")
    else:
        avisos.append("El parte no trae fecha: confirmá cuál es.")

    movimientos, bodega, pendiente = [], None, None
    for linea in texto.split("\n"):
        # De "ACUMULADO" para abajo van los totales del barco, no los del turno:
        # tienen el mismo formato que un movimiento y, si se leen, se duplica todo.
        if "ACUMULADO" in _sin_acentos(linea).upper():
            break
        L = _sin_acentos(re.sub(r"[*_]", "", linea)).strip().upper()
        if not L:
            continue

        b = _BODEGA.match(L)
        if b:
            bodega = int(b.group(1))
            L = L[b.end():].strip()          # el resto puede traer ya el movimiento
            d0 = _DEST.search(L)
            pendiente = DESTINOS.get(d0.group(1)) if d0 else None
            if not L:
                continue

        if L.startswith("TOTAL") or "RESTAN" in L:
            pendiente = None
            continue

        d = _DEST.search(L)
        destino = DESTINOS.get(d.group(1)) if d else pendiente
        v, k = _VIAJES.search(L), _KG.search(L)
        if d and not v and not k:            # el destino solo: el peso viene abajo
            pendiente = DESTINOS.get(d.group(1))
            continue
        if not destino or not v:
            continue

        bultos = _BULTOS.search(L)
        movimientos.append({
            "bodega": bodega,
            "destino": destino,
            "viajes": int(v.group(1)),
            "kg": int(k.group(1)) if k else None,
            "bultos": int(bultos.group(1) or bultos.group(2)) if bultos else None,
        })
        pendiente = None

    if not movimientos:
        return {"ok": False, "error": "Leí el turno pero ningún movimiento. Cada "
                                      "renglón necesita destino, viajes y kilos."}

    if any(x["bodega"] is None for x in movimientos):
        avisos.append("Hay movimientos sin bodega: no suman al avance de ninguna.")
    if any(x["kg"] is None for x in movimientos):
        avisos.append("Hay movimientos sin kilos: se guardan en cero.")

    return {"ok": True, "fecha": fecha, "turno": turno,
            "desde": desde, "hasta": hasta,
            "movimientos": movimientos, "avisos": avisos}


def resumen(movimientos: list[dict]) -> dict:
    """Totales de un parte leído, para mostrarlos antes de guardar."""
    out = {"kg": 0, "viajes": 0, "por_destino": {}}
    for m in movimientos:
        out["kg"] += m["kg"] or 0
        out["viajes"] += m["viajes"]
        d = out["por_destino"].setdefault(m["destino"], {"kg": 0, "viajes": 0})
        d["kg"] += m["kg"] or 0
        d["viajes"] += m["viajes"]
    return out
