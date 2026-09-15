import React, { act } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { config } from '@grafana/runtime';
import { DownloadHtmlReport, readPreviewSnapshot } from '../DownloadHtmlReport';
import * as exporter from '../../../../../services/dashboardHtmlExport';

jest.mock('../../../../../services/dashboardHtmlExport', () => ({
  ...jest.requireActual('../../../../../services/dashboardHtmlExport'),
  exportDashboardHtml: jest.fn(),
}));

function previewFrame() {
  const variables = [
    { name: 'category', current: { value: ['$__all'] }, options: [{ value: 'Bikes' }, { value: 'Parts' }] },
  ];
  const state = {
    uid: 'sales',
    version: 7,
    isDirty: false,
    $timeRange: { state: { value: { from: { valueOf: () => 1000 }, to: { valueOf: () => 2000 } } } },
  };
  const frame = {
    location: { href: `${window.location.origin}/d/sales?orgId=1&from=now-1h&var-category=$__all` },
    System: { import: jest.fn().mockResolvedValue({ getTemplateSrv: () => ({ getVariables: () => variables }) }) },
    __grafanaSceneContext: { state },
  };
  return { frame: frame as unknown as Window, state, variables };
}

describe('current dashboard preview snapshot', () => {
  it('uses the iframe runtime and freezes displayed absolute range, version and live repeat options', async () => {
    const { frame, variables } = previewFrame();
    const result = await readPreviewSnapshot(frame);
    expect(new URL(result.href).searchParams.get('from')).toBe('1000');
    expect(new URL(result.href).searchParams.get('to')).toBe('2000');
    expect(result.version).toBe(7);
    expect(result.variables[0].options).toEqual([{ value: 'Bikes' }, { value: 'Parts' }]);
    variables[0].options.push({ value: 'New later value' });
    variables[0].current.value.push('Later');
    expect(result.variables[0].options).toHaveLength(2);
    expect(result.variables[0].current?.value).toEqual(['$__all']);
  });

  it('rejects unloaded or unsaved scenes instead of using stale saved defaults', async () => {
    await expect(readPreviewSnapshot({} as Window)).rejects.toThrow(/load/);
    const { frame, state } = previewFrame();
    state.isDirty = true;
    await expect(readPreviewSnapshot(frame)).rejects.toThrow(/Save and reload/);
  });
});

describe('download control lifecycle', () => {
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
  afterEach(() => jest.mocked(exporter.exportDashboardHtml).mockReset());

  function mount() {
    config.rendererAvailable = true;
    config.bootData = { ...config.bootData, user: { ...config.bootData?.user, orgId: 1 } };
    const { frame } = previewFrame();
    const iframe = { contentWindow: frame } as HTMLIFrameElement;
    return render(<DownloadHtmlReport iframeRef={{ current: iframe }} />);
  }

  it('requires confirmation, then shows a partial report before download', async () => {
    const generate = jest
      .mocked(exporter.exportDashboardHtml)
      .mockResolvedValue({ blob: new Blob(['report']), filename: 'incomplete.html', failures: 1 });
    mount();
    fireEvent.click(screen.getByRole('button', { name: 'Download HTML report' }));
    expect(screen.getByText(/outside Grafana access control/)).toBeInTheDocument();
    expect(generate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Generate report' }));
    expect(await screen.findByText('Incomplete report')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download incomplete HTML' })).toBeInTheDocument();
    expect(generate).toHaveBeenCalledTimes(1);
  });

  it('cancels on unmount and does not continue with a stale report', async () => {
    let finish: ((value: Awaited<ReturnType<typeof exporter.exportDashboardHtml>>) => void) | undefined;
    let signal: AbortSignal | undefined;
    jest.mocked(exporter.exportDashboardHtml).mockImplementation((_preview, _org, abortSignal) => {
      signal = abortSignal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    const mounted = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Download HTML report' }));
    fireEvent.click(screen.getByRole('button', { name: 'Generate report' }));
    await waitFor(() => expect(signal).toBeDefined());
    expect(screen.getByRole('button', { name: 'Generate report' })).toBeDisabled();
    mounted.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      finish?.({ blob: new Blob(), filename: 'old.html', failures: 0 });
    });
  });

  it('keeps the download outside the document router and releases its object URL', async () => {
    jest
      .mocked(exporter.exportDashboardHtml)
      .mockResolvedValue({ blob: new Blob(['report']), filename: 'report.html', failures: 0 });
    const oldCreate = URL.createObjectURL;
    const oldRevoke = URL.revokeObjectURL;
    URL.createObjectURL = jest.fn(() => 'blob:http://localhost/report');
    URL.revokeObjectURL = jest.fn();
    let attached = true;
    let filename = '';
    const click = jest
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        attached = this.isConnected;
        filename = this.download;
      });
    try {
      mount();
      fireEvent.click(screen.getByRole('button', { name: 'Download HTML report' }));
      fireEvent.click(screen.getByRole('button', { name: 'Generate report' }));
      await screen.findByText('Report ready');
      jest.useFakeTimers();
      fireEvent.click(screen.getAllByRole('button', { name: 'Download HTML report' }).at(-1)!);
      expect(click).toHaveBeenCalledTimes(1);
      expect(attached).toBe(false);
      expect(filename).toBe('report.html');
      act(() => jest.runOnlyPendingTimers());
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:http://localhost/report');
    } finally {
      jest.useRealTimers();
      click.mockRestore();
      URL.createObjectURL = oldCreate;
      URL.revokeObjectURL = oldRevoke;
    }
  });

  it('shows renderer failures with no download action', async () => {
    mount();
    config.rendererAvailable = false;
    fireEvent.click(screen.getByRole('button', { name: 'Download HTML report' }));
    fireEvent.click(screen.getByRole('button', { name: 'Generate report' }));
    expect(await screen.findByText(/Image Renderer is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText('Report ready')).not.toBeInTheDocument();
  });
});
