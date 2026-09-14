"""
Importador del Excel diario de horas ("HORA MTR 2026 NUEVA.xlsx").

FORMATO DEL ARCHIVO
    Una hoja por mes ("HS. SEPTIEMBRE. MTR I 2026"). Dentro de cada hoja, un
    bloque por día:

        FECHA        | 2026-09-03
        M.T.R. S.A.                          ← encabezado de grupo
        Apellido | Nombre | Hr. Ingreso |  | Hr. Egreso |  | Total Hr. | Nota
        Achaval  | Edgardo|      6      | 0|     16     | 0|    10     |
        …
        INGEE S.R.L.                         ← otro grupo
        Ojea     | Mario  |      6      | 0|     18     | 0|    12     |
        PORTERIA MTR I                       ← otro grupo
        Ortiz    | Miguel |      8      | 0|     16     | 0|     8     |

    La hora viene partida: columna C = hora, columna D = minutos. Un egreso de
    "24" es la medianoche.

CRITERIOS
  - `Total Hr.` del Excel NO se usa: el sistema recalcula. Se lee solo para
    contrastarlo en la vista previa y avisar si difiere.
  - Ingreso 0:00 Y egreso 0:00 = la persona no vino (ausente). Si alguno de los
    dos es distinto de cero son horas reales — el turno noche 00:00→06:00 es
    justamente eso y confundirlo con "ausente" borraría media planilla.
  - Los nombres del Excel no coinciden exactamente con la nómina ("Achaval,
    Edgardo" vs "Achaval, Edgardo F."), así que el match normaliza acentos y
    admite prefijos. Lo que no matchea NO se da de alta solo: se reporta.
"""
import io
import re
import unicodedata
from datetime import date, datetime

# Encabezados que no son personas.
_ENCABEZADOS = {"apellido", "fecha", "total", "totales", "nombre"}


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> int:
    """Devuelve el entero contenido en la celda. ':00' → 0, '8' → 8, 8.0 → 8."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    d = re.sub(r"\D", "", str(v))
    return int(d) if d else 0


def normalizar(s: str) -> str:
    """Sin acentos, sin dobles espacios, en minúsculas."""
    s = _txt(s).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def _hora(h: int, m: int):
    """(6, 0) → '06:00'. Devuelve None si no es una hora válida."""
    if not (0 <= h <= 24 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


# ══════════════════════════════════════════════════════════════════════════════
# Parseo
# ══════════════════════════════════════════════════════════════════════════════

def listar_hojas(contenido: bytes) -> list:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def parsear(contenido: bytes, hoja: str = None) -> dict:
    """Lee una hoja del Excel y devuelve los días con sus filas.

    {"hoja": str, "hojas": [...], "dias": [{"fecha": date, "filas": [...]}],
     "avisos": [...]}
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        if hoja and hoja in wb.sheetnames:
            ws = wb[hoja]
        else:
            # Por defecto, la última hoja: el archivo crece agregando meses.
            hoja = wb.sheetnames[-1]
            ws = wb[hoja]

        dias, avisos = [], []
        actual, grupo = None, ""

        for nro, row in enumerate(ws.iter_rows(values_only=True), start=1):
            row = list(row) + [None] * (8 - len(row))
            a, b = _txt(row[0]), _txt(row[1])
            if not a:
                continue

            # ── Nuevo día ────────────────────────────────────────────────────
            if a.upper() == "FECHA":
                f = row[1]
                if isinstance(f, datetime):
                    f = f.date()
                elif isinstance(f, date):
                    pass
                else:
                    f = None
                    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                        try:
                            f = datetime.strptime(_txt(row[1]), fmt).date()
                            break
                        except ValueError:
                            continue
                if f is None:
                    avisos.append(f"Fila {nro}: no pude leer la fecha {_txt(row[1])!r}.")
                    actual = None
                    continue
                actual = {"fecha": f, "filas": [], "fila_excel": nro}
                dias.append(actual)
                grupo = ""
                continue

            if actual is None:
                continue

            # ── Encabezado de columnas ───────────────────────────────────────
            if normalizar(a) in _ENCABEZADOS:
                continue

            # ── Encabezado de grupo: hay texto en A pero no hay Nombre ───────
            if not b:
                grupo = a
                continue

            # ── Fila de persona ──────────────────────────────────────────────
            h_in, m_in = _num(row[2]), _num(row[3])
            h_out, m_out = _num(row[4]), _num(row[5])
            ingreso, egreso = _hora(h_in, m_in), _hora(h_out, m_out)

            # 0:00 y 0:00 = no vino. Con uno solo distinto de cero son horas
            # reales (el turno noche entra justo a las 00:00).
            ausente = (h_in == 0 and m_in == 0 and h_out == 0 and m_out == 0)

            actual["filas"].append({
                "fila_excel": nro,
                "grupo": grupo,
                "apellido": a,
                "nombre": b,
                "ingreso": None if ausente else ingreso,
                "egreso": None if ausente else egreso,
                "ausente": ausente,
                "total_excel": _num(row[6]),
                "nota": _txt(row[7]) or None,
            })

        return {"hoja": hoja, "hojas": list(wb.sheetnames), "dias": dias, "avisos": avisos}
    finally:
        wb.close()


# ══════════════════════════════════════════════════════════════════════════════
# Match contra la nómina
# ══════════════════════════════════════════════════════════════════════════════

