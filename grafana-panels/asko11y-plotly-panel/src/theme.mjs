export function applyGrafanaTheme(layout, theme) {
  const text = theme.colors.text.primary;
  const grid = theme.colors.border.weak;
  const themed = {
    ...layout,
    autosize: true,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { ...layout.font, color: text, family: theme.typography.fontFamily },
    legend: { ...layout.legend, font: { ...layout.legend?.font, color: text } },
  };
  for (const key of Object.keys(themed)) {
    if (/^[xy]axis\d*$/.test(key)) {
      themed[key] = {
        ...themed[key],
        color: text,
        gridcolor: grid,
        zerolinecolor: grid,
        automargin: true,
      };
    }
  }
  delete themed.width;
  delete themed.height;
  return themed;
}
