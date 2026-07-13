import type { Data, Layout } from "plotly.js";
import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

import { chartLayout, PLOTLY_CONFIG } from "../theme/plotlyTheme";
import { TEXT_MUTED } from "../theme/tokens";

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

/** Render a complete Plotly figure spec (e.g. an LLM-generated chart from /api/charts).
 *
 * The LLM is told to emit `template='plotly_dark'` (so it never picks light colours),
 * but that template's opaque rgb(17,17,17) backgrounds + default colorway clash with
 * the app's token-based chart theme. So instead of trusting the LLM layout verbatim,
 * we merge the app theme (`chartLayout()`) on top of it: LLM axis/figure titles are
 * PRESERVED (restyled to match), plotly_dark's slab background + colorway are DROPPED.
 */
export function PlotlyFigure({ figure, height = 320 }: { figure: any; height?: number }) {
  if (!figure?.data) return null;

  const src: Partial<Layout> = figure.layout || {};
  const base = chartLayout();

  // Keep the LLM's figure-title text, but restyle it to the app's muted look.
  const rawTitle: any = (src as any).title;
  const titleText =
    typeof rawTitle === "string" ? rawTitle : rawTitle?.text || "";
  const hasTitle = Boolean(titleText);
  const title = hasTitle
    ? { text: titleText, font: { size: 13, color: TEXT_MUTED } }
    : undefined;

  const layout: Partial<Layout> = {
    ...src,
    ...base,
    // Drop plotly_dark's opaque backgrounds + colorway (base already sets ours).
    template: undefined,
    // Preserve the LLM's axis titles/ranges, restyle colours/grid to the theme.
    xaxis: { ...(src.xaxis || {}), ...base.xaxis },
    yaxis: { ...(src.yaxis || {}), ...base.yaxis },
    title,
    margin: { l: 4, r: 4, t: hasTitle ? 28 : 8, b: 4 },
    autosize: true,
    height,
  };

  return (
    <Plot
      data={figure.data}
      layout={layout}
      config={PLOTLY_CONFIG}
      useResizeHandler
      style={{ width: "100%", height }}
    />
  );
}
