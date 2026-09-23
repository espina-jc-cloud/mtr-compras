"""
Alimenta Próximos Arribos desde el correo: nominaciones de Nutrien y line-ups.

LAS DOS FUENTES NO HACEN LO MISMO
    La NOMINACIÓN es la que da de alta: es Nutrien diciendo "este buque viene y
    lo trabajan ustedes". Del correo se toma lo que está escrito: el buque, el
    producto y los servicios.

    EL ETB NO SE LEE DEL CORREO
        Viene dentro de una captura de pantalla de la planilla de Javier, y
        sacarlo de ahí requiere un modelo de visión. No vale la pena: la fecha
        llega igual en el próximo line-up, que es texto y se lee sin adivinar.
        La captura se guarda con el arribo para poder mirarla y escribir el ETB
        a mano si hace falta antes.

    El LINE-UP no da de alta a nadie. Es la programación del puerto entero, con
    quince o veinte buques que en su mayoría no son nuestros. Lo que hace es
    ACTUALIZAR los que ya seguimos: corrige el ETB, agrega el ready, el ETC, la
    posición y el muelle. Si diera de alta, la pantalla se llenaría del
    movimiento de todo San Nicolás y dejaría de servir.

QUÉ PASA CUANDO LOS NOMBRES NO COINCIDEN
    La nominación dice "MV OCEAN INNOVATION" y el line-up de ese mismo buque
    dice "OCEAN INOVATION", con una N menos. Comparar textual no alcanza y
    comparar de más es peor: unir dos buques distintos mezcla dos operativos.
    Por eso se exige un parecido alto (0,90) y que arranquen igual.

IDEMPOTENTE
    Se puede correr cuantas veces se quiera. Un buque ya dado de alta no se
    duplica, y un campo que alguien corrigió a mano no lo pisa el line-up.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from app.arribos_mail import (buscar_lineups, buscar_nominaciones,
                              imagen_de_la_tabla)
from app.lineup_parser import canon_vessel, parse_lineup_pdf
from app.models_arribos import ArriboUpdate, ProximoArribo
PARECIDO_MINIMO = 0.90

# Días después de la fecha prevista tras los cuales un buque que sigue
# "esperado" ya no está esperado: pasó. Una descarga dura días, no semanas, así
# que siete es margen de sobra incluso para una demora larga.
DIAS_PARA_DARLO_POR_PASADO = 7

# Lo operativo es del puerto: cuándo amarra, dónde y con qué agencia. Acá el
# line-up manda siempre, porque es la programación oficial.
OPERATIVO = {"ready": "ready", "etb": "etb", "etc": "etc",
             "posicion": "posicion", "muelle": "muelle",
             "agencia": "agencia", "operacion": "operacion"}

# Lo comercial es del cliente: qué trae y de dónde. Acá el line-up sólo
# completa lo que está vacío, nunca corrige.
#     El line-up del 21/09 daba MAP para el MV OSSA y la nominación de Nutrien
#     decía DAP. Nutrien sabe qué embarcó; el line-up del puerto se copia de la
#     agencia y se equivoca. Pisarlo habría cambiado el producto de un buque
#     por un dato de segunda mano.
COMERCIAL = {"mercaderia": "material", "procedencia": "origen"}

# Valores que el PDF trae como relleno y no significan nada.
_BASURA = {"", "-", "—", "X", "S/D", "N/D"}


def _parecidos(a: str, b: str) -> bool:
    """Si dos nombres de buque son el mismo, tolerando erratas del line-up."""
    if not a or not b:
        return False
    if a == b:
        return True
    if a.split()[0] != b.split()[0]:      # que al menos arranquen igual
        return False
    return SequenceMatcher(None, a, b).ratio() >= PARECIDO_MINIMO


def buscar_arribo(db, nombre: str):
    """El arribo que ya sigue ese buque, o None."""
    canon = canon_vessel(nombre)
    if not canon:
        return None
    vivos = (db.query(ProximoArribo)
             .filter(ProximoArribo.deleted_at.is_(None),
                     ProximoArribo.estado.notin_(("finalizado", "cancelado")))
             .all())
    exacto = next((a for a in vivos if a.buque_canon == canon), None)
    return exacto or next((a for a in vivos if _parecidos(a.buque_canon, canon)), None)


def _fecha_estimada(arribo):
    """La fecha que ordena la línea de tiempo: el ETB, y si no el ready."""
    for texto in (arribo.etb, arribo.ready):
        m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", texto or "")
        if not m:
            continue
        d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(a if a > 99 else 2000 + a, mes, d).date()
        except ValueError:
            continue
    return None


# ── Nominaciones ──────────────────────────────────────────────────────────────

def _alta_por_nominacion(db, mail, nombre, usuario_id):
    """Da de alta el buque con lo que el correo dice en texto."""
    img = imagen_de_la_tabla(mail["imagenes"])
    a = ProximoArribo(
        buque=re.sub(r"\s+", " ", nombre).strip(),
        buque_canon=canon_vessel(nombre),
        cliente="NUTRIEN",
        mercaderia=mail["producto"] or None,
        servicios="\n".join(mail["servicios"]) or None,
        operacion="DESCARGA",
        estado="esperado",
        origen_alta="nominacion",
        mail_message_id=mail["message_id"],
        # La captura de la planilla se guarda para poder mirar el ETB sin abrir
        # el correo, aunque el sistema no la interprete.
        nominacion_img=(img or {}).get("datos"),
        nominacion_img_tipo=(img or {}).get("tipo"),
        last_update_source="nominacion",
        last_update_at=datetime.utcnow(),
        created_by_id=usuario_id,
    )
    db.add(a)
    db.flush()
    db.add(ArriboUpdate(
        arribo_id=a.id, source="nominacion", created_by_id=usuario_id,
        resumen=f'Alta automática desde la nominación "{mail["asunto"]}". '
                "El ETB lo trae el line-up."))
    return a


def sincronizar_nominaciones(db, usuario_id=None, limite=40, dias=None) -> dict:
    r = buscar_nominaciones(limite=limite, dias=dias)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "altas": [], "vistos": 0}

    altas, ya_estaban = [], set()
    for mail in r["mensajes"]:
        nuevos = [n for n in mail["buques"] if buscar_arribo(db, n) is None]
        ya_estaban.update(set(mail["buques"]) - set(nuevos))
        for nombre in nuevos:
            altas.append(_alta_por_nominacion(db, mail, nombre, usuario_id))
    db.commit()
    return {"ok": True, "error": None, "altas": altas,
            "ya_estaban": sorted(ya_estaban), "vistos": len(r["mensajes"])}


# ── Line-up ───────────────────────────────────────────────────────────────────

def aplicar_lineup(db, vessels, archivo, usuario_id=None) -> list[dict]:
    """Refresca los arribos que seguimos con los datos del line-up."""
    tocados = []
    for v in vessels:
        a = buscar_arribo(db, v.get("buque") or "")
        if a is None:
            continue                     # no es nuestro: el line-up no da de alta
        cambios = []
        for campo, clave in OPERATIVO.items():
            nuevo = (v.get(clave) or "").strip()
            viejo = (getattr(a, campo) or "").strip()
            if nuevo.upper() in _BASURA or nuevo == viejo:
                continue
            setattr(a, campo, nuevo)
            cambios.append(f"{campo}: {viejo or '—'} → {nuevo}")
        for campo, clave in COMERCIAL.items():
            nuevo = (v.get(clave) or "").strip()
            if nuevo.upper() in _BASURA or (getattr(a, campo) or "").strip():
                continue                     # ya lo dijo el cliente: no se toca
            setattr(a, campo, nuevo)
            cambios.append(f"{campo}: — → {nuevo}")
        if not cambios:
            continue
        fecha = _fecha_estimada(a)
        if fecha:
            a.fecha_estimada = fecha
        a.last_update_source = "lineup"
        a.last_update_at = datetime.utcnow()
        a.last_lineup_file = archivo
        db.add(ArriboUpdate(arribo_id=a.id, source="lineup", lineup_file=archivo,
                            created_by_id=usuario_id, resumen=" · ".join(cambios)))
        tocados.append({"arribo": a, "cambios": cambios})
    return tocados


def sincronizar_lineup(db, usuario_id=None, dias=None) -> dict:
    r = buscar_lineups(limite=1, dias=dias)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "tocados": []}
    if not r["mensajes"]:
        return {"ok": True, "error": None, "tocados": [], "archivo": None,
                "fecha": None, "buques": 0}

    mail = r["mensajes"][0]
    try:
        fecha_lineup, vessels = parse_lineup_pdf(mail["contenido"])
    except Exception as e:
        return {"ok": False, "error": f"No pude leer el PDF: {e}", "tocados": []}

    etiqueta = f'{mail["archivo"]} ({fecha_lineup})' if fecha_lineup else mail["archivo"]
    tocados = aplicar_lineup(db, vessels, etiqueta, usuario_id)
    db.commit()
    return {"ok": True, "error": None, "tocados": tocados, "archivo": mail["archivo"],
            "fecha": fecha_lineup, "buques": len(vessels), "asunto": mail["asunto"]}


def cerrar_los_que_ya_pasaron(db, usuario_id=None, hoy: date | None = None) -> list:
    """Da por finalizados los buques cuya fecha quedó muy atrás.

    POR QUÉ HACE FALTA
        Nadie vuelve a entrar a marcar "finalizado" cuando el buque se fue: se
        hace lo urgente y el registro queda. Sin esto la pantalla arrastra el
        MV TAI HONOR de junio arriba de todo, bajo el título "Atrasados", y lo
        que debería ser una alerta —este buque se demoró— pasa a ser ruido que
        se aprende a ignorar. Una alerta que siempre está encendida no avisa
        nada.

    No toca los que están amarrados u operando: esos están pasando ahora,
    aunque la fecha prevista haya quedado atrás.
    """
    hoy = hoy or date.today()
    limite = hoy - timedelta(days=DIAS_PARA_DARLO_POR_PASADO)
    viejos = (db.query(ProximoArribo)
              .filter(ProximoArribo.deleted_at.is_(None),
                      ProximoArribo.estado.in_(("esperado", "confirmado")),
                      ProximoArribo.fecha_estimada.isnot(None),
                      ProximoArribo.fecha_estimada < limite)
              .all())
    for a in viejos:
        dias = (hoy - a.fecha_estimada).days
        a.estado = "finalizado"
        a.last_update_at = datetime.utcnow()
        db.add(ArriboUpdate(
            arribo_id=a.id, source="manual", created_by_id=usuario_id,
            resumen=f"Cerrado solo: la fecha prevista ({a.fecha_estimada:%d/%m/%Y}) "
                    f"quedó {dias} días atrás y seguía como {a.estado!r}."))
    return viejos


def sincronizar(db, usuario_id=None) -> dict:
    """Las tres pasadas: altas, actualización y cierre de lo que ya pasó."""
    nom = sincronizar_nominaciones(db, usuario_id)
    lu = sincronizar_lineup(db, usuario_id)
    cerrados = cerrar_los_que_ya_pasaron(db, usuario_id)
    db.commit()
    return {"nominaciones": nom, "lineup": lu, "cerrados": cerrados,
            "ok": nom.get("ok") and lu.get("ok"),
            "error": nom.get("error") or lu.get("error")}
