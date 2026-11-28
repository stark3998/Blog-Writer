import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleSlash,
  CircleX,
  Play,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useId, useRef, useState, type SyntheticEvent } from 'react';

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useJobStatus } from '@/features/jobs/use-job';
import { isEtagMismatch } from '@/lib/api/envelope';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { cn } from '@/lib/cn';
import { useAppData } from '@/lib/app-data';
import { useApiMutation } from '@/lib/query/api-query';

import { draftKeys } from '../api/queries';
import { formatDateTime } from '../model/format';
import {
  GATE_ORDER,
  GATE_STATE_LABELS,
  GATES,
  gateOverridable,
  gateState,
  RUBRIC_DIMENSIONS,
  RUBRIC_OVERALL_MIN,
  sortFindings,
  summarizeGates,
  type GateState,
} from '../model/gates';
import type { ArticleActions } from '../model/status';
import type { Finding, GateName, GateResult, GatesView, VersionedArticle } from '../model/types';

import { FindingList } from './finding-list';

const STATE_ICONS: Readonly<Record<GateState, { icon: LucideIcon; className: string }>> = {
  pass: { icon: CircleCheck, className: 'text-success' },
  warn: { icon: CircleAlert, className: 'text-warn' },
  fail: { icon: CircleX, className: 'text-danger' },
  skipped: { icon: CircleMinus, className: 'text-ink-subtle' },
  error: { icon: CircleSlash, className: 'text-danger' },
  overridden: { icon: ShieldCheck, className: 'text-ink' },
  'not-run': { icon: CircleDashed, className: 'text-ink-subtle' },
};

/** Labels for the `gates.run` job's steps. */
const RUN_STEP_LABELS: Readonly<Record<string, string>> = {
  deterministic: 'Text checks',
  network: 'Links and diagrams',
  confidentiality: 'Confidentiality',
  fact_check: 'Facts',
  originality: 'Originality',
  rubric: 'Quality critique',
  report: 'Report',
};

const OVERRIDE_MIN = 3;
const OVERRIDE_MAX = 500;

function RubricScores({ result }: { result: GateResult }) {
  const dimensions = result.dimensions ?? {};
  const known = RUBRIC_DIMENSIONS.filter((dimension) => typeof dimensions[dimension.id] === 'number');
  if (result.score === null || result.score === undefined) {
    if (known.length === 0) return null;
  }
  return (
    <dl className="mt-3 grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1 text-sm">
      {typeof result.score === 'number' ? (
        <>
          <dt className="font-semibold text-ink">Overall</dt>
          <dd className="text-right font-semibold tabular-nums text-ink">{result.score.toFixed(1)}</dd>
          <dd className="text-xs text-ink-subtle">{`needs ${String(RUBRIC_OVERALL_MIN)}`}</dd>
        </>
      ) : null}
      {known.map((dimension) => {
        const score = dimensions[dimension.id] ?? 0;
        const below = score < dimension.min;
        return (
          <div key={dimension.id} className="contents">
            <dt className="text-ink-muted">{dimension.label}</dt>
            <dd className={cn('text-right tabular-nums', below ? 'font-semibold text-danger' : 'text-ink')}>
              {score.toFixed(1)}
              {below ? <span className="sr-only"> (below the minimum)</span> : null}
            </dd>
            <dd className="text-xs text-ink-subtle">{`needs ${String(dimension.min)}`}</dd>
          </div>
        );
      })}
    </dl>
  );
}

interface OverrideFormProps {
  gate: GateName;
  pending: boolean;
  problem: unknown;
  onOverride: (reason: string) => void;
}

function OverrideForm({ gate, pending, problem, onOverride }: OverrideFormProps) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string>();
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    const text = reason.trim();
    if (text.length < OVERRIDE_MIN) {
      setError('Say why, in a few words.');
      return;
    }
    onOverride(text);
  };
  return (
    <form className="mt-4 grid gap-3 border-t border-line pt-4" onSubmit={submit} noValidate>
      <Field
        label={`Override ${GATES[gate].name}`}
        description="Your reason is recorded with the report. An override counts when you publish by hand, never for auto-publish."
        error={error ?? (problem ? problemMessage(problem) : undefined)}
      >
        <Textarea
          rows={3}
          maxLength={OVERRIDE_MAX}
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
            setError(undefined);
          }}
        />
      </Field>
      <Button type="submit" size="sm" variant="secondary" disabled={pending} className="justify-self-start">
        {pending ? 'Recording…' : 'Record the override'}
      </Button>
    </form>
  );
}

