"""Tests del motor Polinómica CNA — valores de verificación obligatoria (Jun 2026).

Validados contra el Excel original. Si alguno falla, NO deployar.
Correr:  .venv/bin/python -m pytest tests/ -q
"""
from app.polinomica_calc import calcular_acumulados, calcular_tarifas, HISTORIAL_SEED, fmt_ars


def test_acumulados_jun_2026():
    acc = calcular_acumulados(HISTORIAL_SEED)
    assert abs(acc["supa"]    - 1.744396910741602)  < 0.0001
    assert abs(acc["cam"]     - 1.6113797985594338) < 0.0001
    assert abs(acc["ipc"]     - 1.6080403813464912) < 0.0001
    assert abs(acc["comb"]    - 2.001718495958287)  < 0.0001
    assert abs(acc["usd"]     - 1.493876585489429)  < 0.0001
    assert abs(acc["fadeeac"] - 1.7496591552874217) < 0.0001


def test_tarifas_jun_2026():
    tarifas = calcular_tarifas(HISTORIAL_SEED)
    by_nombre = {t["nombre"]: t for t in tarifas}
    assert abs(by_nombre["Desestiba de productos granel y carga de camiones Pto. San Nicolás"]["nueva"] - 16442.984784399574) < 0.01
    assert abs(by_nombre["Transporte a depósito MTR"]["nueva"] - 10083.285711921411) < 0.01
    assert abs(by_nombre["Pesaje y estiba en depósito"]["nueva"] - 4754.908653248289) < 0.01
    assert abs(by_nombre["Embolsado en bolsas de 25 kg y carga de camiones"]["nueva"] - 30023.613021775836) < 0.01
    assert abs(by_nombre["Embolsado en bolsas de 50 kg y carga de camiones"]["nueva"] - 22963.95251164551) < 0.01
    assert abs(by_nombre["Embolsado en bolsas de 1000 kg y carga de camiones"]["nueva"] - 21292.63136814875) < 0.01
    assert abs(by_nombre["Desestiba de productos en bolsones y carga de camiones Pto. San Nicolás"]["nueva"] - 20134.26708293825) < 0.01
    assert abs(by_nombre["Despacho en bolsones"]["nueva"] - 4754.908653248289) < 0.01


def test_formato_ars():
    assert fmt_ars(16442.984784399574) == "$16.442,98"
    assert fmt_ars(4754.908653248289) == "$4.754,91"
    assert fmt_ars(1000000) == "$1.000.000,00"
