"""
Router: Despachos (Cupos y Despachos operativos de camiones)

Endpoints:
  GET  /despachos                — listado + KPIs + filtros
  GET  /despachos/import         — form de importación
  POST /despachos/import         — procesar upload + preview
  POST /despachos/import/confirm — confirmar e insertar batch
  GET  /despachos/{rid}          — detalle / edición
  POST /despachos/{rid}/status   — cambiar estado
  POST /despachos/{rid}/edit     — guardar cambios
  POST /despachos/{rid}/reprogram — reprogramar a otra fecha
"""

import io
import hashlib
import uuid
from datetime import date, datetime
from typing import Optional

import openpyxl
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import require_role
from app.models_cupos import (
    CupoDespacho, ImportBatch,
    DESPACHO_ESTADOS, DESPACHO_ESTADO_LABELS, ESTADO_CSS,
)
from app.templates import templates

router = APIRouter(prefix="/despachos", tags=["despachos"])

_ROLES = ("admin", "superadmin", "planta", "operador")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de importación
# ══════════════════════════════════════════════════════════════════════════════

def _detect_source(wb: openpyxl.Workbook) -> str:
    """Detecta si el archivo es de Nutrien o CNA basándose en los nombres de hoja."""
    names = [s.lower() for s in wb.sheetnames]
    if "despachos" in names:
        return "cna"
    if any("nutrien" in n or "ganel" in n or "embolsado" in n or "ramallo" in n
           for n in names):
        return "nutrien"
    if "base" in names:
        return "nutrien"
    # Fallback: si la primera hoja tiene columna IN/OUT → CNA
    ws = wb.active
    row1 = [str(ws.cell(1, c).value or "").strip().upper() for c in range(1, 10)]
    if "IN/OUT" in row1:
        return "cna"
    return "nutrien"


def _find_header_row(ws, keyword_col: str, max_scan: int = 20) -> Optional[int]:
    """
    Encuentra la primera fila que contenga keyword_col como encabezado.
    Retorna None si no encuentra.
    """
    keyword = keyword_col.upper().strip()
    for r in range(1, max_scan + 1):
        for c in range(1, ws.max_column + 1):
            val = str(ws.cell(r, c).value or "").upper().strip()
            if val == keyword:
                return r
    return None


def _normalize_str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.upper() not in ("NONE", "N/A", "NULL") else None


def _normalize_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, (datetime,)):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                pass
    return None


