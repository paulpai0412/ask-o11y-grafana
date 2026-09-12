import React, { useRef, useState } from 'react';
import { Button } from '@grafana/ui';

import { createSession, deleteSession } from '../../../../services/backendSessionClient';
import { uploadDataset, type UploadRequest, type UploadedDataset } from '../../../../services/uploadClient';

interface UploadButtonProps {
  disabled?: boolean;
  onUploaded: (message: string, sessionId: string, uploaded: UploadedDataset) => void;
}

export function UploadButton({ disabled, onUploaded }: UploadButtonProps): React.ReactElement {
  const inputRef = useRef<HTMLInputElement>(null);
  const requestRef = useRef<UploadRequest | undefined>(undefined);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  async function submit(file: File, sheet?: string): Promise<void> {
    setUploading(true);
    let createdSessionId: string | undefined;
    try {
      const session = await createSession(`Upload: ${file.name}`, []);
      createdSessionId = session.id;
      let uploaded;
      try {
        requestRef.current = uploadDataset(file, session.id, sheet, setProgress);
        uploaded = await requestRef.current.promise;
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Upload failed';
        if (message.startsWith('SHEET_SELECTION_REQUIRED:')) {
          const sheets = JSON.parse(message.slice('SHEET_SELECTION_REQUIRED:'.length)) as string[];
          const selected = window.prompt(`Choose a sheet:\n${sheets.join('\n')}`, sheets[0]);
          if (!selected || !sheets.includes(selected)) {
            throw new Error('Upload cancelled: no valid sheet selected');
          }
          requestRef.current = uploadDataset(file, session.id, selected, setProgress);
          uploaded = await requestRef.current.promise;
        } else {
          throw error;
        }
      }
      const sheetLabel = uploaded.sheet ? `, sheet: ${uploaded.sheet}` : '';
      onUploaded(
        `Use uploaded dataset \`${uploaded.dataset_id}\` (${uploaded.filename}${sheetLabel}, ${uploaded.rows} rows, ${uploaded.columns} columns). `,
        session.id,
        uploaded
      );
      createdSessionId = undefined;
    } catch (error) {
      if (createdSessionId) {
        await deleteSession(createdSessionId).catch(() => undefined);
      }
      window.alert(error instanceof Error ? error.message : 'Upload failed');
    } finally {
      requestRef.current = undefined;
      setUploading(false);
      setProgress(0);
      if (inputRef.current) {
        inputRef.current.value = '';
      }
    }
  }

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        icon={uploading ? 'times' : 'upload'}
        disabled={disabled}
        onClick={() => uploading ? requestRef.current?.cancel() : inputRef.current?.click()}
        aria-label={uploading ? `Cancel upload (${progress}%)` : 'Upload CSV or Excel'}
        title={uploading ? `取消上傳 (${progress}%)` : '上傳 CSV / Excel'}
      />
      {uploading && <span className="self-center text-xs text-secondary">{progress}%</span>}
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        hidden
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) {
            void submit(file);
          }
        }}
      />
    </>
  );
}
