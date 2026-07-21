"""
Polinómica CNA — tarifas de logística portuaria actualizadas por polinómica
de 6 índices (SUPA, Camioneros, IPC, Combustible, USD BNA, FADEEAC).

La lógica de cálculo vive en app/polinomica_calc.py (pura, testeada en
tests/test_polinomica_calc.py). Este router solo orquesta DB + vistas.
"""
import json
from datetime import datetime, date

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.permissions import require_perm
from app.models_polinomica import PolinomicaIndice, PolinomicaRemito
from app.polinomica_calc import (
    calcular_acumulados, calcular_tarifas, promedio_acumulado, serie_acumulados,
    INDICES, INDICE_LABELS, fmt_ars,
)
from app.templates import templates

router = APIRouter(prefix="/polinomica")
_guard = require_perm("operaciones.polinomica")

_MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
             "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _historial(db: Session):
    rows = db.query(PolinomicaIndice).order_by(PolinomicaIndice.orden).all()
    return [r.as_dict() for r in rows]


def _mes_siguiente(ultimo_mes: str) -> str:
    """'Jun 2026' → 'Jul 2026'."""
    try:
        nombre, anio = ultimo_mes.split()
        i = _MESES_ES.index(nombre)
        if i == 11:
            return f"{_MESES_ES[0]} {int(anio) + 1}"
        return f"{_MESES_ES[i + 1]} {anio}"
    except (ValueError, IndexError):
        return ""


def _parse_pct(raw: str) -> float:
    """'3' → 0.03 · '2,51' → 0.0251 · '-2.53' → -0.0253. Entrada SIEMPRE en %."""
    s = str(raw).strip().replace(",", ".")
    if s == "":
        raise ValueError("vacío")
    return float(s) / 100.0


def _ctx_comun(db: Session, current_user):
    hist = _historial(db)
    return {
        "user": current_user,
        "mes_vigente": hist[-1]["mes"] if hist else "—",
        "fmt_ars": fmt_ars,
    }


# ── Dashboard: tarifas vigentes ────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    hist = _historial(db)
    tarifas = calcular_tarifas(hist) if hist else []
    acc = calcular_acumulados(hist) if hist else {}
    max_aumento = max((t["aumento_pct"] for t in tarifas), default=1)
    ultimo = hist[-1] if hist else None
    return templates.TemplateResponse(request, "polinomica/dashboard.html", {
        **_ctx_comun(db, current_user),
        "tab": "dashboard",
        "tarifas": tarifas,
        "max_aumento": max_aumento,
        "promedio": promedio_acumulado(hist) if hist else 0,
        "periodo": f"{hist[0]['mes']} → {hist[-1]['mes']}" if hist else "—",
        "ultimo": ultimo,
        "acc": acc,
        "indice_labels": INDICE_LABELS,
    })


# ── Actualizar mes ─────────────────────────────────────────────────────────────

