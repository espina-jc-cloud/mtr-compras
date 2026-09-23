"""
Pruebas de lo que alimenta Próximos Arribos desde el correo.

QUÉ SE PRUEBA Y POR QUÉ ESTO
    Todo lo de acá falla en silencio. Si el asunto de la nominación deja de
    reconocerse, no salta ningún error: simplemente no entra ningún buque, y
    eso se descubre cuando uno amarra sin estar cargado. Si el emparejado de
    nombres se afloja, dos buques distintos se fusionan y se mezclan dos
    operativos. Ninguna de las dos cosas rompe la aplicación.

    Los casos no son inventados: salen de los correos reales de septiembre de
    2026 y del line-up del 21/09.
"""
from datetime import date

import pytest

from app.arribos_mail import (buques_del_asunto, imagen_de_la_tabla,
                              producto_del_asunto, servicios_del_cuerpo)
from app.arribos_sync import COMERCIAL, OPERATIVO, _BASURA, _parecidos
from app.lineup_parser import canon_vessel
from app.nominacion_ocr import _fecha, _numero


# ── El asunto de la nominación ───────────────────────────────────────────────

@pytest.mark.parametrize("asunto, buques, producto", [
    ("NOMINACIÓN MV  OCEAN INNOVATION - MAP", ["MV OCEAN INNOVATION"], "MAP"),
    ("NOMINACIÓN MV OSSA - DAP", ["MV OSSA"], "DAP"),
    # Javier nomina dos buques en un mismo correo.
    ("NOMINACIÓN MV PAIWAN DIAMOND y KYVELI GS - UREA",
     ["MV PAIWAN DIAMOND", "KYVELI GS"], "UREA"),
    # Respondido y reenviado: los prefijos no pueden tapar el buque.
    ("RE: [EXT] RE: NOMINACIÓN MV KOCIEWIE - MAP", ["MV KOCIEWIE"], "MAP"),
    # Al reenviar, Carlos le agrega las fechas al asunto. Quedarse con todo lo
    # que sigue al guión dejaba la mercadería como "MAP 17/18 SEP 2026".
    ("RV: NOMINACIÓN MV  OCEAN INNOVATION - MAP 17/18 SEP 2026",
     ["MV OCEAN INNOVATION"], "MAP"),
])
def test_asunto_de_nominacion(asunto, buques, producto):
    assert buques_del_asunto(asunto) == buques
    assert producto_del_asunto(asunto) == producto


def test_un_asunto_que_no_es_nominacion_no_da_buques():
    assert buques_del_asunto("Cupos de carga NUTRIEN MTR 23/9") == []
    assert buques_del_asunto("RV: LINE UP PUERTO SAN NICOLAS 21-09-26") == []


def test_servicios_salen_de_las_vinetas():
    cuerpo = ('Formalizo la nominación del Buque: "MV OSSA".\n\nServicios:\n\n\n'
              "  *   Desestiba\n  *   Flete corto\n  *   Pesaje\n"
              "  *   Almacenamiento en depósito\n\nA disposición.\n")
    assert servicios_del_cuerpo(cuerpo) == [
        "Desestiba", "Flete corto", "Pesaje", "Almacenamiento en depósito"]


# ── Emparejado de nombres entre la nominación y el line-up ───────────────────

@pytest.mark.parametrize("a, b", [
    # El line-up escribe INOVATION con una N.
    ("MV OCEAN INNOVATION", "OCEAN INOVATION"),
    ("MV OSSA", "OSSA"),
    ("MV PAIWAN DIAMOND", "PAIWAN DIAMOND"),
])
def test_son_el_mismo_buque(a, b):
    assert _parecidos(canon_vessel(a), canon_vessel(b))


@pytest.mark.parametrize("a, b", [
    # Comparten la primera palabra y NO son el mismo buque: si esto se afloja,
    # se fusionan dos operativos distintos.
    ("UNION MARK", "UNION MARINE"),
    ("CAPE GULL", "CAPE GRACE"),
    ("STAR WAVE", "STAR DALMATIA"),
    ("MV OSSA", "MV OSAKA"),
])
def test_no_son_el_mismo_buque(a, b):
    assert not _parecidos(canon_vessel(a), canon_vessel(b))


# ── Qué campos puede pisar el line-up ────────────────────────────────────────

def test_el_lineup_no_manda_sobre_el_producto():
    """El producto y la procedencia los dice el cliente, no el puerto.

    El line-up del 21/09 daba MAP para el MV OSSA y la nominación de Nutrien
    decía DAP. Si estos campos vuelven a la lista de lo operativo, el line-up
    cambia el producto de un buque por un dato de segunda mano.
    """
    assert "mercaderia" in COMERCIAL and "mercaderia" not in OPERATIVO
    assert "procedencia" in COMERCIAL and "procedencia" not in OPERATIVO
    assert {"etb", "ready", "etc", "posicion"} <= set(OPERATIVO)


def test_la_basura_del_pdf_no_es_un_valor():
    # El line-up trae "X" en agencia cuando no la sabe.
    assert "X" in _BASURA and "-" in _BASURA and "" in _BASURA


# ── Lectura de la captura ────────────────────────────────────────────────────

@pytest.mark.parametrize("crudo, esperado", [
    ("4,400.00", 4400.0),        # como lo escribe la planilla en inglés
    ("4.400,00", 4400.0),        # y como se escribiría en castellano
    ("$ 18,500.00", 18500.0),
    ("1100", 1100.0),
    ("", None), (None, None), (0, None),
])
def test_numeros_de_la_planilla(crudo, esperado):
    assert _numero(crudo) == esperado


@pytest.mark.parametrize("crudo, esperado", [
    ("2026-09-17", date(2026, 9, 17)),
    ("17/09/2026", date(2026, 9, 17)),
    ("no es una fecha", None),
    (None, None),
])
def test_fechas_de_la_planilla(crudo, esperado):
    assert _fecha(crudo) == esperado


# ── Cuál de las imágenes del mail es la tabla ────────────────────────────────

def _png(ancho, alto) -> bytes:
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (ancho, alto), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_elige_la_tira_ancha_y_no_el_logo():
    """La captura es una tira; el logo de Nutrien es siempre 512x182.

    En la nominación del MV KOCIEWIE venían seis imágenes y la primera era una
    foto del buque: quedarse con la primera guardaba la imagen equivocada.
    """
    foto  = {"nombre": "foto.jpg", "tipo": "image/jpeg", "datos": _png(706, 233)}
    logo  = {"nombre": "logo.png", "tipo": "image/png",  "datos": _png(512, 182)}
    tabla = {"nombre": "tabla.png", "tipo": "image/png", "datos": _png(1217, 94)}
    assert imagen_de_la_tabla([foto, logo, tabla])["nombre"] == "tabla.png"


def test_sin_ninguna_tira_se_queda_con_la_primera():
    """Mejor mostrar algo que no mostrar nada: la confirma una persona igual."""
    a = {"nombre": "a.png", "tipo": "image/png", "datos": _png(512, 182)}
    b = {"nombre": "b.png", "tipo": "image/png", "datos": _png(400, 300)}
    assert imagen_de_la_tabla([a, b])["nombre"] == "a.png"
    assert imagen_de_la_tabla([]) is None
