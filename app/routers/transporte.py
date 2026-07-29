from __future__ import annotations
import io
import os
from datetime import datetime, date
from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from app.database import get_db
from app.deps import get_current_user
from app.permissions import require_path_perm
from app import models_transporte as mt
from app.templates import templates

router = APIRouter(prefix="/transporte", dependencies=[Depends(require_path_perm())])


def _require_access(current_user=Depends(get_current_user)):
    if current_user.role == "operador":
        raise HTTPException(status_code=403, detail="Sin acceso al módulo de Transporte.")
    return current_user


def _parse_date(s: str):
    if not s or not s.strip():
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


# ── NÓMINA MADRE ───────────────────────────────────────────────────────────────

@router.get("/nomina", response_class=HTMLResponse)
async def nomina_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    q = request.query_params.get("q", "").strip()

    query = db.query(mt.TransporteNomina).filter(mt.TransporteNomina.deleted_at == None)

    if q:
        query = query.filter(
            or_(
                mt.TransporteNomina.empresa.ilike(f"%{q}%"),
                mt.TransporteNomina.nombre_chofer.ilike(f"%{q}%"),
                mt.TransporteNomina.dni.ilike(f"%{q}%"),
                mt.TransporteNomina.patente_camion.ilike(f"%{q}%"),
                mt.TransporteNomina.patente_acoplado.ilike(f"%{q}%"),
            )
        )

    nomina = (
        query
        .order_by(
            mt.TransporteNomina.nombre_chofer.asc(),
            mt.TransporteNomina.empresa.asc(),
        )
        .all()
    )

    return templates.TemplateResponse(request, "transporte/nomina_list.html", {
        "request": request,
        "user": current_user,
        "nomina": nomina,
        "q": q,
    })


@router.get("/nomina/new", response_class=HTMLResponse)
async def nomina_new_form(
    request: Request,
    current_user=Depends(_require_access),
):
    return templates.TemplateResponse(request, "transporte/nomina_form.html", {
        "request": request,
        "user": current_user,
        "item": None,
        "errors": [],
    })


