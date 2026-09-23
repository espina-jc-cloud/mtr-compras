"""
Operativos de buque cerrados, tal como los manda balanza cuando termina uno.

POR QUÉ UNA TABLA DE PESAJES Y NO SÓLO TOTALES
    El Excel de balanza trae cada camión: entrada, salida, tara, bruto, neto y
    el peso declarado en origen. Guardar sólo los totales tira a la basura todo
    lo que se puede preguntar después — cuánto tarda un camión adentro, qué
    transportista rindió, en qué turno se movió más, cuánto se despega nuestra
    balanza de la del origen. Los totales se derivan de los pesajes; al revés
    no se puede.

RELACIÓN CON EL RESTO DEL SISTEMA
    Próximos Arribos dice qué viene. Esto dice cómo terminó. Se unen por el
    nombre normalizado del buque.
"""
from datetime import datetime

from sqlalchemy import (Column, Date, DateTime, ForeignKey, Integer, Numeric,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import relationship

from app.database import Base


class BuqueOperativo(Base):
    """Un operativo completo: el resumen que mandó balanza por mail."""
    __tablename__ = "buque_operativos"
    __table_args__ = (
        # Balanza numera los operativos y reenvía el mismo resumen varias veces
        # ("Re:", "Fwd:") y otra vez al cerrar. Esto evita el duplicado.
        UniqueConstraint("buque_canon", "operativo_nro", "inicio",
                         name="uq_buque_operativo"),
    )

    id            = Column(Integer, primary_key=True, index=True)
    buque         = Column(String(200), nullable=False)
    buque_canon   = Column(String(200), nullable=False, index=True)
    buque_crudo   = Column(String(250), nullable=True)   # como lo escribió balanza
    operativo_nro = Column(Integer, nullable=True)
    cliente       = Column(String(200), nullable=True, index=True)
    producto      = Column(String(120), nullable=True, index=True)

    inicio        = Column(Date, nullable=True, index=True)
    fin           = Column(Date, nullable=True)
    # "Fecha Finalizacion: --" mientras el buque sigue descargando.
    cerrado       = Column(Integer, nullable=False, default=0)

    viajes        = Column(Integer, nullable=False, default=0)
    neto_kg       = Column(Numeric(14, 0), nullable=False, default=0)
    origen_kg     = Column(Numeric(14, 0), nullable=False, default=0)

    archivo         = Column(String(250), nullable=True)
    mail_message_id = Column(String(255), nullable=True, index=True)
    mail_asunto     = Column(String(300), nullable=True)
    mail_fecha      = Column(DateTime, nullable=True)
    avisos          = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pesajes = relationship("BuquePesaje", back_populates="operativo",
                           cascade="all, delete-orphan", lazy="selectin")


class BuquePesaje(Base):
    """Un camión pesado: el hecho más chico del que sale todo lo demás."""
    __tablename__ = "buque_pesajes"
    __table_args__ = (
        UniqueConstraint("operativo_id", "nro", name="uq_pesaje_operativo"),
    )

    id           = Column(Integer, primary_key=True, index=True)
    operativo_id = Column(Integer, ForeignKey("buque_operativos.id"),
                          nullable=False, index=True)
    nro          = Column(Integer, nullable=False)
    patente      = Column(String(20), nullable=True, index=True)
    turno        = Column(String(40), nullable=True)
    transporte   = Column(String(160), nullable=True, index=True)

    entrada      = Column(DateTime, nullable=True)
    salida       = Column(DateTime, nullable=True)
    tara         = Column(Integer, nullable=True)
    bruto        = Column(Integer, nullable=True)
    neto         = Column(Integer, nullable=True)
    origen       = Column(Integer, nullable=True)

    operativo = relationship("BuqueOperativo", back_populates="pesajes")
