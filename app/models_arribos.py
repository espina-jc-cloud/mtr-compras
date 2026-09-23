"""
Módulo Próximos Arribos — buques próximos a arribar al Puerto de San Nicolás.

Flujo: alta manual (datos preliminares del cliente) → enriquecimiento por import
del lineup PDF (solo actualiza MIS buques, no carga todo el puerto) → edición
manual posterior. Historial mínimo de cambios en ArriboUpdate.
"""
from datetime import datetime, date
from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer,
                        LargeBinary, Numeric, String, Text)
from sqlalchemy.orm import relationship
from app.database import Base


# Estados del ciclo de vida de un arribo.
ARRIBO_ESTADOS = [
    ("esperado",   "Esperado"),
    ("confirmado", "Confirmado"),
    ("amarrado",   "Amarrado"),
    ("operando",   "Operando"),
    ("finalizado", "Finalizado"),
    ("cancelado",  "Cancelado"),
]
ARRIBO_ESTADO_LABELS = dict(ARRIBO_ESTADOS)

# CSS de badge por estado (reusa el estilo del resto del sistema).
ARRIBO_ESTADO_CSS = {
    "esperado":   "bg-gray-100 text-gray-600",
    "confirmado": "bg-blue-50 text-blue-700",
    "amarrado":   "bg-indigo-50 text-indigo-700",
    "operando":   "bg-amber-50 text-amber-700",
    "finalizado": "bg-emerald-50 text-emerald-700",
    "cancelado":  "bg-red-50 text-red-600",
}

ARRIBO_FUENTES = [("manual", "Manual"), ("lineup", "Lineup PDF"),
                  ("nominacion", "Nominación por mail")]

# De dónde salió el alta. Importa para la pantalla: los de Nutrien entran
# solos y el resto se cargan a mano, y conviene distinguirlos de un vistazo.
ARRIBO_ORIGENES = [("manual", "Carga manual"),
                   ("nominacion", "Nominación de Nutrien"),
                   ("lineup", "Line-up del puerto")]


class ProximoArribo(Base):
    __tablename__ = "proximos_arribos"

    id              = Column(Integer, primary_key=True, index=True)
    # Identificación
    buque           = Column(String(200), nullable=False)
    buque_canon     = Column(String(200), nullable=False, index=True)  # clave de matching
    cliente         = Column(String(200), nullable=True)
    mercaderia      = Column(String(200), nullable=True)
    tonelaje_estimado = Column(Numeric(12, 2), nullable=True)
    procedencia     = Column(String(200), nullable=True)
    agencia         = Column(String(120), nullable=True)
    operacion       = Column(String(60), nullable=True)   # CARGA / DESCARGA / TRASBORDO

    # Operativo (texto libre, editable; se enriquece del lineup)
    estado          = Column(String(20), nullable=False, default="esperado", index=True)
    fecha_estimada  = Column(Date, nullable=True, index=True)   # para ordenar el listado
    etb             = Column(String(80), nullable=True)
    etc             = Column(String(80), nullable=True)
    ready           = Column(String(80), nullable=True)   # "red" / ready
    muelle          = Column(String(120), nullable=True)
    posicion        = Column(String(120), nullable=True)  # sector
    amarre          = Column(String(120), nullable=True)
    observaciones   = Column(Text, nullable=True)
    comentario_operativo = Column(Text, nullable=True)

    # Nominación de Nutrien (ver app/arribos_mail.py y app/arribos_sync.py)
    origen_alta     = Column(String(20), nullable=False, default="manual", index=True)
    # Message-ID del correo que lo originó: es lo que evita dar de alta dos
    # veces el mismo buque cuando la nominación se reenvía o se responde.
    mail_message_id = Column(String(255), nullable=True, index=True)
    proveedor       = Column(String(120), nullable=True)
    tonelaje_mtr    = Column(Numeric(12, 2), nullable=True)   # lo que baja en MTR
    demurrage       = Column(Numeric(12, 2), nullable=True)
    servicios       = Column(Text, nullable=True)             # uno por renglón
    # Quedó de cuando el ETB se leía de la captura con un modelo de visión.
    # Ya no se usa: los datos entran y se corrigen editando. No se borra la
    # columna para no tocar el esquema de producción por nada.
    a_confirmar     = Column(Boolean, nullable=False, default=False, index=True)
    # La captura se guarda con el arribo para poder leer el ETB sin ir al mail.
    nominacion_img  = Column(LargeBinary, nullable=True)
    nominacion_img_tipo = Column(String(40), nullable=True)

    # Trazabilidad
    last_update_source = Column(String(20), nullable=True)   # manual / lineup
    last_update_at     = Column(DateTime, nullable=True)
    last_lineup_file   = Column(String(200), nullable=True)
    created_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at      = Column(DateTime, nullable=True, index=True)

    created_by = relationship("User")
    updates = relationship(
        "ArriboUpdate", back_populates="arribo",
        cascade="all, delete-orphan", order_by="ArriboUpdate.created_at.desc()",
    )


class ArriboUpdate(Base):
    """Registro mínimo de cada cambio (manual o por lineup)."""
    __tablename__ = "arribo_updates"

    id            = Column(Integer, primary_key=True, index=True)
    arribo_id     = Column(Integer, ForeignKey("proximos_arribos.id"), nullable=False, index=True)
    source        = Column(String(20), nullable=False)   # manual / lineup
    resumen       = Column(Text, nullable=True)           # qué cambió
    lineup_file   = Column(String(200), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    arribo     = relationship("ProximoArribo", back_populates="updates")
    created_by = relationship("User")
