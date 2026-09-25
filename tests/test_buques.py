"""
Pruebas del resumen de buque que manda balanza.

QUÉ SE PRUEBA Y POR QUÉ ESTO
    Las dos cosas que fallan sin hacer ruido.

    La primera es el reconocimiento del archivo. A la misma casilla llegan las
    horas del personal, las quincenas de CNA y TYS, el tolling y el stock. Si
    el reconocimiento se afloja, una quincena entra como si fuera un buque y
    suma toneladas que nunca existieron; si se endurece de más, el resumen del
    buque no entra y nadie se entera hasta que alguien pregunta por él.

    La segunda es el conteo. Algunos archivos traen el detalle por turno y
    además una sección "Todos" que repite todo: sin deduplicar, ese operativo
    cuenta el doble de viajes y el doble de toneladas, y el número queda mal
    para siempre porque nadie lo va a revisar contra el Excel.
"""
from datetime import datetime, timedelta

import pytest

from app.balanza_resumen import _sin_repetidos, limpiar_buque
from app.buque_analitica import TOPE_PERMANENCIA, analizar


# ── El nombre del buque ──────────────────────────────────────────────────────

@pytest.mark.parametrize("crudo, cliente, esperado", [
    # Balanza le pega la fecha al nombre del operativo.
    ("MV TAI HONOR (28-06-26)", "NUTRIEN AG SOLUTIONS", "MV TAI HONOR"),
    ("OCEAN INNOVATION 21.09.26", "NUTRIEN AG SOLUTIONS", "OCEAN INNOVATION"),
    # Y a veces el cliente, entre paréntesis o pegado.
    ("MV KOZNITZA (CNA)", "C.N.A. S.A.", "MV KOZNITZA"),
    ("MV SOFIA MOVIPORT (MAP) 14.07.26", "MOVIPORT S.A.", "MV SOFIA"),
    ("M/V MACAW ARROW", "NUTRIEN AG SOLUTIONS", "M/V MACAW ARROW"),
])
def test_nombre_del_buque_sin_la_fecha_ni_el_cliente(crudo, cliente, esperado):
    """Sin limpiarlo, el mismo buque en dos operativos no se reconoce igual."""
    assert limpiar_buque(crudo, cliente) == esperado


# ── Pesajes repetidos ────────────────────────────────────────────────────────

def _ticket(nro, grupo):
    return {"nro": nro, "grupo": grupo, "patente": "AAA111"}


def test_un_pesaje_repetido_se_cuenta_una_sola_vez():
    """El mismo ticket bajo su turno y otra vez bajo "Todos" es uno solo."""
    tickets = [_ticket(1, "0 a 6hs"), _ticket(2, "6 a 12hs"),
               _ticket(1, "Todos"), _ticket(2, "Todos")]
    assert len(_sin_repetidos(tickets)) == 2


def test_gana_el_que_dice_el_turno_real():
    """"Todos" no informa nada: si existe el turno concreto, ése queda."""
    assert _sin_repetidos([_ticket(1, "Todos"), _ticket(1, "18 a 24hs")]
                          )[0]["grupo"] == "18 a 24hs"
    assert _sin_repetidos([_ticket(1, "18 a 24hs"), _ticket(1, "Todos")]
                          )[0]["grupo"] == "18 a 24hs"


# ── Analítica ────────────────────────────────────────────────────────────────

class _Pesaje:
    def __init__(self, neto, origen, entrada, minutos, transporte, turno, patente):
        self.neto, self.origen = neto, origen
        self.entrada = entrada
        self.salida = entrada + timedelta(minutes=minutos) if minutos is not None else None
        self.transporte, self.turno, self.patente = transporte, turno, patente


class _Operativo:
    def __init__(self, pesajes):
        self.pesajes = pesajes


