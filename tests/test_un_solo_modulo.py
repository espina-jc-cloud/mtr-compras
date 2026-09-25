"""
Que haya un solo módulo de buques, y que sacarlo del menú no rompa el día a día.

QUÉ CUIDA
    Se sacaron tres puertas de entrada —Próximos Arribos, Operativos Live y
    Operativos— porque mostraban el mismo barco con tres estados distintos y
    obligaban a saltar entre pantallas para entender un solo operativo.

    El riesgo de esa clase de limpieza es cortar algo que alguien usa todos los
    días sin que nadie se entere hasta que lo necesita. Pegar el parte del
    turno, mirar el avance por bodega, dar de alta un buque a mano e importar
    el line-up tienen que seguir respondiendo: ya no están en el menú, pero se
    llega a ellos desde la ficha del buque.

POR QUÉ FALLA EN SILENCIO
    Una ruta que desaparece no rompe ningún test que no la nombre: la
    aplicación arranca igual y el resto del sistema anda. Se descubre a las
    seis de la mañana, cuando el jefe de turno va a pegar el parte.
"""
import pytest
from fastapi.routing import APIRoute


@pytest.fixture(scope="module")
def rutas():
    from app.main import app
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


@pytest.mark.parametrize("vieja", [
    "/operations",            # Operativos
    "/operations/live",       # Operativos Live
    "/operations/arribos",    # Próximos Arribos
])
def test_las_listas_viejas_llevan_a_buques(vieja):
    """No devuelven 404: redirigen, así un favorito viejo no queda muerto."""
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.get(vieja, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/buques"


@pytest.mark.parametrize("ruta", [
    "/buques",                          # la pantalla única
    "/buques/{buque_slug}",             # la ficha del buque
    "/operations/live/{sid}/parte",     # pegar el parte del turno
    "/operations/live/{sid}/avance",    # el avance por bodega
    "/operations/live/new",             # arrancar un operativo
    "/operations/arribos/new",          # cargar un buque a mano
    "/operations/arribos/{arribo_id}/edit",
    "/operations/arribos/import",       # importar el line-up PDF
])
def test_lo_que_se_usa_todos_los_dias_sigue_existiendo(rutas, ruta):
    assert ruta in rutas


def test_el_menu_tiene_un_solo_modulo_de_buques():
    """Si vuelve a aparecer otra entrada de barcos, la pantalla se parte de nuevo."""
    menu = open("templates/base.html", encoding="utf-8").read()
    for viejo in ('item("/operations/live"', 'item("/operations"',
                  'item("/operations/arribos"', 'item("/operations/buques"'):
        assert viejo not in menu, f"volvió al menú: {viejo}"
    assert 'item("/buques"' in menu


# ── Vista para compartir ─────────────────────────────────────────────────────

def test_compartir_no_se_confunde_con_la_ficha_de_un_buque():
    """/buques/compartir tiene que ganarle a /buques/{slug}.

    Si el orden de las rutas se invierte, FastAPI trata "compartir" como el
    nombre de un buque, no lo encuentra y devuelve 404. No rompe nada más: el
    botón simplemente deja de funcionar.
    """
    from app.main import app
    from app.routers.buques import compartir, ficha
    rutas = [r for r in app.routes if getattr(r, "path", "").startswith("/buques/")]
    orden = {r.endpoint: i for i, r in enumerate(rutas) if hasattr(r, "endpoint")}
    assert orden[compartir] < orden[ficha]


def test_los_controles_no_salen_en_la_captura():
    """Lo que se manda afuera es la tarjeta sola: sin filtros ni menú."""
    html = open("templates/buques/compartir.html", encoding="utf-8").read()
    assert "body.modo-captura .no-captura" in html
    assert "body.modo-captura #sidebar" in html
    # Y la tabla no puede quedar cortada a la derecha en la foto.
    assert "overflow-x: visible" in html


def test_la_seleccion_viaja_en_el_link():
    """Para poder reabrir mañana la misma vista sin rearmarla."""
    html = open("templates/buques/compartir.html", encoding="utf-8").read()
    assert "history.replaceState" in html and "URLSearchParams" in html
