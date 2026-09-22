"""
Carga en Operativos en Tiempo Real los dos buques de septiembre de 2026:
MV MACURU ARROW y MV OCEAN INNOVATION.

DE DÓNDE SALEN LOS DATOS
    Plan de estiba: hoja APUNTADORES del Excel del despachante.
    Partes de turno: el export del grupo de WhatsApp "Operaciones MTR", leído
    con el mismo parser que usa la pantalla de pegar parte.

POR QUÉ ES UN SCRIPT Y NO UNA MIGRACIÓN
    Es carga de datos de dos operativos concretos, no un cambio de esquema.
    Se corre una vez y se puede repetir: es idempotente por nombre de buque —
    si el operativo ya existe, lo deja como está y no duplica nada.

UNA CORRECCIÓN APLICADA
    El parte del 21/09 turno 12–18 del Macuru informó 43 viajes de barita en la
    bodega 7, que había cerrado el 20/09 a las 02:47. Por el peso por bolsón
    (1.414 kg, que es barita de 1,5 t) y porque la barita está en la bodega 6,
    esos viajes se cargan en la 6. Sin la corrección la bodega 7 cierra en
    136 % del plan y la 6 en 76 %; con ella, en 101 % y 102 %.

Uso:
    DATABASE_URL=$DATABASE_PUBLIC_URL .venv/bin/python \\
        scripts/cargar_buques_septiembre.py buques.json
"""
import json
import sys
from datetime import date

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from app.database import SessionLocal          # noqa: E402
from app import models                          # noqa: E402,F401  (registra Operation)
from app.live_partes import CAMPO               # noqa: E402
from app.models_live import (                   # noqa: E402
    OperationLiveBodegaData,
    OperationLiveSession,
    OperationLiveSessionProduct,
    OperationLiveShift,
    OperationLiveStowItem,
)


def _producto_de(plan, bodega, destino, ya_descargado_t):
    """Qué producto salía de esa bodega en ese momento, según el plan.

    Misma regla que usa la pantalla de pegar parte: el primer ítem de la bodega
    que todavía no terminó de salir, del lado que corresponde (nuestro o ajeno).
    """
    lista = sorted([i for i in plan if i["bodega"] == bodega],
                   key=lambda i: i["orden"])
    if not lista:
        return "SIN DETALLAR", None
    nuestro = destino != "TERCERO"
    candidatos = [i for i in lista if (i["destino"] != "TERCERO") == nuestro]
    acum = 0.0
    for it in lista:
        acum += it["mt"]
        if acum > ya_descargado_t and it in candidatos:
            return it["producto"], it["cliente"]
    elegido = candidatos[-1] if candidatos else lista[-1]
    return elegido["producto"], elegido["cliente"]


def cargar(db, buque: dict) -> str:
    ya = (db.query(OperationLiveSession)
          .filter(OperationLiveSession.ship_name == buque["nombre"]).first())
    if ya:
        return f"{buque['nombre']}: ya existe (id {ya.id}), no se toca"

    ses = OperationLiveSession(
        ship_name=buque["nombre"],
        status=buque.get("status", "active"),
        created_by="carga inicial",
        tercero_nombre=buque.get("tercero"),
    )
    db.add(ses)
    db.flush()

    for it in buque["plan"]:
        db.add(OperationLiveStowItem(
            session_id=ses.id, bodega_number=it["bodega"], orden=it["orden"],
            product=it["producto"], client=it["cliente"],
            mt_net=it["mt"], unidades=it.get("unidades"),
            destino=it["destino"],
        ))

    productos = set()
    descargado = {}          # bodega -> toneladas acumuladas, para ubicar el producto
    partes = sorted(buque["partes"], key=lambda x: (x["fecha"], x["turno"]))
    for n, p in enumerate(partes, 1):
        desde, hasta = p["turno"].split("-")
        turno = OperationLiveShift(
            session_id=ses.id, shift_number=n,
            shift_date=date.fromisoformat(p["fecha"]),
            shift_start=f"{int(desde):02d}:00",
            shift_end=f"{int(hasta) % 24:02d}:00",
            status="closed", supervisor_mtr=p.get("quien"),
            notes="Parte del grupo de WhatsApp.",
        )
        db.add(turno)
        db.flush()

        for m in p["mov"]:
            if not m.get("bodega"):
                continue
            prod, cli = _producto_de(buque["plan"], m["bodega"], m["destino"],
                                     descargado.get(m["bodega"], 0.0))
            if prod not in productos:
                db.add(OperationLiveSessionProduct(
                    session_id=ses.id, product=prod, client=cli))
                productos.add(prod)
            fila = OperationLiveBodegaData(
                shift_id=turno.id, bodega_number=m["bodega"], product=prod,
                viajes_mtr=m["viajes"],
                kg_deposito_mtr=0, kg_directo_mtr=0, kg_cv_mtr=0, kg_tercero=0,
            )
            setattr(fila, CAMPO[m["destino"]], m["kg"] or 0)
            db.add(fila)
            descargado[m["bodega"]] = (descargado.get(m["bodega"], 0.0)
                                       + (m["kg"] or 0) / 1000)

    db.commit()
    kg = sum(m["kg"] or 0 for p in buque["partes"] for m in p["mov"])
    return (f"{buque['nombre']}: id {ses.id} · {len(buque['plan'])} productos del "
            f"plan · {len(partes)} turnos · {kg/1000:,.0f} t descargadas")


def main(path):
    datos = json.load(open(path, encoding="utf-8"))
    db = SessionLocal()
    try:
        for buque in datos["buques"]:
            print(" ", cargar(db, buque))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "buques.json")
