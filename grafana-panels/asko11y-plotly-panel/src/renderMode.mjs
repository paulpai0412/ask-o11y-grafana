export function shouldShowPanelNarrative(viewCount) {
  return viewCount > 1;
}

export function resolveRenderMode(options) {
  if (options?.renderMode === "image") {
    return options.fallbackUrl && !options.figure ? "image" : "error";
  }
  const figure = options?.figure;
  // Existing sanitized figure dashboards remain readable, never silently downgraded.
  return (!options?.renderMode || options.renderMode === "plotly") &&
    figure &&
    Array.isArray(figure.data) &&
    figure.layout &&
    typeof figure.layout === "object"
    ? "plotly"
    : "error";
}