interface GateItemProps {
  gate: GateName;
  result: GateResult | undefined;
  stale: boolean;
  canOverride: boolean;
  overriding: GateName | null;
  overrideProblem: unknown;
  onOverride: (gate: GateName, reason: string) => void;
  onShow: (finding: Finding) => void;
}

function GateItem({ gate, result, stale, canOverride, overriding, overrideProblem, onOverride, onShow }: GateItemProps) {
  const state = gateState(result);
  const { icon: Icon, className } = STATE_ICONS[state];
  const findings = sortFindings(result?.findings ?? []);
  const override = result?.override ?? null;
  const overridable = result ? gateOverridable(result) : false;
  return (
    <AccordionItem value={gate}>
      <AccordionTrigger headingLevel={3} className="py-3 text-sm">
        <span className="flex min-w-0 flex-1 items-center gap-2.5">
          <Icon aria-hidden="true" className={cn('size-4 shrink-0', className)} />
          <span className="truncate">{GATES[gate].name}</span>
          <span className={cn('ml-auto shrink-0 text-xs font-medium', className === 'text-ink' ? 'text-ink-muted' : className)}>
            {GATE_STATE_LABELS[state]}
          </span>
        </span>
      </AccordionTrigger>
      <AccordionContent className="text-sm">
        <p className="text-ink-muted">{GATES[gate].checks}</p>
        {gate === 'rubric' && result ? <RubricScores result={result} /> : null}
        {findings.length > 0 ? (
          <FindingList className="mt-3" findings={findings} truncated={result?.truncated ?? 0} onShow={onShow} />
        ) : result ? (
          <p className="mt-3 text-ink-muted">No findings.</p>
        ) : null}
        {override ? (
          <p className="mt-3 rounded-md bg-sunken px-3 py-2 text-ink-muted">
            <span className="font-medium text-ink">{`Overridden ${formatDateTime(override.at)}: `}</span>
            {override.reason}
          </p>
        ) : null}
        {!override && overridable && canOverride && !stale ? (
          <OverrideForm
            gate={gate}
            pending={overriding === gate}
            problem={overriding === null ? overrideProblem : undefined}
            onOverride={(reason) => {
              onOverride(gate, reason);
            }}
          />
        ) : null}
        {!override && result && !overridable && state !== 'pass' && state !== 'warn' ? (
          <p className="mt-3 text-xs text-ink-subtle">This gate can’t be overridden: fix the blocking findings and run the gates again.</p>
        ) : null}
      </AccordionContent>
    </AccordionItem>
  );
}

export interface GatesPanelProps {
  articleId: string;
  actions: ArticleActions;
  view: GatesView | undefined;
  pending: boolean;
  error: unknown;
  onRetry: () => void;
  /** Saves pending edits first; false when they could not be saved. */
  ensureSaved: () => Promise<boolean>;
  onShow: (finding: Finding) => void;
  /** Refreshes the article after a 412 on an override. */
  onStale: () => void;
}

