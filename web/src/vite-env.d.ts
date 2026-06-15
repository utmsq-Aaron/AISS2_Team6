/// <reference types="vite/client" />

declare module "plotly.js-dist-min";
declare module "react-plotly.js/factory" {
  import type Plotly from "plotly.js-dist-min";
  import type { PlotParams } from "react-plotly.js";
  import type { ComponentType } from "react";
  export default function createPlotlyComponent(plotly: typeof Plotly): ComponentType<PlotParams>;
}
