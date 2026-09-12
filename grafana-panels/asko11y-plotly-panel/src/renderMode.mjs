export function resolveRenderMode(options) {
  if (options?.renderMode === "image") {
    return options.fallbackUrl && !options.figure ? "image" : "error";
  }
  const figure = options?.figure;
  return options?.renderMode === "plotly" &&
    figure &&
    !figure.error &&
    Array.isArray(figure.data) &&
    figure.data.length > 0 &&
    figure.layout &&
    typeof figure.layout === "object"
    ? "plotly"
    : "error";
}
