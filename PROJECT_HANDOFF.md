# MTR Compras — Handoff del Proyecto
**Última actualización:** junio 2026  
**Estado:** sistema en producción (Railway) + cambios locales pendientes de deploy  

---

## 1. Visión general

**Sistema:** MTR Gestión — plataforma interna de gestión operativa para una empresa de terminales de carga (MTR1 / MTR2 / Rosario).

**Stack:**
- Backend: FastAPI + SQLAlchemy + Jinja2 (Python 3.14)
- Frontend: Tailwind CSS (via CDN), vanilla JS inline
- Base de datos: PostgreSQL (Railway prod) / SQLite (local dev)
- Archivos: Cloudinary (documentos y fotos)
- Deploy: Railway (auto-deploy desde main)
- Plantillas Excel: openpyxl

**Ejecutar localmente:**
```bash
cd /Users/juancruzespina/Desktop/mtr-compras
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
# DB local en mtr_compras_dev.db (SQLite)
```

**Migrar / crear tablas:**
```bash
python migrate.py           # crea tablas faltantes (no destructivo)
python migrate.py --reset   # borra y recrea todo (⚠ pierde datos locales)
```

**Variables de entorno (`.env`):**
```
DATABASE_URL=sqlite:///./mtr_compras_dev.db   # local
SECRET_KEY=...
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
```

---

## 2. Módulos del sistema

### 2.1 Compras (`/purchases`)
**Qué hace:** flujo completo de solicitud y autorización de compras con aprobación por roles.

**Implementado:**
- Crear / editar / eliminar (soft delete) solicitudes de compra
- Estados: `pendiente → autorizado / rechazado → pagado`
- Subir documentos (remito, factura, otro) via Cloudinary
- Historial de auditoría por compra
- Cotizaciones vinculadas a compras (`/quotes`)
- Filtros por planta, área, estado, proveedor, fechas
- Alertas de monto fuera de rango

**Archivos clave:**
- `app/routers/purchases.py`
- `app/models.py` → `Purchase`, `Document`, `AuditLog`
- `templates/purchases/`

**Limitaciones / pendiente:**
- Sin exportación a PDF/Excel todavía
- Sin notificaciones push (mail) al autorizar

---

### 2.2 Proveedores (`/suppliers`)
**Qué hace:** ABM de proveedores con cuenta corriente básica.

**Implementado:** lista, alta, edición, vista de cuenta (historial de compras ligadas).

**Archivos clave:**
- `app/routers/suppliers.py`, `app/models.py` → `Supplier`
- `templates/suppliers/`

---

### 2.3 Cotizaciones (`/quotes`)
**Qué hace:** gestión de cotizaciones previas a una compra.

**Implementado:**
- Crear cotización con ítems (descripción, cantidad, precio unitario, subtotal)
- Convertir cotización aprobada en solicitud de compra
- Subir documentos adjuntos
- Estados: `borrador → enviado → aprobado / rechazado`

**Archivos clave:**
- `app/routers/quotes.py`, `app/models.py` → `Quote`, `QuoteItem`, `QuoteDocument`
- `templates/quotes/`

---

### 2.4 Equipos y Mantenimiento (`/equipment`, `/maintenance`)
**Qué hace:** registro de equipos de planta y órdenes de mantenimiento.

**Implementado:**
- ABM de equipos con código, categoría, tipo de trabajo (261–266)
- Registros de mantenimiento correctivo / preventivo
- Filtro por equipo, planta, fecha, tipo

**Archivos clave:**
- `app/routers/equipment.py`, `app/routers/maintenance.py`
- `app/models.py` → `Equipment`, `MaintenanceRecord`
- `templates/equipment/`, `templates/maintenance/`

---

### 2.5 Combustible (`/fuel`)
**Qué hace:** registro de cargas de combustible por equipo.

**Implementado:** cargas, listado, filtros por tipo (Gasoil Común / Diesel Premium), equipo y fecha.

**Archivos clave:**
- `app/routers/fuel.py`, `app/models.py` → `FuelLoad`
- `templates/fuel/`

---

### 2.6 Operativos finalizados (`/operations`)
**Qué hace:** registro histórico de operativos de barco importados desde el sistema de balanza.

**Implementado:**
- Lista de operativos con filtros
- Detalle por operativo: toneladas por producto y por viaje
- Acumulados por producto
- Vinculación con sesiones Live (opcional)

**Archivos clave:**
- `app/routers/operations.py`
- `app/models.py` → `Operation`, `OperationTrip`, `OperationCargoSummary`
- `templates/operations/`

---

