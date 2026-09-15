import {
  dashboardLocation,
  panelInventory,
  panelRenderUrl,
  reportHtml,
  reportScope,
  ReportDashboard,
  exportDashboardHtml,
} from './dashboardHtmlExport';

const dashboard: ReportDashboard = {
  uid: 'sales',
  title: 'Sales',
  version: 4,
  time: { from: 'now-1h', to: 'now' },
  timezone: 'utc',
  panels: [{ id: 1, title: 'Sales by category', type: 'barchart' }],
  templating: {
    list: [
      {
        name: 'category',
        current: { value: ['Bikes'] },
        options: [{ value: '$__all' }, { value: 'Bikes' }, { value: 'Parts' }],
      },
    ],
  },
};
const preview = { href: '/d/sales?orgId=1', version: 4, variables: dashboard.templating!.list };
const now = Date.parse('2026-09-15T04:00:00Z');
const scope = reportScope('/d/sales?orgId=1', 'http://localhost', dashboard, 1, now);

describe('dashboard HTML export scope', () => {
  it('freezes relative bounds to one instant and drops unrelated query parameters', () => {
    const result = reportScope(
      '/d/sales?orgId=1&var-category=Bikes&var-category=Parts&token=secret',
      'http://localhost',
      dashboard,
      1,
      now
    );
    expect(result.from).toBe(now - 3600000);
    expect(result.to).toBe(now);
    expect(result.variables.category).toEqual(['Bikes', 'Parts']);
    const url = panelRenderUrl(result, panelInventory(dashboard, result)[0]);
    expect(url).toContain('from=1789441200000');
    expect(url).toContain('panelId=panel-1');
    expect(url).not.toContain('secret');
    expect(url).not.toContain('token');
  });

  it('supports a Grafana subpath without cross-origin export', () => {
    expect(dashboardLocation('/grafana/d/sales/name', 'https://grafana.example').prefix).toBe('/grafana');
    expect(() => dashboardLocation('https://other.example/d/sales', 'https://grafana.example')).toThrow();
    expect(() => dashboardLocation('/explore', 'https://grafana.example')).toThrow();
  });

  it('rejects other orgs, changed UIDs and edit URLs', () => {
    expect(() => reportScope('/d/sales?orgId=2', 'http://localhost', dashboard, 1, now)).toThrow(/organization/);
    expect(() => reportScope('/d/other', 'http://localhost', dashboard, 1, now)).toThrow(/dashboard/);
    expect(() => dashboardLocation('/d/sales?editPanel=1', 'http://localhost')).toThrow(/saved dashboard/);
  });

  it('resolves named-timezone rounding and absolute epoch bounds', () => {
    const rounded = reportScope(
      '/d/sales?from=now%2Fd&to=now&timezone=Asia%2FTaipei',
      'http://localhost',
      dashboard,
      1,
      now
    );
    expect(rounded.from).toBe(Date.parse('2026-09-14T16:00:00Z'));
    const absolute = reportScope(`/d/sales?from=${now - 1000}&to=${now}`, 'http://localhost', dashboard, 1, now);
    expect(absolute.from).toBe(now - 1000);
    expect(absolute.to).toBe(now);
  });

  it('supports the centered time.window URL and rejects invalid bounds', () => {
    const centered = reportScope(`/d/sales?time=${now}&time.window=2000`, 'http://localhost', dashboard, 1, now);
    expect(centered.from).toBe(now - 1000);
    expect(centered.to).toBe(now + 1000);
    expect(() => reportScope('/d/sales?from=bad&to=now', 'http://localhost', dashboard, 1, now)).toThrow();
    expect(() => reportScope(`/d/sales?from=${now}&to=${now - 1}`, 'http://localhost', dashboard, 1, now)).toThrow();
  });
});

