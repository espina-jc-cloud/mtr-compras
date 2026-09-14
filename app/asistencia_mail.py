"""
Lector del buzón donde llega la planilla diaria de horas.

Todas las mañanas llega un mail con asunto "HORAS DEL PERSONAL MTR" y el Excel
adjunto. Este módulo se conecta por IMAP, busca ese mail, baja el adjunto y lo
entrega; quien importa es app/asistencia_import.py, que ya sabe leer el
formato.

POR QUÉ IMAP Y NO UN AGENTE CON IA
    El parser del Excel es determinístico y ya leyó 152 filas sin un solo
    error. Un modelo leyendo la planilla sería más lento, más caro y menos
    confiable. Acá no hace falta interpretar nada: hace falta bajar un archivo.

CONFIGURACIÓN (variables de entorno)
    ASISTENCIA_MAIL_HOST      servidor IMAP           ej. mail.mtr-sa.com.ar
    ASISTENCIA_MAIL_PORT      puerto, default 993
    ASISTENCIA_MAIL_USER      casilla
    ASISTENCIA_MAIL_PASSWORD  contraseña
    ASISTENCIA_MAIL_CARPETA   default INBOX
    ASISTENCIA_MAIL_ASUNTO    default "HORAS DEL PERSONAL MTR"
    ASISTENCIA_MAIL_REMITENTE opcional, filtra por remitente
    ASISTENCIA_MAIL_DIAS      cuántos días hacia atrás mirar, default 7

RECOMENDACIÓN DE SEGURIDAD
    Conviene apuntar esto a una casilla dedicada (planillas@…) que reciba la
    planilla por regla de reenvío o copia fija, y no a la casilla personal: las
    credenciales quedan en las variables de Railway, y con la casilla personal
    cualquiera con acceso a ese proyecto puede leer todo el correo. Con una
    casilla dedicada, lo único expuesto son las planillas.
"""
import email
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

_EXT_OK = (".xlsx", ".xlsm")


def config() -> dict:
    return {
        "host": os.getenv("ASISTENCIA_MAIL_HOST", "").strip(),
        "port": int(os.getenv("ASISTENCIA_MAIL_PORT", "993") or 993),
        "user": os.getenv("ASISTENCIA_MAIL_USER", "").strip(),
        "password": os.getenv("ASISTENCIA_MAIL_PASSWORD", ""),
        "carpeta": os.getenv("ASISTENCIA_MAIL_CARPETA", "INBOX").strip() or "INBOX",
        "asunto": os.getenv("ASISTENCIA_MAIL_ASUNTO", "HORAS DEL PERSONAL MTR").strip(),
        "remitente": os.getenv("ASISTENCIA_MAIL_REMITENTE", "").strip(),
        "dias": int(os.getenv("ASISTENCIA_MAIL_DIAS", "7") or 7),
    }


def configurado() -> bool:
    c = config()
    return bool(c["host"] and c["user"] and c["password"])


def _texto(valor) -> str:
    """Decodifica encabezados MIME ('=?utf-8?B?...?=') a texto legible."""
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor))).strip()
    except Exception:
        return str(valor).strip()


def _normalizar(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().upper()


def probar_conexion() -> dict:
    """Verifica credenciales y carpeta sin descargar nada.

    Se usa desde la UI: si algo está mal, el usuario tiene que poder verlo sin
    esperar a que falle la importación automática de mañana a la mañana.
    """
    c = config()
    if not configurado():
        return {"ok": False, "error": "Faltan ASISTENCIA_MAIL_HOST, USER o PASSWORD."}
    try:
        with imaplib.IMAP4_SSL(c["host"], c["port"]) as m:
            m.login(c["user"], c["password"])
            estado, datos = m.select(c["carpeta"], readonly=True)
            if estado != "OK":
                carpetas = []
                for row in (m.list()[1] or []):
                    try:
                        carpetas.append(row.decode(errors="replace").split(' "" ')[-1].strip('"'))
                    except Exception:
                        pass
                return {"ok": False,
                        "error": f'No pude abrir la carpeta "{c["carpeta"]}".',
                        "carpetas": carpetas[:20]}
            total = int(datos[0]) if datos and datos[0] else 0
            return {"ok": True, "host": c["host"], "user": c["user"],
                    "carpeta": c["carpeta"], "mensajes": total,
                    "asunto": c["asunto"]}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "error": f"El servidor rechazó el acceso: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def buscar_planillas(limite: int = 5) -> dict:
    """Trae los mails recientes que tengan el asunto buscado y un Excel adjunto.

    Devuelve {"ok": bool, "mensajes": [{message_id, asunto, remitente, fecha,
    archivo, contenido}], "error": str|None}, del más reciente al más viejo.
    """
    c = config()
    if not configurado():
        return {"ok": False, "error": "El buzón no está configurado.", "mensajes": []}

    desde = (datetime.now(timezone.utc) - timedelta(days=c["dias"])).strftime("%d-%b-%Y")
    asunto_norm = _normalizar(c["asunto"])
    mensajes = []

    try:
        with imaplib.IMAP4_SSL(c["host"], c["port"]) as m:
            m.login(c["user"], c["password"])
            estado, _ = m.select(c["carpeta"], readonly=True)
            if estado != "OK":
                return {"ok": False, "mensajes": [],
                        "error": f'No pude abrir la carpeta "{c["carpeta"]}".'}

            # Se filtra por fecha en el servidor y por asunto en el cliente: los
            # servidores difieren mucho en cómo tratan acentos y mayúsculas en
            # SEARCH SUBJECT, y un falso negativo acá significa que la planilla
            # del día no entra.
            criterio = ["SINCE", desde]
            if c["remitente"]:
                criterio += ["FROM", c["remitente"]]
            estado, datos = m.search(None, *criterio)
            if estado != "OK":
                return {"ok": False, "mensajes": [], "error": "La búsqueda IMAP falló."}

            ids = (datos[0] or b"").split()
            for uid in reversed(ids):           # del más nuevo al más viejo
                if len(mensajes) >= limite:
                    break
                estado, crudo = m.fetch(uid, "(RFC822)")
                if estado != "OK" or not crudo or not crudo[0]:
                    continue
                msg = email.message_from_bytes(crudo[0][1])

                asunto = _texto(msg.get("Subject"))
                if asunto_norm and asunto_norm not in _normalizar(asunto):
                    continue

                adjunto, nombre = None, None
                for parte in msg.walk():
                    if parte.get_content_maintype() == "multipart":
                        continue
                    fn = _texto(parte.get_filename())
                    if fn and fn.lower().endswith(_EXT_OK):
                        adjunto = parte.get_payload(decode=True)
                        nombre = fn
                        break
                if not adjunto:
                    continue

                try:
                    fecha = parsedate_to_datetime(msg.get("Date"))
                    if fecha.tzinfo:
                        fecha = fecha.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    fecha = None

                mensajes.append({
                    "message_id": (msg.get("Message-ID") or "").strip()[:400] or None,
                    "asunto": asunto[:400],
                    "remitente": _texto(msg.get("From"))[:300],
                    "fecha": fecha,
                    "archivo": nombre[:300],
                    "contenido": adjunto,
                })

        return {"ok": True, "mensajes": mensajes, "error": None}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "mensajes": [], "error": f"El servidor rechazó el acceso: {e}"}
    except Exception as e:
        return {"ok": False, "mensajes": [], "error": f"{type(e).__name__}: {e}"}


