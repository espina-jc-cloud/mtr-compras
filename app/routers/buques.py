"""
Buques: una sola pantalla para todo el recorrido de un barco.

POR QUÉ REEMPLAZA A TRES
    Próximos Arribos, Operativos Live y Buques terminados mostraban el mismo
    buque con tres estados distintos, porque cada uno guardaba el suyo. El MV
    KOCIEWIE terminó de descargar el 11/09 —balanza mandó el resumen con 67
    viajes— y Próximos Arribos lo seguía mostrando como que venía.

    Acá el estado se deriva una sola vez (ver app/buque_ficha.py) y las tres
    etapas viven en la misma página. Las pantallas viejas siguen existiendo
    para lo suyo —dar de alta un arribo, pegar un parte— pero dejan de ser la
    puerta de entrada.
"""
import re
from datetime import datetime
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import arribos_sync, balanza_sync
from app.buque_analitica import analizar
from app.live_avance import avance
from app.buque_ficha import DESCARGANDO, ESPERADO, CONFIRMADO, TERMINADO, fichas, panorama
from app.database import get_db
from app.models_buques import BuqueOperativo
from app.permissions import require_perm
from app.templates import templates

router = APIRouter(prefix="/buques")
_guard = require_perm("operaciones.arribos")

# Cuántos terminados se muestran sin pedirlos todos: los últimos de un vistazo.
TERMINADOS_VISIBLES = 8


def slug(canon: str) -> str:
    """El buque en la URL: /buques/ocean-innovation."""
    return re.sub(r"[^a-z0-9]+", "-", (canon or "").lower()).strip("-")