def _normalize_num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _row_hash(*parts) -> str:
    joined = "|".join(str(p or "").strip().upper() for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


# ─── Parser Nutrien ───────────────────────────────────────────────────────────

def _sheet_max_date(ws, fc: int, hrow: int) -> Optional[date]:
    """Retorna la fecha más reciente de una hoja (columna fc, a partir de hrow+1)."""
    latest = None
    for r in range(hrow + 1, min(ws.max_row + 1, hrow + 500)):
        v = _normalize_date(ws.cell(r, fc).value)
        if v and (latest is None or v > latest):
            latest = v
    return latest


def _parse_nutrien(
    wb: openpyxl.Workbook,
    date_from: Optional[date] = None,
    date_to:   Optional[date] = None,
) -> list[dict]:
    """
    Parsea las hojas de cupos de Nutrien.

    Lógica de selección de hoja (CAMBIADA):
    - Prioridad 1: hoja BASE (más limpia, sin duplicados con otras hojas)
    - Prioridad 2: primera hoja operativa (ganel_embolsado_ramallo, etc.)
    - Se omiten hojas cuya fecha más reciente sea anterior a hace 180 días
      (evita importar datos viejos de AMSUL PROFERTIL, 2,000 urea espacio, etc.)
    - Se toma UNA sola hoja, no varias (evita doble importación BASE+ganel)

    date_from / date_to: filtro de fecha aplicado ANTES de agregar la fila.
    """
    from datetime import timedelta
    cutoff_stale = date.today() - timedelta(days=180)  # hojas "viejas" = se omiten

    rows = []

    # ── Prioridad 1: buscar hoja BASE ────────────────────────────────────────
    # BASE es la más limpia: header en fila 1, sin mezcla de hojas
    if "BASE" in wb.sheetnames:
        chosen_sheet = "BASE"
    else:
        # ── Prioridad 2: primera hoja operativa reciente ─────────────────────
        chosen_sheet = None
        candidates = []
        for name in wb.sheetnames:
            nl = name.lower()
            if any(k in nl for k in ("ganel", "embolsado", "ramallo",
                                     "urea espacio", "amsul", "profertil")):
                candidates.append(name)

        for name in candidates:
            ws_test = wb[name]
            hrow_test = None
            for keyword in ("ST / SD / OD", "Destinatario", "ST/SD", "Destinatario "):
                hrow_test = _find_header_row(ws_test, keyword, max_scan=15)
                if hrow_test:
                    break
            if not hrow_test:
                continue
            # Encontrar col de fecha para chequear si la hoja es reciente
            fc_test = None
            for c in range(1, ws_test.max_column + 1):
                if "FECHA" in str(ws_test.cell(hrow_test, c).value or "").upper():
                    fc_test = c
                    break
            if not fc_test:
                chosen_sheet = name  # sin fecha, aceptar de todas formas
                break
            max_date = _sheet_max_date(ws_test, fc_test, hrow_test)
            if max_date and max_date >= cutoff_stale:
                chosen_sheet = name
                break  # tomamos la primera hoja reciente y paramos

        if not chosen_sheet:
            chosen_sheet = wb.sheetnames[0]

    # ── Parsear la hoja elegida ──────────────────────────────────────────────
    for sheet_name in [chosen_sheet]:
        ws = wb[sheet_name]

        # Buscar fila de cabecera (contiene "ST" o "Destinatario" o "Producto")
        hrow = None
        for keyword in ("ST / SD / OD", "Destinatario", "ST/SD", "Destinatario "):
            hrow = _find_header_row(ws, keyword, max_scan=15)
            if hrow:
                break
        if hrow is None:
            continue

        # Mapear columnas por nombre
        headers = {}
        for c in range(1, ws.max_column + 1):
            val = str(ws.cell(hrow, c).value or "").strip()
            if val:
                headers[val] = c

        def col(name, aliases=()):
            names_to_try = [name] + list(aliases)
            for n in names_to_try:
                if n in headers:
                    return headers[n]
                # búsqueda parcial
                for h in headers:
                    if n.upper() in h.upper():
                        return headers[h]
            return None

        c_st       = col("ST / SD / OD", ("ST/SD", "S3", "O3"))
        c_dest     = col("Destinatario ", ("Destinatario", "DESTINATARIO"))
        c_prod     = col("Producto", ("PRODUCTO",))
        c_cant     = col("Cantidad 35(MT)", ("Cantidad (MT)", "CANTIDAD"))
        c_orig     = col("Origen", ("ORIGEN",))
        c_trans    = col("Transporte", ("TRANSPORTE",))
        c_fecha    = col("Fecha de carga", ("FECHA ENTREGA", "FECHA DE CARGA", "FECHA"))
        c_ac       = col("AC",)
        c_mezcla   = col("MEZCLA",)
        c_bolsa    = col("BOLSA",)

        for r in range(hrow + 1, ws.max_row + 1):
            # Parar si la fila está totalmente vacía
            sample = [ws.cell(r, c).value for c in range(1, min(ws.max_column + 1, 8))]
            if not any(v is not None for v in sample):
                continue

            fecha = _normalize_date(ws.cell(r, c_fecha).value if c_fecha else None)
            st    = _normalize_str(ws.cell(r, c_st).value if c_st else None)
            prod  = _normalize_str(ws.cell(r, c_prod).value if c_prod else None)
            trans = _normalize_str(ws.cell(r, c_trans).value if c_trans else None)

            if not prod and not st:
                continue

            # ── Filtro de fecha (Nutrien) ────────────────────────────────────
            if fecha:
                if date_from and fecha < date_from:
                    continue
                if date_to and fecha > date_to:
                    continue

            rows.append({
                "source_type":    "nutrien",
                "document_type":  "cupo",
                "source_sheet":   sheet_name,
                "scheduled_date": fecha,
                "st_sd_od":       st,
                "external_ref":   st,
                "destinatario":   _normalize_str(ws.cell(r, c_dest).value if c_dest else None),
                "producto":       prod,
                "cantidad_mt":    _normalize_num(ws.cell(r, c_cant).value if c_cant else None),
                "origen":         _normalize_str(ws.cell(r, c_orig).value if c_orig else None),
                "transporte":     trans,
                "ac":             _normalize_str(ws.cell(r, c_ac).value if c_ac else None),
                "presentacion":   _normalize_str(ws.cell(r, c_bolsa).value if c_bolsa else None),
                "row_hash":       _row_hash("nutrien", fecha, st, prod, trans),
            })

    return rows


# ─── Parser CNA ───────────────────────────────────────────────────────────────

def _parse_cna(
    wb: openpyxl.Workbook,
    date_from: Optional[date] = None,
    date_to:   Optional[date] = None,
) -> list[dict]:
    """
    Parsea la hoja 'Despachos' del Excel de CNA.
    Header en fila 1 con columnas fijas.
    """
    if "Despachos" not in wb.sheetnames:
        return []

    ws = wb["Despachos"]

    # Mapear columnas de la fila 1
    headers = {}
    for c in range(1, ws.max_column + 1):
        val = str(ws.cell(1, c).value or "").strip()
        if val:
            headers[val] = c

    def col(name):
        if name in headers:
            return headers[name]
        for h in headers:
            if name.upper() in h.upper():
                return headers[h]
        return None

    c_fecha   = col("Fecha")
    c_inout   = col("IN/OUT")
    c_client  = col("Cliente")
    c_cuit_c  = col("Cuit Cliente")
    c_np      = col("NP o FC")
    c_oc      = col("OC")
    c_prod    = col("Producto")
    c_kg_oc   = col("KG. OC")
    c_pres    = col("Presentacion")
    c_dest    = col("Destino")
    c_trans   = col("Transporte")
    c_cuit_t  = col("Cuit transporte")
    c_chofer  = col("Chofer")
    c_dni     = col("Dni chofer")
    c_chasis  = col("Pat. Chasis")
    c_acop    = col("Pat. Acoplado")
    c_obs     = col("Observaciones")
    c_neto    = col("Neto")
    c_remito  = col("Remito")

    rows = []
    for r in range(2, ws.max_row + 1):
        fecha = _normalize_date(ws.cell(r, c_fecha).value if c_fecha else None)
        prod  = _normalize_str(ws.cell(r, c_prod).value if c_prod else None)
        if not fecha and not prod:
            continue

        # ── Filtro de fecha (CNA) ────────────────────────────────────────────
        if fecha:
            if date_from and fecha < date_from:
                continue
            if date_to and fecha > date_to:
                continue

        np_fc  = _normalize_str(ws.cell(r, c_np).value if c_np else None)
        trans  = _normalize_str(ws.cell(r, c_trans).value if c_trans else None)
        chasis = _normalize_str(ws.cell(r, c_chasis).value if c_chasis else None)

        # DNI: puede venir como float (Excel trata números grandes como float)
        dni_raw = ws.cell(r, c_dni).value if c_dni else None
        if isinstance(dni_raw, float):
            dni_raw = str(int(dni_raw))
        dni = _normalize_str(dni_raw)

        # CUIT cliente: similar problema
        cuit_c_raw = ws.cell(r, c_cuit_c).value if c_cuit_c else None
        if isinstance(cuit_c_raw, float):
            cuit_c_raw = str(int(cuit_c_raw))
        cuit_c = _normalize_str(cuit_c_raw)

        rows.append({
            "source_type":    "cna",
            "document_type":  "despacho",
            "source_sheet":   "Despachos",
            "scheduled_date": fecha,
            "actual_date":    fecha,   # CNA registra hechos consumados
            "in_out":         _normalize_str(ws.cell(r, c_inout).value if c_inout else None),
            "cliente":        _normalize_str(ws.cell(r, c_client).value if c_client else None),
            "cuit_cliente":   cuit_c,
            "external_ref":   np_fc,
            "order_number":   _normalize_str(ws.cell(r, c_oc).value if c_oc else None),
            "producto":       prod,
            "kg_oc":          _normalize_num(ws.cell(r, c_kg_oc).value if c_kg_oc else None),
            "presentacion":   _normalize_str(ws.cell(r, c_pres).value if c_pres else None),
            "destino":        _normalize_str(ws.cell(r, c_dest).value if c_dest else None),
            "transporte":     trans,
            "cuit_transporte": _normalize_str(ws.cell(r, c_cuit_t).value if c_cuit_t else None),
            "chofer":         _normalize_str(ws.cell(r, c_chofer).value if c_chofer else None),
            "dni_chofer":     dni,
            "patente_chasis":  chasis,
            "patente_acoplado": _normalize_str(ws.cell(r, c_acop).value if c_acop else None),
            "notes":          _normalize_str(ws.cell(r, c_obs).value if c_obs else None),
            "neto":           _normalize_num(ws.cell(r, c_neto).value if c_neto else None),
            "remito":         _normalize_str(ws.cell(r, c_remito).value if c_remito else None),
            "row_hash":       _row_hash("cna", fecha, np_fc, prod, trans, chasis),
            # CNA = registro consumado → estado cargado por defecto
            "status":         "cargado",
        })

    return rows


def _parse_excel(
    content:   bytes,
    filename:  str,
    date_from: Optional[date] = None,
    date_to:   Optional[date] = None,
) -> tuple[str, list[dict], list[str]]:
    """
    Lee el Excel, detecta la fuente y parsea las filas.
    date_from / date_to: si se pasan, solo se incluyen filas dentro del rango.
    Retorna (source_type, rows, warnings).
    """
    warnings = []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as e:
        return "unknown", [], [f"Error al abrir el archivo: {e}"]

    source = _detect_source(wb)
    if source == "nutrien":
        rows = _parse_nutrien(wb, date_from=date_from, date_to=date_to)
    else:
        rows = _parse_cna(wb, date_from=date_from, date_to=date_to)

    if not rows:
        msg = "No se encontraron filas con datos válidos en el archivo."
        if date_from or date_to:
            msg += f" Revisá el rango de fechas ({date_from or '—'} → {date_to or '—'})."
        warnings.append(msg)

    return source, rows, warnings


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de KPIs
# ══════════════════════════════════════════════════════════════════════════════

def _kpis(registros: list) -> dict:
    from collections import Counter
    counter = Counter(r.status for r in registros)
    ton_prog = sum(
        float(r.cantidad_mt or 0) + float(r.kg_oc or 0) / 1000
        for r in registros
    )
    ton_real = sum(
        float(r.neto or 0) / 1000
        for r in registros if r.status == "cargado"
    )
    return {
        "total":         len(registros),
        "programado":    counter.get("programado", 0),
        "arribo":        counter.get("arribo", 0),
        "cargado":       counter.get("cargado", 0),
        "no_vino":       counter.get("no_vino", 0),
        "reprogramado":  counter.get("reprogramado", 0),
        "cancelado":     counter.get("cancelado", 0),
        "ton_programadas": round(ton_prog, 1),
        "ton_operadas":    round(ton_real, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Vista 1 — Listado principal
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_class=HTMLResponse)
async def list_despachos(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_ROLES)),
    fecha_desde: str = "",
    fecha_hasta: str = "",
    cliente:     str = "",
    producto:    str = "",
    transporte:  str = "",
    status:      str = "",
    source_type: str = "",
    q:           str = "",
):
    query = db.query(CupoDespacho)

    if fecha_desde:
        try:
            query = query.filter(CupoDespacho.scheduled_date >= date.fromisoformat(fecha_desde))
        except ValueError:
            pass
    if fecha_hasta:
        try:
            query = query.filter(CupoDespacho.scheduled_date <= date.fromisoformat(fecha_hasta))
        except ValueError:
            pass
    if cliente:
        query = query.filter(
            (CupoDespacho.cliente.ilike(f"%{cliente}%")) |
            (CupoDespacho.destinatario.ilike(f"%{cliente}%"))
        )
    if producto:
        query = query.filter(CupoDespacho.producto.ilike(f"%{producto}%"))
    if transporte:
        query = query.filter(CupoDespacho.transporte.ilike(f"%{transporte}%"))
    if status:
        query = query.filter(CupoDespacho.status == status)
    if source_type:
        query = query.filter(CupoDespacho.source_type == source_type)
    if q:
        query = query.filter(
            (CupoDespacho.cliente.ilike(f"%{q}%")) |
            (CupoDespacho.destinatario.ilike(f"%{q}%")) |
            (CupoDespacho.producto.ilike(f"%{q}%")) |
            (CupoDespacho.transporte.ilike(f"%{q}%")) |
            (CupoDespacho.patente_chasis.ilike(f"%{q}%")) |
            (CupoDespacho.chofer.ilike(f"%{q}%")) |
            (CupoDespacho.remito.ilike(f"%{q}%")) |
            (CupoDespacho.st_sd_od.ilike(f"%{q}%"))
        )

    registros = query.order_by(
        CupoDespacho.scheduled_date.desc(),
        CupoDespacho.id.desc()
    ).limit(500).all()

    kpis = _kpis(registros)

    return templates.TemplateResponse(
        request,
        "despachos/list.html",
        {
            "current_user":  current_user,
            "registros":     registros,
            "kpis":          kpis,
            "estados":       DESPACHO_ESTADOS,
            "estado_labels": DESPACHO_ESTADO_LABELS,
            "estado_css":    ESTADO_CSS,
            # filtros activos
            "f_fecha_desde": fecha_desde,
            "f_fecha_hasta": fecha_hasta,
            "f_cliente":     cliente,
            "f_producto":    producto,
            "f_transporte":  transporte,
            "f_status":      status,
            "f_source":      source_type,
            "f_q":           q,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# Vista 2 — Importador: upload
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/import", response_class=HTMLResponse)
async def import_form(
    request: Request,
    current_user=Depends(require_role(*_ROLES)),
):
    return templates.TemplateResponse(
        request,
        "despachos/import.html",
        {"current_user": current_user, "preview": None, "error": None},
    )


@router.post("/import", response_class=HTMLResponse)
async def import_preview(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_ROLES)),
    file: UploadFile = File(...),
    source_override: str = Form("auto"),    # 'auto' | 'nutrien' | 'cna'
    import_fecha_desde: str = Form(""),     # filtro de fecha en importación
    import_fecha_hasta: str = Form(""),
):
    content = await file.read()

    # Parsear fechas de filtro
    df = None
    dt = None
    try:
        if import_fecha_desde:
            df = date.fromisoformat(import_fecha_desde)
    except ValueError:
        pass
    try:
        if import_fecha_hasta:
            dt = date.fromisoformat(import_fecha_hasta)
    except ValueError:
        pass

    source, rows, warnings = _parse_excel(content, file.filename, date_from=df, date_to=dt)

    if source_override != "auto":
        source = source_override

    if not rows:
        return templates.TemplateResponse(
            request,
            "despachos/import.html",
            {
                "current_user": current_user,
                "preview":      None,
                "error":        "No se encontraron datos en el archivo. " + " ".join(warnings),
            },
        )

    # Verificar duplicados existentes
    existing_hashes = {
        h for (h,) in db.query(CupoDespacho.row_hash).filter(
            CupoDespacho.row_hash.in_([r["row_hash"] for r in rows if r.get("row_hash")])
        ).all()
    }

    for r in rows:
        r["_duplicate"] = r.get("row_hash") in existing_hashes

    dup_count = sum(1 for r in rows if r["_duplicate"])
    new_count = len(rows) - dup_count

    # Guardar en sesión HTTP para el confirm
    import json
    # Serializar filas para la sesión (convertir date a str)
    def serial(obj):
        if isinstance(obj, date):
            return obj.isoformat()
        return obj

    rows_serializable = [
        {k: serial(v) for k, v in row.items()}
        for row in rows
    ]

    # Usar un campo hidden en el form para pasar el preview (max 200 filas para preview)
    preview_rows = rows[:200]

    return templates.TemplateResponse(
        request,
        "despachos/import.html",
        {
            "current_user":       current_user,
            "preview":            preview_rows,
            "all_rows_json":      json.dumps(rows_serializable),
            "source":             source,
            "filename":           file.filename,
            "total_rows":         len(rows),
            "dup_count":          dup_count,
            "new_count":          new_count,
            "warnings":           warnings,
            "error":              None,
            "estado_css":         ESTADO_CSS,
            "estado_labels":      DESPACHO_ESTADO_LABELS,
            "import_fecha_desde": import_fecha_desde,
            "import_fecha_hasta": import_fecha_hasta,
        },
    )


