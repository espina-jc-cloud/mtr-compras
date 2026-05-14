from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Numeric
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)  # planta | autorizador | admin | superadmin
    plant = Column(String, nullable=False)  # MTR1 | MTR2 | ROSARIO | TODAS
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    purchases_requested = relationship("Purchase", foreign_keys="Purchase.requested_by_id", back_populates="requester")
    purchases_authorized = relationship("Purchase", foreign_keys="Purchase.authorized_by_id", back_populates="authorizer")

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    cuit = Column(String)
    contact_name = Column(String)
    contact_phone = Column(String)
    email = Column(String)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    purchases = relationship("Purchase", back_populates="supplier")

class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True, index=True)
    plant = Column(String, nullable=False)  # MTR1 | MTR2
    area = Column(String, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    description = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    estimated_amount = Column(Numeric(12, 2))
    actual_amount = Column(Numeric(12, 2))
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    requested_at = Column(DateTime, default=datetime.utcnow)
    authorized_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    authorized_at = Column(DateTime, nullable=True)
    status = Column(String, default="pendiente")
    rejection_reason = Column(Text)
    purchase_order_ref = Column(String)
    notes = Column(Text)
    amount_alert = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    supplier = relationship("Supplier", back_populates="purchases")
    requester = relationship("User", foreign_keys=[requested_by_id], back_populates="purchases_requested")
    authorizer = relationship("User", foreign_keys=[authorized_by_id], back_populates="purchases_authorized")
    documents = relationship("Document", back_populates="purchase", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="purchase", cascade="all, delete-orphan")

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    doc_type = Column(String, nullable=False)  # remito | factura | otro
    file_url = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    invoice_number = Column(String)
    invoice_date = Column(String)
    invoice_amount = Column(Numeric(12, 2))
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    purchase = relationship("Purchase", back_populates="documents")
    uploader = relationship("User")

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    old_status = Column(String)
    new_status = Column(String)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    purchase = relationship("Purchase", back_populates="audit_logs")
    user = relationship("User")