@router.post("/nomina/new", response_class=HTMLResponse)
async def nomina_create(
    request: Request,
    empresa: str          = Form(""),
    nombre_chofer: str    = Form(""),
    dni: str              = Form(""),
    marca_camion: str     = Form(""),
    patente_camion: str   = Form(""),
    patente_acoplado: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    errors = []
    if not empresa.strip():
        errors.append("La empresa es requerida.")
    if not nombre_chofer.strip():
        errors.append("El nombre del chofer es requerido.")

    if errors:
        return templates.TemplateResponse(request, "transporte/nomina_form.html", {
            "request": request,
            "user": current_user,
            "item": None,
            "errors": errors,
            "form": {
                "empresa": empresa,
                "nombre_chofer": nombre_chofer,
                "dni": dni,
                "marca_camion": marca_camion,
                "patente_camion": patente_camion,
                "patente_acoplado": patente_acoplado,
            },
        }, status_code=422)

    item = mt.TransporteNomina(
        empresa          = empresa.strip(),
        nombre_chofer    = nombre_chofer.strip(),
        dni              = dni.strip() or None,
        marca_camion     = marca_camion.strip() or None,
        patente_camion   = patente_camion.strip().upper() or None,
        patente_acoplado = patente_acoplado.strip().upper() or None,
    )
    db.add(item)
    db.commit()
    return RedirectResponse("/transporte/nomina", status_code=303)


@router.get("/nomina/{item_id}/edit", response_class=HTMLResponse)
async def nomina_edit_form(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    item = db.query(mt.TransporteNomina).filter(
        mt.TransporteNomina.id == item_id,
        mt.TransporteNomina.deleted_at == None,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")

    return templates.TemplateResponse(request, "transporte/nomina_form.html", {
        "request": request,
        "user": current_user,
        "item": item,
        "errors": [],
    })


@router.post("/nomina/{item_id}/edit", response_class=HTMLResponse)
async def nomina_update(
    item_id: int,
    request: Request,
    empresa: str          = Form(""),
    nombre_chofer: str    = Form(""),
    dni: str              = Form(""),
    marca_camion: str     = Form(""),
    patente_camion: str   = Form(""),
    patente_acoplado: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    item = db.query(mt.TransporteNomina).filter(
        mt.TransporteNomina.id == item_id,
        mt.TransporteNomina.deleted_at == None,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")

    errors = []
    if not empresa.strip():
        errors.append("La empresa es requerida.")
    if not nombre_chofer.strip():
        errors.append("El nombre del chofer es requerido.")

    if errors:
        return templates.TemplateResponse(request, "transporte/nomina_form.html", {
            "request": request,
            "user": current_user,
            "item": item,
            "errors": errors,
            "form": {
                "empresa": empresa,
                "nombre_chofer": nombre_chofer,
                "dni": dni,
                "marca_camion": marca_camion,
                "patente_camion": patente_camion,
                "patente_acoplado": patente_acoplado,
            },
        }, status_code=422)

    item.empresa          = empresa.strip()
    item.nombre_chofer    = nombre_chofer.strip()
    item.dni              = dni.strip() or None
    item.marca_camion     = marca_camion.strip() or None
    item.patente_camion   = patente_camion.strip().upper() or None
    item.patente_acoplado = patente_acoplado.strip().upper() or None
    item.updated_at       = datetime.utcnow()
    db.commit()
    return RedirectResponse("/transporte/nomina", status_code=303)


@router.post("/nomina/{item_id}/delete")
async def nomina_delete(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Solo administradores pueden eliminar registros.")

    item = db.query(mt.TransporteNomina).filter(
        mt.TransporteNomina.id == item_id,
        mt.TransporteNomina.deleted_at == None,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")

    item.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/transporte/nomina", status_code=303)


# ── IMPORTAR NÓMINA DESDE WORD ────────────────────────────────────────────────
#
# Permite subir un .docx con la nómina actualizada. El sistema lee la tabla,
# la compara contra la nómina vigente y muestra un resumen (altas / modificaciones
# / bajas) que el usuario confirma antes de aplicar.
#
# Identidad de la persona: DNI (si existe) y, en su defecto, nombre del chofer.
# Los datos del camión (marca, patente camión, patente acoplado) se tratan como
# actualizables, ya que son justamente los que suelen cambiar. Para un chofer con
# varios camiones, las filas se emparejan por similitud dentro del mismo chofer.

import unicodedata as _unicodedata

# Campos que se comparan / actualizan de cada registro de nómina
_NOMINA_FIELDS = (
    "empresa",
    "nombre_chofer",
    "dni",
    "marca_camion",
    "patente_camion",
    "patente_acoplado",
)

# Etiquetas legibles para el resumen
_NOMINA_LABELS = {
    "empresa":          "Empresa",
    "nombre_chofer":    "Chofer",
    "dni":              "DNI",
    "marca_camion":     "Marca camión",
    "patente_camion":   "Patente camión",
    "patente_acoplado": "Patente acoplado",
}


def _norm(s):
    """Normaliza un texto para comparar: sin acentos, minúsculas, sin espacios extra."""
    if s is None:
        return ""
    s = str(s).strip()
    s = _unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not _unicodedata.combining(c))
    return " ".join(s.lower().split())


def _clean_cell(s, *, upper=False):
    """Limpia el valor de una celda del Word."""
    if s is None:
        return None
    s = str(s).strip()
    if s in ("", "—", "-", "–", "N/A", "n/a", "s/d", "S/D"):
        return None
    return s.upper() if upper else s


def _digits(s):
    return "".join(c for c in (s or "") if c.isdigit())


def _match_header(cells):
    """Dado el texto de las celdas de una fila, intenta mapear columna->campo.
    Devuelve un dict {campo: índice} o {} si no parece un encabezado de nómina.

    Nota: puede haber dos columnas llamadas simplemente "Patente" (una del camión
    y otra del acoplado, sin aclararlo). En ese caso se resuelven por posición:
    la primera es la del camión y la segunda la del acoplado."""
    mapping = {}
    patente_ambiguas = []  # índices de columnas "Patente" sin contexto, en orden
    for idx, raw in enumerate(cells):
        h = _norm(raw)
        if not h:
            continue
        es_patente = ("patente" in h or "chapa" in h or "dominio" in h)
        if es_patente and ("acoplado" in h or "trailer" in h or "semi" in h or "batea" in h):
            mapping["patente_acoplado"] = idx
        elif es_patente and ("camion" in h or "chasis" in h or "tractor" in h):
            mapping["patente_camion"] = idx
        elif es_patente:
            # "Patente" a secas -> se resuelve después por posición
            patente_ambiguas.append(idx)
        elif "empresa" in h:
            mapping.setdefault("empresa", idx)
        elif ("dni" in h or "documento" in h) and "dni" not in mapping:
            mapping["dni"] = idx
        elif ("apellido" in h or "chofer" in h or "conductor" in h
              or "nombre y apellido" in h or h == "nombre") and "nombre_chofer" not in mapping:
            mapping["nombre_chofer"] = idx
        elif ("acoplado" in h or "semi" in h or "batea" in h) and "patente_acoplado" not in mapping:
            mapping["patente_acoplado"] = idx
        elif ("marca" in h or h == "camion" or "vehiculo" in h) and "marca_camion" not in mapping:
            mapping["marca_camion"] = idx

    # Resolver columnas "Patente" ambiguas por orden de aparición:
    # la primera libre va al camión, la siguiente al acoplado.
    for idx in patente_ambiguas:
        if "patente_camion" not in mapping:
            mapping["patente_camion"] = idx
        elif "patente_acoplado" not in mapping:
            mapping["patente_acoplado"] = idx
    return mapping


def _logical_cells(row):
    """Devuelve el texto de las celdas de una fila colapsando las celdas
    combinadas (merged). Word repite la misma celda por cada columna de grilla
    que ocupa; acá cada celda combinada aparece una sola vez."""
    out, seen = [], set()
    for cell in row.cells:
        key = id(cell._tc)
        if key in seen:
            continue
        seen.add(key)
        out.append(cell.text)
    return out


def _parse_nomina_docx(content: bytes):
    """Lee un .docx y devuelve (filas, warnings).
    Cada fila es un dict con los campos de _NOMINA_FIELDS."""
    from docx import Document

    warnings = []
    try:
        doc = Document(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo abrir el Word: {exc}")

    best_table = None
    best_mapping = {}
    best_header_idx = 0

    for table in doc.tables:
        for r_idx, row in enumerate(table.rows[:3]):  # el encabezado suele estar en las primeras filas
            cells = _logical_cells(row)
            mapping = _match_header(cells)
            # Consideramos válido si al menos identifica chofer y (empresa o alguna patente)
            has_person = "nombre_chofer" in mapping
            has_extra = any(k in mapping for k in ("empresa", "patente_camion", "dni", "marca_camion"))
            if has_person and has_extra and len(mapping) > len(best_mapping):
                best_table = table
                best_mapping = mapping
                best_header_idx = r_idx
        # No cortamos: nos quedamos con la tabla de encabezado más completo

    if best_table is None:
        raise HTTPException(
            status_code=400,
            detail=("No se encontró una tabla de nómina en el Word. "
                    "Debe tener una fila de encabezado con columnas como "
                    "Empresa, Chofer, DNI, Camión, Patente camión, Patente acoplado."),
        )

    # La columna de empresa suele no tener encabezado. Si no se detectó,
    # tomamos la primera columna lógica libre (normalmente la primera).
    if "empresa" not in best_mapping:
        header_cells = _logical_cells(best_table.rows[best_header_idx])
        usados = set(best_mapping.values())
        for i in range(len(header_cells)):
            if i not in usados:
                best_mapping["empresa"] = i
                break

    rows = []
    for row in best_table.rows[best_header_idx + 1:]:
        cells = _logical_cells(row)

        def get(field, *, upper=False):
            i = best_mapping.get(field)
            if i is None or i >= len(cells):
                return None
            return _clean_cell(cells[i], upper=upper)

        empresa = get("empresa")
        chofer  = get("nombre_chofer")
        # Fila vacía o de subtotal -> ignorar
        if not chofer and not empresa:
            continue
        if not chofer:
            warnings.append(f"Fila ignorada sin chofer (empresa: {empresa or '—'}).")
            continue

        rows.append({
            "empresa":          empresa or "",
            "nombre_chofer":    chofer,
            "dni":              _digits(get("dni")) or None,
            "marca_camion":     get("marca_camion"),
            "patente_camion":   get("patente_camion", upper=True),
            "patente_acoplado": get("patente_acoplado", upper=True),
        })

    if not rows:
        raise HTTPException(status_code=400, detail="La tabla del Word no tiene filas de datos.")

    return rows, warnings


def _person_key(dni, nombre):
    d = _digits(dni)
    if d:
        return ("dni", d)
    return ("nombre", _norm(nombre))


def _fields_equal(a, b):
    return _norm(a) == _norm(b)


def _similarity(item, row):
    """Cuántos campos de camión coinciden entre un registro existente y una fila del Word."""
    score = 0
    for f in ("empresa", "marca_camion", "patente_camion", "patente_acoplado"):
        av = getattr(item, f, None)
        if _fields_equal(av, row.get(f)):
            score += 1
    return score


def _build_nomina_plan(rows, current_items):
    """Compara las filas parseadas contra la nómina vigente.
    Devuelve dict con altas, modificaciones, bajas y sin_cambios."""
    # Agrupar por persona
    current_by_key = {}
    for it in current_items:
        current_by_key.setdefault(_person_key(it.dni, it.nombre_chofer), []).append(it)

    rows_by_key = {}
    for r in rows:
        rows_by_key.setdefault(_person_key(r.get("dni"), r.get("nombre_chofer")), []).append(r)

    altas = []          # dicts a insertar
    modificaciones = []  # {id, before{}, after{}, cambios[campos]}
    sin_cambios = 0
    matched_item_ids = set()

    for key, group_rows in rows_by_key.items():
        old_group = list(current_by_key.get(key, []))
        # Emparejamiento greedy por similitud
        for row in group_rows:
            best_it = None
            best_score = -1
            for it in old_group:
                if it.id in matched_item_ids:
                    continue
                sc = _similarity(it, row)
                if sc > best_score:
                    best_score = sc
                    best_it = it
            if best_it is not None:
                matched_item_ids.add(best_it.id)
                cambios = []
                for f in _NOMINA_FIELDS:
                    if not _fields_equal(getattr(best_it, f, None), row.get(f)):
                        cambios.append(f)
                if cambios:
                    modificaciones.append({
                        "id": best_it.id,
                        "before": {f: getattr(best_it, f, None) for f in _NOMINA_FIELDS},
                        "after":  {f: row.get(f) for f in _NOMINA_FIELDS},
                        "cambios": cambios,
                    })
                else:
                    sin_cambios += 1
            else:
                altas.append({f: row.get(f) for f in _NOMINA_FIELDS})

    # Bajas: registros vigentes que no quedaron emparejados con ninguna fila del Word
    bajas = [
        {"id": it.id, "data": {f: getattr(it, f, None) for f in _NOMINA_FIELDS}}
        for it in current_items
        if it.id not in matched_item_ids
    ]

    return {
        "altas":          altas,
        "modificaciones": modificaciones,
        "bajas":          bajas,
        "sin_cambios":    sin_cambios,
    }


@router.get("/nomina/importar", response_class=HTMLResponse)
async def nomina_importar_form(
    request: Request,
    current_user=Depends(_require_access),
):
    return templates.TemplateResponse(request, "transporte/nomina_import.html", {
        "request": request,
        "user": current_user,
        "plan": None,
        "error": None,
        "warnings": [],
        "labels": _NOMINA_LABELS,
    })


@router.post("/nomina/importar", response_class=HTMLResponse)
async def nomina_importar_preview(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    filename = file.filename or ""
    if not filename.lower().endswith(".docx"):
        return templates.TemplateResponse(request, "transporte/nomina_import.html", {
            "request": request,
            "user": current_user,
            "plan": None,
            "error": "El archivo debe ser un Word (.docx).",
            "warnings": [],
            "labels": _NOMINA_LABELS,
        }, status_code=422)

    content = await file.read()
    rows, warnings = _parse_nomina_docx(content)

    current_items = (
        db.query(mt.TransporteNomina)
        .filter(mt.TransporteNomina.deleted_at == None)
        .all()
    )

    plan = _build_nomina_plan(rows, current_items)

    import json
    return templates.TemplateResponse(request, "transporte/nomina_import.html", {
        "request": request,
        "user": current_user,
        "plan": plan,
        "plan_json": json.dumps({
            "altas":          plan["altas"],
            "modificaciones": [{"id": m["id"], "after": m["after"]} for m in plan["modificaciones"]],
            "bajas":          [b["id"] for b in plan["bajas"]],
        }),
        "filename": filename,
        "total_word": len(rows),
        "warnings": warnings,
        "error": None,
        "labels": _NOMINA_LABELS,
    })


@router.post("/nomina/importar/confirmar")
async def nomina_importar_confirmar(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    import json
    form = await request.form()
    try:
        plan = json.loads(form.get("plan_json", "{}"))
    except Exception:
        raise HTTPException(status_code=400, detail="Datos de importación inválidos.")

    aplicar_bajas = form.get("aplicar_bajas", "1") == "1"

    def _norm_field(field, value):
        if value in ("", None):
            return None
        if field in ("patente_camion", "patente_acoplado"):
            return str(value).strip().upper() or None
        if field == "dni":
            return _digits(value) or None
        return str(value).strip()

    creadas = 0
    modificadas = 0
    dadas_baja = 0

    # Altas
    for a in plan.get("altas", []):
        empresa = _norm_field("empresa", a.get("empresa")) or ""
        chofer  = _norm_field("nombre_chofer", a.get("nombre_chofer"))
        if not chofer:
            continue
        db.add(mt.TransporteNomina(
            empresa          = empresa,
            nombre_chofer    = chofer,
            dni              = _norm_field("dni", a.get("dni")),
            marca_camion     = _norm_field("marca_camion", a.get("marca_camion")),
            patente_camion   = _norm_field("patente_camion", a.get("patente_camion")),
            patente_acoplado = _norm_field("patente_acoplado", a.get("patente_acoplado")),
        ))
        creadas += 1

    # Modificaciones
    for m in plan.get("modificaciones", []):
        it = db.query(mt.TransporteNomina).filter(
            mt.TransporteNomina.id == m.get("id"),
            mt.TransporteNomina.deleted_at == None,
        ).first()
        if not it:
            continue
        after = m.get("after", {})
        for f in _NOMINA_FIELDS:
            nv = _norm_field(f, after.get(f))
            # empresa y nombre_chofer son obligatorios: si el Word los trae
            # vacíos no se pisan (se conserva el valor actual).
            if f in ("empresa", "nombre_chofer") and not nv:
                continue
            setattr(it, f, nv)
        modificadas += 1

    # Bajas
    if aplicar_bajas:
        for bid in plan.get("bajas", []):
            it = db.query(mt.TransporteNomina).filter(
                mt.TransporteNomina.id == bid,
                mt.TransporteNomina.deleted_at == None,
            ).first()
            if it:
                it.deleted_at = datetime.utcnow()
                dadas_baja += 1

    db.commit()
    return RedirectResponse(
        f"/transporte/nomina?import_ok=1&creadas={creadas}&modificadas={modificadas}&bajas={dadas_baja}",
        status_code=303,
    )


# ── HISTORIAL ─────────────────────────────────────────────────────────────────

@router.get("/historial", response_class=HTMLResponse)
async def historial_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    operativos = (
        db.query(mt.TransporteOperativo)
        .filter(mt.TransporteOperativo.deleted_at == None)
        .order_by(mt.TransporteOperativo.fecha_inicio.desc().nullslast(),
                  mt.TransporteOperativo.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(request, "transporte/historial_list.html", {
        "request": request,
        "user": current_user,
        "operativos": operativos,
    })


@router.get("/historial/new", response_class=HTMLResponse)
async def historial_new_form(
    request: Request,
    current_user=Depends(_require_access),
):
    return templates.TemplateResponse(request, "transporte/historial_new.html", {
        "request": request,
        "user": current_user,
        "errors": [],
    })


@router.post("/historial/new", response_class=HTMLResponse)
async def historial_create(
    request: Request,
    nombre_barco: str = Form(""),
    producto: str     = Form(""),
    cliente: str      = Form(""),
    deposito: str     = Form(""),
    mercaderia_a_mover: str = Form(""),
    fecha_inicio: str = Form(""),
    fecha_fin: str    = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    errors = []
    if not nombre_barco.strip():
        errors.append("El nombre del barco es requerido.")

    if errors:
        return templates.TemplateResponse(request, "transporte/historial_new.html", {
            "request": request,
            "user": current_user,
            "errors": errors,
            "form": {
                "nombre_barco": nombre_barco,
                "producto": producto,
                "cliente": cliente,
                "deposito": deposito,
                "mercaderia_a_mover": mercaderia_a_mover,
                "fecha_inicio": fecha_inicio,
                "fecha_fin": fecha_fin,
            },
        }, status_code=422)

    op = mt.TransporteOperativo(
        nombre_barco  = nombre_barco.strip(),
        producto      = producto.strip() or None,
        cliente       = cliente.strip() or None,
        deposito      = deposito.strip() or None,
        fecha_inicio  = _parse_date(fecha_inicio),
        mercaderia_a_mover = mercaderia_a_mover.strip() or None,
        fecha_fin     = _parse_date(fecha_fin),
        created_by_id = current_user.id,
    )
    db.add(op)
    db.commit()
    db.refresh(op)
    return RedirectResponse(f"/transporte/historial/{op.id}/puerto", status_code=303)


@router.get("/historial/{op_id}", response_class=HTMLResponse)
async def historial_detail(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    op = (
        db.query(mt.TransporteOperativo)
        .options(
            joinedload(mt.TransporteOperativo.asignaciones)
            .joinedload(mt.TransporteOperativoAsignacion.assigned_by),
        )
        .filter(
            mt.TransporteOperativo.id == op_id,
            mt.TransporteOperativo.deleted_at == None,
        )
        .first()
    )
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")

    # IDs ya asignados para no mostrarlos en el selector
    asignados_ids = {a.nomina_id for a in op.asignaciones if a.nomina_id}

    disponibles = (
        db.query(mt.TransporteNomina)
        .filter(
            mt.TransporteNomina.deleted_at == None,
            mt.TransporteNomina.id.notin_(asignados_ids) if asignados_ids else True,
        )
        .order_by(
            mt.TransporteNomina.nombre_chofer.asc(),
            mt.TransporteNomina.empresa.asc(),
        )
        .all()
    )

    return templates.TemplateResponse(request, "transporte/historial_detail.html", {
        "request": request,
        "user": current_user,
        "op": op,
        "disponibles": disponibles,
    })



@router.get("/historial/{op_id}/balanza", response_class=HTMLResponse)
async def historial_balanza(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    return await historial_detail(op_id, request, db, current_user)


@router.get("/historial/{op_id}/edit", response_class=HTMLResponse)
async def historial_edit_form(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    op = db.query(mt.TransporteOperativo).filter(
        mt.TransporteOperativo.id == op_id,
        mt.TransporteOperativo.deleted_at == None,
    ).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")

    return templates.TemplateResponse(request, "transporte/historial_edit.html", {
        "request": request,
        "user": current_user,
        "op": op,
        "errors": [],
    })


@router.post("/historial/{op_id}/edit", response_class=HTMLResponse)
async def historial_edit_save(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
    nombre_barco: str = Form(""),
    producto: str = Form(""),
    cliente: str = Form(""),
    deposito: str = Form(""),
    mercaderia_a_mover: str = Form(""),
    fecha_inicio: str = Form(""),
    fecha_fin: str = Form(""),
):
    op = db.query(mt.TransporteOperativo).filter(
        mt.TransporteOperativo.id == op_id,
        mt.TransporteOperativo.deleted_at == None,
    ).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")
    op.nombre_barco = nombre_barco
    op.producto = producto
    op.cliente = cliente
    op.deposito = deposito
    op.mercaderia_a_mover = mercaderia_a_mover
    from datetime import date as _date
    op.fecha_inicio = _date.fromisoformat(fecha_inicio) if fecha_inicio else None
    op.fecha_fin = _date.fromisoformat(fecha_fin) if fecha_fin else None
    db.commit()
    return RedirectResponse(url=f"/transporte/historial/{op_id}", status_code=303)


@router.post("/historial/{op_id}/delete", response_class=HTMLResponse)
async def historial_delete(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    op = db.query(mt.TransporteOperativo).filter(
        mt.TransporteOperativo.id == op_id,
        mt.TransporteOperativo.deleted_at == None,
    ).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")
    from datetime import datetime
    op.deleted_at = datetime.now()
    db.commit()
    return RedirectResponse(url="/transporte/historial?ok=Operativo+eliminado", status_code=303)


@router.get("/historial/{op_id}/exportar-word-puerto")
async def historial_exportar_word_puerto(
    op_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    try:
        from docx import Document
        from docx.shared import Pt, Cm, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="python-docx no está instalado.",
        )
    from pathlib import Path

    op = (
        db.query(mt.TransporteOperativo)
        .filter(
            mt.TransporteOperativo.id == op_id,
            mt.TransporteOperativo.deleted_at == None,
        )
        .first()
    )
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")

    excluidos_ids = {
        x.nomina_id
        for x in db.query(mt.TransportePuertoExclusion)
        .filter(mt.TransportePuertoExclusion.operativo_id == op_id)
        .all()
    }

    nomina_puerto = (
        db.query(mt.TransporteNomina)
        .filter(
            mt.TransporteNomina.deleted_at == None,
            mt.TransporteNomina.id.notin_(excluidos_ids) if excluidos_ids else True,
        )
        .order_by(
            mt.TransporteNomina.nombre_chofer.asc(),
            mt.TransporteNomina.empresa.asc(),
        )
        .all()
    )

    MESES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
    }
    hoy = date.today()
    fecha_str = (
        f"San Nicolás de los Arroyos, "
        f"{hoy.day} de {MESES[hoy.month]} de {hoy.year}"
    )

    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    if os.path.isfile(_LOGO_PATH):
        logo_p = doc.add_paragraph()
        logo_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        logo_p.paragraph_format.space_after = Pt(6)
        logo_p.add_run().add_picture(_LOGO_PATH, width=Inches(2.2))

    doc.add_paragraph()

    p_fecha = doc.add_paragraph()
    p_fecha.paragraph_format.space_after = Pt(2)
    r_fecha = p_fecha.add_run(fecha_str)
    r_fecha.font.size = Pt(11)

    doc.add_paragraph()

    p_dest = doc.add_paragraph()
    p_dest.paragraph_format.space_after = Pt(0)
    r_dest = p_dest.add_run("Al señor /es,")
    r_dest.font.size = Pt(11)

    p_consorcio = doc.add_paragraph()
    p_consorcio.paragraph_format.space_after = Pt(0)
    r_consorcio = p_consorcio.add_run("CONSORCIO DE GESTION DEL PUERTO SAN NICOLAS")
    r_consorcio.bold = True
    r_consorcio.font.size = Pt(11)

    p_aduana = doc.add_paragraph()
    p_aduana.paragraph_format.space_after = Pt(0)
    r_aduana = p_aduana.add_run("ADUANA SAN NICOLAS")
    r_aduana.bold = True
    r_aduana.font.size = Pt(11)

    p_sd = doc.add_paragraph()
    p_sd.paragraph_format.space_after = Pt(2)
    r_sd = p_sd.add_run("S / D")
    r_sd.font.size = Pt(11)

    p_ref = doc.add_paragraph()
    p_ref.paragraph_format.space_after = Pt(0)
    r_ref = p_ref.add_run("Ref. Solicitud de ingreso")
    r_ref.bold = True
    r_ref.font.size = Pt(11)

    p_buque = doc.add_paragraph()
    p_buque.paragraph_format.space_after = Pt(8)
    r_buque_label = p_buque.add_run("Buque: ")
    r_buque_label.bold = True
    r_buque_label.font.size = Pt(11)
    r_buque = p_buque.add_run(op.nombre_barco or "—")
    r_buque.bold = True
    r_buque.font.size = Pt(11)

    p_consideracion = doc.add_paragraph()
    p_consideracion.paragraph_format.space_after = Pt(4)
    r_consideracion = p_consideracion.add_run("De nuestra consideración.")
    r_consideracion.font.size = Pt(11)

    mercaderia = op.mercaderia_a_mover or "mercadería"

    p_cuerpo = doc.add_paragraph()
    p_cuerpo.paragraph_format.space_after = Pt(8)
    r_cuerpo = p_cuerpo.add_run(
        f"Por la presente y a fin de cumplimentar con las resoluciones vigentes, "
        f"solicitamos tengan a bien autorizar el ingreso de choferes y vehículos, "
        f"con el motivo de realizar el movimiento de {mercaderia} de la zona de balanza "
        f"y muelle de puerto SAN NICOLAS con destino a Deposito MTR S.A."
    )
    r_cuerpo.font.size = Pt(11)

    p_detalle = doc.add_paragraph()
    p_detalle.paragraph_format.space_after = Pt(8)
    r_detalle = p_detalle.add_run(
        "A continuación, detallo las unidades y el personal a ingresar:"
    )
    r_detalle.font.size = Pt(11)

    headers = [
        "Empresa",
        "Apellido y nombre",
        "DNI",
        "Camión",
        "Patente camión",
        "Patente acoplado",
    ]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    hdr_row = table.rows[0]
    for i, h in enumerate(headers):
        cell = hdr_row.cells[i]
        cell.paragraphs[0].clear()
        run = cell.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(10)

    for n in nomina_puerto:
        row = table.add_row()
        vals = [
            n.empresa,
            n.nombre_chofer,
            n.dni or "—",
            n.marca_camion or "—",
            n.patente_camion or "—",
            n.patente_acoplado or "—",
        ]
        for i, v in enumerate(vals):
            cell = row.cells[i]
            cell.paragraphs[0].clear()
            run = cell.paragraphs[0].add_run(v)
            run.font.size = Pt(10)

    doc.add_paragraph()

    firma_path = (
        Path(__file__).resolve().parent.parent.parent
        / "static" / "firmas" / "fernando_martinez.png"
    )
    if firma_path.is_file():
        firma_p = doc.add_paragraph()
        firma_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        firma_p.add_run().add_picture(
            str(firma_path), width=Inches(1.8)
        )

    p_nombre = doc.add_paragraph()
    p_nombre.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_nombre.paragraph_format.space_after = Pt(0)
    r_nombre = p_nombre.add_run("Fernando Martínez")
    r_nombre.bold = True
    r_nombre.font.size = Pt(11)

    p_cargo = doc.add_paragraph()
    p_cargo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_cargo = p_cargo.add_run("MTR Logística")
    r_cargo.font.size = Pt(11)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    safe_name = op.nombre_barco.replace(" ", "_").replace("/", "-")
    filename = f"carta_puerto_{safe_name}_{op_id}.docx"

    WORD_MIME = (
        "application/vnd.openxmlformats-"
        "officedocument.wordprocessingml.document"
    )
    return StreamingResponse(
        buf,
        media_type=WORD_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@router.post("/historial/{op_id}/asignar", response_class=HTMLResponse)
async def historial_asignar(
    op_id: int,
    nomina_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    op = db.query(mt.TransporteOperativo).filter(
        mt.TransporteOperativo.id == op_id,
        mt.TransporteOperativo.deleted_at == None,
    ).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")

    nomina = db.query(mt.TransporteNomina).filter(
        mt.TransporteNomina.id == nomina_id,
        mt.TransporteNomina.deleted_at == None,
    ).first()
    if not nomina:
        raise HTTPException(status_code=404, detail="Registro de nómina no encontrado.")

    # Evitar duplicados por nomina_id en el mismo operativo
    ya_asignado = db.query(mt.TransporteOperativoAsignacion).filter(
        mt.TransporteOperativoAsignacion.operativo_id == op_id,
        mt.TransporteOperativoAsignacion.nomina_id == nomina_id,
    ).first()
    if ya_asignado:
        return RedirectResponse(f"/transporte/historial/{op_id}", status_code=303)

    # Guardar snapshot de los datos actuales del transporte
    asignacion = mt.TransporteOperativoAsignacion(
        operativo_id          = op_id,
        nomina_id             = nomina_id,
        empresa_snap          = nomina.empresa,
        nombre_chofer_snap    = nomina.nombre_chofer,
        dni_snap              = nomina.dni,
        marca_camion_snap     = nomina.marca_camion,
        patente_camion_snap   = nomina.patente_camion,
        patente_acoplado_snap = nomina.patente_acoplado,
        assigned_by_id        = current_user.id,
    )
    db.add(asignacion)
    db.commit()
    return RedirectResponse(f"/transporte/historial/{op_id}", status_code=303)


@router.post("/historial/{op_id}/desasignar/{asig_id}")
async def historial_desasignar(
    op_id: int,
    asig_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    asig = db.query(mt.TransporteOperativoAsignacion).filter(
        mt.TransporteOperativoAsignacion.id == asig_id,
        mt.TransporteOperativoAsignacion.operativo_id == op_id,
    ).first()
    if not asig:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")

    db.delete(asig)
    db.commit()
    return RedirectResponse(f"/transporte/historial/{op_id}", status_code=303)


# ── EXPORTAR WORD ─────────────────────────────────────────────────────────────

# Ruta esperada del logo: static/logo_mtr.png en la raíz del proyecto.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOGO_PATH    = os.path.join(_PROJECT_ROOT, "static", "logo_mtr.png")


@router.get("/historial/{op_id}/exportar-word")
async def historial_exportar_word(
    op_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    try:
        from docx import Document
        from docx.shared import Pt, Cm, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="python-docx no está instalado. Ejecutá: pip install python-docx",
        )

    op = (
        db.query(mt.TransporteOperativo)
        .options(joinedload(mt.TransporteOperativo.asignaciones))
        .filter(
            mt.TransporteOperativo.id == op_id,
            mt.TransporteOperativo.deleted_at == None,
        )
        .first()
    )
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")

    def _fmt_date(d):
        if not d:
            return "—"
        return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)

    def _remove_table_borders(table):
        tbl = table._tbl
        tblPr = tbl.find(qn("w:tblPr"))
        if tblPr is None:
            tblPr = OxmlElement("w:tblPr")
            tbl.insert(0, tblPr)
        tblBorders = OxmlElement("w:tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            b = OxmlElement(f"w:{side}")
            b.set(qn("w:val"), "none")
            b.set(qn("w:sz"), "0")
            b.set(qn("w:space"), "0")
            b.set(qn("w:color"), "auto")
            tblBorders.append(b)
        tblPr.append(tblBorders)

    # ── Documento ──────────────────────────────────────────────────────────────
    doc = Document()

    for section in doc.sections:
        section.top_margin    = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    # ── Logo ───────────────────────────────────────────────────────────────────
    if os.path.isfile(_LOGO_PATH):
        logo_p = doc.add_paragraph()
        logo_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        logo_p.paragraph_format.space_after = Pt(6)
        logo_p.add_run().add_picture(_LOGO_PATH, width=Inches(2.2))

    # ── Título ─────────────────────────────────────────────────────────────────
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_before = Pt(0)
    title_p.paragraph_format.space_after  = Pt(14)
    r = title_p.add_run("Nómina de choferes y equipos")
    r.bold = True
    r.font.size = Pt(16)

    # ── Datos del operativo ────────────────────────────────────────────────────
    def _field(label, value):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after  = Pt(2)
        r_lbl = p.add_run(f"{label}: ")
        r_lbl.bold = True
        r_lbl.font.size = Pt(11)
        r_val = p.add_run(value or "—")
        r_val.font.size = Pt(11)

    _field("Buque",        op.nombre_barco)
    _field("Producto",     op.producto)
    _field("Cliente",      op.cliente)
    _field("Depósito",     op.deposito)
    _field("Fecha inicio", _fmt_date(op.fecha_inicio))
    _field("Fecha fin",    _fmt_date(op.fecha_fin))

    doc.add_paragraph()

    # ── Tabla de transportes ───────────────────────────────────────────────────
    headers = [
        "Empresa",
        "Apellido y nombre / Chofer",
        "DNI",
        "Camión",
        "Patente camión",
        "Patente acoplado",
    ]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"

    hdr_row = table.rows[0]
    for i, h in enumerate(headers):
        cell = hdr_row.cells[i]
        cell.paragraphs[0].clear()
        run = cell.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(10)

    for a in op.asignaciones:
        row = table.add_row()
        vals = [
            a.empresa_snap,
            a.nombre_chofer_snap,
            a.dni_snap              or "—",
            a.marca_camion_snap     or "—",
            a.patente_camion_snap   or "—",
            a.patente_acoplado_snap or "—",
        ]
        for i, v in enumerate(vals):
            cell = row.cells[i]
            cell.paragraphs[0].clear()
            run = cell.paragraphs[0].add_run(v)
            run.font.size = Pt(10)

    doc.add_paragraph()

    from pathlib import Path
    firma_path = (
        Path(__file__).resolve().parent.parent.parent
        / "static" / "firmas" / "fernando_martinez.png"
    )
    if firma_path.is_file():
        firma_p = doc.add_paragraph()
        firma_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        firma_p.add_run().add_picture(str(firma_path), width=Inches(1.8))

    p_nombre = doc.add_paragraph()
    p_nombre.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_nombre.paragraph_format.space_after = Pt(0)
    r_nombre = p_nombre.add_run("Fernando Martínez")
    r_nombre.bold = True
    r_nombre.font.size = Pt(11)

    p_cargo = doc.add_paragraph()
    p_cargo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_cargo = p_cargo.add_run("MTR Logística")
    r_cargo.font.size = Pt(11)

    # ── Serializar ─────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    safe_name = op.nombre_barco.replace(" ", "_").replace("/", "-")
    filename  = f"operativo_transporte_{safe_name}.docx"

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# ── NÓMINA PARA PUERTO ───────────────────────────────────────────────────────

@router.get("/historial/{op_id}/puerto", response_class=HTMLResponse)
async def historial_puerto(
    op_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    op = db.query(mt.TransporteOperativo).filter(
        mt.TransporteOperativo.id == op_id,
        mt.TransporteOperativo.deleted_at == None,
    ).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operativo no encontrado.")

    excluidos_ids = {
        x.nomina_id
        for x in db.query(mt.TransportePuertoExclusion)
        .filter(mt.TransportePuertoExclusion.operativo_id == op_id)
        .all()
    }

    nomina = (
        db.query(mt.TransporteNomina)
        .filter(mt.TransporteNomina.deleted_at == None)
        .order_by(mt.TransporteNomina.nombre_chofer.asc(), mt.TransporteNomina.empresa.asc())
        .all()
    )

    incluidos = [n for n in nomina if n.id not in excluidos_ids]
    excluidos = [n for n in nomina if n.id in excluidos_ids]

    return templates.TemplateResponse(request, "transporte/historial_puerto.html", {
        "request": request,
        "user": current_user,
        "op": op,
        "incluidos": incluidos,
        "excluidos": excluidos,
    })


@router.post("/historial/{op_id}/puerto/quitar/{nomina_id}")
async def historial_puerto_quitar(
    op_id: int,
    nomina_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    existe = db.query(mt.TransportePuertoExclusion).filter(
        mt.TransportePuertoExclusion.operativo_id == op_id,
        mt.TransportePuertoExclusion.nomina_id == nomina_id,
    ).first()

    if not existe:
        db.add(mt.TransportePuertoExclusion(
            operativo_id=op_id,
            nomina_id=nomina_id,
            removed_by_id=current_user.id,
        ))
        db.commit()

    return RedirectResponse(f"/transporte/historial/{op_id}/puerto", status_code=303)


@router.post("/historial/{op_id}/puerto/restaurar/{nomina_id}")
async def historial_puerto_restaurar(
    op_id: int,
    nomina_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_require_access),
):
    excl = db.query(mt.TransportePuertoExclusion).filter(
        mt.TransportePuertoExclusion.operativo_id == op_id,
        mt.TransportePuertoExclusion.nomina_id == nomina_id,
    ).first()

    if excl:
        db.delete(excl)
        db.commit()

    return RedirectResponse(f"/transporte/historial/{op_id}/puerto", status_code=303)
