"""
Módulo Próximos Arribos — buques próximos a arribar al Puerto de San Nicolás.

Flujo: alta manual (datos preliminares del cliente) → enriquecimiento por import
del lineup PDF (solo actualiza MIS buques, no carga todo el puerto) → edición
manual posterior. Historial mínimo de cambios en ArriboUpdate.
"""
from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Text, Date, DateTime, ForeignKey, Numeric
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

ARRIBO_FUENTES = [("manual", "Manual"), ("lineup", "Lineup PDF")]


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
