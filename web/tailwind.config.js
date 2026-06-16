/** @type {import('tailwindcss').Config} */
// Palette mirrors AISS2_Team6/ui/styles.py + .streamlit/config.toml so the React
// UI is a faithful reproduction of the Streamlit dashboard.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: { DEFAULT: "#FC4C02", hover: "#e04400" },
        bg: {
          app: "#0F0F1E", // backgroundColor / BG_CARD
          card: "#0F0F1E",
          surface: "#16162A", // secondaryBackgroundColor / BG_SURFACE
          sidebar: "#0A0A18",
        },
        border: { DEFAULT: "#2A2A45" },
        text: { primary: "#EEEEFF", muted: "#9BA3C8" },
        // Per-metric colours (styles.py)
        metric: {
          green: "#22C55E",
          rose: "#FB7185",
          indigo: "#818CF8",
          cyan: "#22D3EE",
          purple: "#C084FC",
          amber: "#FCD34D",
          red: "#EF4444",
        },
      },
      borderRadius: { card: "14px" },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
};
