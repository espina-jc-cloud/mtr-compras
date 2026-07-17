#!/bin/sh
# Compila el CSS del design system MTR.
# Correr después de agregar clases Tailwind nuevas en templates/ o app/.
cd "$(dirname "$0")/.."
./node_modules/.bin/tailwindcss -c tailwind.config.js -i assets/app.src.css -o static/css/app.css --minify
echo "→ static/css/app.css regenerado ($(wc -c < static/css/app.css | tr -d ' ') bytes)"