@router.post("/import/confirm")
async def import_confirm(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_ROLES)),
):
    form = await request.form()
    import json

    rows_json  = form.get("rows_json", "[]")
    source     = str(form.get("source", "unknown"))
    filename   = str(form.get("filename", ""))
    skip_dups  = form.get("skip_duplicates", "1") == "1"

    try:
        rows = json.loads(rows_json)
    except Exception:
        raise HTTPException(400, "Datos de importación inválidos.")

    if not rows:
        raise HTTPException(400, "No hay filas para importar.")

    # Crear batch
    batch_uuid = str(uuid.uuid4())
    batch = ImportBatch(
        batch_uuid=batch_uuid,
        source_type=source,
        filename=filename,
        sheet_name=rows[0].get("source_sheet", ""),
        imported_by=current_user.name,
    )
    db.add(batch)
    db.flush()

    # Verificar hashes existentes
    all_hashes = [r.get("row_hash") for r in rows if r.get("row_hash")]
    existing = {
        h for (h,) in db.query(CupoDespacho.row_hash).filter(
            CupoDespacho.row_hash.in_(all_hashes)
        ).all()
    } if all_hashes else set()

    inserted = 0
    skipped  = 0

    for r in rows:
        h = r.get("row_hash")
        if skip_dups and h and h in existing:
            skipped += 1
            continue

        def to_date(v):
            if not v:
                return None
            if isinstance(v, str):
                try:
                    return date.fromisoformat(v)
                except ValueError:
                    return None
            return v

        cd = CupoDespacho(
            batch_id         = batch.id,
            source_type      = r.get("source_type", source),
            document_type    = r.get("document_type", "cupo"),
            row_hash         = h,
            scheduled_date   = to_date(r.get("scheduled_date")),
            actual_date      = to_date(r.get("actual_date")),
            st_sd_od         = r.get("st_sd_od"),
            external_ref     = r.get("external_ref"),
            order_number     = r.get("order_number"),
            remito           = r.get("remito"),
            cliente          = r.get("cliente"),
            cuit_cliente     = r.get("cuit_cliente"),
            destinatario     = r.get("destinatario"),
            destino          = r.get("destino"),
            ac               = r.get("ac"),
            producto         = r.get("producto"),
            cantidad_mt      = r.get("cantidad_mt"),
            kg_oc            = r.get("kg_oc"),
            neto             = r.get("neto"),
            presentacion     = r.get("presentacion"),
            origen           = r.get("origen"),
            in_out           = r.get("in_out"),
            modo_transporte  = r.get("modo_transporte"),
            transporte       = r.get("transporte"),
            cuit_transporte  = r.get("cuit_transporte"),
            chofer           = r.get("chofer"),
            dni_chofer       = r.get("dni_chofer"),
            patente_chasis   = r.get("patente_chasis"),
            patente_acoplado = r.get("patente_acoplado"),
            notes            = r.get("notes"),
            status           = r.get("status", "programado"),
            imported_by      = current_user.name,
        )
        db.add(cd)
        inserted += 1

    batch.row_count = inserted
    db.commit()

    return RedirectResponse(
        url=f"/despachos?import_ok=1&inserted={inserted}&skipped={skipped}",
        status_code=303,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Vista 3 — Detalle / Edición
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{rid}", response_class=HTMLResponse)
async def detail(
    request: Request,
    rid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_ROLES)),
):
    reg = db.query(CupoDespacho).filter_by(id=rid).first()
    if not reg:
        raise HTTPException(404, "Registro no encontrado")

    return templates.TemplateResponse(
        request,
        "despachos/detail.html",
        {
            "current_user":  current_user,
            "reg":           reg,
            "estados":       DESPACHO_ESTADOS,
            "estado_labels": DESPACHO_ESTADO_LABELS,
            "estado_css":    ESTADO_CSS,
        },
    )


