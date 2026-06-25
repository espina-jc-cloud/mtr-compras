"""
Utilidades de Finanzas — cálculo de saldos de tesorería.

El saldo nunca se persiste: se deriva siempre de los movimientos no anulados.
Así no hay forma de que el saldo "se descuadre" respecto del detalle.
"""
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models_finanzas import CuentaTesoreria, MovimientoTesoreria


def saldo_cuenta(db: Session, cuenta: CuentaTesoreria) -> Decimal:
    """Saldo actual = saldo_inicial + Σ ingresos − Σ egresos (no anulados)."""
    base = Decimal(cuenta.saldo_inicial or 0)
    ingresos = db.query(func.coalesce(func.sum(MovimientoTesoreria.monto), 0)).filter(
        MovimientoTesoreria.cuenta_id == cuenta.id,
        MovimientoTesoreria.tipo == "ingreso",
        MovimientoTesoreria.deleted_at.is_(None),
    ).scalar() or 0
    egresos = db.query(func.coalesce(func.sum(MovimientoTesoreria.monto), 0)).filter(
        MovimientoTesoreria.cuenta_id == cuenta.id,
        MovimientoTesoreria.tipo == "egreso",
        MovimientoTesoreria.deleted_at.is_(None),
    ).scalar() or 0
    return base + Decimal(ingresos) - Decimal(egresos)


def saldos_por_moneda(db: Session):
    """Devuelve {moneda: {'cuentas': n, 'saldo': Decimal}} para el dashboard."""
    cuentas = db.query(CuentaTesoreria).filter(CuentaTesoreria.activo.is_(True)).all()
    out = {}
    for c in cuentas:
        m = c.moneda or "ARS"
        bucket = out.setdefault(m, {"cuentas": 0, "saldo": Decimal(0)})
        bucket["cuentas"] += 1
        bucket["saldo"] += saldo_cuenta(db, c)
    return out
