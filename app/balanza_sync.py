"""
Trae del correo los resúmenes de buque que manda balanza y los guarda.

CÓMO SE ELIGEN LOS CORREOS
    No por el asunto: balanza lo escribe distinto cada vez ("RESUMEN - MV
    MERAIO (UREA) NUTRIEN", "BUQUE KOZNITZA CAN - CNA", "MACAW ARROW
    (AMSUL -NUTRIEN)") y a la misma casilla llegan también las horas del
    personal, las quincenas de CNA y TYS, el tolling y el stock.

    Se miran todos los Excel que manda y decide el parser: si el archivo tiene
    el encabezado "Operativo: NN)" y la columna de patentes, es un resumen de
    buque; si no, se descarta sin ruido. Es la única regla que no depende de
    cómo alguien redactó el asunto esa mañana.

REENVÍOS Y CIERRES
    El mismo resumen llega varias veces: el original, el "Re:", el "Fwd:", y
    otra vez cuando el buque termina. La segunda vez trae más pesajes que la
    primera. Por eso un operativo ya guardado se reemplaza sólo si el archivo
    nuevo tiene al menos tantos pesajes como el que está: así el cierre pisa al
    parcial y un reenvío viejo no borra lo bueno.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from app.arribos_mail import _abrir, _cuerpo_texto, _fecha as _fecha_mail
from app.asistencia_mail import _texto, config, configurado
from app.balanza_resumen import parsear_resumen
from app.lineup_parser import canon_vessel
from app.models_buques import BuqueOperativo, BuquePesaje

# Los resúmenes de buque pesan decenas de KB. El tolling pesa 6 MB y no hay por
# qué bajarlo una y otra vez para que el parser lo rechace.
MAX_BYTES = 2_000_000
_EXT = (".xlsx", ".xlsm", ".xls")


def _excels(msg) -> list[dict]:
    out = []
    for parte in msg.walk():
        nombre = parte.get_filename() or ""
        if not nombre.lower().endswith(_EXT):
            continue
        datos = parte.get_payload(decode=True) or b""
        if not datos or len(datos) > MAX_BYTES:
            continue
        out.append({"nombre": _texto(nombre).strip(), "datos": datos})
    return out


def buscar_resumenes(limite: int = 200, dias: int | None = None) -> dict:
    """Los resúmenes de buque del buzón, ya parseados.

    El buzón se abre en solo lectura, igual que el de las horas.
    """
    dias = dias or int(os.getenv("BALANZA_MAIL_DIAS", "30") or 30)
    remitente = os.getenv("BALANZA_MAIL_REMITENTE", "balanzamtr").strip()
    c = config()
    if not configurado():
        return {"ok": False, "error": "El buzón no está configurado.", "resumenes": []}

    from datetime import timedelta, timezone
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%d-%b-%Y")
    try:
        m = _abrir(c)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "resumenes": []}

    try:
        estado, datos = m.search(None, f'(SINCE {desde} FROM "{remitente}")')
        if estado != "OK":
            return {"ok": False, "error": "La búsqueda IMAP falló.", "resumenes": []}
        ids = (datos[0] or b"").split()
        out, mirados = [], 0
        import email as _email
        for uid in reversed(ids):
            if len(out) >= limite:
                break
            estado, bruto = m.fetch(uid, "(BODY.PEEK[])")
            if estado != "OK" or not bruto or not isinstance(bruto[0], tuple):
                continue
            msg = _email.message_from_bytes(bruto[0][1])
            mirados += 1
            for x in _excels(msg):
                r = parsear_resumen(x["datos"], x["nombre"])
                if r is None:
                    continue                 # no es un resumen de buque
                r.update(
                    mail_message_id=(msg.get("Message-ID")
                                     or f"uid:{uid.decode()}").strip(),
                    mail_asunto=_texto(msg.get("Subject")),
                    mail_fecha=_fecha_mail(msg))
                out.append(r)
        return {"ok": True, "error": None, "resumenes": out, "mirados": mirados}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "resumenes": []}
    finally:
        try:
            m.logout()
        except Exception:
            pass


def _guardar(db, r: dict):
    """Guarda un resumen. Devuelve ('alta'|'reemplazo'|'ya_estaba', operativo)."""
    canon = canon_vessel(r["buque"])
    ya = (db.query(BuqueOperativo)
          .filter(BuqueOperativo.buque_canon == canon,
                  BuqueOperativo.operativo_nro == r.get("operativo_nro"),
                  BuqueOperativo.inicio == r.get("inicio"))
          .first())
    if ya is not None and len(r["tickets"]) < (ya.viajes or 0):
        return "ya_estaba", ya               # un reenvío viejo no pisa el cierre

    accion = "alta" if ya is None else "reemplazo"
    op = ya or BuqueOperativo(buque_canon=canon)
    if ya is not None:
        # Vaciar la colección, no borrar los hijos uno por uno: con
        # delete-orphan el vaciado ya los elimina, y el borrado manual deja al
        # padre apuntando a objetos muertos que SQLAlchemy después intenta
        # volver a guardar ("Instance has been deleted").
        ya.pesajes.clear()
        # El flush es obligatorio: dentro de una misma transacción los INSERT
        # van antes que los DELETE, así que sin esto los pesajes nuevos chocan
        # contra los viejos por (operativo, nro). Pasa siempre que el mismo
        # resumen llega dos veces, que es lo normal — el original y su "Re:".
        db.flush()

    op.buque = r["buque"]
    op.buque_crudo = r.get("buque_crudo")
    op.operativo_nro = r.get("operativo_nro")
    op.cliente = r.get("cliente")
    op.producto = r.get("producto")
    op.inicio = r.get("inicio")
    op.fin = r.get("fin")
    op.cerrado = 1 if r.get("fin") else 0
    op.viajes = len(r["tickets"])
    op.neto_kg = sum(t["neto"] or 0 for t in r["tickets"])
    op.origen_kg = sum(t["origen"] or 0 for t in r["tickets"])
    op.archivo = r.get("archivo")
    op.mail_message_id = r.get("mail_message_id")
    op.mail_asunto = r.get("mail_asunto")
    op.mail_fecha = (r.get("mail_fecha").replace(tzinfo=None)
                     if r.get("mail_fecha") else None)
    op.avisos = "\n".join(r.get("avisos") or []) or None
    db.add(op)
    db.flush()

    op.pesajes.extend(
        BuquePesaje(
            nro=t["nro"], patente=t["patente"],
            turno=t["turno"], transporte=t["transporte"],
            entrada=t["entrada"], salida=t["salida"],
            tara=t["tara"], bruto=t["bruto"], neto=t["neto"], origen=t["origen"])
        for t in r["tickets"])
    return accion, op


def sincronizar(db, limite: int = 200, dias: int | None = None) -> dict:
    r = buscar_resumenes(limite=limite, dias=dias)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "altas": [], "reemplazos": []}

    altas, reemplazos, fallados, iguales = [], [], [], 0
    # Del más viejo al más nuevo: si el mismo buque llegó parcial y después
    # completo, el completo tiene que entrar último y quedar.
    for resumen in sorted(r["resumenes"],
                          key=lambda x: (x.get("mail_fecha") or datetime.min
                                         ).replace(tzinfo=None)):
        # Cada resumen en su propia transacción anidada: un Excel raro no
        # puede tirar abajo la importación de los otros cincuenta.
        try:
            with db.begin_nested():
                accion, op = _guardar(db, resumen)
            if accion == "alta":
                altas.append(op)
            elif accion == "reemplazo":
                reemplazos.append(op)
            else:
                iguales += 1
        except Exception as e:
            fallados.append({"archivo": resumen.get("archivo"),
                             "buque": resumen.get("buque"),
                             "error": f"{type(e).__name__}: {e}"[:200]})
    db.commit()
    return {"ok": True, "error": None, "altas": altas, "reemplazos": reemplazos,
            "fallados": fallados, "iguales": iguales,
            "mirados": r.get("mirados", 0), "resumenes": len(r["resumenes"])}
