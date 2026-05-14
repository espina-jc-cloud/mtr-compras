# MTR-Compras — Instrucciones Claude Code

## Objetivo
Sistema interno de gestión de compras para MTR. Plantas en San Nicolás (MTR1, MTR2), administración en Rosario.

## Stack
- FastAPI + Jinja2 + HTMX + Tailwind CDN
- SQLite (Railway filesystem — migrate.py recrea si falta)
- JWT en cookie httpOnly
- Cloudinary para archivos (remitos, facturas)

## Reglas de datos
- No mover a RECIBIDA sin remito adjunto.
- No mover a PAGADA sin factura adjunta.
- Si factura > estimado * 1.10 → amount_alert = True.
- Toda transición de estado genera registro en audit_log.

## Endpoints obligatorios
- GET /health → {"status": "ok"}
- GET /api/debug → {"git_sha": ..., "counts": {...}}

## Flujo diario
1. Planta crea solicitud → autorizador aprueba → planta sube remito → admin carga factura → admin paga.

## Restricciones
- No refactorizar sin razón.
- Cambios mínimos y verificables.
