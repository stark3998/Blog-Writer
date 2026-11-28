import { ShieldCheck, Sparkles } from 'lucide-react';
import { useId, useState, type SubmitEvent } from 'react';
import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useJobEvents } from '@/features/jobs/use-job';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';

import { JobPanel } from '../components/job-panel';
import { serverFieldErrors } from '../lib/errors';
import { formatInteger, plural } from '../lib/format';
import { charCount } from '../lib/spans';
import { SEED_MAX_CHARS } from './model';

const linkClass = 'text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink';

const SEED_STEPS: Readonly<Record<string, string>> = {
  extract: 'Find stories and positions in the text',
  classify: 'Check each one for confidential details',
  save: 'Add them to your review queue',
};

/** What the seed job reported when it saved (`stories.seeded {count, existing}`). */
function SeedResult({ jobId }: { jobId: string }) {
  const events = useJobEvents(jobId);
  const seeded = events.find((event) => event.type === 'stories.seeded');
  if (!seeded) return <p>The proposed entries are in your review queue above.</p>;
  const count = typeof seeded.data.count === 'number' ? seeded.data.count : 0;
  const existing = typeof seeded.data.existing === 'number' ? seeded.data.existing : 0;
  return (
    <p>
      {count === 0
        ? 'Nothing new was found in this text.'
        : `${plural(count, 'entry is', 'entries are')} waiting for your review above.`}
      {existing > 0 ? ` ${plural(existing, 'was', 'were')} proposed before.` : ''}
    </p>
  );
}

/**
 * Proposes stories and positions from the owner's resume or portfolio text: public material,
 * read by a model, each proposal checked by the classifier and waiting in the review queue.
 */
export function SeedPanel() {
  const formId = useId();
  const [source, setSource] = useState<'cv' | 'portfolio'>('cv');
  const [text, setText] = useState('');
  const [jobId, setJobId] = useState<string | undefined>();
  const seed = useApiMutation('post', '/api/stories/seed', {
    gcTime: 0,
    errorToast: 'Couldn’t start proposing entries.',
    handles: ['ai_use_not_confirmed', 'confidential_text', 'blocklist_not_configured', 'validation_failed', 'too_large'],
  });
  const count = charCount(text);
  const errors = serverFieldErrors(seed.error);
  const textError =
    count > SEED_MAX_CHARS
      ? `At most ${formatInteger(SEED_MAX_CHARS)} characters. Paste the relevant part.`
      : (errors.text ?? (isProblem(seed.error, 'too_large') ? problemMessage(seed.error) : undefined));

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (text.trim() === '' || count > SEED_MAX_CHARS) return;
    seed.mutate(
      { body: { text, source } },
      {
        onSuccess: (accepted) => {
          setJobId(accepted.jobId);
          setText('');
        },
      },
    );
  };

  return (
    <div className="space-y-5">
      <form id={formId} noValidate onSubmit={submit} className="space-y-5">
        <Fieldset legend="Where the text comes from">
          {(group) => (
            <SegmentedControl
              {...group}
              value={source}
              onValueChange={(value) => {
                setSource(value === 'portfolio' ? 'portfolio' : 'cv');
              }}
            >
              <SegmentedControlItem value="cv">Resume</SegmentedControlItem>
              <SegmentedControlItem value="portfolio">Portfolio</SegmentedControlItem>
            </SegmentedControl>
          )}
        </Fieldset>
        <Field
          label={source === 'cv' ? 'Resume text' : 'Portfolio text'}
          description={`Paste public material only: it is read by a model. ${formatInteger(count)} of ${formatInteger(SEED_MAX_CHARS)} characters.`}
          error={textError}
        >
          <Textarea
            rows={8}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
            }}
          />
        </Field>
        <p className="flex items-start gap-2 text-sm text-ink-muted">
          <ShieldCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          Facts are proposed, not your voice. Nothing is used until you approve it.
        </p>
        {isProblem(seed.error, 'ai_use_not_confirmed') ? (
          <Alert variant="error">
            <AlertTitle>Confirm your employer’s AI-use policy first.</AlertTitle>
            <AlertDescription>
              <p>
                Do it in{' '}
                <Link to="/settings/policy" className={linkClass}>
                  Blocklist & policy
                </Link>
                .
              </p>
            </AlertDescription>
          </Alert>
        ) : null}
        {isProblem(seed.error, ['confidential_text', 'blocklist_not_configured']) ? (
          <ProblemAlert error={seed.error} title="The text can’t be sent as it is." />
        ) : null}
        <Button type="submit" disabled={seed.isPending || text.trim() === '' || count > SEED_MAX_CHARS}>
          <Sparkles aria-hidden="true" />
          {seed.isPending ? 'Starting…' : 'Propose entries'}
        </Button>
      </form>
      {jobId ? (
        <JobPanel
          jobId={jobId}
          title="Proposing entries"
          labels={SEED_STEPS}
          done={<SeedResult jobId={jobId} />}
          onDismiss={() => {
            setJobId(undefined);
          }}
        />
      ) : null}
    </div>
  );
}

/**
 * Runs the confidentiality classifier over every entry it has not judged (or whose text changed
 * since): entries saved while the AI was off, or flagged as "could not judge".
 */
export function ClassifyAction() {
  const [jobId, setJobId] = useState<string | undefined>();
  const classify = useApiMutation('post', '/api/stories/classify', {
    errorToast: 'Couldn’t start the check.',
    handles: ['ai_use_not_confirmed', 'classifier_unavailable', 'ai_unavailable'],
  });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="secondary"
          disabled={classify.isPending}
          onClick={() => {
            classify.mutate(undefined, {
              onSuccess: (accepted) => {
                setJobId(accepted.jobId);
              },
            });
          }}
        >
          <ShieldCheck aria-hidden="true" />
          {classify.isPending ? 'Starting…' : 'Check unchecked entries'}
        </Button>
      </div>
      {classify.error ? <ProblemAlert error={classify.error} title="The check can’t run yet." /> : null}
      {jobId ? (
        <JobPanel
          jobId={jobId}
          title="Checking entries"
          labels={{ classify: 'Check entries for confidential details' }}
          done="Every entry has a verdict now."
          onDismiss={() => {
            setJobId(undefined);
          }}
        />
      ) : null}
    </div>
  );
}