def _recorrido(f) -> list[dict]:
    """Las cuatro etapas del buque, con la fecha de cada una si ya pasó."""
    a, live, r = f["arribo"], f["live"], f["resumen"]
    nominado = a is not None and a.origen_alta == "nominacion"
    return [
        {"titulo": "Nominado",
         "hecho": a is not None,
         "valor": (a.cliente if a else None) or ("Carga manual" if a else None),
         "nota": ("por mail" if nominado else "cargado a mano") if a else None},
        {"titulo": "Con fecha",
         "hecho": bool(a and (a.etb or a.fecha_estimada)),
         "valor": (a.etb if a and a.etb else
                   (a.fecha_estimada.strftime("%d/%m/%Y") if a and a.fecha_estimada else None)),
         "nota": ("ETB del line-up" if a and a.last_update_source == "lineup" else None)},
        {"titulo": "Descargando",
         "hecho": r is not None or live is not None,
         "valor": (r.inicio.strftime("%d/%m/%Y") if r and r.inicio else
                   ("operativo abierto" if live else None)),
         "nota": (f"{r.viajes} viajes" if r else None)},
        {"titulo": "Terminado",
         "hecho": f["estado"] == TERMINADO,
         "valor": (r.fin.strftime("%d/%m/%Y") if r and r.fin else
                   ("sin fecha de cierre" if f["estado"] == TERMINADO else None)),
         "nota": (f"{f['toneladas']:,} t".replace(",", ".") if f["toneladas"] else None)},
    ]


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db),
                 current_user=Depends(_guard)):
    q = (request.query_params.get("q") or "").strip()
    todos = request.query_params.get("todos") == "1"

    lista = fichas(db)
    for f in lista:
        f["slug"] = slug(f["canon"])

    descargando = [f for f in lista if f["estado"] == DESCARGANDO]
    por_venir = [f for f in lista if f["estado"] in (ESPERADO, CONFIRMADO)]
    terminados = [f for f in lista if f["estado"] == TERMINADO]
    terminados.sort(key=lambda f: (f["fecha"] is None, f["fecha"]), reverse=True)
    total_terminados = len(terminados)
    if q:
        aguja = q.lower()
        terminados = [f for f in terminados
                      if aguja in f["buque"].lower()
                      or aguja in (f["producto"] or "").lower()
                      or aguja in (f["cliente"] or "").lower()]
    elif not todos:
        terminados = terminados[:TERMINADOS_VISIBLES]

    return templates.TemplateResponse(request, "buques/list.html", {
        "user": current_user, "vista": panorama(lista),
        "descargando": descargando, "por_venir": por_venir,
        "terminados": terminados, "terminados_total": total_terminados,
        "params": {"q": q},
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@router.post("/revisar-correo")
async def revisar_correo(db: Session = Depends(get_db), current_user=Depends(_guard)):
    """Una sola pasada: nominaciones, line-up y resúmenes de balanza."""
    uid = getattr(current_user, "id", None)
    a = arribos_sync.sincronizar(db, uid)
    b = balanza_sync.sincronizar(db)
    if not a["ok"] or not b["ok"]:
        return RedirectResponse("/buques?error=" + quote_plus(
            a.get("error") or b.get("error") or "Falló la revisión."), status_code=303)

    partes = []
    if a["nominaciones"].get("altas"):
        partes.append(f"{len(a['nominaciones']['altas'])} buque(s) nuevo(s)")
    if a["lineup"].get("tocados"):
        partes.append(f"{len(a['lineup']['tocados'])} con fecha del line-up")
    if a.get("cerrados"):
        partes.append(f"{len(a['cerrados'])} cerrado(s)")
    if b.get("altas"):
        partes.append(f"{len(b['altas'])} resumen(es) de balanza")
    if b.get("reemplazos"):
        partes.append(f"{len(b['reemplazos'])} resumen(es) actualizado(s)")
    return RedirectResponse(
        "/buques?saved=" + quote_plus(" · ".join(partes) or "Sin novedades"),
        status_code=303)


# Columnas que se pueden mandar, con cuáles vienen tildadas de entrada.
# El orden es el de la tabla; lo primero es lo que casi siempre se manda.
COLUMNAS_COMPARTIR = [
    # Cuántas toneladas bajan en MTR es el dato que decide camiones, gente y
    # depósito: va tildado siempre y no al final de la fila.
    ("t_mtr", "Toneladas a MTR", True),
    ("estado", "Estado", True),
    ("etb", "ETB", True),
    ("ready", "Ready", False),
    ("etc", "ETC", False),
    ("producto", "Producto", True),
    ("cliente", "Cliente", True),
    ("t_buque", "Total del buque", False),
    ("muelle", "Muelle / posición", False),
    ("agencia", "Agencia", False),
]


@router.get("/compartir", response_class=HTMLResponse)
async def compartir(request: Request, db: Session = Depends(get_db),
                    current_user=Depends(_guard)):
    """Una vista limpia para sacarle captura y mandarla.

    Lo que se manda afuera casi nunca es todo lo que hay: a un cliente le
    importan sus buques, al depósito los de esta semana, a la agencia el ETB y
    nada más. Por eso qué entra se elige acá y no queda fijo.

    Los terminados no se ofrecen: esto es para avisar lo que viene.
    """
    lista = [f for f in fichas(db) if f["estado"] != TERMINADO]
    for f in lista:
        f["slug"] = slug(f["canon"])
    ahora = datetime.now()
    return templates.TemplateResponse(request, "buques/compartir.html", {
        "user": current_user, "fichas": lista,
        "columnas": COLUMNAS_COMPARTIR,
        "hoy": ahora.date(), "hora": ahora.strftime("%H:%M"),
    })


@router.get("/{buque_slug}", response_class=HTMLResponse)
async def ficha(buque_slug: str, request: Request, db: Session = Depends(get_db),
                current_user=Depends(_guard)):
    f = next((x for x in fichas(db) if slug(x["canon"]) == buque_slug), None)
    if f is None:
        raise HTTPException(404)
    f["slug"] = buque_slug

    # Por defecto se muestra el operativo más reciente; con ?op= otro anterior.
    pedido = request.query_params.get("op")
    op = f["resumen"]
    if pedido:
        elegido = next((o for o in f["operativos"] if str(o.id) == pedido), None)
        op = elegido or op
    # El avance por bodega sólo tiene sentido mientras el buque descarga: una
    # vez que llegó el resumen de balanza, los pesajes son la fuente buena.
    av = None
    if f["live"] is not None and f["resumen"] is None:
        try:
            av = avance(db, f["live"])
        except Exception:
            av = None
    return templates.TemplateResponse(request, "buques/ficha.html", {
        "user": current_user, "f": f, "op": op, "av": av,
        "a": analizar(op) if op is not None else None,
        "recorrido": _recorrido(f),
    })
