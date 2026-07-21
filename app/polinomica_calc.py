"""
Polinómica CNA — motor de cálculo puro (sin framework).

Traducción 1:1 del JS/Excel validado. NO simplificar ni cambiar:
  tarifa_vigente = Σ (componente_base_índice × acumulado_índice)
  acumulado_índice = Π (1 + variación_mensual_i) sobre todo el historial

Los componentes base son montos fijos en pesos (no proporciones), con la
precisión decimal exacta del Excel original.
"""

# (categoría, nombre, base_total, supa, cam, ipc, comb, fadeeac, usd)
TARIFAS_BASE = [
    ("Granel",   "Desestiba de productos granel y carga de camiones Pto. San Nicolás",
     9604,   5762.4, 0,      1440.6, 960.4,   0,    1440.6),
    ("Granel",   "Transporte a depósito MTR",
     5763,   0,      0,      0,      0,       5763, 0),
    ("Granel",   "Pesaje y estiba en depósito",
     2845,   0,      1422.5, 426.75, 569,     0,    426.75),
    ("Granel",   "Despacho a granel zarandeado",
     2845,   0,      1422.5, 426.75, 569,     0,    426.75),
    ("Bolsones", "Pesaje, ingreso y estiba en depósito",
     2845,   0,      1422.5, 426.75, 569,     0,    426.75),
    ("Bolsones", "Embolsado en bolsas de 25 kg y carga de camiones",
     17964,  0,      8982,   2694.6, 3592.8,  0,    2694.6),
    ("Bolsones", "Embolsado en bolsas de 50 kg y carga de camiones",
     13740,  0,      6870,   2061,   2748,    0,    2061),
    ("Bolsones", "Embolsado en bolsas de 1000 kg y carga de camiones",
     12740,  0,      6370,   1911,   2548,    0,    1911),
    ("Bolsones", "Desestiba de productos en bolsones y carga de camiones Pto. San Nicolás",
     11760,  7056,   0,      1764,   1176,    0,    1764),
    ("Bolsones", "Despacho en bolsones",
     2845,   0,      1422.5, 426.75, 569,     0,    426.75),
]

# (mes, supa, cam, ipc, comb, usd, fadeeac) — variaciones mensuales como fracción
HISTORIAL_SEED = [
    ("Nov 2024", 0.05,   0.03,   0.024, 0.027705627705627706, 0.01965725806451613,  0.02),
    ("Dic 2024", 0.05,   0.03,   0.027, 0.018534119629317607, 0.020266930301532378, 0.017),
    ("Ene 2025", 0.03,   0.025,  0.022, 0.018196856906534328, 0.020833333333333332, 0.0262),
    ("Feb 2025", 0.02,   0.025,  0.024, 0.020308692120227456, 0.010678690080683437, 0.0162),
    ("Mar 2025", 0.02,   0.012,  0.037, 0.01910828025477707,  0.008687485325193707, 0.0192),
    ("Abr 2025", 0.04,   0.0288, 0.028, 0.0172,               0.08472998137802606,  0.0377),
    ("May 2025", 0.03,   0.01,   0.015, -0.0253,              0.019742489270386267, 0.0081),
    ("Jun 2025", 0.03,   0.01,   0.016, 0.011,                0.01430976430976431,  0.0256),
    ("Jul 2025", 0.02,   0.0636, 0.019, 0.08028059236165237,  0.14024896265560166,  0.0403),
    ("Ago 2025", 0.02,   0.01,   0.019, 0.024531024531024532, -0.023289665211062592, 0.0354),
    ("Sep 2025", 0.02,   0.012,  0.019, 0.0387,               0.028315946348733235, 0.0292),
    ("Oct 2025", 0.02,   0.0387, 0.023, 0.0373,               0.0471,               0.0327),
    ("Nov 2025", 0.02,   0.01,   0.025, 0.0667,               0.0045,               0.0265),
    ("Dic 2025", 0.025,  0.047,  0.028, 0.06311274509803921,  0.002411298656562177, 0.0227),
    ("Ene 2026", 0.025,  0.01,   0.029, 0.0173,               0.0151,               0.028),
    ("Feb 2026", 0.025,  0.01,   0.024, 0.0374,               -0.0542,              0.0228),
    ("Mar 2026", 0.03,   0.0729, 0.034, 0.11086837793555435,  -0.010737294201861132, 0.1015),
    ("Abr 2026", 0.03,   0.01,   0.028, 0.0978,               0.0065,               0.0242),
    ("May 2026", 0.03,   0.016,  0.021, 0.0251,               0.0122,               0.0191),
    ("Jun 2026", 0.03,   0.015,  0.019, 0.01,                 0.0526,               0.0181),
]

