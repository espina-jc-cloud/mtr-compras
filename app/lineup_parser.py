"""
Parser del lineup del Puerto de San Nicolás (PDF) — para el módulo Próximos Arribos.

Portado y simplificado del parser probado de HidrovíaData. Extrae UN registro por
buque (no por cliente/fila) con los campos operativos del lineup, para enriquecer
los arribos que el usuario ya viene siguiendo. NO importa todo el puerto: el
matching contra los arribos del usuario se hace en el router.

Columnas de la tabla del lineup (índice):
  0 BUQUE · 1 AGENCIA · 2 MATERIAL · 3 CLIENTES · 4 TONS · 5 OPERADOR
  6 OPERACIÓN · 7 READY · 8 SECTOR · 9 ETB · 10 OBSERVACION · 11 ETC
"""
from __future__ import annotations
import io
import re
from datetime import datetime


# ── Limpieza de celdas ─────────────────────────────────────────────────────────

def is_dimension_only(val: str) -> bool:
    return bool(re.match(r"^\d+\s*[Mm]ts\.?\s*$", (val or "").strip()))


def clean_cell(raw) -> str:
    if raw is None:
        return ""
    val = str(raw).strip()
    val = re.sub(r"^X\s*\n?", "", val)
    return val.split("\n")[0].strip()


def cell_join(raw) -> str:
    """Une las líneas de una celda con espacio (para conservar 'ETB AM/PM')."""
    if raw is None:
        return ""
    val = re.sub(r"^X\s*\n?", "", str(raw).strip())
    val = val.replace("\n", " ").strip()
    val = re.sub(r"\s+", " ", val)
    return "" if val in ("", "X") else val


def clean_agency(raw: str) -> str:
    if not raw:
        return ""
    val = re.sub(r"^X\s*\n", "", str(raw).strip()).split("\n")[0].strip()
    return re.sub(r"\s+\d+\s*$", "", val).strip()


def normalize_operacion(raw: str) -> str:
    if not raw:
        return ""
    val = re.sub(r"^X\s*\n?", "", str(raw).strip()).split("\n")[0].strip()
    val = re.sub(r"X", "", val).strip()
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", val):
        return ""
    return val


def extract_origin(obs: str) -> str:
    if not obs:
        return ""
    obs = re.sub(r"^X\s*\n?", "", str(obs).strip()).replace("\n", " ").strip()
    if not obs:
        return ""
    m = re.search(r"-\s*(.+)$", obs)
    if m:
        return m.group(1).strip()
    if re.match(r"^\d[\d.,\s]*$", obs):
        return ""
    return obs


def clean_vessel_name(raw: str) -> str:
    """Extrae el nombre real del buque (saca prefijo X, dimensiones, artefactos OCR)."""
    if not raw:
        return ""
    raw = re.sub(r"^X\s*\n?", "", raw.strip())

    name = ""
    for line in raw.split("\n"):
        line = line.strip()
        if not line or re.match(r"^[Xx]+$", line) or is_dimension_only(line):
            continue
        if line[0].isupper() and len(line) >= 4:
            name = line
            break
    if not name:
        for line in raw.split("\n"):
            line = line.strip()
            if line and not re.match(r"^[Xx]+$", line):
                name = line
                break
    if not name:
        name = raw.split("\n")[0].strip()

    name = re.sub(r"(\s+(TYS|MTR\.?))+\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+\d+\s*[Mm][Tt][Ss]?\.?\s*$", "", name)
    name = re.sub(r"\s+CALADO\s+[\d,\.]+\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^[a-z]\S{0,2}\s+", "", name)
    if name and name[0].islower():
        m = re.search(r"[A-Z]{2}", name)
        if m and m.start() <= 3:
            name = name[m.start():]

    name = name.strip()
    if not name or not name[0].isupper():
        return ""
    return name


# ── Clasificación de filas ──────────────────────────────────────────────────────

def is_separator_row(row: list) -> bool:
    for cell in row:
        if cell and re.match(r"^(X\s*)+$", str(cell).strip()):
            if sum(1 for c in row if c and str(c).strip() not in ("", "X")) == 0:
                return True
    return False