describe('panel inventory', () => {
  it('includes children of collapsed rows, offscreen panels and selected repeated instances', () => {
    const model = {
      ...dashboard,
      panels: [
        { id: 10, type: 'row', panels: [{ id: 2, type: 'table', repeat: 'category' }] },
        { id: 3, type: 'stat' },
      ],
    };
    const selection = { ...scope, variables: { category: ['Bikes', 'Parts'] } };
    const items = panelInventory(model, selection);
    expect(items.map((item) => item.panel.id)).toEqual([2, 2, 3]);
    expect(items[0].variables.category).toEqual(['Bikes']);
    expect(items[1].variables.category).toEqual(['Parts']);
  });

  it('repeats flattened row panels without losing the row selection', () => {
    const model = {
      ...dashboard,
      panels: [
        { id: 10, type: 'row', repeat: 'category' },
        { id: 2, type: 'stat' },
        { id: 11, type: 'row' },
        { id: 3, type: 'stat' },
      ],
    };
    const items = panelInventory(model, { ...scope, variables: { category: ['Bikes', 'Parts'] } });
    expect(items.map((item) => item.panel.id)).toEqual([2, 2, 3]);
    expect(items[1].variables.category).toEqual(['Parts']);
    expect(items[2].variables.category).toEqual(['Bikes', 'Parts']);
  });

  it('expands the live All sentinel without dropping an actual option named All', () => {
    const model = {
      ...dashboard,
      panels: [{ id: 1, type: 'stat', repeat: 'category' }],
      templating: {
        list: [{ name: 'category', options: [{ value: '$__all' }, { value: 'All' }, { value: 'Bikes' }] }],
      },
    };
    const items = panelInventory(model, { ...scope, variables: { category: ['$__all'] } });
    expect(items.map((item) => item.variables.category)).toEqual([['All'], ['Bikes']]);
  });

  it('does not apply a collapsed repeated row selection to following standalone panels', () => {
    const model = {
      ...dashboard,
      panels: [
        { id: 10, type: 'row', repeat: 'category', panels: [{ id: 2, type: 'stat' }] },
        { id: 3, type: 'stat' },
      ],
    };
    const items = panelInventory(model, { ...scope, variables: { category: ['Bikes', 'Parts'] } });
    expect(items.map((item) => item.panel.id)).toEqual([2, 2, 3]);
    expect(items[2].variables.category).toEqual(['Bikes', 'Parts']);
  });

  it('does not silently drop unresolvable repeats or empty dashboards', () => {
    const model = { ...dashboard, panels: [{ id: 1, type: 'stat', repeat: 'unknown' }] };
    expect(() => panelInventory(model, scope)).toThrow(/Cannot enumerate/);
    expect(() => panelInventory({ ...dashboard, panels: [] }, scope)).toThrow(/no exportable/);
  });
});

describe('offline report safety', () => {
  it('escapes metadata and only embeds raster data, never query models or scripts', () => {
    const payload = '<script src="https://evil.example/x">bad</script>';
    const model = { ...dashboard, title: payload, description: payload, targets: [{ rawSql: 'PRIVATE SQL' }] };
    const html = reportHtml(model, scope, [
      {
        panel: { id: 1, type: 'table', title: payload, description: payload },
        variables: { category: [payload] },
        image: 'data:image/png;base64,iVBORw==',
      },
    ]);
    const doc = new DOMParser().parseFromString(html, 'text/html');
    expect(doc.querySelector('script')).toBeNull();
    expect(doc.querySelectorAll('img')).toHaveLength(1);
    expect(doc.querySelector('h1')?.textContent).toBe(payload);
    expect(doc.querySelector('img')?.src).toMatch(/^data:image\/png/);
    expect(html).not.toContain('PRIVATE SQL');
    expect(html).toContain('visible rows only');
    expect(html).toContain("default-src 'none'");
    expect(html).toContain('outside Grafana access control');
  });

  it('rejects SVG/external image injection and labels partial failures', () => {
    const item = panelInventory(dashboard, scope)[0];
    expect(() => reportHtml(dashboard, scope, [{ ...item, image: 'https://evil.example/a.png' }])).toThrow();
    expect(() => reportHtml(dashboard, scope, [{ ...item, image: 'data:image/svg+xml,<svg/>' }])).toThrow();
    const html = reportHtml(dashboard, scope, [{ ...item, error: '<img src=x>' }]);
    expect(html).toContain('INCOMPLETE: 1 of 1');
    expect(html).toContain('&lt;img src=x&gt;');
  });
});

