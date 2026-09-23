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