INDICES = ["supa", "cam", "ipc", "comb", "usd", "fadeeac"]

INDICE_LABELS = {
    "supa":    "Jornal SUPA",
    "cam":     "Jornal Camioneros",
    "ipc":     "IPC",
    "comb":    "Combustible",
    "usd":     "USD BNA",
    "fadeeac": "FADEEAC",
}


def _rows_as_dicts(historial):
    """Acepta tuplas del seed (mes, supa, cam, ipc, comb, usd, fadeeac) o dicts."""
    out = []
    for r in historial:
        if isinstance(r, dict):
            out.append(r)
        else:
            mes, supa, cam, ipc, comb, usd, fadeeac = r
            out.append({"mes": mes, "supa": supa, "cam": cam, "ipc": ipc,
                        "comb": comb, "usd": usd, "fadeeac": fadeeac})
    return out


def calcular_acumulados(historial):
    """Π (1 + variación) por índice sobre todo el historial → dict índice→multiplicador."""
    rows = _rows_as_dicts(historial)
    acc = {k: 1.0 for k in INDICES}
    for r in rows:
        for k in INDICES:
            acc[k] *= (1.0 + float(r[k]))
    return acc


def calcular_tarifas(historial):
    """Tarifas vigentes: Σ (componente_base × acumulado) por servicio.

    Devuelve [{cat, nombre, base, nueva, aumento_pct}] en el orden de TARIFAS_BASE.
    """
    acc = calcular_acumulados(historial)
    out = []
    for cat, nombre, base, c_supa, c_cam, c_ipc, c_comb, c_fadeeac, c_usd in TARIFAS_BASE:
        nueva = (c_supa    * acc["supa"]
                 + c_cam     * acc["cam"]
                 + c_ipc     * acc["ipc"]
                 + c_comb    * acc["comb"]
                 + c_fadeeac * acc["fadeeac"]
                 + c_usd     * acc["usd"])
        out.append({
            "cat": cat, "nombre": nombre, "base": base, "nueva": nueva,
            "aumento_pct": (nueva / base - 1.0) * 100.0 if base else 0.0,
        })
    return out


def promedio_acumulado(historial):
    """Promedio simple de los aumentos acumulados de los 6 índices (en %)."""
    acc = calcular_acumulados(historial)
    return sum((v - 1.0) * 100.0 for v in acc.values()) / len(INDICES)


def serie_acumulados(historial):
    """Acumulados mes a mes (para el line chart): [{mes, supa, ..., tarifas:{nombre→valor}}]."""
    rows = _rows_as_dicts(historial)
    acc = {k: 1.0 for k in INDICES}
    serie = []
    for r in rows:
        for k in INDICES:
            acc[k] *= (1.0 + float(r[k]))
        punto = {"mes": r["mes"], **{k: acc[k] for k in INDICES}}
        # Tarifas clave en ese punto del tiempo
        tarifas = {}
        for cat, nombre, base, c_supa, c_cam, c_ipc, c_comb, c_fadeeac, c_usd in TARIFAS_BASE:
            tarifas[nombre] = (c_supa * acc["supa"] + c_cam * acc["cam"]
                               + c_ipc * acc["ipc"] + c_comb * acc["comb"]
                               + c_fadeeac * acc["fadeeac"] + c_usd * acc["usd"])
        punto["tarifas"] = tarifas
        serie.append(punto)
    return serie


def fmt_ars(valor) -> str:
    """$16.442,98 — formato argentino, siempre 2 decimales."""
    return "$" + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