### 2.7 Live Operations — Operativos en tiempo real (`/operations/live`)
**Qué hace:** módulo completo de seguimiento de un barco en tiempo real, turno a turno.

**Implementado:**
- Crear sesión live (barco + productos + kg contratados)
- Multi-producto desde el inicio (sin producto único de sesión)
- **Multi-borrador:** se permiten N turnos simultáneos en borrador (quitada restricción de "un solo turno abierto")
- Crear turno (Parte) con:
  - Bodegas: viajes, toneladas MTR, toneladas Cooperativa, producto por bodega
  - Demoras: tipo, minutos, observación
  - Equipos: máquina, horas trabajadas
  - Personal: nombre, rol, horas
- **Guardar borrador** funciona correctamente en desktop y mobile (fix de sincronización viewport-aware)
- Finalizar turno → estado `closed`
- Galería de fotos por sesión (Cloudinary)
- Factura cooperativa (parte diario, jornales, ítems especiales)
- Revisión MTR de factura (campos `_revisado`)
- Conciliación final por producto
- Cierre formal de sesión

**Bug importante ya resuelto — Guardar borrador en desktop:**  
Starlette `form.get()` devuelve el ÚLTIMO valor cuando hay claves duplicadas.  
El formulario generaba dos inputs por fila (uno desktop, uno mobile). En desktop, el último era el input mobile vacío → los datos se perdían.  
Fix: antes del submit, `getComputedStyle` detecta el div visible y deshabilita los inputs del div invisible.  
Archivo: `templates/operations/live/shift_form.html` (fondo del `<script>`, IIFE al final).

**Archivos clave:**
- `app/routers/operations_live.py`
- `app/models_live.py` → `OperationLiveSession`, `OperationLiveShift`, `OperationLiveBodegaData`, `OperationLiveDelayEntry`, `OperationLiveEquipmentEntry`, `OperationLivePersonalEntry`, `OperationLivePhoto`, `OperationLiveInvoice`, `OperationLiveReconciliation`
- `app/live_utils.py` — helpers de acumulados
- `templates/operations/live/`

**Pendiente:**
- Exportar parte a PDF
- Resumen ejecutivo del operativo completo

---

### 2.8 Despachos / Cupos (`/despachos`) ⚠ LOCAL ONLY — NO DEPLOYADO
**Qué hace:** importación y seguimiento de cupos de carga de camiones (Nutrien) y despachos reales (CNA).

**Implementado:**
- Importar Excel Nutrien (cupos) o CNA (despachos) con preview antes de confirmar
- Filtro de fecha en importación para evitar importar histórico
- Detección automática de fuente por nombre de hojas
- Parser Nutrien entiende 3 tipos de fila:
  - **Simple:** 1 fila = 1 camión
  - **Embolsado:** SP + BOLSA=SI + sub-fila packaging → 1 camión, presentación inferida
  - **Mezcla:** D1/D2 (ingredientes, descartados) + SM (total, guardado)
- Detección de unidades: promedio > 200 → valores en kg → divide / 1000
- Deduplicación por SHA256 de campos clave
- Descarga de Plantilla MTR (Excel con hoja NUTRIEN + hoja Despachos)
- Lista con 8 KPIs: Total, Prog, Arribo, Cargado, No vino, Reprog, Ton prog, Ton oper
- Filtros: texto libre, fecha, estado, fuente, producto, cliente, transporte
- Detalle con:
  - Cambio rápido de estado (8 estados)
  - Panel de reprogramación (fecha nueva + notas)
  - Edición inline de campos operativos
  - Trazabilidad: CUITs, DNI, patentes, IN/OUT, origen

**Archivos clave:**
- `app/routers/despachos.py` (parser + rutas)
- `app/models_cupos.py` → `ImportBatch`, `CupoDespacho`
- `templates/despachos/list.html`, `import.html`, `detail.html`
- `migrate.py` — registra `models_cupos` en Base.metadata

**Plantilla física:** `~/Desktop/Plantilla_Despachos_MTR.xlsx`  
(también descargable desde `/despachos/template`)

**Estados del despacho:**
`programado → arribo → cargado / no_vino / parcial / reprogramado / cancelado / novedad`

**Pendiente:**
- Dashboard de despachos con gráfico por día
- Cruce automático con Operativos Live (misma fecha, mismo producto)
- Exportar lista filtrada a CSV/Excel
- Notificaciones si un camión no aparece (no_vino automático después de X horas)

---

### 2.9 Usuarios / Admin (`/admin/users`)
**Qué hace:** ABM de usuarios con roles.

