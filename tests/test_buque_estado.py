"""
Pruebas del estado derivado del buque.

EL BUG QUE VINO A ARREGLAR
    El MV KOCIEWIE terminó de descargar el 11/09/2026 —balanza mandó el resumen
    con 67 viajes y 1.964 t— y Próximos Arribos lo seguía mostrando como que
    venía. El OCEAN INNOVATION figuraba a la vez como esperado, cerrado y en
    curso, según en qué pantalla se mirara.

    No era un error de carga: cada módulo guardaba su propio estado y nadie los
    conciliaba. Estas pruebas fijan el criterio que los reemplazó — gana la
    evidencia más fuerte, y un hecho le gana a una intención.

POR QUÉ ESTO FALLA EN SILENCIO
    Un estado mal derivado no rompe nada: la pantalla abre igual y muestra un
    número. Simplemente miente, y se descubre cuando alguien va a buscar un
    barco que ya se fue.
"""
from datetime import date, timedelta

import pytest

from app.buque_ficha import (CANCELADO, CONFIRMADO, DESCARGANDO, ESPERADO,
                             TERMINADO, DIAS_SIN_MOVIMIENTO, estado_de,
                             panorama)

HOY = date(2026, 9, 23)


class _Arribo:
    def __init__(self, estado="esperado", etb=None, fecha_estimada=None):
        self.estado, self.etb, self.fecha_estimada = estado, etb, fecha_estimada


class _Live:
    def __init__(self, status="active"):
        self.status = status


class _Resumen:
    def __init__(self, cerrado=True):
        self.cerrado = 1 if cerrado else 0


def test_el_resumen_de_balanza_le_gana_al_arribo():
    """El caso KOCIEWIE: balanza pesó cada camión, el arribo es una intención."""
    assert estado_de(_Arribo("esperado"), None, _Resumen(cerrado=True),
                     ultima=date(2026, 9, 11), hoy=HOY) == TERMINADO


def test_un_operativo_abierto_sin_movimiento_reciente_ya_termino():
    """Tres resúmenes quedaron sin "Fecha Finalizacion" porque nunca llegó la
    versión final. IC PROGRESS es de noviembre de 2025: no está descargando."""
    vieja = HOY - timedelta(days=DIAS_SIN_MOVIMIENTO + 1)
    assert estado_de(None, _Live("active"), _Resumen(cerrado=False),
                     ultima=vieja, hoy=HOY) == TERMINADO


def test_con_movimiento_en_los_ultimos_dias_si_esta_descargando():
    reciente = HOY - timedelta(days=DIAS_SIN_MOVIMIENTO)
    assert estado_de(None, _Live("active"), _Resumen(cerrado=False),
                     ultima=reciente, hoy=HOY) == DESCARGANDO
    assert estado_de(None, _Live("active"), None,
                     ultima=HOY, hoy=HOY) == DESCARGANDO


def test_un_live_viejo_sin_resumen_tambien_esta_terminado():
    """MV STAR DALMATIA: un turno de junio y nadie cerró el operativo."""
    assert estado_de(None, _Live("active"), None,
                     ultima=date(2026, 6, 3), hoy=HOY) == TERMINADO


@pytest.mark.parametrize("arribo, esperado", [
    (_Arribo("esperado"), ESPERADO),
    (_Arribo("esperado", etb="23/9/2026 AM"), CONFIRMADO),
    (_Arribo("confirmado", fecha_estimada=date(2026, 10, 2)), CONFIRMADO),
    (_Arribo("cancelado"), CANCELADO),
])
def test_sin_pesajes_manda_lo_que_dice_el_arribo(arribo, esperado):
    assert estado_de(arribo, None, None, ultima=None, hoy=HOY) == esperado


def test_un_buque_del_que_no_se_sabe_nada_esta_esperado():
    assert estado_de(None, None, None, ultima=None, hoy=HOY) == ESPERADO


def test_el_panorama_cuenta_solo_el_mes_en_curso():
    """Las toneladas del mes son del mes: un buque de agosto no suma."""
    f = [
        {"estado": TERMINADO, "fecha": date(2026, 9, 11), "toneladas": 1964},
        {"estado": TERMINADO, "fecha": date(2026, 9, 3), "toneladas": 1795},
        {"estado": TERMINADO, "fecha": date(2026, 8, 31), "toneladas": 866},
        {"estado": DESCARGANDO, "fecha": date(2026, 9, 21), "toneladas": 1890},
        {"estado": CONFIRMADO, "fecha": date(2026, 10, 2), "toneladas": None},
        {"estado": ESPERADO, "fecha": None, "toneladas": None},
    ]
    p = panorama(f, hoy=HOY)
    assert p == {"descargando": 1, "por_venir": 2,
                 "terminados_mes": 2, "toneladas_mes": 1964 + 1795}