def is_column_header_row(row: list) -> bool:
    return str(row[0] or "").strip() in (
        "BUQUE", "MUELLE CARGA GENERAL", "MUELLE ELEVADOR", "MUELLE AES", "REFERENCIAS"
    )


def is_total_row(row: list) -> bool:
    return any(cell and "TOTAL" in str(cell).upper() for cell in row)


def is_empty_row(row: list) -> bool:
    return all(not c or not str(c).strip() for c in row)


def detect_muelle(cell_text: str) -> str | None:
    m = re.match(r"MUELLE\s+(.+)", str(cell_text or "").strip(), re.IGNORECASE)
    return m.group(1).strip().split("\n")[0] if m else None


def is_lineup_pdf(full_text: str) -> bool:
    t = (full_text or "").upper()
    return "LINE UP PUERTO SAN NICOLAS" in t or "MUELLE" in t


def extract_pdf_date(full_text: str) -> str | None:
    for m in re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", full_text or ""):
        try:
            return datetime.strptime(m, "%d/%m/%Y").date().isoformat()
        except ValueError:
            pass
    return None


# ── Normalización para matching ───────────────────────────────────────────────

def canon_vessel(name: str) -> str:
    """Clave de comparación: mayúsculas, sin M/V, sin dimensiones, espacios únicos."""
    s = (name or "").upper().split("\n")[0]
    s = re.sub(r"\bM[\./]?\s*V\b", " ", s)     # M/V, M.V, MV
    s = re.sub(r"\bREM\.?\b", " ", s)           # remolcador
    s = re.sub(r"\d+\s*MT?S?\.?", " ", s)       # dimensiones
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── Parser principal ────────────────────────────────────────────────────────────

def parse_lineup_pdf(file_bytes: bytes):
    """Devuelve (pdf_date_iso, vessels). `vessels` = lista de dicts, uno por buque.

    Lanza ValueError si no se puede leer o no parece un lineup de San Nicolás.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("El servidor no puede leer PDFs (falta pdfplumber).")

    if not file_bytes:
        raise ValueError("El archivo está vacío.")

    try:
        pdf = pdfplumber.open(io.BytesIO(file_bytes))
    except Exception:
        raise ValueError("El archivo no es un PDF válido.")

    vessels: list[dict] = []
    seen_canon: set[str] = set()
    current_muelle = "CARGA GENERAL"

    try:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        if not is_lineup_pdf(full_text):
            raise ValueError("El PDF no parece un lineup del Puerto de San Nicolás.")
        pdf_date = extract_pdf_date(full_text)

        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if not table:
                    continue
                muelle_cand = detect_muelle(str(table[0][0] or "").strip())
                if muelle_cand:
                    current_muelle = muelle_cand

                for row in table:
                    if (len(row) < 12 or is_separator_row(row) or is_column_header_row(row)
                            or is_total_row(row) or is_empty_row(row)):
                        continue
                    col0 = re.sub(r"^X\s*\n?", "", str(row[0] or "").strip()).strip()
                    if not col0 or col0 == "X" or is_dimension_only(col0):
                        continue
                    name = clean_vessel_name(col0)
                    if not name:
                        continue
                    cn = canon_vessel(name)
                    # Descartar ruido (filas 'X X X', restos) — exigir 3 letras seguidas.
                    if not cn or cn in seen_canon or not re.search(r"[A-Z]{3}", cn):
                        continue
                    seen_canon.add(cn)
                    vessels.append({
                        "buque":       name,
                        "buque_canon": cn,
                        "agencia":     clean_agency(str(row[1] or "")),
                        "material":    clean_cell(row[2]),
                        "operacion":   normalize_operacion(clean_cell(row[6])),
                        "ready":       cell_join(row[7]),
                        "posicion":    cell_join(row[8]),
                        "etb":         cell_join(row[9]),
                        "origen":      extract_origin(str(row[10] or "")),
                        "etc":         cell_join(row[11]),
                        "muelle":      current_muelle,
                    })
    finally:
        pdf.close()

    return pdf_date, vessels
