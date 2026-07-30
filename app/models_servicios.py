"""
Servicios de equipo — trabajos esporádicos de alquiler de equipo (ej. autoelevador
Yale) con conductor propio. Se documentan a partir de los mensajes que manda el
supervisor por WhatsApp.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Date, DateTime, Numeric, Text

from app.database import Base


class ServicioEquipo(Base):
    __tablename__ = "servicios_equipo"

    id            = Column(Integer, primary_key=True, autoincrement=True)

    fecha         = Column(Date, index=True)
    cliente       = Column(String(200), index=True)   # RASA, etc.
    equipo        = Column(String(120))               # "Yale 10 tn"
    conductor     = Column(String(120))               # Achabal / Java

    hora_inicio   = Column(String(10))                # "08:00"
    hora_fin      = Column(String(10))                # "11:00"
    horas_facturadas = Column(Numeric(6, 2))          # puede diferir de las reales

    origen        = Column(String(200))               # cargó en…
    destino       = Column(String(200))               # descargó en…
    descripcion   = Column(Text)                       # qué hizo

    tarifa_hora   = Column(Numeric(12, 2))            # se completa después
    importe       = Column(Numeric(12, 2))            # horas × tarifa (o manual)

    estado        = Column(String(20), default="por_facturar")  # por_facturar | facturado | cobrado
    mensaje_original = Column(Text)                    # el WhatsApp pegado
    notas         = Column(Text)

    created_at    = Column(DateTime, default=datetime.utcnow)
    created_by    = Column(String(120))
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


ESTADOS = [
    ("por_facturar", "Por facturar"),
    ("facturado",    "Facturado"),
    ("cobrado",      "Cobrado"),
]
ESTADO_CSS = {
    "por_facturar": "bg-amber-50 text-amber-700",
    "facturado":    "bg-blue-50 text-blue-700",
    "cobrado":      "bg-emerald-50 text-emerald-700",
}
