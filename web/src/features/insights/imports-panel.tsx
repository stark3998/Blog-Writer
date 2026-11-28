import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FileSpreadsheet, Upload } from 'lucide-react';
import { useRef, useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { ApiClient } from '@/lib/api/client';
import type { Versioned } from '@/lib/api/envelope';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { apiRequest } from '@/lib/api/request';
import { toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { useApiInfiniteQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { BlockLoading } from '../settings/components/settings-block';
import { useNow } from '@/lib/use-now';
import { formatDateTime, formatRelative } from '../settings/format';
import { insightsKeys, type AnalyticsImport } from './queries';

const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
/** The server's limit (`BW_ANALYTICS_IMPORT_MAX_BYTES`, 5 MB by default). */
const MAX_BYTES = 5 * 1024 * 1024;

/** The media type the server accepts for a file, from its name when the browser gives none. */
export function exportContentType(file: Pick<File, 'name' | 'type'>): string | undefined {
  const name = file.name.toLowerCase();
  if (name.endsWith('.xlsx')) return XLSX;
  if (name.endsWith('.csv')) return 'text/csv';
  return file.type === XLSX || file.type === 'text/csv' ? file.type : undefined;
}

/** `POST /api/analytics/imports?filename=` with the file itself as the raw body (.xlsx or .csv). */
export async function uploadExport(api: ApiClient, file: File): Promise<Versioned<AnalyticsImport>> {
  const contentType = exportContentType(file);
  return apiRequest(api, 'post', '/api/analytics/imports', {
    params: { query: { filename: file.name.slice(0, 255) } },
    body: file,
    bodySerializer: (body) => body,
    headers: { 'Content-Type': contentType ?? 'application/octet-stream' },
  });
}

/**
 * LinkedIn's own analytics export (beta): numbers matched to posts by date, then by URN. Until a
 * real export confirms the layout the server may keep it switched off; the snapshot form always
 * works.
 */
export function ImportsPanel() {
  const { api } = useAppData();
  const now = useNow();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const imports = useApiInfiniteQuery({
    queryKey: insightsKeys.imports(),
    path: '/api/analytics/imports',
    pageSize: 10,
  });
  const upload = useMutation({
    mutationFn: (file: File) => uploadExport(api, file),
    onSuccess: async (stored) => {
      toastSuccess(`Imported: ${String(stored.item.matched)} of ${String(stored.item.rows)} rows matched a post.`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.posts.lists() });
    },
  });
  const [tooBig, setTooBig] = useState(false);
  const choose = (file: File | undefined) => {
    if (!file) return;
    upload.reset();
    setTooBig(file.size > MAX_BYTES);
    if (file.size > MAX_BYTES) return;
    upload.mutate(file);
  };

  const disabled = isProblem(upload.error, 'import_unavailable') || isProblem(imports.error, 'import_unavailable');

  return (
    <div className="grid max-w-measure gap-4">
      <input
        ref={input}
        type="file"
        accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          choose(event.target.files?.[0]);
          event.target.value = '';
        }}
      />
      <Button
        variant="secondary"
        className="justify-self-start"
        disabled={upload.isPending || disabled}
        onClick={() => input.current?.click()}
      >
        <Upload aria-hidden="true" />
        {upload.isPending ? 'Importing…' : 'Import a LinkedIn export'}
      </Button>
      {tooBig ? (
        <p role="alert" className="text-sm font-medium text-danger">
          That file is larger than 5 MB. Export a shorter period and try again.
        </p>
      ) : null}
      {upload.isError ? (
        isProblem(upload.error, 'import_unavailable') ? (
          <Alert variant="info">
            <AlertTitle>Export import is switched off on the server.</AlertTitle>
            <AlertDescription>
              <p>It stays off until a real export confirms the layout. Use the snapshot form meanwhile.</p>
            </AlertDescription>
          </Alert>
        ) : isProblem(upload.error, ['unknown_export_format', 'too_large', 'unsupported_media_type']) ? (
          <Alert variant="error">
            <AlertTitle>That file wasn’t imported.</AlertTitle>
            <AlertDescription>
              <p>{problemMessage(upload.error)}</p>
            </AlertDescription>
          </Alert>
        ) : (
          <ProblemAlert error={upload.error} title="Couldn’t import the export." />
        )
      ) : null}
      {imports.isPending ? (
        <BlockLoading label="Loading the imports" lines={2} />
      ) : imports.isError ? (
        <ProblemAlert error={imports.error} title="Couldn’t load the imports." onRetry={() => void imports.refetch()} />
      ) : imports.data.length === 0 ? (
        <p className="text-sm text-ink-muted">No exports imported yet.</p>
      ) : (
        <ul aria-label="Imported exports" className="border-t border-line text-sm">
          {imports.data.map(({ item }) => (
            <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2.5">
              <span className="flex min-w-0 items-center gap-2 text-ink">
                <FileSpreadsheet aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
                <span className="truncate">{item.filename ?? 'Export'}</span>
              </span>
              <span className="text-ink-muted">
                {`${String(item.matched)} of ${String(item.rows)} rows matched, `}
                <time dateTime={item.importedAt} title={formatDateTime(item.importedAt)}>
                  {formatRelative(item.importedAt, now)}
                </time>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
