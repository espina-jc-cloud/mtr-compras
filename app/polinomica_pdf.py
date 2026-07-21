"""
Polinómica CNA — PDF del remito de tarifas (ReportLab).

Réplica del preview: logo MTR + MTR S.A. + nº de remito, bloque navy con el
operativo en gold, 3 info boxes, tabla Servicio/Tarifa vigente, observaciones.
Sin footer, sin columna base ni % de aumento.
"""
import io
import os
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                Spacer, Image, HRFlowable)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm

from app.polinomica_calc import fmt_ars

NAVY  = colors.HexColor("#0B1F3A")
GOLD  = colors.HexColor("#C9A84C")
GOLD_LIGHT = colors.HexColor("#E8C97A")
MUTED = colors.HexColor("#6B7A8D")
LIGHT = colors.HexColor("#F4F6F9")
BORDER = colors.HexColor("#E0E6EF")
TEXT  = colors.HexColor("#1A2B3C")

_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "static", "logo_mtr.png")

_F = "Helvetica"
_FB = "Helvetica-Bold"


def _p(text, size=10, color=TEXT, bold=False, align=0, upper=False):
    if upper:
        text = str(text).upper()
    return Paragraph(str(text), ParagraphStyle(
        "p", fontName=_FB if bold else _F, fontSize=size, textColor=color,
        alignment=align, leading=size * 1.3))


def generar_pdf_remito(remito, tarifas: list[dict]) -> bytes:
    """remito: PolinomicaRemito · tarifas: [{nombre, cat, nueva}] → bytes del PDF."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    story = []

    # ── Header: logo + MTR S.A. | nº remito ──────────────────────────────────
    logo = Image(_LOGO, width=17 * mm, height=17 * mm) if os.path.exists(_LOGO) else _p("")
    head = Table(
        [[logo,
          _p("MTR S.A.", size=17, bold=True, color=NAVY),
          _p(remito.numero, size=11, bold=True, color=MUTED, align=2)]],
        colWidths=[22 * mm, 90 * mm, 62 * mm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
    ]))
    story.append(head)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER))
    story.append(Spacer(1, 5 * mm))

    # ── Bloque navy con el operativo ─────────────────────────────────────────
    op = Table([[_p(remito.operativo, size=13, bold=True, color=GOLD_LIGHT)]],
               colWidths=[174 * mm])
    op.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(op)
    story.append(Spacer(1, 5 * mm))

    # ── 3 info boxes: Producto | Inicio | Fin ────────────────────────────────
    def _fecha(d):
        return d.strftime("%d/%m/%Y") if d else "—"
    boxes = Table([[
        Table([[_p("PRODUCTO", size=7.5, color=MUTED, bold=True)],
               [_p(remito.producto or "—", size=10.5, bold=True)]], colWidths=[52 * mm]),
        Table([[_p("INICIO", size=7.5, color=MUTED, bold=True)],
               [_p(_fecha(remito.fecha_ini), size=10.5, bold=True)]], colWidths=[52 * mm]),
        Table([[_p("FIN", size=7.5, color=MUTED, bold=True)],
               [_p(_fecha(remito.fecha_fin), size=10.5, bold=True)]], colWidths=[52 * mm]),
    ]], colWidths=[58 * mm, 58 * mm, 58 * mm])
    boxes.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (0, 0), 0.7, BORDER),
        ("BOX", (1, 0), (1, 0), 0.7, BORDER),
        ("BOX", (2, 0), (2, 0), 0.7, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(boxes)
    story.append(Spacer(1, 6 * mm))

    # ── Tabla de tarifas: Servicio | Tarifa vigente ──────────────────────────
    data = [[_p("SERVICIO", size=8.5, bold=True, color=colors.white),
             _p("TARIFA VIGENTE", size=8.5, bold=True, color=colors.white, align=2)]]
    for t in tarifas:
        data.append([
            _p(t["nombre"], size=9.5),
            _p(fmt_ars(t["nueva"]), size=10, bold=True, color=NAVY, align=2),
        ])
    tbl = Table(data, colWidths=[128 * mm, 46 * mm], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F7F9FC")))
    tbl.setStyle(TableStyle(style))
    story.append(tbl)

    # ── Observaciones ────────────────────────────────────────────────────────
    if remito.observaciones:
        story.append(Spacer(1, 6 * mm))
        story.append(_p("OBSERVACIONES", size=7.5, color=MUTED, bold=True))
        story.append(Spacer(1, 1.5 * mm))
        story.append(_p(remito.observaciones, size=9.5))

    doc.build(story)
    return buf.getvalue()
