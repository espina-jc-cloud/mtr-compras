from datetime import datetime
from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class TransporteNomina(Base):
    __tablename__ = "transporte_nomina"

    id               = Column(Integer, primary_key=True, index=True)
    empresa          = Column(String(300), nullable=False)
    nombre_chofer    = Column(String(300), nullable=False)
    dni              = Column(String(20),  nullable=True)
    marca_camion     = Column(String(200), nullable=True)
    patente_camion   = Column(String(20),  nullable=True)
    patente_acoplado = Column(String(20),  nullable=True)
    deleted_at       = Column(DateTime,    nullable=True)
    created_at       = Column(DateTime,    default=datetime.utcnow)
    updated_at       = Column(DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)

    asignaciones = relationship("TransporteOperativoAsignacion", back_populates="nomina")


class TransporteOperativo(Base):
    __tablename__ = "transporte_operativos"

    id           = Column(Integer, primary_key=True, index=True)
    nombre_barco = Column(String(300), nullable=False)
    producto     = Column(String(300), nullable=True)
    cliente      = Column(String(300), nullable=True)
    deposito     = Column(String(300), nullable=True)
    fecha_inicio = Column(Date,        nullable=True)
    fecha_fin    = Column(Date,        nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    deleted_at    = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    created_by   = relationship("User", foreign_keys=[created_by_id])
    asignaciones = relationship(
        "TransporteOperativoAsignacion",
        back_populates="operativo",
        order_by="TransporteOperativoAsignacion.assigned_at",
    )


class TransporteOperativoAsignacion(Base):
    """Snapshot histórico del transporte al momento de ser asignado al operativo."""
    __tablename__ = "transporte_operativo_asignaciones"

    id               = Column(Integer, primary_key=True, index=True)
    operativo_id     = Column(Integer, ForeignKey("transporte_operativos.id"), nullable=False)
    nomina_id        = Column(Integer, ForeignKey("transporte_nomina.id"),     nullable=True)

    # Datos copiados al momento de la asignación (no se modifican si cambia la nómina)
    empresa_snap          = Column(String(300), nullable=False)
    nombre_chofer_snap    = Column(String(300), nullable=False)
    dni_snap              = Column(String(20),  nullable=True)
    marca_camion_snap     = Column(String(200), nullable=True)
    patente_camion_snap   = Column(String(20),  nullable=True)
    patente_acoplado_snap = Column(String(20),  nullable=True)

    assigned_at   = Column(DateTime, default=datetime.utcnow)
    assigned_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    operativo   = relationship("TransporteOperativo", back_populates="asignaciones")
    nomina      = relationship("TransporteNomina",    back_populates="asignaciones")
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])
