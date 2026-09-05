export type RenderModeOptions = {
  renderMode?: "image" | "plotly";
  figure?: { data?: unknown[]; layout?: Record<string, unknown> };
  fallbackUrl?: string;
};

export function shouldShowPanelNarrative(viewCount: number): boolean;

export function resolveRenderMode(
  options: RenderModeOptions,
): "plotly" | "image" | "error";
