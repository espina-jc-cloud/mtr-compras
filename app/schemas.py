from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class FacturaRemitoResponse(BaseModel):
    id: int
    numero: Optional[str] = None
    fecha: Optional[str] = None
    monto: Optional[float] = None


class FacturaCreate(BaseModel):
    numero_factura: Optional[str] = None
    proveedor_id: int
    tipo_comprobante: str = "Factura A"
    fecha_emision: Optional[date] = None
    monto_total: Optional[float] = None
    cuit_proveedor: Optional[str] = None
    observaciones: Optional[str] = None
    remito_ids: Optional[List[int]] = None


class FacturaUpdate(BaseModel):
    numero_factura: Optional[str] = None
    proveedor_id: Optional[int] = None
    tipo_comprobante: Optional[str] = None
    fecha_emision: Optional[date] = None
    monto_total: Optional[float] = None
    cuit_proveedor: Optional[str] = None
    observaciones: Optional[str] = None
    remito_ids: Optional[List[int]] = None


class FacturaResponse(BaseModel):
    id: int
    numero_factura: Optional[str] = None
    proveedor_id: int
    proveedor_nombre: str
    tipo_comprobante: str
    fecha_emision: Optional[date] = None
    monto_total: Optional[float] = None
    cuit_proveedor: Optional[str] = None
    observaciones: Optional[str] = None
    archivo_url: Optional[str] = None
    archivo_nombre: Optional[str] = None
    archivo_public_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    remitos: List[FacturaRemitoResponse] = []
    cantidad_remitos: int = 0
    total_remitos: float = 0.0

    class Config:
        from_attributes = True
