import { CalendarClock, ExternalLink, ShieldCheck } from 'lucide-react';
import { useState } from 'react';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { toastSuccess } from '@/lib/api/toast';

import { usePublishStatus, useVeto } from '../api/queries';
import { formatDateTime } from '../model/format';
import { lastErrorMessage } from '../model/status';
import type { Article } from '../model/types';

import { ConfirmDialog } from './confirm-dialog';
import { clearanceValid } from './preflight';
import type { ArticleCommands } from './use-article-commands';

/** Labels for the publish jobs' steps (contract W-B2). */
const PUBLISH_STEP_LABELS: Readonly<Record<string, string>> = {
  check: 'Checks before publishing',
  render: 'Rendering the page',
  upsert: 'Updating the portfolio data',
  dispatch: 'Starting the portfolio build',
  wait_build: 'Portfolio build',
  verify: 'Checking the live page',
  finalize: 'Finishing',
};

const AWAITING: Readonly<Record<string, string>> = {
  portfolio: 'Publishing to the portfolio isn’t set up yet, so the job is waiting.',
};

/** Where publishing stands: scheduled, running, live or failed (contract W-B2, `publish-status`). */
export function PublishStanding({ article }: { article: Article }) {
  const status = usePublishStatus(article.id, article.status);
  const publish = article.publish;
  const job = status.data?.job ?? null;
  const awaiting = job?.awaiting ? (AWAITING[job.awaiting] ?? 'The publish job is waiting for something it needs.') : undefined;

  switch (article.status) {
    case 'approved':
      return (
        <Alert variant="info" icon={<CalendarClock aria-hidden="true" />}>
          <AlertTitle>
            {publish?.publishAt ? `Publishes ${formatDateTime(publish.publishAt)}` : 'Approved for publishing'}
          </AlertTitle>
          <AlertDescription>
            <p>
              {publish?.auto
                ? 'Approved by auto-publish. You can veto it until it goes out.'
                : 'You approved it. Pull it back to review to change anything before then.'}
            </p>
            {awaiting ? <p>{awaiting}</p> : null}
          </AlertDescription>
        </Alert>
      );
    case 'publishing':
    case 'unpublishing':
      return publish?.jobId ? (
        <JobTimeline
          jobId={publish.jobId}
          title={article.status === 'publishing' ? 'Publishing' : 'Unpublishing'}
          labels={PUBLISH_STEP_LABELS}
        />
      ) : (
        <p className="text-sm text-ink-muted">{article.status === 'publishing' ? 'Publishing…' : 'Unpublishing…'}</p>
      );
    case 'published':
      return (
        <Alert variant="success">
          <AlertTitle>{publish?.publishedAt ? `Live since ${formatDateTime(publish.publishedAt)}` : 'Live'}</AlertTitle>
          <AlertDescription>
            {publish?.url ? (
              <p>
                <a
                  href={publish.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 font-medium text-ink underline underline-offset-4"
                >
                  Open the live page
                  <ExternalLink aria-hidden="true" className="size-3.5" />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              </p>
            ) : null}
            {publish?.lastPublishedVersion ? <p>{`Version ${String(publish.lastPublishedVersion)} is live.`}</p> : null}
          </AlertDescription>
        </Alert>
      );
    case 'publish_failed':
      return (
        <Alert variant="error" role={undefined}>
          <AlertTitle>
            {publish?.lastOperation === 'unpublish' ? 'Unpublishing failed' : 'Publishing failed'}
          </AlertTitle>
          <AlertDescription>
            <p>
              {lastErrorMessage(job?.errorCode ?? publish?.lastError ?? article.lastError) ??
                'The publish job stopped with an error.'}
            </p>
            {publish?.live ? <p>The previous version is still live.</p> : null}
          </AlertDescription>
        </Alert>
      );
    case 'planned':
    case 'researching':
    case 'interviewing':
    case 'drafting':
    case 'gating':
    case 'in_review':
    case 'unpublished':
    case 'needs_attention':
    case 'archived':
      return null;
  }
}

/** The auto-publish veto (contract W-B4): when the notice went out, and the veto itself. */
export function VetoNotice({ article, commands }: { article: Article; commands: ArticleCommands }) {
  const enabled = article.status === 'approved' && (article.publish?.auto ?? false);
  const veto = useVeto(article.id, enabled);
  const [open, setOpen] = useState(false);
  if (!enabled) return null;
  const notice = veto.data?.notice ?? article.veto ?? null;
  const sent = notice?.sentAt
    ? notice.delivered
      ? `The veto notice reached your phone ${formatDateTime(notice.receiptAt ?? notice.sentAt)}.`
      : `The veto notice went out ${formatDateTime(notice.sentAt)} but wasn’t delivered, so publishing holds until you approve it yourself.`
    : 'The veto notice goes out before it publishes.';
  return (
    <Alert variant="warn">
      <AlertTitle>{`Auto-publish ${veto.data?.publishAt ? formatDateTime(veto.data.publishAt) : 'scheduled'}`}</AlertTitle>
      <AlertDescription>
        <p>{sent}</p>
      </AlertDescription>
      <AlertActions>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setOpen(true);
          }}
        >
          Veto
        </Button>
      </AlertActions>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Veto this auto-publish?"
        description={
          <p>It goes back to review and the scheduled publish is cancelled. The auto-publish streak starts again.</p>
        }
        confirmLabel="Veto"
        pendingLabel="Vetoing…"
        cancelLabel="Let it publish"
        variant="danger"
        onConfirm={async () => {
          await commands.veto();
          toastSuccess('Vetoed. It’s back in review.');
        }}
      />
    </Alert>
  );
}

const STATEMENT_MAX = 1000;

/**
 * The clearance attestation (contract W-A7): case studies and anonymized stories need the
 * owner's statement for the exact version that ships; any change to the text needs it again.
 */
export function ClearanceNotice({ article, commands, canAttest }: { article: Article; commands: ArticleCommands; canAttest: boolean }) {
  const [open, setOpen] = useState(false);
  const [statement, setStatement] = useState('');
  if (!article.clearance?.required) return null;
  const valid = clearanceValid(article);
  return (
    <Alert variant={valid ? 'success' : 'warn'} icon={<ShieldCheck aria-hidden="true" />}>
      <AlertTitle>{valid ? 'Cleared for publication' : 'Clearance needed for this version'}</AlertTitle>
      <AlertDescription>
        <p>
          {valid
            ? `You cleared this version ${formatDateTime(article.clearance.attestedAt)}. Any change to the text needs it again.`
            : 'It is a case study or uses an anonymized story, so you confirm it is cleared before it publishes.'}
        </p>
      </AlertDescription>
      {!valid && canAttest ? (
        <AlertActions>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setOpen(true);
            }}
          >
            Confirm clearance
          </Button>
        </AlertActions>
      ) : null}
      <ConfirmDialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setStatement('');
        }}
        title="Confirm clearance"
        description={
          <p>
            You confirm this exact version is cleared to publish under your employer’s policy. Leave the statement empty
            to use the one in your policy settings.
          </p>
        }
        confirmLabel="Confirm clearance"
        pendingLabel="Confirming…"
        onConfirm={async () => {
          await commands.attestClearance(statement.trim() === '' ? undefined : statement.trim());
          toastSuccess('Clearance confirmed for this version.');
        }}
      >
        <Field label="Statement" description="Optional. Saved with the attestation.">
          <Textarea
            rows={3}
            maxLength={STATEMENT_MAX}
            value={statement}
            onChange={(event) => {
              setStatement(event.target.value);
            }}
          />
        </Field>
      </ConfirmDialog>
    </Alert>
  );
}
