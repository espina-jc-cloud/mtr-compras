"""
Búsqueda de buque global (Etapa 4).

Endpoint mínimo que busca un buque por nombre en los tres lugares donde vive:
Próximos Arribos, Operativos Live y Operativos históricos. Respeta permisos:
solo consulta las secciones que el usuario puede ver.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.permissions import can
from app import models
from app.models_arribos import ProximoArribo, ARRIBO_ESTADO_CSS, ARRIBO_ESTADO_LABELS
from app.models_live import OperationLiveSession
from app.templates import templates

router = APIRouter()


@router.get("/buscar", response_class=HTMLResponse)
async def buscar(request: Request, q: str = "", db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    q = (q or "").strip()
    arribos, lives, historicos = [], [], []

    if len(q) >= 2:
        like = f"%{q}%"
        if can(current_user, "operaciones.arribos"):
            arribos = (db.query(ProximoArribo)
                       .filter(ProximoArribo.deleted_at.is_(None),
                               ProximoArribo.buque.ilike(like))
                       .order_by(ProximoArribo.updated_at.desc())
                       .limit(15).all())
        if can(current_user, "operaciones.live"):
            lives = (db.query(OperationLiveSession)
                     .filter(OperationLiveSession.ship_name.ilike(like))
                     .order_by(OperationLiveSession.created_at.desc())
                     .limit(15).all())
        if can(current_user, "operaciones.finalizados"):
            historicos = (db.query(models.Operation)
                          .filter(models.Operation.ship_name.ilike(like))
                          .order_by(models.Operation.id.desc())
                          .limit(15).all())

    total = len(arribos) + len(lives) + len(historicos)
    return templates.TemplateResponse(request, "search.html", {
        "user": current_user, "q": q, "total": total,
        "arribos": arribos, "lives": lives, "historicos": historicos,
        "estado_css": ARRIBO_ESTADO_CSS, "estado_labels": ARRIBO_ESTADO_LABELS,
    })