def _operativo():
    base = datetime(2026, 8, 20, 6, 0)
    return _Operativo([
        _Pesaje(30000, 29900, base, 20, "DONATI", "6 a 12hs", "AAA111"),
        _Pesaje(28000, 28000, base + timedelta(hours=1), 10, "DONATI", "6 a 12hs", "AAA111"),
        _Pesaje(32000, 32100, base + timedelta(hours=2), 40, "LENTINI", "6 a 12hs", "BBB222"),
        # Un camión que quedó adentro ocho horas: no es una espera, es un error
        # de carga. Tiene que quedar afuera de la mediana.
        _Pesaje(10000, 10000, base + timedelta(days=1), 480, "LENTINI", "0 a 6hs", "BBB222"),
    ])


def test_los_totales_salen_de_los_pesajes():
    a = analizar(_operativo())
    assert a["viajes"] == 4
    assert a["neto_t"] == 100.0                      # 30+28+32+10
    assert a["diferencia_kg"] == 100000 - 100000
    assert a["camiones_distintos"] == 2


def test_la_permanencia_ignora_al_que_quedo_adentro_ocho_horas():
    """Un outlier arrastra el promedio; por eso van mediana y p90.

    Con el de 480 minutos adentro, cualquier promedio diría que un camión
    tarda más de dos horas, cuando la realidad son veinte minutos.
    """
    a = analizar(_operativo())
    assert a["permanencia"]["medidos"] == 3
    assert a["permanencia"]["largos"] == 1
    assert a["permanencia"]["mediana"] == 20
    assert a["permanencia"]["minimo"] == 10
    assert timedelta(minutes=480) > TOPE_PERMANENCIA


def test_el_reparto_por_transportista_suma_cien():
    """Ordenado por toneladas, no por viajes: los dos hicieron 2 viajes y el
    que movió más carga tiene que salir primero."""
    a = analizar(_operativo())
    assert [t["nombre"] for t in a["transportes"]] == ["DONATI", "LENTINI"]
    assert [t["t"] for t in a["transportes"]] == [58.0, 42.0]
    assert sum(t["viajes"] for t in a["transportes"]) == 4
    assert abs(sum(t["pct"] for t in a["transportes"]) - 100) <= 1


def test_un_operativo_sin_pesajes_no_revienta():
    """Puede pasar con un archivo recortado: la pantalla tiene que abrir igual."""
    a = analizar(_Operativo([]))
    assert a["viajes"] == 0 and a["neto_t"] == 0
    assert a["permanencia"]["mediana"] == 0
    assert a["transportes"] == [] and a["dias"] == []


# ── El parte que no nombra la bodega ─────────────────────────────────────────

def test_se_infiere_la_bodega_solo_cuando_queda_una(monkeypatch):
    """Al final del operativo Carlos escribe "MTR 37 VIAJES ... KG" a secas.

    El MACURU ARROW perdió 2.747.300 kg en sus tres últimos turnos porque el
    movimiento se descartaba por no tener bodega, y el faltante no lo avisaba
    nada: el turno quedaba creado en cero.

    Con una sola bodega abierta no hay ambigüedad. Con dos, imputar al azar
    sería peor que perder el dato, así que se sigue descartando.
    """
    import app.live_avance
    from app.routers.operations_live import _bodega_unica_abierta

    class _DB:
        def get(self, *_):
            return object()

    def con(bodegas, sin_plan=False):
        monkeypatch.setattr(app.live_avance, "avance",
                            lambda db, s: {"sin_plan": sin_plan, "bodegas": bodegas})
        return _bodega_unica_abierta(_DB(), 1)

    assert con([{"numero": 3, "resta": 1200.0}, {"numero": 4, "resta": 0.0}]) == 3
    assert con([{"numero": 3, "resta": 1200.0}, {"numero": 4, "resta": 800.0}]) is None
    assert con([{"numero": 3, "resta": 0.0}]) is None
    # Sin plan de estiba no se sabe qué bodega queda abierta.
    assert con([{"numero": 3, "resta": 1200.0}], sin_plan=True) is None
