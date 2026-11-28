import { Circle, CircleCheck, CircleDashed, ExternalLink, Pencil, Plus, Trash2, type LucideIcon } from 'lucide-react';
import { useId, useState, type SubmitEvent } from 'react';
import { Link } from 'react-router';

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
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { components } from '@/lib/api/schema';
import { cn } from '@/lib/cn';

import { PillarTag } from '../components/pillar';
import { DialogActions, ResponsiveDialog } from '../components/responsive-dialog';
import { StatusBadge } from '../components/status-badge';
import { ARTICLE_STATUS } from '../lib/labels';
import { flagshipMilestones, milestoneState, type MilestoneState, type WeekPlan } from '../lib/plan';
import type { CadenceView, FormatOption, PillarView } from '../lib/settings';
import { momentLabel, zoneAbbreviation } from '../lib/time';

import { articleHref } from './slot-model';

type ArticleSummary = components['schemas']['ArticleSummary'];
export type FlagshipInput = components['schemas']['FlagshipIn'];

const STEP_ICON: Record<MilestoneState, { icon: LucideIcon; tone: string; words: string }> = {
  done: { icon: CircleCheck, tone: 'text-success', words: 'done' },
  current: { icon: CircleDashed, tone: 'text-ink', words: 'in progress' },
  upcoming: { icon: Circle, tone: 'text-ink-subtle', words: 'coming up' },
  passed: { icon: Circle, tone: 'text-ink-subtle', words: 'time passed' },
};

interface FlagshipSectionProps {
  plan: WeekPlan;
  article: ArticleSummary | undefined;
  cadence: CadenceView;
  pillars: readonly PillarView[];
  formats: readonly FormatOption[];
  nowMs: number;
  editable: boolean;
  saving: boolean;
  onSave: (flagship: FlagshipInput | null, success: string) => void;
}

/**
 * The week's flagship article and its timeline, research to publication, as one thread through
 * the week. Once the plan is approved the article exists and each step follows its status.
 */
export function FlagshipSection({
  plan,
  article,
  cadence,
  pillars,
  formats,
  nowMs,
  editable,
  saving,
  onSave,
}: FlagshipSectionProps) {
  const headingId = useId();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const flagship = plan.flagship ?? undefined;
  const zone = plan.timezone;
  const formatName = (id: string) => formats.find((format) => format.id === id)?.name ?? id;
  const milestones = flagshipMilestones(plan.weekStart, cadence, zone, flagship?.publishAt);
  const status = article?.status;

  return (
    <section aria-labelledby={headingId} className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h3 id={headingId} className="font-semibold text-ink">
          Flagship article
        </h3>
        {editable && flagship ? (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setEditing(true);
              }}
            >
              <Pencil aria-hidden="true" />
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setRemoving(true);
              }}
            >
              <Trash2 aria-hidden="true" />
              Remove
            </Button>
          </div>
        ) : null}
      </div>

      {flagship ? (
        <>
          <div className="space-y-2">
            <p className="font-serif text-2xl font-medium text-ink">{flagship.title}</p>
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
              <PillarTag pillarId={flagship.pillar} pillars={pillars} />
              <span>{formatName(flagship.format)}</span>
              {flagship.optional ? <span>Optional this week</span> : null}
            </p>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              {article ? (
                <>
                  <StatusBadge status={ARTICLE_STATUS[article.status]} />
                  <Link
                    to={articleHref(article.id)}
                    className="inline-flex items-center gap-1 text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
                  >
                    Open in Studio
                    <ExternalLink aria-hidden="true" className="size-3.5" />
                  </Link>
                </>
              ) : (
                <p className="text-ink-muted">
                  {plan.status === 'approved'
                    ? 'The article is being set up.'
                    : 'The article is created when you approve the plan.'}
                </p>
              )}
            </div>
          </div>
          <ol aria-label="The flagship’s week" className="border-t border-line">
            {milestones.map((milestone) => {
              const state = milestoneState(milestone, status, nowMs);
              const { icon: Icon, tone, words } = STEP_ICON[state];
              return (
                <li key={milestone.id} className="flex items-start gap-3 border-b border-line py-2.5">
                  <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
                  <span className="min-w-0 flex-1">
                    <span className={state === 'upcoming' || state === 'passed' ? 'text-ink-muted' : 'text-ink'}>
                      {milestone.label}
                    </span>
                    <span className="sr-only">{`: ${words}`}</span>
                  </span>
                  <time
                    dateTime={new Date(milestone.utcMs).toISOString()}
                    className="shrink-0 text-sm tabular-nums text-ink-muted"
                  >
                    {`${momentLabel(milestone.utcMs, zone)} ${zoneAbbreviation(milestone.utcMs, zone)}`}
                  </time>
                </li>
              );
            })}
          </ol>
        </>
      ) : (
        <div className="space-y-3 text-ink-muted">
          <p>
            {plan.mode === 'away'
              ? 'Away weeks have no flagship article.'
              : plan.mode === 'light'
                ? 'No flagship this week. Light weeks can do without one.'
                : 'No flagship this week. A normal week has one.'}
          </p>
          {editable && plan.mode !== 'away' ? (
            <Button
              variant="secondary"
              onClick={() => {
                setEditing(true);
              }}
            >
              <Plus aria-hidden="true" />
              Add a flagship
            </Button>
          ) : null}
        </div>
      )}

      <ResponsiveDialog
        open={editing}
        onOpenChange={setEditing}
        title={flagship ? 'Edit the flagship' : 'Add a flagship'}
        description="The article researched this week and published on the next article day."
      >
        {editing ? (
          <FlagshipForm
            flagship={flagship}
            pillars={pillars}
            formats={formats}
            approved={plan.status === 'approved'}
            started={article !== undefined && article.status !== 'planned'}
            light={plan.mode === 'light'}
            saving={saving}
            onCancel={() => {
              setEditing(false);
            }}
            onSave={(value) => {
              setEditing(false);
              onSave(value, flagship ? 'Flagship saved.' : 'Flagship added.');
            }}
          />
        ) : null}
      </ResponsiveDialog>

      <AlertDialog open={removing} onOpenChange={setRemoving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove the flagship?</AlertDialogTitle>
            <AlertDialogDescription>
              {plan.status === 'approved'
                ? 'The plan goes back to you for approval. An article that is still only planned is archived; one already in progress stays.'
                : 'The week keeps its posts. You can add a flagship again before you approve.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              onClick={() => {
                onSave(null, 'Flagship removed.');
              }}
            >
              Remove flagship
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

