"""
Buques terminados: qué mandó balanza cuando cerró cada operativo.

Próximos Arribos dice qué viene; esto dice cómo terminó. El detalle no es una
ficha con cuatro totales sino un análisis, porque el Excel de balanza trae cada
camión pesado y ahí está todo lo que después se discute: el ritmo real, quién
puso los camiones, cuánto tardaron adentro y cuánto se despegó la balanza.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from urllib.parse import quote_plus

from app import balanza_sync
from app.buque_analitica import analizar
from app.database import get_db
from app.models_buques import BuqueOperativo
from app.permissions import require_perm
from app.templates import templates

router = APIRouter(prefix="/operations/buques")
_guard = require_perm("operaciones.finalizados")


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db),
                 current_user=Depends(_guard)):
    def qp(n, d=""):
        v = request.query_params.getlist(n)
        return v[0].strip() if v else d

    q_texto, q_cliente = qp("q"), qp("cliente")
    bq = db.query(BuqueOperativo)
    if q_texto:
        bq = bq.filter(or_(BuqueOperativo.buque.ilike(f"%{q_texto}%"),
                           BuqueOperativo.producto.ilike(f"%{q_texto}%")))
    if q_cliente:
        bq = bq.filter(BuqueOperativo.cliente.ilike(f"%{q_cliente}%"))
    operativos = bq.order_by(BuqueOperativo.inicio.desc().nullslast(),
                             BuqueOperativo.id.desc()).all()

    clientes = [c[0] for c in db.query(BuqueOperativo.cliente)
                .filter(BuqueOperativo.cliente.isnot(None)).distinct().all()]
    return templates.TemplateResponse(request, "operations/buques/list.html", {
        "user": current_user, "operativos": operativos,
        "clientes": sorted(clientes),
        "totales": {
            "buques": len(operativos),
            "viajes": sum(o.viajes or 0 for o in operativos),
            "toneladas": round(sum(int(o.neto_kg or 0) for o in operativos) / 1000),
        },
        "params": {"q": q_texto, "cliente": q_cliente},
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@router.post("/revisar-correo")
async def revisar_correo(db: Session = Depends(get_db), current_user=Depends(_guard)):
    r = balanza_sync.sincronizar(db)
    if not r["ok"]:
        return RedirectResponse(
            "/operations/buques?error=" + quote_plus(r["error"] or "Falló la revisión."),
            status_code=303)
    partes = [f"{len(r['altas'])} operativo(s) nuevo(s)"]
    if r["reemplazos"]:
        partes.append(f"{len(r['reemplazos'])} actualizado(s) con el cierre")
    partes.append(f"{r.get('mirados', 0)} mail(s) revisado(s)")
    return RedirectResponse("/operations/buques?saved=" + quote_plus(" · ".join(partes)),
                            status_code=303)


@router.get("/{op_id}", response_class=HTMLResponse)
async def detalle(op_id: int, request: Request, db: Session = Depends(get_db),
                  current_user=Depends(_guard)):
    op = db.get(BuqueOperativo, op_id)
    if op is None:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "operations/buques/detail.html", {
        "user": current_user, "op": op, "a": analizar(op),
        "saved": request.query_params.get("saved"),
    })
