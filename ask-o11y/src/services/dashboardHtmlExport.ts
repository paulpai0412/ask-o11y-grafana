import { dateMath, dateTimeParse } from '@grafana/data';

export interface ReportPanel {
  id: number;
  title?: string;
  description?: string;
  type: string;
  repeat?: string;
  panels?: ReportPanel[];
  gridPos?: { h: number };
}

export interface ReportVariable {
  name: string;
  current?: { value?: string | string[] };
  options?: Array<{ value: string }>;
}

export interface ReportDashboard {
  uid: string;
  title: string;
  version: number;
  description?: string;
  time: { from: string; to: string };
  timezone?: string;
  panels: ReportPanel[];
  templating?: { list: ReportVariable[] };
}

export interface PanelImage {
  panel: ReportPanel;
  variables: Record<string, string[]>;
  image?: string;
  error?: string;
}

export interface PreviewSnapshot {
  href: string;
  version: number;
  variables: ReportVariable[];
}

export interface ReportScope {
  uid: string;
  prefix: string;
  orgId: string;
  from: number;
  to: number;
  timezone: string;
  generatedAt: string;
  variables: Record<string, string[]>;
}

function absoluteTime(value: string, timezone: string, now: number, roundUp: boolean): number {
  const parsed = value.startsWith('now')
    ? dateMath.parseDateMath(value.slice(3), dateTimeParse(now, { timeZone: timezone }), roundUp)
    : dateTimeParse(/^\d+$/.test(value) ? Number(value) : value, { timeZone: timezone, roundUp });
  if (!parsed?.isValid()) {
    throw new Error('The dashboard time range could not be resolved. Select an explicit time range.');
  }
  return parsed.valueOf();
}

export function dashboardLocation(href: string, origin: string): { url: URL; uid: string; prefix: string } {
  const url = new URL(href, origin);
  const match = url.pathname.match(/^(.*?)\/d\/([a-zA-Z0-9_-]+)(?:\/|$)/);
  if (
    url.origin !== origin ||
    url.username ||
    url.password ||
    !match ||
    url.searchParams.has('editPanel') ||
    url.searchParams.has('editview')
  ) {
    throw new Error('Open a saved dashboard preview on this Grafana instance before exporting.');
  }
  return { url, uid: match[2], prefix: match[1] };
}

export function reportScope(
  href: string,
  origin: string,
  dashboard: ReportDashboard,
  orgId: number,
  now = Date.now()
): ReportScope {
  const { url, uid, prefix } = dashboardLocation(href, origin);
  if (uid !== dashboard.uid || (url.searchParams.has('orgId') && url.searchParams.get('orgId') !== String(orgId))) {
    throw new Error('The preview belongs to a different dashboard or organization. Reload it before exporting.');
  }
  const requestedZone = url.searchParams.get('timezone') || dashboard.timezone || 'browser';
  const timezone = requestedZone === 'browser' ? Intl.DateTimeFormat().resolvedOptions().timeZone : requestedZone;
  const variables: Record<string, string[]> = Object.create(null);
  for (const variable of dashboard.templating?.list || []) {
    const values = url.searchParams.getAll(`var-${variable.name}`);
    const selected = variable.current?.value;
    variables[variable.name] = values.length
      ? values
      : Array.isArray(selected)
      ? selected
      : selected === undefined
      ? []
      : [selected];
  }
  const center = url.searchParams.get('time');
  const windowMs = Number(url.searchParams.get('time.window') || 10000);
  const from = center
    ? absoluteTime(center, timezone, now, false) - windowMs / 2
    : absoluteTime(url.searchParams.get('from') || dashboard.time.from, timezone, now, false);
  const to = center
    ? absoluteTime(center, timezone, now, false) + windowMs / 2
    : absoluteTime(url.searchParams.get('to') || dashboard.time.to, timezone, now, true);
  if (!Number.isFinite(from) || !Number.isFinite(to) || from > to || windowMs <= 0) {
    throw new Error('The dashboard time range is invalid.');
  }
  return { uid, prefix, orgId: String(orgId), from, to, timezone, generatedAt: new Date(now).toISOString(), variables };
}

