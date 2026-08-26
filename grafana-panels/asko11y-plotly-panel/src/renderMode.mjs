export function resolveRenderMode(options) {
  const figure = options?.figure;
  return figure && Array.isArray(figure.data) && figure.layout && typeof figure.layout === 'object'
    ? 'plotly'
    : 'fallback';
}
