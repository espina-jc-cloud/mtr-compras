from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    DateTime,
    Text,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from app.database import Base


class DailyOpDay(Base):
    __tablename__ = "daily_op_days"

    id = Column(Integer, primary_key=True, index=True)
    op_date = Column(Date, nullable=False, unique=True, index=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    imports = relationship(
        "DailyOpImport",
        back_populates="day",
        cascade="all, delete-orphan",
        order_by="DailyOpImport.imported_at.desc()",
    )
    trips = relationship(
        "DailyOpTrip",
        back_populates="day",
        cascade="all, delete-orphan",
        order_by="DailyOpTrip.entry_date.asc()",
    )


class DailyOpImport(Base):
    __tablename__ = "daily_op_imports"

    id = Column(Integer, primary_key=True, index=True)
    day_id = Column(Integer, ForeignKey("daily_op_days.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    upload_group_id = Column(String, nullable=True, index=True)
    operativo = Column(String, nullable=True, index=True)
    row_count = Column(Integer, nullable=False, default=0)
    imported_at = Column(DateTime, default=datetime.utcnow)
    imported_by = Column(String, nullable=True)

    day = relationship("DailyOpDay", back_populates="imports")
    trips = relationship(
        "DailyOpTrip",
        back_populates="import_file",
        cascade="all, delete-orphan",
    )


class DailyOpTrip(Base):
    __tablename__ = "daily_op_trips"

    id = Column(Integer, primary_key=True, index=True)

    day_id = Column(Integer, ForeignKey("daily_op_days.id"), nullable=False, index=True)
    import_id = Column(Integer, ForeignKey("daily_op_imports.id"), nullable=False, index=True)

    trip_code = Column(Integer, nullable=True, index=True)

    entry_date = Column(DateTime, nullable=True, index=True)
    entry_time = Column(String, nullable=True)
    exit_date = Column(DateTime, nullable=True)
    exit_time = Column(String, nullable=True)

    plate = Column(String, nullable=True, index=True)
    trailer_plate = Column(String, nullable=True, index=True)

    tara_kg = Column(Integer, nullable=True)
    bruto_kg = Column(Integer, nullable=True)
    neto_kg = Column(Integer, nullable=True, default=0)
    origen_kg = Column(Integer, nullable=True, default=0)
    diff_kg = Column(Integer, nullable=True, default=0)

    driver = Column(String, nullable=True, index=True)
    client = Column(String, nullable=True, index=True)
    product = Column(String, nullable=True, index=True)
    transporte = Column(String, nullable=True, index=True)
    operation = Column(String, nullable=True, index=True)
    remito = Column(String, nullable=True, index=True)
    operativo = Column(String, nullable=True, index=True)
    planta = Column(String, nullable=True, index=True)

    duration_min = Column(Numeric(8, 2), nullable=True)
    shift_number = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    day = relationship("DailyOpDay", back_populates="trips")
    import_file = relationship("DailyOpImport", back_populates="trips")

    __table_args__ = (
        UniqueConstraint(
            "import_id",
            "trip_code",
            "plate",
            "entry_date",
            name="uq_daily_op_trip_import_ticket",
        ),
        Index("ix_daily_op_trips_day_client", "day_id", "client"),
        Index("ix_daily_op_trips_day_product", "day_id", "product"),
        Index("ix_daily_op_trips_day_operativo", "day_id", "operativo"),
    )