export function panelInventory(dashboard: ReportDashboard, scope: ReportScope): PanelImage[] {
  const images: PanelImage[] = [];
  function expand(name: string | undefined, variables: Record<string, string[]>): Array<Record<string, string[]>> {
    if (!name) {
      return [variables];
    }
    let values = variables[name] || [];
    if (values.includes('$__all')) {
      values = (dashboard.templating?.list.find((v) => v.name === name)?.options || [])
        .map((option) => option.value)
        .filter((value) => value !== '$__all');
    }
    if (!values.length) {
      throw new Error(
        `Cannot enumerate repeated variable "${name}". Select explicit values in the preview, then export again.`
      );
    }
    return values.map((value) => ({ ...variables, [name]: [value] }));
  }
  function visit(panels: ReportPanel[], inherited: Record<string, string[]>): void {
    let rowScopes = [inherited];
    for (const panel of panels) {
      if (panel.type === 'row') {
        rowScopes = expand(panel.repeat, inherited);
        for (const variables of rowScopes) {
          visit(panel.panels || [], variables);
        }
        if (panel.panels?.length) {
          rowScopes = [inherited];
        }
      } else {
        for (const rowScope of rowScopes) {
          for (const variables of expand(panel.repeat, rowScope)) {
            if (!Number.isInteger(panel.id)) {
              throw new Error('A panel has no saved ID. Save the dashboard before exporting.');
            }
            images.push({ panel, variables });
          }
        }
      }
      if (images.length > 100) {
        throw new Error('This report exceeds 100 panel images. Choose fewer repeat values before exporting.');
      }
    }
  }
  visit(dashboard.panels, scope.variables);
  if (!images.length) {
    throw new Error('This saved dashboard has no exportable panels.');
  }
  return images;
}

export function panelRenderUrl(scope: ReportScope, item: PanelImage): string {
  const params = new URLSearchParams({
    orgId: scope.orgId,
    from: String(scope.from),
    to: String(scope.to),
    tz: scope.timezone,
    timezone: scope.timezone,
    panelId: `panel-${item.panel.id}`,
    width: '1200',
    height: String(Math.max(240, Math.min(1200, (item.panel.gridPos?.h || 10) * 30))),
    scale: '1',
    timeout: '60',
  });
  for (const [name, values] of Object.entries(item.variables)) {
    for (const value of values) {
      params.append(`var-${name}`, value);
    }
  }
  return `${scope.prefix}/render/d-solo/${scope.uid}/report?${params}`;
}

const escapeHtml = (value: string): string =>
  value.replace(
    /[&<>"']/g,
    (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]!)
  );
const variableText = (variables: Record<string, string[]>): string =>
  Object.entries(variables)
    .map(([name, values]) => `${name}: ${values.join(', ')}`)
    .join(' · ');

export function reportHtml(dashboard: ReportDashboard, scope: ReportScope, items: PanelImage[]): string {
  const failures = items.filter((item) => item.error).length;
  const cards = items
    .map(({ panel, variables, image, error }) => {
      if (image && !/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(image)) {
        throw new Error('Invalid report image.');
      }
      return `<section><h2>${escapeHtml(panel.title || `Panel ${panel.id}`)}</h2><p>${escapeHtml(
        panel.description || ''
      )}</p><p>${escapeHtml(variableText(variables))}</p>${
        image
          ? `<img alt="${escapeHtml(panel.title || `Panel ${panel.id}`)}" src="${image}">`
          : `<p class="warning">Image unavailable: ${escapeHtml(error || 'Not rendered')}</p>`
      }${
        panel.type === 'table' ? '<p>Table image includes visible rows only; not a full data export.</p>' : ''
      }</section>`;
    })
    .join('\n');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>${escapeHtml(
    dashboard.title
  )}</title><style>html{color-scheme:light dark}body{font:16px system-ui,sans-serif;background:Canvas;color:CanvasText;max-width:1280px;margin:2rem auto;padding:0 1rem}h1,h2,p{overflow-wrap:anywhere}section{border-top:1px solid;padding:1rem 0;break-inside:avoid}img{display:block;width:100%;height:auto}.warning{font-weight:bold}dt{font-weight:bold}dd{margin:0 0 .5rem}@media print{body{margin:0;max-width:none}img{max-height:85vh;object-fit:contain}}</style></head><body><header><h1>${escapeHtml(
    dashboard.title
  )}</h1><p>${escapeHtml(dashboard.description || '')}</p><dl><dt>Source</dt><dd>${escapeHtml(scope.uid)} · version ${
    dashboard.version
  } · organization ${escapeHtml(scope.orgId)}</dd><dt>Generated</dt><dd>${
    scope.generatedAt
  }</dd><dt>Query time range (UTC)</dt><dd>${new Date(scope.from).toISOString()} — ${new Date(
    scope.to
  ).toISOString()}</dd><dt>Display timezone</dt><dd>${escapeHtml(scope.timezone)}</dd><dt>Selected variables</dt><dd>${
    escapeHtml(variableText(scope.variables)) || 'None'
  }</dd></dl><p class="warning">${
    failures
      ? `INCOMPLETE: ${failures} of ${items.length} panel images failed.`
      : `${items.length} panel images rendered.`
  }</p><p>Static images: no hover or zoom. Tables show visible rows only. Fixed query time range, not an atomic database snapshot; panels are queried sequentially. Grafana error or no-data messages may appear in images; inspect them before sharing.</p><p>This downloaded file is outside Grafana access control. Share only with authorized recipients.</p></header>${cards}</body></html>`;
}

async function readDashboard(path: string, signal: AbortSignal): Promise<ReportDashboard> {
  const response = await fetch(path, { credentials: 'same-origin', signal, cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Dashboard read failed (${response.status}). Check access and reload the preview.`);
  }
  const result: { dashboard: ReportDashboard } = await response.json();
  return result.dashboard;
}