# ══════════════════════════════════════════════════════════════════════════════
# Orquestador: buzón → importación
# ══════════════════════════════════════════════════════════════════════════════

def _planta_de_hoja(hoja: str) -> str:
    h = (hoja or "").upper().replace(".", " ")
    if re.search(r"MTR\s*II\b|MTR\s*2\b", h):
        return "MTR2"
    if re.search(r"MTR\s*I\b|MTR\s*1\b", h):
        return "MTR1"
    return ""


def procesar_buzon(db, usuario_id=None, origen="buzon", forzar=False) -> list:
    """Revisa el buzón e importa lo que sea seguro importar.

    Criterio (elegido por el usuario): se aplican los días nuevos y los que no
    pisan ninguna decisión ya tomada. Un día que tocaría una extra justificada,
    aprobada o rechazada NO se aplica solo — queda anotado como pendiente para
    que una persona lo mire en la pantalla de importación.

    Devuelve la lista de AsistenciaImportacion creadas.
    """
    import json
    from app.models_asistencia import AsistenciaImportacion, AsistenciaPersona
    from app.asistencia_import import (aplicar_dia, buscar_persona, clasificar_dia,
                                       indexar_personas, parsear)

    res = buscar_planillas()
    if not res["ok"]:
        reg = AsistenciaImportacion(origen=origen, estado="error",
                                    error=res["error"], usuario_id=usuario_id)
        db.add(reg)
        db.commit()
        return [reg]

    if not res["mensajes"]:
        return []

    creadas = []
    for msg in res["mensajes"]:
        # Deduplicación: el buzón se revisa seguido y el mismo mail no puede
        # importarse dos veces.
        if msg["message_id"] and not forzar:
            ya = (db.query(AsistenciaImportacion)
                  .filter(AsistenciaImportacion.message_id == msg["message_id"],
                          AsistenciaImportacion.estado != "error")
                  .first())
            if ya:
                continue

        reg = AsistenciaImportacion(
            origen=origen, message_id=msg["message_id"], asunto=msg["asunto"],
            remitente=msg["remitente"], recibido_at=msg["fecha"],
            archivo=msg["archivo"], usuario_id=usuario_id,
        )
        try:
            datos = parsear(msg["contenido"])
            reg.hoja = datos["hoja"]
            planta = _planta_de_hoja(datos["hoja"])

            personas = db.query(AsistenciaPersona).filter(
                AsistenciaPersona.activo == True,  # noqa: E712
                AsistenciaPersona.tipo == "mtr").all()
            idx = indexar_personas(personas)

            aplicados, filas, sin_match, pendientes = 0, 0, 0, []
            for d in datos["dias"]:
                matcheadas = []
                for f in d["filas"]:
                    p, _ = buscar_persona(idx, f["apellido"], f["nombre"])
                    if p is not None:
                        matcheadas.append(p)

                seguro, motivo = clasificar_dia(db, d["fecha"], matcheadas)
                if not seguro:
                    pendientes.append({"fecha": d["fecha"].isoformat(), "motivo": motivo})
                    continue

                a, sm = aplicar_dia(db, d["fecha"], d["filas"], idx,
                                    planta=planta, tipo="mtr", user_id=usuario_id)
                aplicados += 1
                filas += a
                sin_match += sm

            reg.dias_aplicados = aplicados
            reg.filas_aplicadas = filas
            reg.dias_pendientes = len(pendientes)
            reg.sin_reconocer = sin_match
            reg.detalle = json.dumps({"pendientes": pendientes}, ensure_ascii=False)
            reg.estado = ("parcial" if (pendientes or sin_match)
                          else ("ok" if aplicados else "sin_novedad"))
            db.add(reg)
            db.commit()
        except Exception as e:
            db.rollback()
            reg.estado = "error"
            reg.error = f"{type(e).__name__}: {e}"
            db.add(reg)
            db.commit()
        creadas.append(reg)

    return creadas
