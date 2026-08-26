function axisLayoutKey(reference, axis) {
  const suffix = reference.startsWith(axis) ? reference.slice(axis.length) : '';
  return `${axis}axis${suffix}`;
}

function independentAxis(axis = {}) {
  const result = { ...axis };
  for (const key of ['domain', 'anchor', 'overlaying', 'side']) {
    delete result[key];
  }
  if (typeof result.title === 'string') {
    result.title = { text: result.title };
  }
  return result;
}

export function splitFigureViews(figure, selectedViewIds = [], viewSpecs = []) {
  const groups = new Map();
  for (const trace of figure.data || []) {
    const xRef = trace.xaxis || 'x';
    const yRef = trace.yaxis || 'y';
    const key = `${xRef}|${yRef}`;
    if (!groups.has(key)) {
      groups.set(key, { xRef, yRef, traces: [] });
    }
    groups.get(key).traces.push(trace);
  }
  const selected = new Set(selectedViewIds);
  const specs = new Map(viewSpecs.map((item) => [item.view_id, item]));
  const commonLayout = Object.fromEntries(
    Object.entries(figure.layout || {}).filter(([key]) => !/^[xy]axis\d*$/.test(key) && key !== 'width' && key !== 'height' && key !== 'title'),
  );
  return Array.from(groups.values()).map((group, index) => {
    const viewId = `view-${index + 1}`;
    const spec = specs.get(viewId);
    const traces = group.traces.map((trace) => {
      const result = { ...trace };
      delete result.xaxis;
      delete result.yaxis;
      return result;
    });
    const names = traces.map((trace) => trace.name).filter(Boolean);
    return {
      viewId,
      title: spec?.title || names.join(' / ') || viewId,
      figure: {
        data: traces,
        layout: {
          ...commonLayout,
          showlegend: traces.length > 1 && commonLayout.showlegend !== false,
          margin: { l: 52, r: 16, t: 16, b: 64, ...(commonLayout.margin || {}) },
          xaxis: independentAxis(figure.layout?.[axisLayoutKey(group.xRef, 'x')]),
          yaxis: independentAxis(figure.layout?.[axisLayoutKey(group.yRef, 'y')]),
        },
        config: figure.config || {},
      },
    };
  }).filter((view) => selected.size === 0 || selected.has(view.viewId));
}