interface FlagshipFormProps {
  flagship: WeekPlan['flagship'] | undefined;
  pillars: readonly PillarView[];
  formats: readonly FormatOption[];
  approved: boolean;
  started: boolean;
  light: boolean;
  saving: boolean;
  onCancel: () => void;
  onSave: (flagship: FlagshipInput) => void;
}

function FlagshipForm({ flagship, pillars, formats, approved, started, light, saving, onCancel, onSave }: FlagshipFormProps) {
  const [title, setTitle] = useState(flagship?.title ?? '');
  const [pillar, setPillar] = useState(flagship?.pillar ?? pillars[0]?.id ?? '');
  const [format, setFormat] = useState(flagship?.format ?? formats[0]?.id ?? '');
  const [optional, setOptional] = useState(flagship?.optional ?? light);
  const [submitted, setSubmitted] = useState(false);
  const trimmed = title.trim();
  const errors = {
    title:
      trimmed === '' ? 'Give the article a working title.' : trimmed.length > 150 ? 'Keep it under 150 characters.' : undefined,
    pillar: pillar === '' ? 'Pick a pillar.' : undefined,
    format: format === '' ? 'Pick a format.' : undefined,
  };
  const shown: Partial<typeof errors> = submitted ? errors : {};

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitted(true);
    if (Object.values(errors).some(Boolean)) {
      const form = event.currentTarget;
      setTimeout(() => focusFirstInvalid(form), 0);
      return;
    }
    onSave({ title: trimmed, pillar, format, ideaId: flagship?.ideaId ?? null, optional });
  };

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <Field label="Working title" error={shown.title} required>
        <Input value={title} maxLength={150} onChange={(event) => { setTitle(event.target.value); }} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pillar" error={shown.pillar} required>
          {(control) => (
            <Select value={pillar} onValueChange={setPillar}>
              <SelectTrigger {...control}>
                <SelectValue placeholder="Choose a pillar" />
              </SelectTrigger>
              <SelectContent>
                {pillars.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Format" error={shown.format} required>
          {(control) => (
            <Select value={format} onValueChange={setFormat}>
              <SelectTrigger {...control}>
                <SelectValue placeholder="Choose a format" />
              </SelectTrigger>
              <SelectContent>
                {formats.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <Field
        label="Optional this week"
        description="Skip it without a warning if the week gets busy (light weeks)."
        orientation="horizontal"
      >
        <Checkbox checked={optional} onCheckedChange={(value) => { setOptional(value === true); }} />
      </Field>
      {approved ? (
        <Alert variant="info">
          <AlertDescription>
            <p>
              {started
                ? 'This plan is approved and the article is already under way: saving changes the plan, not the article.'
                : 'This plan is approved. Changing the flagship sends it back to you for approval.'}
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      <DialogActions>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={saving}>
          {flagship ? 'Save flagship' : 'Add flagship'}
        </Button>
      </DialogActions>
    </form>
  );
}
