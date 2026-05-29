"""
Central product name normalization for MTR Gestión.

Ensures that product name variants resolve to a single canonical form
before any comparison, so "TRIPLE", "TRIPLE (STP)", "Súper Fosfato Triple"
and "STP" are all treated as the same product.

Usage:
    from app.product_normalize import normalize_product

    normalize_product("TRIPLE (STP)")  → "TRIPLE"
    normalize_product("MAP (10-50)")   → "MAP"
    normalize_product("Sulfato de Amonio") → "AMSUL"
"""
import re
import unicodedata


# ── Canonical rules (priority order — most specific first) ──────────────────
# Each entry: (canonical_name, [trigger_keywords])
# A product matches if ANY trigger keyword appears as a whole word in the
# cleaned product string.
_RULES: list[tuple[str, list[str]]] = [
    ("TRIPLE",  ["TRIPLE", "STP", "SUPERFOSFATO TRIPLE", "SUPER FOSFATO TRIPLE",
                 "SÚPER FOSFATO", "SUPER FOSFATO", "FOSFATO TRIPLE"]),
    ("MAP",     ["MAP", "MONOAMONICO", "MONOAMÓNICO", "FOSFATO MONOAMONICO",
                 "MONO AMONICO"]),
    ("DAP",     ["DAP", "DIAMONICO", "DIAMÓNICO", "FOSFATO DIAMONICO",
                 "DI AMONICO"]),
    ("UREA",    ["UREA"]),
    ("MOP",     ["MOP", "CLORURO DE POTASIO", "CLORURO POTASIO"]),
    ("AMSUL",   ["AMSUL", "SULFATO DE AMONIO", "SULFATO AMONIO"]),
]


def _clean(s: str) -> str:
    """
    Uppercase, remove accents/diacritics, replace non-alphanumeric chars
    with a single space, collapse multiple spaces.
    """
    # NFD decomposition → drop combining marks (accents)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper()
    # Replace non-word characters (parens, dashes, slashes, etc.) with space
    s = re.sub(r"[^\w\s]", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_product(product: str | None) -> str:
    """
    Return the canonical product name.

    If no rule matches, returns the cleaned uppercase string.
    An empty / None input returns "".

    Examples:
        "TRIPLE"              → "TRIPLE"
        "TRIPLE (STP)"        → "TRIPLE"
        "Súper Fosfato Triple" → "TRIPLE"
        "MAP (10-50)"         → "MAP"
        "MAP ( 10-50)"        → "MAP"
        "Cloruro de Potasio"  → "MOP"
        "Sulfato de Amonio"   → "AMSUL"
        "UREA GRANULADA"      → "UREA"
    """
    if not product:
        return ""
    clean = _clean(product)

    for canonical, triggers in _RULES:
        for trigger in triggers:
            trigger_clean = _clean(trigger)
            # Word-boundary match: the trigger must appear as a whole word
            pattern = r"\b" + re.escape(trigger_clean) + r"\b"
            if re.search(pattern, clean):
                return canonical

    # No rule matched — return cleaned version (stable for unknown products)
    return clean
