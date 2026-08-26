export type PlotlyViewSpec = { view_id: string; title: string };
export type PlotlyView = {
  viewId: string;
  title: string;
  figure: { data: Array<Record<string, unknown>>; layout: Record<string, unknown>; config: Record<string, unknown> };
};

export function splitFigureViews(
  figure: { data: Array<Record<string, unknown>>; layout: Record<string, unknown>; config?: Record<string, unknown> },
  selectedViewIds?: string[],
  viewSpecs?: PlotlyViewSpec[],
): PlotlyView[];