**Roles:** `planta | autorizador | admin | superadmin`  
**Plantas:** `MTR1 | MTR2 | ROSARIO | TODAS`

---

## 3. Modelos / Base de datos

### Tablas principales

| Tabla | Módulo | Descripción |
|---|---|---|
| `users` | Auth | Usuarios del sistema |
| `suppliers` | Compras | Proveedores |
| `purchases` | Compras | Solicitudes de compra |
| `documents` | Compras | Archivos adjuntos (Cloudinary) |
| `audit_log` | Compras | Historial de cambios por compra |
| `quotes` | Cotizaciones | Cotizaciones previas |
| `quote_items` | Cotizaciones | Ítems por cotización |
| `quote_documents` | Cotizaciones | Adjuntos de cotizaciones |
| `quote_audit_log` | Cotizaciones | Historial de cotizaciones |
| `equipment` | Mantenimiento | Equipos de planta |
| `maintenance_records` | Mantenimiento | Registros de mantenimiento |
| `fuel_loads` | Combustible | Cargas de combustible |
| `operations` | Operativos | Operativos históricos (balanza) |
| `operation_trips` | Operativos | Viajes por operativo |
| `operation_cargo_summaries` | Operativos | Resúmenes por producto |
| `operation_live_sessions` | Live | Sesiones en tiempo real |
| `operation_live_session_products` | Live | Productos del barco por sesión |
| `operation_live_shifts` | Live | Turnos / Partes |
| `operation_live_bodega_data` | Live | Toneladas por bodega por turno |
| `operation_live_delay_entries` | Live | Demoras por turno |
| `operation_live_equipment_entries` | Live | Equipos por turno |
| `operation_live_personal_entries` | Live | Personal por turno |
| `operation_live_photos` | Live | Fotos (Cloudinary URL) |
| `operation_live_invoices` | Live | Facturas cooperativa |
| `operation_live_invoice_tonnage_lines` | Live | Líneas de toneladas de factura |
| `operation_live_invoice_labor_lines` | Live | Jornales de factura |
| `operation_live_reconciliations` | Live | Conciliación final |
| `despacho_import_batches` | Despachos | Lotes de importación Excel |
| `cupos_despachos` | Despachos | Cupos (Nutrien) y despachos (CNA) |

### Diferencias local vs prod
- Local: SQLite (`mtr_compras_dev.db`), no requiere config especial
- Prod: PostgreSQL en Railway, variable `DATABASE_URL` con postgres://…
- El check de `SECRET_KEY` en `main.py` impide arrancar en prod con la clave insegura

---

## 4. Rutas / Pantallas principales

```
GET  /                          → redirect a /dashboard
GET  /login                     → formulario de login
GET  /dashboard                 → dashboard general (KPIs)
GET  /health                    → {"status":"ok"} (Railway healthcheck)

# Compras
GET  /purchases                 → lista con filtros
GET  /purchases/new             → formulario nueva compra
GET  /purchases/{id}            → detalle
POST /purchases/{id}/authorize  → autorizar
POST /purchases/{id}/reject     → rechazar

# Operativos Live
GET  /operations/live                     → lista de sesiones
GET  /operations/live/{id}                → detalle de sesión (borradores + resumen)
GET  /operations/live/{id}/shift/new      → nuevo turno
POST /operations/live/{id}/shift/new      → guardar borrador
GET  /operations/live/{id}/shift/{sid}    → ver turno
POST /operations/live/{id}/shift/{sid}/close  → finalizar turno

# Despachos (⚠ solo local)
GET  /despachos                 → lista con KPIs y filtros
GET  /despachos/import          → paso 1: upload Excel
POST /despachos/import          → paso 2: preview
POST /despachos/import/confirm  → confirmar importación
GET  /despachos/{id}            → detalle + cambio de estado
POST /despachos/{id}/status     → cambiar estado
POST /despachos/{id}/reprogram  → reprogramar
GET  /despachos/template        → descargar Plantilla MTR .xlsx
```

---

## 5. Cambios importantes ya implementados

| # | Qué | Commit | Deploy |
|---|---|---|---|
| 1 | Nav dropdown "Operativos" (Live + Despachos + Finalizados) + fix mobile 5 ítems | `6e785b5` | ✅ Railway |
| 2 | Multi-borrador en Live (N turnos simultáneos) + "Finalizar turno" | `6e785b5` | ✅ Railway |
| 3 | Fix guardar borrador desktop (viewport-aware sync, deshabilita inputs invisible) | `7351ba5` | ✅ Railway |
| 4 | Redirect 401 → /login para requests HTML del browser | `0647923` | ✅ Railway |
| 5 | Módulo Despachos completo (modelos, rutas, templates, migrate) | `c56b4b1` | ❌ Local only |
| 6 | Importador operativo: filtro de fecha + una sola hoja Nutrien | `aa632da` | ❌ Local only |
| 7 | Plantilla MTR descargable desde /despachos/template | `e7a57b3` | ❌ Local only |
| 8 | Parser Nutrien estructura real: D1/D2/SM/embolsado/unidades kg→MT | `af0cad9` | ❌ Local only |

