"""
Carga one-off del operativo MDS APHRODITE (25→27/07/2026, UREA G, Nutrien AG)
desde los 5 partes diarios de C.P.S.N. + factura cooperativa en borrador para
revisión. Fuente: partes en papel + PDF 'MTR MDS APHRODITE 25-07-26.pdf'.

Idempotente: si ya existe una sesión 'MDS APHRODITE' no vuelve a crear nada.
Uso:  python scripts/cargar_mds_aphrodite.py [--force]
"""
import sys
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, ".")

from app.database import SessionLocal
from app import models  # noqa: F401 — registra 'operations' para la FK de sessions
from app.models_live import (
    OperationLiveSession, OperationLiveSessionProduct, OperationLiveShift,
    OperationLiveBodegaData, OperationLiveStaff,
    OperationLiveInvoice, OperationLiveInvoiceTonnageLine,
    OperationLiveInvoiceLaborLine, OperationLiveInvoiceCargoLine,
    OperationLiveInvoiceTotals,
)

SHIP = "MDS APHRODITE"
PRODUCT = "UREA GRANULADA"


def main(force: bool = False):
    db = SessionLocal()
    try:
        existing = db.query(OperationLiveSession).filter_by(ship_name=SHIP).first()
        if existing and not force:
            print(f"✗ Ya existe la sesión #{existing.id} '{SHIP}' — no se carga de nuevo. Usá --force para duplicar.")
            return

        # ── Sesión (cerrada: lista para cargar/revisar factura) ────────────
        ses = OperationLiveSession(
            ship_name=SHIP, status="closed",
            created_at=datetime(2026, 7, 25, 3, 0),
            closed_at=datetime(2026, 7, 28, 12, 0),
            created_by="Carga partes CPSN",
            closed_by="Carga partes CPSN",
            closing_notes="Descarga finalizada 27/07. Cargado desde los 5 partes diarios CPSN.",
        )
        db.add(ses)
        db.flush()
        db.add(OperationLiveSessionProduct(
            session_id=ses.id, product=PRODUCT, client="NUTRIEN AG",
            kg_contracted=2_750_000,
            notes="Consignatario MTR S.A. Restan según parte 4: 54.960 kg antes del último turno.",
        ))

        # ── Partes: (nº, fecha, ini, fin, tipo, manos, apuntador, coordinador,
        #             inicio_bodega, guinche, viajes, kg, staff[], nota) ──────
        P = [
            (1, date(2026, 7, 25), "00:00", "06:00", "inhabil", 1, "Ramírez César",
             "Flores Leandro", "01:25", "abordo", 27, 772_040,
             [("otro", "Pala regulando de Coop", 1), ("maquinista_autoelevador", None, 1),
              ("guinchero", None, 1)],
             "Capataz: Ramírez Carlos. Grampa MTR."),
            (2, date(2026, 7, 25), "06:00", "12:00", "habil", 1, "Ramírez César",
             "Flores Leandro", "06:25", "abordo", 27, 775_980,
             [("otro", "Pala regulando de Coop", 1), ("maquinista_autoelevador", None, 1),
              ("guinchero", None, 1), ("maquinista_retro", "Retro de MTR", 2)],
             "Capataz: Chávez Darío. Grampa MTR."),
            (3, date(2026, 7, 25), "12:00", "18:00", "inhabil", 1, "Ramallo Gustavo",
             "Gómez Matías", "12:10", "abordo", 33, 949_080,
             [("otro", "Pala Coop regulando en muelle", 1), ("maquinista_autoelevador", None, 1),
              ("maquinista_retro", "Retro MTR", 2), ("guinchero", "APU", 1)],
             "Capataz: Chávez Mario. Grampa MTR S.A."),
            (4, date(2026, 7, 25), "18:00", "24:00", "inhabil", 1, "Ramallo Gustavo",
             "Gómez Matías", "18:10", "abordo", 7, 197_940,
             [("otro", "Pala Coop regulando en muelle", 1), ("maquinista_autoelevador", None, 1),
              ("maquinista_retro", "Retro MTR", 2), ("guinchero", "APU", 2),
              ("limpieza", "Limpieza de tolva", 1)],
             "Capataz: Campestrini Javier. Grampa MTR."),
            (5, date(2026, 7, 27), "18:00", "24:00", "habil", 1, None,
             None, "18:45", "fiscal", 2, 44_560,
             [],
             "Mano nombrada por TVS. Guinche y grampa FISCAL. Último turno — descarga finalizada."),
        ]

        for (n, f, hi, hf, tipo, manos, apunt, coord, ini_bod, guinche,
             viajes, kg, staff, nota) in P:
            sh = OperationLiveShift(
                session_id=ses.id, shift_number=n, shift_date=f,
                shift_start=hi, shift_end=hf, status="closed",
                turno_tipo=tipo, manos=manos,
                apuntador=apunt, supervisor_mtr=coord,
                is_final_shift=(n == 5), notes=nota,
            )
            db.add(sh)
            db.flush()
            db.add(OperationLiveBodegaData(
                shift_id=sh.id, bodega_number=5, product=PRODUCT,
                measurement="grampa" if guinche == "abordo" else "fiscal",
                tipo_guinche=guinche,
                tipo_grampa=("fiscal" if guinche == "fiscal" else None),
                viajes_coop=viajes, kg_coop=kg,
            ))
            for (fun, texto, cant) in staff:
                db.add(OperationLiveStaff(
                    shift_id=sh.id, funcion=fun, funcion_texto=texto,
                    cantidad=cant, turno_range=f"{hi[:2]}A{hf[:2]}", empresa="coop",
                ))

        # ── Factura cooperativa en borrador (valores DECLARADOS del PDF) ────
        inv = OperationLiveInvoice(
            session_id=ses.id, status="draft", upload_method="manual",
            invoice_date=date(2026, 7, 28),
            origen_nota="Cargada desde PDF 'MTR MDS APHRODITE 25-07-26.pdf' "
                        "(detalle de descarga + por administración).",
        )
        db.add(inv)
        db.flush()

        TH, TI = Decimal("4837.00"), Decimal("6433.00")
        TON = [  # fecha, rango, guinche declarado, hab, inh, manos
            (date(2026, 7, 25), "00-06", "abordo", None, Decimal("772.04"), 1),
            (date(2026, 7, 25), "06-12", "abordo", Decimal("775.98"), None, 1),
            (date(2026, 7, 25), "12-18", "abordo", None, Decimal("949.08"), 1),
            (date(2026, 7, 25), "18-24", "abordo", None, Decimal("197.94"), 1),
            # OJO: la factura lo tarifa como guinche de ABORDO/hábil, pero el
            # parte 5 dice guinche FISCAL — punto a discutir en la revisión.
            (date(2026, 7, 27), "18-24", "abordo", Decimal("44.56"), None, 0),
        ]
        for (f, rango, g, hab, inh, manos) in TON:
            db.add(OperationLiveInvoiceTonnageLine(
                invoice_id=inv.id, shift_date=f, turno_range=rango,
                guinche_tipo=g, product=PRODUCT,
                tns_habiles_recibido=hab, tns_inhabiles_recibido=inh,
                manos_recibido=manos, tarifa_habiles=TH, tarifa_inhabiles=TI,
            ))

        PJ_I, PJ_H = Decimal("189530.98"), Decimal("121841.34")
        LIMP, APU = Decimal("153076.99"), Decimal("180722.50")
        LAB = [  # fecha, rango, tipo, funcion, texto, cant, precio
            (date(2026, 7, 25), "00-06", "inhabil", "guinchero",  None, 1, PJ_I),
            (date(2026, 7, 25), "00-06", "inhabil", "maquinista", None, 2, PJ_I),
            (date(2026, 7, 25), "06-12", "habil",   "guinchero",  None, 1, PJ_H),
            (date(2026, 7, 25), "06-12", "habil",   "maquinista", None, 4, PJ_H),
            (date(2026, 7, 25), "12-18", "inhabil", "guinchero",  None, 1, PJ_I),
            (date(2026, 7, 25), "12-18", "inhabil", "maquinista", None, 4, PJ_I),
            (date(2026, 7, 25), "18-24", "inhabil", "guinchero",  None, 2, PJ_I),
            (date(2026, 7, 25), "18-24", "inhabil", "maquinista", None, 4, PJ_I),
            (date(2026, 7, 25), "18-24", "inhabil", "limpieza",   "Limpieza tolva", 1, LIMP),
            (date(2026, 7, 27), "18-24", "habil",   "apuntador",  None, 1, APU),
            (date(2026, 7, 27), "18-24", "habil",   "limpieza",   None, 1, LIMP),
        ]
        for (f, rango, tipo, fun, texto, cant, precio) in LAB:
            db.add(OperationLiveInvoiceLaborLine(
                invoice_id=inv.id, shift_date=f, turno_range=rango,
                turno_tipo=tipo, funcion=fun, funcion_texto=texto,
                cantidad_recibido=cant, precio_unitario_recibido=precio,
            ))

        db.add(OperationLiveInvoiceCargoLine(
            invoice_id=inv.id, tipo="pala_orden", descripcion="Pala a la orden",
            cantidad=Decimal("175"), unidad="uds",
            precio_unitario=Decimal("1515.00"), subtotal=Decimal("265125.00"),
        ))
        db.add(OperationLiveInvoiceTotals(
            invoice_id=inv.id,
            base_tonelaje_recibido=Decimal("16314264.96"),
            base_jornales_recibido=Decimal("3749516.90"),
            supa_pct=Decimal("9.5"), contrib_coop_pct=Decimal("85.0"),
            iva_pct=Decimal("21.0"), iibb_pct=Decimal("5.0"),
            supa_monto=Decimal("356204.11"),
            contrib_coop_monto=Decimal("3187089.37"),
            adm_total=Decimal("7292810.37"),
            iva_monto=Decimal("5013162.07"),
            iibb_monto=Decimal("1193610.02"),
            total_factura_declarado=Decimal("30078972.42"),
        ))

        db.commit()

        # ── Verificación ───────────────────────────────────────────────────
        tot_kg = sum(p[11] for p in P)
        tot_vj = sum(p[10] for p in P)
        print(f"✓ Sesión #{ses.id} '{SHIP}' creada (closed) con {len(P)} partes.")
        print(f"  Viajes {tot_vj} (esperado 96) · kg {tot_kg:,} (esperado 2.739.600)")
        assert tot_vj == 96 and tot_kg == 2_739_600, "¡Totales no coinciden con el acumulado del parte 5!"
        print(f"✓ Factura draft #{inv.id}: 5 líneas tonelaje + {len(LAB)} jornales + pala + totales.")
        print("  Revisar en: /operations/live/%d/invoice/%d/review" % (ses.id, inv.id))
    finally:
        db.close()


if __name__ == "__main__":
    main(force="--force" in sys.argv)