export function GatesPanel({ articleId, actions, view, pending, error, onRetry, ensureSaved, onShow, onStale }: GatesPanelProps) {
  const headingId = useId();
  const { queryClient } = useAppData();
  const [runJobId, setRunJobId] = useState<string | null>(null);
  const [overriding, setOverriding] = useState<GateName | null>(null);
  const [overrideProblem, setOverrideProblem] = useState<unknown>();
  const [notSaved, setNotSaved] = useState(false);
  const runStatus = useJobStatus(runJobId ?? '');

  const run = useApiMutation('post', '/api/articles/{article_id}/gates/run', {
    errorToast: 'Couldn’t start the gates.',
  });
  const override = useApiMutation('post', '/api/articles/{article_id}/gates/{gate}/override', {
    updates: () => draftKeys.article(articleId),
    invalidates: [draftKeys.gates(articleId)],
    successToast: 'Override recorded.',
    handles: ['etag_mismatch', 'not_overridable', 'report_stale', 'validation_failed'],
  });

  // A finished run: read the report again, once (the event may have arrived before the timeline).
  const finished = runStatus === 'succeeded' || runStatus === 'failed' || runStatus === 'cancelled';
  const refreshed = useRef<string | null>(null);
  useEffect(() => {
    if (!finished || runJobId === null || refreshed.current === runJobId) return;
    refreshed.current = runJobId;
    onRetry();
  }, [finished, runJobId, onRetry]);

  const startRun = async () => {
    if (!(await ensureSaved())) {
      setNotSaved(true);
      return;
    }
    setNotSaved(false);
    run.mutate(
      { params: { path: { article_id: articleId } } },
      {
        onSuccess: (accepted) => {
          setRunJobId(accepted.jobId);
        },
      },
    );
  };

  const doOverride = async (gate: GateName, reason: string) => {
    setOverrideProblem(undefined);
    if (!(await ensureSaved())) {
      setNotSaved(true);
      return;
    }
    // Saving may have changed the etag since this render: read the one in the cache now.
    const etag = queryClient.getQueryData<VersionedArticle>(draftKeys.article(articleId))?.etag;
    if (!etag) return;
    setOverriding(gate);
    override.mutate(
      { params: { path: { article_id: articleId, gate } }, body: { reason }, ifMatch: etag },
      {
        onError: (problem) => {
          setOverrideProblem(problem);
          if (isEtagMismatch(problem) || isProblem(problem, 'report_stale')) onStale();
        },
        onSettled: () => {
          setOverriding(null);
        },
      },
    );
  };

  const summary = summarizeGates(view);
  const results = new Map((view?.report?.results ?? []).map((result) => [result.gate, result]));
  const running = runJobId !== null && !finished;

  return (
    <section aria-labelledby={headingId} className="grid gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 id={headingId} className="font-serif text-xl font-medium text-ink">
          Gates
        </h2>
        {actions.runGates ? (
          <Button size="sm" variant="secondary" disabled={run.isPending || running} onClick={() => void startRun()}>
            <Play aria-hidden="true" />
            {running ? 'Running…' : view?.report ? 'Run again' : 'Run the gates'}
          </Button>
        ) : null}
      </div>

      {pending ? (
        <div className="grid gap-2" aria-busy="true">
          <p className="sr-only">Loading the gate report</p>
          <Skeleton className="h-14" />
          <Skeleton className="h-40" />
        </div>
      ) : error ? (
        <ProblemAlert error={error} title="Couldn’t load the gate report." onRetry={onRetry} />
      ) : (
        <>
          <Alert
            variant={summary.tone === 'success' ? 'success' : summary.tone === 'danger' ? 'error' : summary.tone === 'warn' ? 'warn' : 'info'}
            role={summary.tone === 'danger' ? undefined : 'status'}
          >
            <AlertTitle>{summary.title}</AlertTitle>
            <AlertDescription>
              <p>{summary.detail}</p>
              {view?.report ? (
                <p className="text-xs">{`Report for version ${String(view.report.version)}, ${formatDateTime(view.report.ranAt)}.`}</p>
              ) : null}
            </AlertDescription>
          </Alert>
          {notSaved ? (
            <p role="alert" className="text-sm text-danger">
              Your latest changes aren’t saved, and the gates check the saved text. Resolve the save first.
            </p>
          ) : null}
          {runJobId ? <JobTimeline jobId={runJobId} title="Running the gates" labels={RUN_STEP_LABELS} /> : null}
          <Accordion type="multiple" defaultValue={view?.report && !view.stale ? [...view.blocking] : []}>
            {GATE_ORDER.map((gate) => (
              <GateItem
                key={gate}
                gate={gate}
                result={results.get(gate)}
                stale={view?.stale ?? false}
                canOverride={actions.ownerEdits}
                overriding={overriding}
                overrideProblem={overrideProblem}
                onOverride={(name, reason) => void doOverride(name, reason)}
                onShow={onShow}
              />
            ))}
          </Accordion>
        </>
      )}
    </section>
  );
}

