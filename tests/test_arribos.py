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
from app.arribos_sync import (COMERCIAL, DIAS_PARA_DARLO_POR_PASADO, OPERATIVO,
                               _BASURA, _parecidos, cerrar_los_que_ya_pasaron)
from app.lineup_parser import canon_vessel
# ProximoArribo tiene una relación con User: sin importar los modelos enteros,
# SQLAlchemy no puede resolver ese nombre al construir el objeto.
from app import models  # noqa: F401
from app.models_arribos import ProximoArribo


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


# ── Cerrar los que ya pasaron ────────────────────────────────────────────────

class _FakeQuery:
    """Un query mínimo: filter() encadenable y all() que devuelve la lista."""
    def __init__(self, filas):
        self._filas = filas

    def filter(self, *_):
        return self

    def all(self):
        return self._filas


class _FakeDB:
    def __init__(self, filas):
        self._filas = filas
        self.agregados = []

    def query(self, *_):
        return _FakeQuery(self._filas)

    def add(self, x):
        self.agregados.append(x)


def test_cierra_el_que_quedo_meses_atras():
    """El MV TAI HONOR de junio no puede seguir encabezando "Atrasados".

    Una alerta que siempre está encendida deja de avisar: se aprende a
    ignorarla, y con ella el buque que sí se demoró de verdad.
    """
    viejo = ProximoArribo(buque="MV TAI HONOR", buque_canon="TAI HONOR",
                          estado="esperado", fecha_estimada=date(2026, 6, 27))
    db = _FakeDB([viejo])
    cerrados = cerrar_los_que_ya_pasaron(db, hoy=date(2026, 9, 23))
    assert cerrados == [viejo]
    assert viejo.estado == "finalizado"
    assert db.agregados, "tiene que quedar registrado por qué se cerró"


def test_el_margen_es_de_una_semana():
    """Una descarga dura días: siete es margen de sobra para una demora larga.

    El filtro corre en SQL, así que acá se verifica la constante — bajarla a
    dos días cerraría buques que todavía están descargando.
    """
    assert DIAS_PARA_DARLO_POR_PASADO == 7


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


def test_sin_ninguna_tira_no_guarda_nada():
    """La nominación del MV MERAIO no traía tabla y se guardó la firma de MTR.

    Quedaba una imagen inútil rotulada "captura de la nominación" y una
    llamada al modelo que no podía salir bien. Vale más decir que no llegó.
    """
    firma = {"nombre": "firma.png", "tipo": "image/png", "datos": _png(637, 209)}
    logo = {"nombre": "logo.png", "tipo": "image/png", "datos": _png(512, 182)}
    assert imagen_de_la_tabla([firma, logo]) is None
    assert imagen_de_la_tabla([]) is None


# ── Toneladas del line-up ────────────────────────────────────────────────────

@pytest.mark.parametrize("crudo, esperado", [
    ("23.158", 23158.0),     # el PDF usa punto de miles: no son 23 toneladas
    ("1.506", 1506.0),
    ("349", 349.0),
    ("1.234,5", 1234.5),     # y coma decimal
    ("", None), ("X", None), ("0,5", None),
])
def test_toneladas_del_lineup(crudo, esperado):
    from app.lineup_parser import parse_tons
    assert parse_tons(crudo) == esperado


def test_el_tonelaje_propio_no_es_el_del_buque():
    """Un buque trae carga de varios operadores, y sólo parte es nuestra.

    El OCEAN INNOVATION declara 24.750 t en el line-up del 21/09 y sólo 4.400
    las opera MTR —las mismas 4.400 que decía la nominación de Nutrien—.
    Mostrar el total como si fuera nuestro sería inflar el número por seis.
    """
    from app.lineup_parser import _sumar_tons
    buque = {"tons": None, "tons_mtr": None}
    filas = [
        ["OCEAN INOVATION", "MARSA", "MAP", "BUNGE", "9.900", "PTP"],
        ["", "", "MAP", "NUTRIEN", "4.400", "MTR"],
        ["", "", "UREA", "OTRO", "10.450", "PTP"],
    ]
    for f in filas:
        _sumar_tons(buque, f)
    assert buque["tons"] == 24750.0
    assert buque["tons_mtr"] == 4400.0


def test_el_lineup_no_pisa_un_tonelaje_ya_cargado():
    """Si alguien ya lo puso a mano, el PDF no lo corrige."""
    from app.arribos_sync import TONELAJES
    assert set(TONELAJES) == {"tonelaje_estimado", "tonelaje_mtr"}
    codigo = open("app/arribos_sync.py", encoding="utf-8").read()
    assert "getattr(a, campo) is not None" in codigo


# ── Toneladas a MTR: de dónde salen y quién le gana a quién ─────────────────

def test_la_fila_de_la_captura_se_asigna_al_buque_correcto():
    """La captura puede traer dos buques nominados en el mismo mail."""
    from app.arribos_sync import _fila_para
    filas = [{"buque": "MV PAIWAN DIAMOND", "mt_mtr": 2000.0},
             {"buque": "KYVELI GS", "mt_mtr": 6000.0}]
    assert _fila_para(filas, "KYVELI GS")["mt_mtr"] == 6000.0
    assert _fila_para(filas, "MV PAIWAN DIAMOND")["mt_mtr"] == 2000.0
    assert _fila_para(filas, "MV OSSA") is None


def test_el_lineup_no_pisa_las_toneladas_que_declaro_el_cliente():
    """Nutrien dice cuánto baja en MTR; el puerto informa todo el buque.

    El OCEAN INNOVATION declara 24.750 t en el line-up y 4.400 en la
    nominación. Si el line-up pisara ese número, el depósito se prepararía
    para seis veces la carga que va a recibir.
    """
    codigo = open("app/arribos_sync.py", encoding="utf-8").read()
    # Sólo completa lo que está vacío...
    assert "if not nuevo or getattr(a, campo) is not None:" in codigo
    # ...y deja constancia de que el número lo puso el puerto.
    assert 'a.tonelaje_origen = "lineup"' in codigo


def test_la_nominacion_marca_su_propio_origen():
    codigo = open("app/arribos_sync.py", encoding="utf-8").read()
    assert 'tonelaje_origen="nominacion" if fila.get("mt_mtr") else None' in codigo


def test_las_toneladas_a_mtr_van_primero_y_tildadas_al_compartir():
    """Es el dato que decide camiones, gente y depósito: no puede quedar
    escondido detrás de un checkbox que nadie tilda."""
    from app.routers.buques import COLUMNAS_COMPARTIR
    clave, etiqueta, por_defecto = COLUMNAS_COMPARTIR[0]
    assert clave == "t_mtr" and por_defecto is True
    assert "MTR" in etiqueta
