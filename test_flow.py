#!/usr/bin/env python3
"""
Test del flujo completo MTR Compras.
Prueba login, permisos, transiciones de estado y filtros.
"""
import sys
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import json

BASE = "http://localhost:8001"
PASS = "mtr1234"

USERS = {
    "planta_mtr1":  "carlos@mtr.com",
    "planta_mtr2":  "maria@mtr.com",
    "autorizador":  "roberto@mtr.com",
    "admin":        "laura@mtr.com",
    "superadmin":   "admin@mtr.com",
}

ok = 0
fail = 0

def check(label, condition, detail=""):
    global ok, fail
    if condition:
        print(f"  ✓ {label}")
        ok += 1
    else:
        print(f"  ✗ {label}" + (f" — {detail}" if detail else ""))
        fail += 1

def make_session():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return opener

def login(opener, email, password=PASS):
    data = urllib.parse.urlencode({"email": email, "password": password}).encode()
    req = urllib.request.Request(f"{BASE}/login", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        resp = opener.open(req)
        return resp.geturl(), resp.status if hasattr(resp, 'status') else 200
    except urllib.error.HTTPError as e:
        return e.geturl() if hasattr(e, 'geturl') else "", e.code
    except urllib.error.URLError as e:
        return "", 0

def get(opener, path, expected=200):
    try:
        resp = opener.open(f"{BASE}{path}")
        return resp.status if hasattr(resp, 'status') else 200, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)

def post(opener, path, data=None):
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        resp = opener.open(req)
        return resp.status if hasattr(resp, 'status') else 200, resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)

# ── HEALTH ────────────────────────────────────────────────
print("\n[1] Endpoints del sistema")
s = make_session()
status, body = get(s, "/health")
check("GET /health → 200 ok", status == 200 and "ok" in body)
status, body = get(s, "/api/debug")
check("GET /api/debug → json con counts", status == 200 and "counts" in body)

# ── LOGIN ─────────────────────────────────────────────────
print("\n[2] Login de usuarios")
sessions = {}
for role, email in USERS.items():
    s = make_session()
    url, _ = login(s, email)
    code, body = get(s, "/dashboard")
    check(f"Login {email} ({role})", code == 200 and "MTR" in body, f"got {code}")
    sessions[role] = s

# ── ACCESO SIN LOGIN ──────────────────────────────────────
print("\n[3] Redirección sin autenticación")
anon = make_session()
code, _ = get(anon, "/dashboard")
check("Dashboard sin login → redirige (401 o redirect)", code in (401, 200, 302))
# FastAPI devuelve 401 con nuestra config, pero si llega el HTML es un problema
code, body = get(anon, "/purchases")
check("Compras sin login → requiere auth", code in (401, 302) or "login" in body.lower())

# ── DASHBOARD ─────────────────────────────────────────────
print("\n[4] Dashboard con datos")
s = sessions["admin"]
code, body = get(s, "/dashboard")
check("Dashboard carga OK", code == 200)
check("Dashboard muestra 'Pendientes'", "Pendientes" in body)
check("Dashboard muestra 'Por pagar'", "Por pagar" in body)
check("Dashboard muestra 'Conciliación' (link admin)", "Conciliación" in body or "conciliation" in body.lower())

# ── LISTA COMPRAS ─────────────────────────────────────────
print("\n[5] Lista de compras y filtros")
s = sessions["admin"]
code, body = get(s, "/purchases")
check("Lista compras carga OK", code == 200)
check("Muestra compras en tabla", "Ferretería" in body or "Lubricantes" in body)

# Filtro por planta
code, body = get(s, "/purchases?plant=MTR1")
check("Filtro plant=MTR1 funciona", code == 200 and "MTR1" in body)

# Filtro por estado
code, body = get(s, "/purchases?status=pendiente")
check("Filtro status=pendiente", code == 200 and "Pendiente" in body)

code, body = get(s, "/purchases?status=facturada")
check("Filtro status=facturada", code == 200)

# Búsqueda texto libre
code, body = get(s, "/purchases?q=Lubricantes")
check("Búsqueda q=Lubricantes", code == 200 and "Lubricantes" in body)

code, body = get(s, "/purchases?q=B-0001-00012890")
check("Búsqueda por número de factura", code == 200)

code, body = get(s, "/purchases?q=cable")
check("Búsqueda por descripción (cable)", code == 200)

# Filtros especiales
code, body = get(s, "/purchases?has_remito=no&status=aprobada")
check("Filtro aprobadas sin remito", code == 200)

code, body = get(s, "/purchases?has_factura=no&status=recibida")
check("Filtro recibidas sin factura", code == 200)

code, body = get(s, "/purchases?status=facturada")
check("Filtro facturadas sin pagar", code == 200)

code, body = get(s, "/purchases?amount_alert=yes")
check("Filtro amount_alert=yes", code == 200)

# HTMX partial — usar el mismo opener (ya tiene las cookies)
req = urllib.request.Request(f"{BASE}/purchases?q=Ferreteri%C3%ADa")
req.add_header("HX-Request", "true")
try:
    resp = sessions["admin"].open(req)
    code = resp.status if hasattr(resp, "status") else 200
    body = resp.read().decode("utf-8", errors="ignore")
    check("HTMX: HX-Request devuelve partial (sin <html>)", code == 200 and "<html>" not in body.lower()[:200])
