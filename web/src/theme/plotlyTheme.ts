// Dark Plotly theme — mirror of styles.chart_style() so React charts match the
// Streamlit ones exactly. Apply by merging applyChartTheme(title) into a figure's
// layout, and using CHART_COLORS as the colorway.

import type { Layout } from "plotly.js";
import { BORDER, CHART_COLORS, TEXT_MUTED, TEXT_PRIMARY } from "./tokens";

export function chartLayout(title = "", extra: Partial<Layout> = {}): Partial<Layout> {
  return {
    title: title
      ? { text: title, font: { size: 13, color: TEXT_MUTED }, pad: { t: 0 } }
      : undefined,
    plot_bgcolor: "rgba(0,0,0,0)",
    paper_bgcolor: "rgba(0,0,0,0)",
    margin: { l: 4, r: 4, t: title ? 28 : 8, b: 4 },
    font: { color: TEXT_MUTED, size: 11, family: "system-ui, sans-serif" },
    legend: {
      orientation: "h",
      yanchor: "bottom",
      y: 1.02,
      xanchor: "right",
      x: 1,
      bgcolor: "rgba(0,0,0,0)",
      font: { color: TEXT_MUTED, size: 11 },
    },
    hoverlabel: { bgcolor: "#16212B", font: { color: TEXT_PRIMARY }, bordercolor: BORDER },
    colorway: CHART_COLORS,
    xaxis: {
      showgrid: false,
      zeroline: false,
      color: TEXT_MUTED,
      linecolor: BORDER,
      tickfont: { size: 10, color: TEXT_MUTED },
    },
    yaxis: {
      gridcolor: "rgba(155,163,200,0.08)",
      zeroline: false,
      color: TEXT_MUTED,
      linecolor: BORDER,
      tickfont: { size: 10, color: TEXT_MUTED },
    },
    ...extra,
  };
}

export const PLOTLY_CONFIG = { displayModeBar: false, responsive: true } as const;
