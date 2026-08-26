import React, { useEffect, useRef, useState } from 'react';
import { PanelPlugin } from '@grafana/data';
import Plotly from 'plotly.js-dist-min';
import { resolveRenderMode } from './renderMode.mjs';

type PlotlyFigure = {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
  config?: Record<string, unknown>;
};

type Options = {
  figure?: PlotlyFigure;
  fallbackUrl?: string;
  alt?: string;
  caption?: string;
};

function Fallback({ options }: { options: Options }) {
  if (!options.fallbackUrl) {
    return <div style={{ padding: 16 }}>互動圖無法顯示，且沒有 PNG fallback。</div>;
  }
  return (
    <figure style={{ margin: 0, height: '100%', display: 'grid', gridTemplateRows: 'minmax(0, 1fr) auto' }}>
      <img src={options.fallbackUrl} alt={options.alt || 'ML 圖表'} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      {options.caption ? <figcaption style={{ padding: '8px 12px', lineHeight: 1.45 }}>{options.caption}</figcaption> : null}
    </figure>
  );
}

function PlotlyPanel({ options, width, height }: { options: Options; width: number; height: number }) {
  const container = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  const mode = failed ? 'fallback' : resolveRenderMode(options);

  useEffect(() => {
    if (mode !== 'plotly' || !container.current || !options.figure) {
      return;
    }
    let active = true;
    setFailed(false);
    try {
      const layout = { ...options.figure.layout, width, height: Math.max(120, height - (options.caption ? 40 : 0)) };
      const config = { ...options.figure.config, displaylogo: false, responsive: true, scrollZoom: false };
      Promise.resolve(Plotly.react(container.current, options.figure.data, layout, config)).catch(() => {
        if (active) {
          setFailed(true);
        }
      });
    } catch {
      setFailed(true);
    }
    return () => {
      active = false;
      if (container.current) {
        Plotly.purge(container.current);
      }
    };
  }, [height, mode, options.caption, options.figure, width]);

  if (mode === 'fallback') {
    return <Fallback options={options} />;
  }
  return (
    <div style={{ height: '100%', display: 'grid', gridTemplateRows: 'minmax(0, 1fr) auto' }}>
      <div ref={container} aria-label={options.alt || '互動式 ML 圖表'} />
      {options.caption ? <div style={{ padding: '8px 12px', lineHeight: 1.45 }}>{options.caption}</div> : null}
    </div>
  );
}

export const plugin = new PanelPlugin<Options>(PlotlyPanel)
  .setPanelOptions((builder) => builder
    .addTextInput({ path: 'alt', name: '替代文字' })
    .addTextInput({ path: 'caption', name: '圖表說明' })
    .addTextInput({ path: 'fallbackUrl', name: 'PNG fallback URL' }));