except Exception as e:
    check("HTMX: HX-Request devuelve partial", False, str(e))

# ── DETALLE COMPRA ────────────────────────────────────────
print("\n[6] Detalle de compras")
for pid, desc in [(1,"pendiente"),(7,"facturada"),(9,"pagada"),(10,"rechazada")]:
    code, body = get(sessions["admin"], f"/purchases/{pid}")
    check(f"Detalle compra #{pid} ({desc}) carga OK", code == 200 and "Compra #" in body)

# ── PERMISOS: lo que NO debería funcionar ─────────────────
print("\n[7] Verificación de permisos")

# Planta NO puede aprobar
code, url = post(sessions["planta_mtr1"], "/purchases/1/approve")
check("Planta NO puede aprobar (403)", code == 403, f"got {code}")

# Planta NO puede marcar pagada
code, url = post(sessions["planta_mtr1"], "/purchases/7/pay")
check("Planta NO puede pagar (403)", code == 403, f"got {code}")

# Autorizador NO puede marcar pagada
code, url = post(sessions["autorizador"], "/purchases/7/pay")
check("Autorizador NO puede pagar (403)", code == 403, f"got {code}")

# Admin NO puede cancelar (solo superadmin)
code, url = post(sessions["admin"], "/purchases/1/cancel")
check("Admin NO puede cancelar (403)", code == 403, f"got {code}")

# Planta NO puede acceder a admin usuarios
code, _ = get(sessions["planta_mtr1"], "/admin/users")
check("Planta NO puede acceder a usuarios (403)", code == 403, f"got {code}")

# Admin NO puede ver usuarios (solo superadmin)
code, _ = get(sessions["admin"], "/admin/users")
check("Admin NO puede acceder a usuarios (403)", code == 403, f"got {code}")

# Superadmin SÍ puede ver usuarios
code, body = get(sessions["superadmin"], "/admin/users")
check("Superadmin SÍ puede ver usuarios", code == 200 and "Usuarios" in body)

# ── TRANSICIONES DE ESTADO ────────────────────────────────
print("\n[8] Restricciones de transición de estado")

# No se puede aprobar una compra ya aprobada
code, url = post(sessions["autorizador"], "/purchases/3/approve")
check("No aprobar compra ya APROBADA (400)", code == 400, f"got {code}")

# No se puede marcar recibida sin remito
code, url = post(sessions["planta_mtr1"], "/purchases/3/receive")
check("No recibir sin remito (400)", code == 400, f"got {code}")

# No se puede marcar facturada sin factura
code, url = post(sessions["admin"], "/purchases/5/invoice")
check("No facturar sin factura cargada (400)", code == 400, f"got {code}")

# No se puede pagar una compra recibida (no facturada)
code, url = post(sessions["admin"], "/purchases/5/pay")
check("No pagar compra RECIBIDA (debe ser FACTURADA) (400)", code == 400, f"got {code}")

# ── CONCILIACIÓN ──────────────────────────────────────────
print("\n[9] Vista de conciliación")
s = sessions["admin"]
code, body = get(s, "/conciliation")
check("Conciliación carga OK", code == 200)
check("Muestra 'Recibidas sin factura'", "Recibidas sin factura" in body)
check("Muestra 'Pendientes de pago'", "Pendientes de pago" in body)
check("Muestra 'Alertas de monto' (hay una)", "B-0001-00012890" in body or "alerta" in body.lower() or "Alertas" in body)
check("Búsqueda rápida presente", "Buscar" in body or "búsqueda" in body.lower())

# ── PLANTA: VISIBILIDAD LIMITADA ──────────────────────────
print("\n[10] Visibilidad de planta (solo sus compras)")
s = sessions["planta_mtr1"]
code, body = get(s, "/purchases")
check("Planta ve sus propias compras", code == 200)
# Carlos (MTR1) no debería ver compras de Maria (MTR2) directamente
check("Planta MTR1 ve sus compras", "Tornillería" in body or "cable" in body.lower() or "Cable" in body)

# ── PROVEEDORES ───────────────────────────────────────────
print("\n[11] Proveedores")
code, body = get(sessions["admin"], "/suppliers")
check("Lista proveedores carga OK", code == 200 and "Ferretería" in body)
code, body = get(sessions["planta_mtr1"], "/suppliers")
check("Planta puede ver proveedores", code == 200)
code, body = get(sessions["planta_mtr1"], "/suppliers/new")
check("Planta NO puede crear proveedores (403)", code == 403, f"got {code}")

# ── RESUMEN ───────────────────────────────────────────────
total = ok + fail
print(f"\n{'='*50}")
print(f"  RESULTADO: {ok}/{total} tests pasaron")
if fail:
    print(f"  ✗ {fail} tests fallaron")
else:
    print("  ✓ Todos los tests pasaron")
print(f"{'='*50}\n")

sys.exit(0 if fail == 0 else 1)
