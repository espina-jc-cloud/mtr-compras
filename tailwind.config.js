/**
 * Design System MTR — Tailwind compilado (reemplaza cdn.tailwindcss.com).
 *
 * Decisiones clave (rediseño 2026-06):
 *  - `indigo` se REMAPEA a la escala navy corporativa de MTR (#0e1c3f, el azul
 *    del logo). Así los ~290 usos existentes de indigo-* en los templates
 *    adoptan la marca sin reescribir 92 archivos.
 *  - Radios unificados: xl y 2xl colapsan al mismo valor → una sola redondez
 *    visual en todo el sistema.
 *  - Acentos semánticos: emerald=ok, amber=atención, red=problema. El resto
 *    de colores decorativos se irá limpiando por módulo (Fase 5).
 *
 * Regenerar CSS tras tocar templates:  ./scripts/build_css.sh
 */
module.exports = {
  content: [
    "./templates/**/*.html",
    // Clases construidas en Python (estado_css, badges, etc.)
    "./app/**/*.py",
    // Clases inyectadas por JS (diálogo de confirmación, etc.)
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        // Navy MTR (marca) — ocupa el slot `indigo` usado en todo el sistema.
        indigo: {
          50:  "#f4f6fb",
          100: "#e6ebf5",
          200: "#c9d4ea",
          300: "#a3b4d8",
          400: "#748cc0",
          500: "#4d68a5",
          600: "#2e4a86",   // primario (botones, links, activos)
          700: "#1b2f5e",
          800: "#142347",
          900: "#0e1c3f",   // navy logo
          950: "#081226",
        },
        brand: {
          DEFAULT: "#0e1c3f",
          soft:    "#9db0d6",
        },
      },
      borderRadius: {
        lg:    "0.625rem",
        xl:    "0.75rem",
        "2xl": "0.75rem",   // colapsa con xl → una sola redondez
        "3xl": "1rem",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "sans-serif"],
      },
      boxShadow: {
        sm: "0 1px 2px 0 rgb(14 28 63 / 0.05)",
        DEFAULT: "0 1px 3px 0 rgb(14 28 63 / 0.07), 0 1px 2px -1px rgb(14 28 63 / 0.07)",
        md: "0 4px 6px -1px rgb(14 28 63 / 0.07), 0 2px 4px -2px rgb(14 28 63 / 0.06)",
        lg: "0 10px 15px -3px rgb(14 28 63 / 0.08), 0 4px 6px -4px rgb(14 28 63 / 0.05)",
      },
      transitionDuration: { DEFAULT: "150ms" },
    },
  },
  plugins: [],
};
