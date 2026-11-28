import { ChevronDown, DatabaseBackup, Download, ShieldCheck, X } from 'lucide-react';
import { useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { LiveJob } from '../components/live-job';
import { BlockLoading, SubHeading } from '../components/settings-block';
import { formatDate, formatDateTime, formatDay, formatNumber } from '../format';
import { retryUnless, settingsKeys, type Schemas } from '../queries';

type BackupDump = Schemas['BackupDumpView'];
type BackupsView = Schemas['BackupsView'];

const BACKUP_STEPS: Readonly<Record<string, string>> = {
  dump: 'Copy each container',
  manifest: 'Write the manifest',
};

const bytes = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

/** "840 KB", "1.2 MB". */
export function formatBytes(size: number): string {
  if (size < 1000) return `${String(size)} bytes`;
  if (size < 1_000_000) return `${bytes.format(size / 1000)} KB`;
  return `${bytes.format(size / 1_000_000)} MB`;
}

/** Same-origin download link of one dump file (`GET /api/backups/{date}/{container}`). */
export function dumpHref(date: string, container: string): string {
  return `/api/backups/${encodeURIComponent(date)}/${encodeURIComponent(container)}`;
}

function DumpFiles({ dump }: { dump: BackupDump }) {
  return (
    <ul aria-label={`Files of ${formatDay(dump.date)}`} className="grid gap-1.5 sm:grid-cols-2">
      {dump.files.map((file) => (
        <li key={file.blob}>
          <a
            href={dumpHref(dump.date, file.container)}
            download
            className="inline-flex min-h-11 items-center gap-2 text-sm text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink md:min-h-0"
          >
            <Download aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
            {file.container}
            <span className="text-ink-subtle no-underline">{`(${formatBytes(file.sizeBytes)})`}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

function Dumps({ view }: { view: BackupsView }) {
  const [open, setOpen] = useState(false);
  const [latest, ...older] = [...view.dumps].sort((a, b) => b.date.localeCompare(a.date));
  if (!latest) return <p className="text-ink-muted">No backups are stored yet. The first one runs tonight, or now if you start it.</p>;
  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <p className="font-medium text-ink">
          {formatDay(latest.date)}
          {latest.complete ? null : <span className="font-normal text-ink-muted"> (incomplete: it didn’t finish)</span>}
        </p>
        <DumpFiles dump={latest} />
      </div>
      {older.length > 0 ? (
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="justify-self-start">
              <ChevronDown aria-hidden="true" className={open ? 'rotate-180' : undefined} />
              {open ? 'Hide older backups' : `Show ${String(older.length)} older ${older.length === 1 ? 'backup' : 'backups'}`}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="mt-3 grid gap-5">
              {older.map((dump) => (
                <div key={dump.date} className="grid gap-2">
                  <p className="font-medium text-ink">
                    {formatDay(dump.date)}
                    {dump.complete ? null : <span className="font-normal text-ink-muted"> (incomplete)</span>}
                  </p>
                  <DumpFiles dump={dump} />
                </div>
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}

/**
 * Backups and the data export: the daily dumps of every durable container (gzip JSON Lines, kept
 * 35 days), "Back up now", the downloads, and the monthly restore drill the owner attests.
 */
export function BackupsPanel() {
  const backups = useApiQuery(
    { queryKey: settingsKeys.backups, path: '/api/backups' },
    { retry: retryUnless('storage_unavailable') },
  );
  const [jobId, setJobId] = useState<string | undefined>();
  const [confirming, setConfirming] = useState(false);
  const run = useApiMutation('post', '/api/backups/run', {
    successToast: 'Backing up now.',
    errorToast: 'Couldn’t start the backup.',
  });
  const drill = useApiMutation('post', '/api/backups/restore-drill', {
    invalidates: [settingsKeys.backups, queryKeys.today],
    successToast: 'Restore drill recorded. The next one is due in a month.',
    errorToast: 'Couldn’t record the restore drill.',
  });

  if (backups.isPending) return <BlockLoading label="Loading the backups" />;
  if (backups.isError) {
    if (isProblem(backups.error, 'storage_unavailable')) {
      return (
        <Alert variant="info" className="max-w-measure">
          <AlertTitle>File storage isn’t set up, so backups and the data export are off.</AlertTitle>
          <AlertDescription>
            <p>The database keeps its own seven-day point-in-time restore either way.</p>
          </AlertDescription>
        </Alert>
      );
    }
    return (
      <ProblemAlert
        error={backups.error}
        title="Couldn’t load the backups."
        onRetry={() => void backups.refetch()}
        retryLabel="Try loading the backups again"
      />
    );
  }
  const view = backups.data;
  const status = view.status.item;
  const last = status.lastSuccess;
  const documents = last?.files?.reduce((sum, file) => sum + file.documents, 0) ?? 0;

  return (
    <div className="grid gap-8">
      <div className="grid max-w-measure gap-3">
        {status.consecutiveFailures > 0 ? (
          <Alert variant="error">
            <AlertTitle>
              {status.consecutiveFailures === 1
                ? 'The last backup failed.'
                : `The last ${String(status.consecutiveFailures)} backups failed.`}
            </AlertTitle>
            <AlertDescription>
              <p>
                {status.lastError ? `It stopped with “${status.lastError}”. ` : null}
                Start one now; if it fails again, check its job below.
              </p>
            </AlertDescription>
          </Alert>
        ) : null}
        <p className="text-ink">
          {last
            ? `Last backup ${formatDate(last.finishedAt)}: ${formatNumber(documents, { whole: true })} documents in ${String(last.files?.length ?? 0)} containers.`
            : 'No backup has finished yet.'}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={run.isPending}
            onClick={() => {
              run.mutate(undefined, {
                onSuccess: (accepted) => {
                  setJobId(accepted.jobId);
                },
              });
            }}
          >
            <DatabaseBackup aria-hidden="true" />
            {run.isPending ? 'Starting…' : 'Back up now'}
          </Button>
        </div>
        {jobId ? (
          <div className="flex items-start justify-between gap-4 rounded-lg border border-line p-4">
            <LiveJob jobId={jobId} title="Backing up" labels={BACKUP_STEPS} />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Hide the backup progress"
              onClick={() => {
                setJobId(undefined);
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </div>
        ) : null}
      </div>

      <div className="grid max-w-3xl gap-3">
        <SubHeading>Download your data</SubHeading>
        <p className="max-w-measure text-ink-muted">
          One file per container, gzip-compressed JSON Lines: everything the app stores except job records, sign-in states and
          other members’ pasted text.
        </p>
        <Dumps view={view} />
      </div>

      <div className="grid max-w-measure gap-3">
        <SubHeading>Restore drill</SubHeading>
        {view.restoreDrillDue ? (
          <Alert variant="warn">
            <AlertTitle>A restore drill is due.</AlertTitle>
            <AlertDescription>
              <p>Once a month, restore the latest dump into a scratch environment following RESTORE.md, then record it here.</p>
            </AlertDescription>
          </Alert>
        ) : null}
        <p className="text-ink-muted">
          {status.lastRestoreDrillAt
            ? `Last drill ${formatDateTime(status.lastRestoreDrillAt)}.`
            : 'No restore drill recorded yet.'}
        </p>
        <Button
          variant={view.restoreDrillDue ? 'primary' : 'secondary'}
          className="justify-self-start"
          disabled={drill.isPending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          <ShieldCheck aria-hidden="true" />
          I did the restore drill
        </Button>
      </div>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Record the restore drill?</AlertDialogTitle>
            <AlertDialogDescription>
              Confirm that you restored the latest backup somewhere safe and checked it opened, following RESTORE.md.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Not yet</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                drill.mutate();
              }}
            >
              Record the drill
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
