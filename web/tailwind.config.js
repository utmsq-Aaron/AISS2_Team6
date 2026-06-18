/** @type {import('tailwindcss').Config} */
// "Training Copilot Professional" design system — premium dark mode.
// Deep-navy canvas, dark-slate cards, vibrant-teal primary accent, energizing
// orange secondary. Kept in sync with src/theme/tokens.ts.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: { DEFAULT: "#2DD4BF", hover: "#14B8A6" }, // vibrant teal (primary)
        secondary: { DEFAULT: "#F97316", hover: "#EA580C" }, // energizing orange
        bg: {
          app: "#0B1219", // deep navy canvas
          card: "#16212B", // dark slate cards
          surface: "#16212B",
          sidebar: "#0B1219",
          header: "#0B1219",
        },
        border: { DEFAULT: "#1E293B" },
        text: { primary: "#F8FAFC", muted: "#94A3B8" },
        // Status + per-metric chart colours
        metric: {
          green: "#10B981", // status positive — emerald
          amber: "#F59E0B", // status caution — amber
          rose: "#FB7185",
          indigo: "#818CF8",
          cyan: "#22D3EE",
          purple: "#C084FC",
          red: "#EF4444",
        },
      },
      borderRadius: { card: "16px" },
      boxShadow: {
        // Subtle elevation + faint teal glow for cards (design: "very slight elevation/glow")
        card: "0 1px 3px rgba(0,0,0,0.35), 0 0 0 1px rgba(30,41,59,0.4)",
        glow: "0 0 0 1px rgba(45,212,191,0.35), 0 4px 20px rgba(45,212,191,0.08)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
