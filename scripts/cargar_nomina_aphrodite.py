"""
Carga la nómina de personal MTR (planilla de puestos) de los partes del
MDS APHRODITE, tal cual figura en los papeles. Idempotente: reemplaza la
nómina de cada parte, no toca el resto de los datos.
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app import models  # noqa: F401
from app.models_live import OperationLiveSession, OperationLiveShift, OperationLiveStaff

# (puesto, nombre, funcion, observaciones) por número de parte
NOMINA = {
    1: [  # 25/07 00 a 06
        ("coordinador", "FLOREZ LEANDRO", None, None),
        ("capataz",     "RAMIREZ CARLOS", None, None),
        ("gangos",      "MIÑO JUAN",      None, None),
        ("guinchero",   "DELGADO JOSE",   None, None),
        ("guinchero",   "NUÑEZ LUCIANO",  None, None),
        ("guinchero",   "MONZON DARIO",   "ELECTRICISTA", None),
        ("apuntador",   "RAMIREZ CESAR",  None, None),
        ("maquinista",  "RODRIGUEZ NAHUEL", "PALA COOP", None),
        ("maquinista",  "VELO CARLOS",    "AUTOELEVADOR MTR", None),
        ("aguatero",    "CAMPESTRINI LEONARDO", None, None),
        ("tolvero",     "SOTELO JORGE",   None, None),
        ("botonero",    "MARECO MIGUEL",  None, None),
        ("limpieza",    "CORREA LAUTARO", None, None),
        ("limpieza",    "ALANIZ WALTER",  None, None),
    ],
    2: [  # 25/07 06 a 12
        ("coordinador", "FLORES LEANDRO", None, None),
        ("capataz",     "CHAVEZ DARIO",   None, None),
        ("gangos",      "MIÑO JUAN",      None, None),
        ("guinchero",   "CAMPESTRINI CARLOS", None, None),
        ("guinchero",   "CORREA LAUTARO", None, None),
        ("guinchero",   "MONZON DARIO",   "GRAMPAS", None),
        ("apuntador",   "RAMIREZ CESAR",  None, None),
        ("maquinista",  "VELO CARLOS",    "PALA EN PLAZOLETA", None),
        ("maquinista",  "MEJIA IVAN",     "AUTOELEVADOR", None),
        ("maquinista",  "RODRIGUEZ NAHUEL", "RETRO", None),
        ("maquinista",  "MARINEZ JUAN",   "RETRO", None),
        ("aguatero",    "PIEDRA GONZALO", None, None),
        ("tolvero",     "PESECHE BRAIAN", None, None),
        ("botonero",    "FERNANDEZ CRISTIAN", None, None),
        ("soguero",     "CARO JUAN",      None, None),
        ("limpieza",    "TAPARE RUBEN",   None, None),
        ("limpieza",    "ALANIZ WALTER",  None, None),
    ],
    3: [  # 25/07 12 a 18
        ("coordinador", "GOMEZ MATIAS",   None, None),
        ("capataz",     "CHAVEZ MARIO",   None, None),
        ("gangos",      "FERREYRA DIEGO", None, None),
        ("guinchero",   "HERRERA MARCELO", None, None),
        ("guinchero",   "CAMPESTRINI CARLOS", None, None),
        ("guinchero",   "MONZON GABRIEL", "GRAMPAS x2", None),
        ("apuntador",   "RAMALLO GUSTAVO", None, None),
        ("maquinista",  "VELOS CARLOS",   "RETRO", None),
        ("maquinista",  "PONCE JOSE",     "RETRO", None),
        ("maquinista",  "RODRIGUEZ NAHUEL", "AUTOELEVADOR", None),
        ("aguatero",    "RAMOS ANGEL",    None, None),
        ("tolvero",     "BARRIOS IVAN",   None, None),
        ("botonero",    "MARECO MIGUEL",  None, None),
        ("soguero",     "RAMIREZ ANGEL",  None, None),
        ("limpieza",    "FIGUEROA EDUARDO", None, None),
        ("limpieza",    "CAMPESTRINI YAMIL", None, None),
    ],
    4: [  # 25/07 18 a 24
        ("coordinador", "GOMEZ MATIAS",   None, None),
        ("capataz",     "JAVIER CAMPESTRINI", None, None),
        ("gangos",      "QUIROGA BRAIAN", None, None),
        ("guinchero",   "HERRERA MARCELO", None, None),
        ("guinchero",   "NUÑEZ LUCIANO",  None, None),
        ("guinchero",   "MONZON GABRIEL", "GRAMPAS", None),
        ("apuntador",   "RAMALLO GUSTAVO", None, None),
        ("maquinista",  "RODRIGUEZ NICOLAS", "RETRO", None),
        ("maquinista",  "VELO CARLOS",    "RETRO", None),
        ("maquinista",  "BUSTOS MATIAS",  "AUTOELEVADOR", None),
        ("aguatero",    "BARRIOS IVAN",   None, None),
        ("tolvero",     "CESPEDES MAURICIO", None, None),
        ("botonero",    "FIGUEROA GUSTAVO", None, None),
        ("soguero",     "RAMIREZ ANGEL",  None, None),
        ("limpieza",    "FIGUEROA EDUARDO", None, None),
        ("limpieza",    "PIEDRA GONZALO", None, None),
        ("limpieza",    "PIEDRA LUIS",    "TOLVA", None),
    ],
}


def run():
    db = SessionLocal()
    try:
        s = (db.query(OperationLiveSession)
             .filter_by(ship_name="MDS APHRODITE")
             .order_by(OperationLiveSession.id.desc()).first())
        if not s:
            print("✗ No existe la sesión MDS APHRODITE"); return
        total = 0
        for num, filas in NOMINA.items():
            sh = (db.query(OperationLiveShift)
                  .filter_by(session_id=s.id, shift_number=num).first())
            if not sh:
                print(f"  ! parte {num} no encontrado"); continue
            # Reemplazar solo la nómina (filas con puesto), no la gente por adm.
            (db.query(OperationLiveStaff)
               .filter(OperationLiveStaff.shift_id == sh.id,
                       OperationLiveStaff.puesto.isnot(None)).delete(synchronize_session=False))
            for puesto, nombre, funcion, obs in filas:
                db.add(OperationLiveStaff(
                    shift_id=sh.id, funcion=puesto, puesto=puesto, nombre=nombre,
                    funcion_texto=funcion, observaciones=obs,
                    cantidad=1, turno_range=None, empresa="mtr",
                ))
            total += len(filas)
            print(f"  ✓ parte {num}: {len(filas)} personas")
        db.commit()
        print(f"✓ Nómina cargada: {total} personas en {len(NOMINA)} partes.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
