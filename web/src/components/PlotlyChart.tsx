import type { Data, Layout } from "plotly.js";
import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

import { chartLayout, PLOTLY_CONFIG } from "../theme/plotlyTheme";

const Plot = createPlotlyComponent(Plotly);

/** Themed chart built from traces + a partial layout (used by the dashboard tabs). */
export function PlotlyChart({
  data,
  layout = {},
  title = "",
  height = 300,
}: {
  data: Data[];
  layout?: Partial<Layout>;
  title?: string;
  height?: number;
}) {
  const base = chartLayout(title);
  const merged: Partial<Layout> = {
    ...base,
    ...layout,
    xaxis: { ...base.xaxis, ...(layout.xaxis || {}) },
    yaxis: { ...base.yaxis, ...(layout.yaxis || {}) },
    height,
  };
  return (
    <Plot
      data={data}
      layout={merged}
      config={PLOTLY_CONFIG}
      useResizeHandler
      style={{ width: "100%", height }}
    />
  );
}

/** Render a complete Plotly figure spec (e.g. an LLM-generated chart from /api/charts). */
export function PlotlyFigure({ figure, height = 320 }: { figure: any; height?: number }) {
  if (!figure?.data) return null;
  return (
    <Plot
      data={figure.data}
      layout={{ ...figure.layout, height, autosize: true }}
      config={PLOTLY_CONFIG}
      useResizeHandler
      style={{ width: "100%", height }}
    />
  );
}
