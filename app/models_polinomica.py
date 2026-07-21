"""
Polinómica CNA — modelos.

  - PolinomicaIndice: un row por mes de variaciones (fracciones: 0.03 = 3%).
  - PolinomicaRemito: cada remito de tarifas generado queda persistido con el
    snapshot de tarifas en JSON (inmutable aunque después cambien los índices).
"""
from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Float, Text, Date, DateTime
from app.database import Base


class PolinomicaIndice(Base):
    __tablename__ = "polinomica_indices"

    id         = Column(Integer, primary_key=True, index=True)
    mes        = Column(String(20), unique=True, nullable=False)   # "Jun 2026"
    orden      = Column(Integer, nullable=False, index=True)
    supa       = Column(Float, nullable=False)
    cam        = Column(Float, nullable=False)
    ipc        = Column(Float, nullable=False)
    comb       = Column(Float, nullable=False)
    usd        = Column(Float, nullable=False)
    fadeeac    = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def as_dict(self):
        return {"mes": self.mes, "supa": self.supa, "cam": self.cam,
                "ipc": self.ipc, "comb": self.comb, "usd": self.usd,
                "fadeeac": self.fadeeac}


class PolinomicaRemito(Base):
    __tablename__ = "polinomica_remitos"

    id            = Column(Integer, primary_key=True, index=True)
    numero        = Column(String(30), unique=True, nullable=False)  # TAR-20260717-001
    operativo     = Column(String(200), nullable=False)
    producto      = Column(String(200), nullable=True)
    fecha_ini     = Column(Date, nullable=True)
    fecha_fin     = Column(Date, nullable=True)
    observaciones = Column(Text, nullable=True)
    tarifas_json  = Column(Text, nullable=False)   # [{nombre, cat, base, nueva}]
    mes_vigencia  = Column(String(20), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    created_by    = Column(String(100), nullable=True)