describe('native read/render integration', () => {
  const originalFetch = global.fetch;
  beforeEach(() => {
    global.fetch = jest.fn();
  });
  // The installed jsdom predates AbortSignal.throwIfAborted (supported by Grafana's browsers).
  const originalAbortCheck = Object.getOwnPropertyDescriptor(AbortSignal.prototype, 'throwIfAborted');
  beforeAll(() =>
    Object.defineProperty(AbortSignal.prototype, 'throwIfAborted', {
      configurable: true,
      value(this: AbortSignal) {
        if (this.aborted) {
          throw new DOMException('Aborted', 'AbortError');
        }
      },
    })
  );
  afterAll(() => {
    if (originalAbortCheck) {
      Object.defineProperty(AbortSignal.prototype, 'throwIfAborted', originalAbortCheck);
    } else {
      Reflect.deleteProperty(AbortSignal.prototype, 'throwIfAborted');
    }
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });
  const read = (model = dashboard) => ({ ok: true, json: async () => ({ dashboard: model }) });
  const png = () => ({
    ok: true,
    headers: new Headers({ 'content-type': 'image/png' }),
    arrayBuffer: async () => new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0]).buffer,
  });

  it('only issues credentialed native GETs, renders each panel and returns an offline HTML blob', async () => {
    const fetchMock = global.fetch as jest.Mock;
    fetchMock.mockResolvedValueOnce(read()).mockResolvedValueOnce(png()).mockResolvedValueOnce(read());
    const progress = jest.fn();
    const result = await exportDashboardHtml(preview, 1, new AbortController().signal, progress);
    expect(result.failures).toBe(0);
    expect(result.filename).toMatch(/^sales-.*\.html$/);
    expect(result.blob.type).toContain('text/html');
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/dashboards/uid/sales',
      expect.stringContaining('/render/d-solo/sales/'),
      '/api/dashboards/uid/sales',
    ]);
    expect(fetchMock.mock.calls.every((call) => call[1].credentials === 'same-origin' && !call[1].method)).toBe(true);
    expect(progress).toHaveBeenLastCalledWith(1, 1);
  });

  it('does not generate a report on denied access, all failed images or version changes', async () => {
    const fetchMock = global.fetch as jest.Mock;
    fetchMock.mockResolvedValueOnce({ ok: false, status: 403 });
    await expect(exportDashboardHtml(preview, 1, new AbortController().signal, jest.fn())).rejects.toThrow(/403/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock
      .mockResolvedValueOnce(read())
      .mockResolvedValueOnce({ ok: false, status: 500 })
      .mockResolvedValueOnce(read());
    await expect(exportDashboardHtml(preview, 1, new AbortController().signal, jest.fn())).rejects.toThrow(
      /No panel images/
    );
    fetchMock
      .mockResolvedValueOnce(read())
      .mockResolvedValueOnce(png())
      .mockResolvedValueOnce(read({ ...dashboard, version: 5 }));
    await expect(exportDashboardHtml(preview, 1, new AbortController().signal, jest.fn())).rejects.toThrow(
      /changed during export/
    );
  });

  it('aborts rather than downloading after cancellation', async () => {
    const controller = new AbortController();
    (global.fetch as jest.Mock).mockImplementationOnce(async () => {
      controller.abort();
      return read();
    });
    await expect(exportDashboardHtml(preview, 1, controller.signal, jest.fn())).rejects.toThrow();
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });
});