def indexar_personas(personas) -> dict:
    """Índice de búsqueda por apellido normalizado."""
    idx = {}
    for p in personas:
        idx.setdefault(normalizar(p.apellido), []).append(p)
    return idx


def buscar_persona(idx: dict, apellido: str, nombre: str):
    """Devuelve (persona, motivo). persona=None si no hay match unívoco.

    El Excel escribe "Achaval, Edgardo" y la nómina tiene "Achaval, Edgardo F.";
    también "Morales, Nicolas" contra "Morales, Santiago Nicolás". Por eso se
    prueba, en orden: igualdad exacta, prefijo en cualquiera de los dos
    sentidos, y por último que compartan alguna palabra del nombre.
    """
    ap, nom = normalizar(apellido), normalizar(nombre)
    candidatos = idx.get(ap, [])
    if not candidatos:
        return None, "apellido no está en la nómina"
    if len(candidatos) == 1 and not nom:
        return candidatos[0], "único con ese apellido"

    exactos = [p for p in candidatos if normalizar(p.nombre) == nom]
    if len(exactos) == 1:
        return exactos[0], "exacto"

    prefijo = [p for p in candidatos
               if normalizar(p.nombre).startswith(nom) or nom.startswith(normalizar(p.nombre))]
    if len(prefijo) == 1:
        return prefijo[0], "por prefijo"

    palabras = set(nom.split())
    comunes = [p for p in candidatos if palabras & set(normalizar(p.nombre).split())]
    if len(comunes) == 1:
        return comunes[0], "por nombre de pila"

    # NO se cae de nuevo a "único con ese apellido": si el Excel trae un nombre
    # y no coincide por ninguna vía, son personas distintas. Aceptarlo fusionaba
    # a "Morales, Nicolás" con "Morales, Juan Ignacio" y les mezclaba las horas.
    nombres = " / ".join(f"{p.apellido}, {p.nombre}" for p in candidatos)
    return None, (f"el apellido está pero el nombre no coincide con: {nombres}")


# ══════════════════════════════════════════════════════════════════════════════
# Aplicación — servicio reutilizable (pantalla manual y buzón automático)
# ══════════════════════════════════════════════════════════════════════════════

def clasificar_dia(db, fecha, filas_matcheadas):
    """¿Es seguro aplicar este día sin que una persona lo mire?

    Devuelve (seguro: bool, motivo: str|None).

    NO es seguro si tocaría algo que alguien ya decidió: una extra justificada,
    aprobada o rechazada. Ahí el dato de la planilla puede ser correcto, pero
    pisarlo borraría una decisión de gestión — eso lo mira una persona.
    """
    from app.models_asistencia import AsistenciaJornada

    if not filas_matcheadas:
        return False, "ninguna fila coincide con la nómina"

    decididas = (
        db.query(AsistenciaJornada)
        .filter(AsistenciaJornada.fecha == fecha,
                AsistenciaJornada.persona_id.in_([p.id for p in filas_matcheadas]),
                AsistenciaJornada.estado_extra.in_(
                    ["justificada", "aprobada", "rechazada"]))
        .count()
    )
    if decididas:
        return False, f"{decididas} jornada(s) con la extra ya resuelta"
    return True, None


def aplicar_dia(db, fecha, filas, idx, planta="", tipo="mtr", user_id=None,
                fuente="import", alta_nuevos=False):
    """Escribe un día completo. Devuelve (filas_aplicadas, sin_reconocer)."""
    from datetime import date as _date
    from app.models_asistencia import AsistenciaPersona, AsistenciaAuditLog
    from app.asistencia_calc import guardar_bloque, limpiar_dia, recalcular_jornada
    from sqlalchemy import func as _func

    aplicadas, sin_match = 0, 0
    for f in filas:
        persona, _ = buscar_persona(idx, f["apellido"], f["nombre"])

        if persona is None and tipo == "tercero" and alta_nuevos:
            ultimo = (db.query(_func.coalesce(
                _func.max(AsistenciaPersona.orden_planilla), 0)).scalar() or 0)
            persona = AsistenciaPersona(
                apellido=f["apellido"].strip(), nombre=f["nombre"].strip(),
                tipo="tercero", planta=planta or None, grupo=f["grupo"] or None,
                orden_planilla=ultimo + 10, activo=True, fecha_alta=_date.today())
            db.add(persona)
            db.flush()
            idx.setdefault(normalizar(persona.apellido), []).append(persona)
            db.add(AsistenciaAuditLog(
                entidad="persona", entidad_id=persona.id, accion="crear",
                valor_nuevo=f"{persona.apellido}, {persona.nombre} ({f['grupo']})",
                motivo="Alta automática desde la importación de terceros",
                user_id=user_id))

        if persona is None:
            sin_match += 1
            continue

        if f["grupo"] and persona.grupo != f["grupo"]:
            persona.grupo = f["grupo"]

        limpiar_dia(db, persona, fecha, user_id)
        if f["ausente"] or not f["ingreso"]:
            j = recalcular_jornada(db, persona, fecha, marcar_ausente=True)
        else:
            guardar_bloque(db, persona, fecha, f["ingreso"], f["egreso"] or "",
                           user_id=user_id, fuente=fuente)
            j = recalcular_jornada(db, persona, fecha)
        if j is not None:
            j.nota_origen = f["nota"]
            j.grupo_snap = f["grupo"] or None
            if planta:
                j.planta_snap = planta
        aplicadas += 1
    return aplicadas, sin_match