export async function exportDashboardHtml(
  preview: PreviewSnapshot,
  orgId: number,
  signal: AbortSignal,
  onProgress: (done: number, total: number) => void
): Promise<{ blob: Blob; filename: string; failures: number }> {
  const location = dashboardLocation(preview.href, window.location.origin);
  const dashboardPath = `${location.prefix}/api/dashboards/uid/${location.uid}`;
  const saved = await readDashboard(dashboardPath, signal);
  if (saved.version !== preview.version) {
    throw new Error('The saved dashboard no longer matches the preview. Reload it before exporting.');
  }
  const dashboard = { ...saved, templating: { list: preview.variables } };
  const scope = reportScope(preview.href, window.location.origin, dashboard, orgId);
  const items = panelInventory(dashboard, scope);
  let totalBytes = 0;
  onProgress(0, items.length);
  for (const [index, item] of items.entries()) {
    signal.throwIfAborted();
    try {
      const response = await fetch(panelRenderUrl(scope, item), {
        credentials: 'same-origin',
        signal,
        cache: 'no-store',
      });
      if (!response.ok) {
        throw new Error(`Grafana render failed (${response.status}).`);
      }
      if (!response.headers.get('content-type')?.startsWith('image/png')) {
        throw new Error('Grafana did not return a PNG image.');
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.length < 8 || [137, 80, 78, 71, 13, 10, 26, 10].some((byte, i) => bytes[i] !== byte)) {
        throw new Error('Grafana returned an invalid PNG image.');
      }
      totalBytes += bytes.length;
      if (totalBytes > 48 * 1024 * 1024) {
        throw new Error('Report images exceed the 48 MiB download limit.');
      }
      item.image = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error('Could not encode the report image.'));
        reader.readAsDataURL(new Blob([bytes], { type: 'image/png' }));
      });
    } catch (error) {
      signal.throwIfAborted();
      if (totalBytes > 48 * 1024 * 1024) {
        throw error;
      }
      item.error = error instanceof Error ? error.message : 'Image rendering failed.';
    }
    onProgress(index + 1, items.length);
  }
  const after = await readDashboard(dashboardPath, signal);
  if (after.version !== dashboard.version) {
    throw new Error('The dashboard changed during export. Reload the preview and export again.');
  }
  signal.throwIfAborted();
  const failures = items.filter((item) => item.error).length;
  if (failures === items.length) {
    throw new Error('No panel images could be rendered. Check Grafana Image Renderer and dashboard access.');
  }
  return {
    blob: new Blob([reportHtml(dashboard, scope, items)], { type: 'text/html;charset=utf-8' }),
    filename: `${scope.uid}-${scope.generatedAt.replace(/[:.]/g, '-')}${failures ? '-incomplete' : ''}.html`,
    failures,
  };
}
