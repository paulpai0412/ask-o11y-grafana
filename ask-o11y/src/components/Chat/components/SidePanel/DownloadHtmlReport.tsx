import React, { RefObject, useEffect, useRef, useState } from 'react';
import { config } from '@grafana/runtime';
import { Alert, Button, Modal } from '@grafana/ui';
import {
  dashboardLocation,
  exportDashboardHtml,
  PreviewSnapshot,
  ReportVariable,
} from '../../../../services/dashboardHtmlExport';

// Grafana 13 dashboard scenes expose their active context; the runtime module must
// be read in the iframe's realm, not the parent app's unrelated template service.
type PreviewWindow = Window & {
  System?: { import: (name: string) => Promise<{ getTemplateSrv: () => { getVariables: () => ReportVariable[] } }> };
  __grafanaSceneContext?: {
    state: {
      uid: string;
      version: number;
      isDirty?: boolean;
      $timeRange?: { state: { value?: { from: { valueOf: () => number }; to: { valueOf: () => number } } } };
    };
  };
};

export async function readPreviewSnapshot(frame: Window): Promise<PreviewSnapshot> {
  const preview = frame as PreviewWindow;
  if (!preview.System || !preview.__grafanaSceneContext) {
    throw new Error('Wait for a supported Grafana dashboard preview to load before exporting.');
  }
  const runtime = await preview.System.import('@grafana/runtime');
  const state = preview.__grafanaSceneContext.state;
  const { url, uid } = dashboardLocation(preview.location.href, window.location.origin);
  const range = state.$timeRange?.state.value;
  if (state.uid !== uid || state.isDirty || !range || !Number.isInteger(state.version)) {
    throw new Error('Save and reload the dashboard before exporting this preview.');
  }
  url.searchParams.delete('time');
  url.searchParams.delete('time.window');
  url.searchParams.set('from', String(range.from.valueOf()));
  url.searchParams.set('to', String(range.to.valueOf()));
  const variables = runtime
    .getTemplateSrv()
    .getVariables()
    .map(({ name, current, options }) => ({
      name,
      current: { value: Array.isArray(current?.value) ? [...current.value] : current?.value },
      options: options?.map(({ value }) => ({ value })),
    }));
  for (const variable of variables) {
    const selected = variable.current.value;
    if (selected !== undefined) {
      url.searchParams.delete(`var-${variable.name}`);
      for (const value of Array.isArray(selected) ? selected : [selected]) {
        url.searchParams.append(`var-${variable.name}`, value);
      }
    }
  }
  return { href: url.href, version: state.version, variables };
}

interface Props {
  iframeRef: RefObject<HTMLIFrameElement>;
}

export function DownloadHtmlReport({ iframeRef }: Props) {
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState<string>();
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<Awaited<ReturnType<typeof exportDashboardHtml>>>();
  const controller = useRef<AbortController>();

  useEffect(() => () => controller.current?.abort(), []);

  function dismiss() {
    controller.current?.abort();
    controller.current = undefined;
    setOpen(false);
    setProgress(undefined);
    setResult(undefined);
    setError(undefined);
  }

  async function generate() {
    if (controller.current) {
      return;
    }
    const pending = new AbortController();
    controller.current = pending;
    setError(undefined);
    setProgress('Preparing report…');
    try {
      const frameWindow = iframeRef.current?.contentWindow;
      if (!frameWindow) {
        throw new Error('Wait for the dashboard preview to load before exporting.');
      }
      if (!config.rendererAvailable) {
        throw new Error('Grafana Image Renderer is unavailable. Ask your administrator to enable it.');
      }
      const preview = await readPreviewSnapshot(frameWindow);
      pending.signal.throwIfAborted();
      const report = await exportDashboardHtml(preview, config.bootData.user.orgId, pending.signal, (done, total) => {
        if (!pending.signal.aborted) {
          setProgress(`Rendering panel images: ${done} / ${total}`);
        }
      });
      if (!pending.signal.aborted) {
        setResult(report);
      }
    } catch (failure) {
      if (!pending.signal.aborted) {
        setError(failure instanceof Error ? failure.message : 'Report generation failed.');
      }
    } finally {
      if (!pending.signal.aborted) {
        controller.current = undefined;
        setProgress(undefined);
      }
    }
  }

  function download() {
    if (!result) {
      return;
    }
    const url = URL.createObjectURL(result.blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = result.filename;
    // A detached download anchor avoids Grafana's document-level SPA link interceptor.
    try {
      anchor.click();
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }

  return (
    <>
      <Button
        variant="secondary"
        fill="text"
        size="sm"
        icon="download-alt"
        onClick={() => setOpen(true)}
        aria-label="Download HTML report"
        title="Download HTML report"
      >
        HTML
      </Button>
      {open && (
        <Modal isOpen title="Download HTML report" onDismiss={dismiss}>
          <p>
            Export this saved dashboard and the preview’s current time range and variables as a single offline HTML
            file.
          </p>
          <p>
            Charts are static images without hover or zoom. Tables contain visible rows only, not the full dataset.
            Panel queries are sequential, not an atomic data snapshot.
          </p>
          <Alert severity="warning" title="Downloaded files are outside Grafana access control">
            Review the images before sharing. Only send this report to authorized recipients.
          </Alert>
          {progress && (
            <p role="status" aria-live="polite">
              {progress}
            </p>
          )}
          {error && (
            <Alert severity="error" title="Could not generate report">
              {error}
            </Alert>
          )}
          {result && (
            <Alert
              severity={result.failures ? 'warning' : 'info'}
              title={result.failures ? 'Incomplete report' : 'Report ready'}
            >
              {result.failures
                ? `${result.failures} panel images failed. Missing images are identified inside the report.`
                : 'Panel images are ready. Grafana error or no-data messages may be present in images; inspect the report before sharing.'}
            </Alert>
          )}
          <Modal.ButtonRow>
            <Button variant="secondary" onClick={dismiss}>
              {progress ? 'Cancel export' : 'Close'}
            </Button>
            {result ? (
              <Button icon="download-alt" onClick={download}>
                {result.failures ? 'Download incomplete HTML' : 'Download HTML report'}
              </Button>
            ) : (
              <Button disabled={!!progress} icon={progress ? 'spinner' : 'download-alt'} onClick={generate}>
                Generate report
              </Button>
            )}
          </Modal.ButtonRow>
        </Modal>
      )}
    </>
  );
}
