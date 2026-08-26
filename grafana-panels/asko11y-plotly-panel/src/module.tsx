import React, { useEffect, useMemo, useRef, useState } from 'react';
import { PanelPlugin } from '@grafana/data';
import { useTheme2 } from '@grafana/ui';
import Plotly from 'plotly.js-dist-min';
import { splitFigureViews, type PlotlyView, type PlotlyViewSpec } from './figureViews.mjs';
import { resolveRenderMode } from './renderMode.mjs';
import { applyGrafanaTheme } from './theme.mjs';

type PlotlyFigure = {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
  config?: Record<string, unknown>;
};

type PlotlyRuntime = typeof Plotly & { Plots: { resize(element: HTMLElement): void } };
const plotlyRuntime = Plotly as PlotlyRuntime;

type NarrativeEvidence = { fact_ref: string; label: string; display: string };
type Narrative = {
  headline: string;
  observation: string;
  interpretation: string;
  cross_chart_context: string;
  limitation: string;
  next_step: string;
  evidence: NarrativeEvidence[];
};

type Options = {
  figure?: PlotlyFigure;
  fallbackUrl?: string;
  alt?: string;
  narrative?: Narrative;
  selectedViewIds?: string[];
  viewSpecs?: PlotlyViewSpec[];
};

type Theme = ReturnType<typeof useTheme2>;

function NarrativeBlock({ narrative }: { narrative?: Narrative }) {
  if (!narrative) {
    return null;
  }
  const rows = [
    ['观察', narrative.observation],
    ['解读', narrative.interpretation],
    ['跨图关系', narrative.cross_chart_context],
    ['限制', narrative.limitation],
    ['下一步', narrative.next_step],
  ];
  return (
    <div style={{ padding: '8px 12px', lineHeight: 1.45, overflowY: 'auto', minHeight: 0 }}>
      <strong>{narrative.headline}</strong>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
        {narrative.evidence.map((item) => (
          <span key={item.fact_ref} style={{ border: '1px solid currentColor', borderRadius: 4, padding: '3px 7px' }}>
            {item.label}: <b>{item.display}</b>
          </span>
        ))}
      </div>
      {rows.map(([label, text]) => <div key={label} style={{ marginTop: 6 }}><b>{label}：</b>{text}</div>)}
    </div>
  );
}

function Fallback({ options }: { options: Options }) {
  if (!options.fallbackUrl) {
    return <div style={{ padding: 16 }}>互动图无法显示，且没有 PNG fallback。</div>;
  }
  return (
    <figure style={{ margin: 0, height: '100%', display: 'grid', gridTemplateRows: options.narrative ? 'minmax(220px, 3fr) minmax(140px, 2fr)' : '1fr', overflow: 'hidden' }}>
      <img src={options.fallbackUrl} alt={options.alt || '分析图表'} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      <NarrativeBlock narrative={options.narrative} />
    </figure>
  );
}

function PlotView({ view, theme, onFailure }: { view: PlotlyView; theme: Theme; onFailure: () => void }) {
  const container = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!container.current) {
      return;
    }
    const element = container.current;
    let active = true;
    const layout = applyGrafanaTheme(view.figure.layout, theme);
    const config = { ...view.figure.config, displaylogo: false, responsive: true, scrollZoom: false };
    try {
      Promise.resolve(Plotly.react(element, view.figure.data, layout, config)).catch(() => {
        if (active) {
          onFailure();
        }
      });
    } catch {
      onFailure();
    }
    const observer = new ResizeObserver(() => plotlyRuntime.Plots.resize(element));
    observer.observe(element);
    return () => {
      active = false;
      observer.disconnect();
      Plotly.purge(element);
    };
  }, [onFailure, theme, view]);

  return <div ref={container} aria-label={view.title} style={{ width: '100%', height: 320, minHeight: 260 }} />;
}

function PlotlyPanel({ options }: { options: Options }) {
  const theme = useTheme2();
  const [failed, setFailed] = useState(false);
  const mode = failed ? 'fallback' : resolveRenderMode(options);
  const views = useMemo(
    () => options.figure ? splitFigureViews(options.figure, options.selectedViewIds, options.viewSpecs) : [],
    [options.figure, options.selectedViewIds, options.viewSpecs],
  );

  if (mode === 'fallback' || views.length === 0) {
    return <Fallback options={options} />;
  }
  return (
    <div style={{ height: '100%', display: 'grid', gridTemplateRows: options.narrative ? 'minmax(260px, 3fr) minmax(140px, 2fr)' : '1fr', overflow: 'hidden' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 12, overflow: 'auto', padding: 8 }}>
        {views.map((view) => (
          <section key={view.viewId} style={{ border: `1px solid ${theme.colors.border.weak}`, borderRadius: 4, padding: 8, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{view.title}</div>
            <PlotView view={view} theme={theme} onFailure={() => setFailed(true)} />
          </section>
        ))}
      </div>
      <NarrativeBlock narrative={options.narrative} />
    </div>
  );
}

export const plugin = new PanelPlugin<Options>(PlotlyPanel).setPanelOptions((builder) => builder
  .addTextInput({ path: 'alt', name: '替代文字' })
  .addTextInput({ path: 'fallbackUrl', name: 'PNG fallback URL' }));