---

## 6. Estado del deploy

**En Railway (main):** commits hasta `7351ba5` inclusive.  
Los 6 commits del módulo Despachos (`c56b4b1` → `af0cad9`) están **solo locales**.

**Antes de deployar — checklist:**
1. Correr `python migrate.py` en Railway después del deploy para crear tablas `despacho_import_batches` y `cupos_despachos`
2. Verificar que `models_cupos` está importado en `migrate.py` (ya está en `c56b4b1`)
3. Validar localmente con el Excel real antes de deployar:
   ```bash
   # Correr servidor local
   uvicorn app.main:app --reload
   # Ir a http://localhost:8000/despachos/import
   # Subir CUPOS NUTRIENN .xlsx con filtro de fecha
   # Confirmar que la preview muestra ~41 camiones con toneladas en MT (no kg)
   ```
4. No hay migraciones destructivas: solo CREATE TABLE si no existe

**Riesgo bajo.** El módulo es aditivo: no modifica tablas existentes.

---

## 7. Problemas abiertos / TODOs

### Bugs pendientes
- [ ] Nada crítico conocido en este momento

### Módulo Despachos — mejoras pendientes
- [ ] Dashboard con gráfico de despachos por día/producto
- [ ] Exportar lista filtrada a CSV o Excel
- [ ] Cruce automático Despachos ↔ Operativos Live (misma fecha + producto)
- [ ] Marcar automáticamente como `no_vino` si pasó la fecha programada y sigue en `programado`
- [ ] Importación combo (NUTRIEN + CNA en un solo archivo) desde la Plantilla MTR — ya soportada en parser (`source == 'combo'`), falta testear end-to-end

### Live Operations — mejoras pendientes
- [ ] Exportar parte de turno a PDF
- [ ] Resumen ejecutivo completo del operativo (todas las sesiones)

### General
- [ ] Exportar compras a Excel
- [ ] Notificaciones por email al autorizar/rechazar compras
- [ ] Móvil: mejorar layout de lista de despachos en pantallas pequeñas

---

## 8. Próximos pasos recomendados

**Hacer primero:**
1. Validar módulo Despachos en local con el Excel real (`CUPOS NUTRIENN .xlsx`)
2. Deploy a Railway (los 6 commits pendientes son seguros)
3. Probar importación en producción con filtro de fecha

**Dejar para después:**
- Dashboard gráfico de despachos (no es urgente operativamente)
- Cruce con Live (requiere diseño cuidadoso de matching)
- Exportaciones PDF/Excel (calidad de vida, no bloquea operación)

**No tocar por ahora:**
- `app/models_live.py` — el diseño de factura/conciliación está cerrado
- `app/product_normalize.py` — normalización de productos ya estabilizada
- Flujo de compras — funciona en producción, no tocar sin razón

---

## 9. Contexto operativo del módulo Despachos

El usuario usa este módulo para el día a día de despacho de camiones de fertilizantes.

**Flujo diario:**
1. Recibe Excel de Nutrien (cupos del día siguiente) → importa con filtro de fecha = mañana
2. Recibe Excel de CNA (despachos de ayer o hoy) → importa con filtro de fechas últimos 2–3 días
3. A medida que los camiones llegan/cargan, actualiza el estado desde el detalle
4. Si un camión no viene → `no_vino`; si se reprograma → `reprogramado` + nueva fecha

**Estructura del Excel Nutrien (importante para parsear correctamente):**
- 3 tipos de fila conviven en la misma hoja:
  - **Simple:** ST=número + producto → 1 camión directo
  - **Embolsado:** ST=SP, BOLSA=SI + sub-fila EMBOLSADO/BOLSONES (packaging, descartar) → presentación inferida del lookahead
  - **Mezcla:** filas D1/D2 (ingredientes, descartar) + fila SM o "SM XXXXX" → solo conservar si MEZCLADO en producto
- Columna Cantidad: puede estar en MT o en kg. Si promedio > 200 → kg → dividir /1000
- Priorizar hoja `ganel_embolsado_ramallo` sobre `BASE` (misma data, mejor calidad)