@router.get("/actualizar", response_class=HTMLResponse)
async def actualizar_form(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    hist = _historial(db)
    ultimo = hist[-1] if hist else None
    return templates.TemplateResponse(request, "polinomica/actualizar.html", {
        **_ctx_comun(db, current_user),
        "tab": "actualizar",
        "ultimo": ultimo,
        "mes_sugerido": _mes_siguiente(ultimo["mes"]) if ultimo else "",
        "indice_labels": INDICE_LABELS,
        "indices": INDICES,
        "error": request.query_params.get("error"),
        "ok": request.query_params.get("ok"),
    })


@router.post("/actualizar")
async def actualizar_guardar(
    request: Request,
    mes: str = Form(...),
    supa: str = Form(...), cam: str = Form(...), ipc: str = Form(...),
    comb: str = Form(...), usd: str = Form(...), fadeeac: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(_guard),
):
    mes = mes.strip()
    if not mes:
        return RedirectResponse(url="/polinomica/actualizar?error=El+mes+es+obligatorio", status_code=303)
    if db.query(PolinomicaIndice).filter(PolinomicaIndice.mes == mes).first():
        return RedirectResponse(url=f"/polinomica/actualizar?error=El+mes+{mes}+ya+existe", status_code=303)
    try:
        vals = {k: _parse_pct(v) for k, v in
                [("supa", supa), ("cam", cam), ("ipc", ipc),
                 ("comb", comb), ("usd", usd), ("fadeeac", fadeeac)]}
    except ValueError:
        return RedirectResponse(url="/polinomica/actualizar?error=Valores+inválidos:+cargá+las+variaciones+en+%25", status_code=303)

    max_orden = db.query(PolinomicaIndice).count()
    db.add(PolinomicaIndice(mes=mes, orden=max_orden + 1, **vals))
    db.commit()
    return RedirectResponse(url=f"/polinomica?ok=Mes+{mes}+guardado", status_code=303)


# ── Historial ──────────────────────────────────────────────────────────────────

@router.get("/historial", response_class=HTMLResponse)
async def historial_view(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    hist = _historial(db)
    return templates.TemplateResponse(request, "polinomica/historial.html", {
        **_ctx_comun(db, current_user),
        "tab": "historial",
        "historial": hist,
        "indice_labels": INDICE_LABELS,
        "indices": INDICES,
    })


# ── Acumulados ─────────────────────────────────────────────────────────────────

@router.get("/acumulados", response_class=HTMLResponse)
async def acumulados_view(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    hist = _historial(db)
    acc = calcular_acumulados(hist) if hist else {}
    tarifas = calcular_tarifas(hist) if hist else []
    return templates.TemplateResponse(request, "polinomica/acumulados.html", {
        **_ctx_comun(db, current_user),
        "tab": "acumulados",
        "acc": acc,
        "tarifas": tarifas,
        "granel":   [t for t in tarifas if t["cat"] == "Granel"],
        "bolsones": [t for t in tarifas if t["cat"] == "Bolsones"],
        "indice_labels": INDICE_LABELS,
        "indices": INDICES,
    })


# ── Remito ─────────────────────────────────────────────────────────────────────

@router.get("/remito", response_class=HTMLResponse)
async def remito_form(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    hist = _historial(db)
    tarifas = calcular_tarifas(hist) if hist else []
    return templates.TemplateResponse(request, "polinomica/remito.html", {
        **_ctx_comun(db, current_user),
        "tab": "remito",
        "tarifas": tarifas,
        "tarifas_json": json.dumps([
            {"i": i, "nombre": t["nombre"], "cat": t["cat"], "nueva": t["nueva"]}
            for i, t in enumerate(tarifas)
        ]),
        "hoy": date.today().isoformat(),
    })


def _proximo_numero(db: Session) -> str:
    hoy = datetime.utcnow().strftime("%Y%m%d")
    pref = f"TAR-{hoy}-"
    n = db.query(PolinomicaRemito).filter(PolinomicaRemito.numero.like(pref + "%")).count()
    return f"{pref}{n + 1:03d}"


@router.post("/remito/guardar")
async def remito_guardar(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    body = await request.json()
    operativo = str(body.get("operativo", "")).strip()
    seleccion = body.get("tarifas", [])
    if not operativo:
        raise HTTPException(422, "El nombre del operativo es obligatorio.")
    if not seleccion:
        raise HTTPException(422, "Seleccioná al menos una tarifa.")

    def _d(s):
        try:
            return date.fromisoformat(s) if s else None
        except ValueError:
            return None

    hist = _historial(db)
    remito = PolinomicaRemito(
        numero=_proximo_numero(db),
        operativo=operativo,
        producto=str(body.get("producto", "")).strip() or None,
        fecha_ini=_d(body.get("fecha_ini")),
        fecha_fin=_d(body.get("fecha_fin")),
        observaciones=str(body.get("observaciones", "")).strip() or None,
        tarifas_json=json.dumps(seleccion),
        mes_vigencia=hist[-1]["mes"] if hist else None,
        created_by=getattr(current_user, "name", None),
    )
    db.add(remito)
    db.commit()
    db.refresh(remito)
    return JSONResponse({"id": remito.id, "numero": remito.numero})


@router.get("/remitos", response_class=HTMLResponse)
async def remitos_list(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    remitos = db.query(PolinomicaRemito).order_by(PolinomicaRemito.created_at.desc()).limit(200).all()
    filas = [(r, len(json.loads(r.tarifas_json or "[]"))) for r in remitos]
    return templates.TemplateResponse(request, "polinomica/remitos.html", {
        **_ctx_comun(db, current_user),
        "tab": "remito",
        "filas": filas,
    })


@router.get("/remito/{rid}", response_class=HTMLResponse)
async def remito_detalle(rid: int, request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    r = db.query(PolinomicaRemito).filter(PolinomicaRemito.id == rid).first()
    if not r:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "polinomica/remito_detalle.html", {
        **_ctx_comun(db, current_user),
        "tab": "remito",
        "r": r,
        "tarifas": json.loads(r.tarifas_json or "[]"),
    })


@router.get("/remito/{rid}/pdf")
async def remito_pdf(rid: int, db: Session = Depends(get_db), current_user=Depends(_guard)):
    from app.polinomica_pdf import generar_pdf_remito
    r = db.query(PolinomicaRemito).filter(PolinomicaRemito.id == rid).first()
    if not r:
        raise HTTPException(404)
    pdf = generar_pdf_remito(r, json.loads(r.tarifas_json or "[]"))
    safe_op = "".join(c if c.isalnum() or c in "-_ " else "" for c in r.operativo)[:40].strip().replace(" ", "_")
    fname = f"tarifas_cna_{safe_op}_{r.numero}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ── APIs JSON ──────────────────────────────────────────────────────────────────

@router.get("/api/tarifas")
async def api_tarifas(db: Session = Depends(get_db), current_user=Depends(_guard)):
    return calcular_tarifas(_historial(db))


@router.get("/api/historial")
async def api_historial(db: Session = Depends(get_db), current_user=Depends(_guard)):
    return _historial(db)


@router.get("/api/acumulados")
async def api_acumulados(db: Session = Depends(get_db), current_user=Depends(_guard)):
    hist = _historial(db)
    return {"acumulados": calcular_acumulados(hist) if hist else {},
            "serie": serie_acumulados(hist) if hist else []}