@router.post("/{rid}/status")
async def change_status(
    request: Request,
    rid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_ROLES)),
):
    reg = db.query(CupoDespacho).filter_by(id=rid).first()
    if not reg:
        raise HTTPException(404, "Registro no encontrado")

    form       = await request.form()
    new_status = str(form.get("status", "")).strip()
    notes      = str(form.get("notes", "")).strip()

    if new_status not in DESPACHO_ESTADO_LABELS:
        raise HTTPException(400, f"Estado inválido: {new_status}")

    reg.status = new_status
    if notes:
        reg.notes = notes
    reg.updated_at = datetime.utcnow()
    db.commit()

    # Redirect back
    back = str(form.get("back", "list"))
    if back == "detail":
        return RedirectResponse(url=f"/despachos/{rid}", status_code=303)
    return RedirectResponse(url="/despachos", status_code=303)


@router.post("/{rid}/edit")
async def edit_registro(
    request: Request,
    rid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_ROLES)),
):
    reg = db.query(CupoDespacho).filter_by(id=rid).first()
    if not reg:
        raise HTTPException(404, "Registro no encontrado")

    form = await request.form()

    def fget(name):
        v = form.get(name, "")
        return str(v).strip() or None

    def dget(name):
        v = fget(name)
        if not v:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None

    def nget(name):
        v = fget(name)
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    reg.actual_date      = dget("actual_date") or reg.actual_date
    reg.cliente          = fget("cliente")      or reg.cliente
    reg.destinatario     = fget("destinatario") or reg.destinatario
    reg.destino          = fget("destino")      or reg.destino
    reg.producto         = fget("producto")     or reg.producto
    reg.cantidad_mt      = nget("cantidad_mt")  or reg.cantidad_mt
    reg.neto             = nget("neto")         or reg.neto
    reg.presentacion     = fget("presentacion") or reg.presentacion
    reg.transporte       = fget("transporte")   or reg.transporte
    reg.chofer           = fget("chofer")       or reg.chofer
    reg.dni_chofer       = fget("dni_chofer")   or reg.dni_chofer
    reg.patente_chasis   = fget("patente_chasis")  or reg.patente_chasis
    reg.patente_acoplado = fget("patente_acoplado") or reg.patente_acoplado
    reg.remito           = fget("remito")       or reg.remito
    reg.notes            = fget("notes")        # puede ser None para borrar

    new_status = fget("status")
    if new_status and new_status in DESPACHO_ESTADO_LABELS:
        reg.status = new_status

    reg.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=f"/despachos/{rid}", status_code=303)


@router.post("/{rid}/reprogram")
async def reprogram(
    request: Request,
    rid: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(*_ROLES)),
):
    reg = db.query(CupoDespacho).filter_by(id=rid).first()
    if not reg:
        raise HTTPException(404, "Registro no encontrado")

    form        = await request.form()
    nueva_fecha = str(form.get("nueva_fecha", "")).strip()
    notes       = str(form.get("notes", "")).strip()

    try:
        nf = date.fromisoformat(nueva_fecha)
    except ValueError:
        raise HTTPException(400, "Fecha inválida")

    # Actualizar registro original
    reg.status               = "reprogramado"
    reg.reprogrammed_to_date = nf
    if notes:
        reg.notes = (reg.notes or "") + f"\n[Reprogramado a {nf}: {notes}]".strip()
    reg.updated_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(url=f"/despachos/{rid}", status_code=303)
