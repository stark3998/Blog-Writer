import { Play, Sparkles } from 'lucide-react';
import { useId, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';

import { JobPanel } from './components/job-panel';
import { LibrarySection, LoadError, LoadingBlock, Subsection } from './components/section-intro';
import { formatInteger, plural } from './lib/format';
import type { GuideView } from './lib/types';
import { DigestsPanel } from './voice/digests';
import { GuidePanel } from './voice/guide';
import { GUIDE_VIEW_KEY } from './voice/model';
import { SamplesPanel } from './voice/samples';

const BUILD_STEPS: Readonly<Record<string, string>> = {
  score: 'Score your samples for AI tells',
  classify: 'Check example passages for confidential details',
  synthesize: 'Write the guide',
};

const DIGEST_STEPS: Readonly<Record<string, string>> = {
  collect: 'Collect what you posted',
  synthesize: 'Compare the drafts with what you posted',
  save: 'Save the digest',
};

/** The LinkedIn-posts counter and the build button: the guide needs posts the owner wrote by hand. */
function CorpusStatus({ view }: { view: GuideView }) {
  const labelId = useId();
  const [jobId, setJobId] = useState<string | undefined>();
  const build = useApiMutation('post', '/api/voice/guide/build', {
    errorToast: 'Couldn’t start building the guide.',
    handles: ['not_enough_samples', 'blocklist_not_configured', 'ai_use_not_confirmed', 'ai_unavailable'],
  });
  const have = view.linkedinSamples;
  const need = view.required;
  const missing = Math.max(0, need - have);
  const start = (provisional: boolean) => {
    build.mutate(provisional ? { body: { provisional: true } } : undefined, {
      onSuccess: (accepted) => {
        setJobId(accepted.jobId);
      },
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 rounded-lg border border-line p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:p-5">
        <div className="space-y-2">
          <p id={labelId} className="text-ink">
            <span className="font-semibold">{`${formatInteger(have)} of ${formatInteger(need)}`}</span>
            {' LinkedIn posts you wrote by hand'}
          </p>
          <Progress
            value={Math.min(have, need)}
            max={Math.max(need, 1)}
            aria-labelledby={labelId}
            getValueLabel={() => `${String(have)} of ${String(need)}`}
            tone={missing === 0 ? 'success' : 'ink'}
            className="max-w-sm"
          />
          <p className="text-sm text-ink-muted">
            {missing === 0
              ? 'Enough to build your guide. More posts make it sharper.'
              : `Add ${plural(missing, 'more post', 'more posts')} to build your guide: drafts can’t start without one.`}
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:items-end">
          <Button
            variant={view.active ? 'secondary' : 'primary'}
            disabled={!view.canBuild || build.isPending}
            onClick={() => {
              start(false);
            }}
          >
            <Sparkles aria-hidden="true" />
            {build.isPending ? 'Starting…' : view.active ? 'Build a new version' : 'Build your guide'}
          </Button>
          {view.canBuild ? null : (
            <Button
              variant="secondary"
              disabled={build.isPending}
              onClick={() => {
                start(true);
              }}
            >
              Build a provisional guide now
            </Button>
          )}
        </div>
      </div>
      {view.canBuild ? null : (
        <p className="text-sm text-ink-muted">
          A provisional guide uses the samples you have (your portfolio and articles) without the
          LinkedIn minimum. It is labelled provisional until you add your posts and build again.
        </p>
      )}
      {build.error ? <ProblemAlert error={build.error} title="The guide can’t be built yet." /> : null}
      {jobId ? (
        <JobPanel
          jobId={jobId}
          title="Building your guide"
          labels={BUILD_STEPS}
          done="A new version is waiting for your approval below."
          onDismiss={() => {
            setJobId(undefined);
          }}
        />
      ) : null}
    </div>
  );
}

/** The digest block: "Run the digest now", its progress, and the digests. */
function DigestBlock() {
  const [jobId, setJobId] = useState<string | undefined>();
  const run = useApiMutation('post', '/api/voice/digests/run', {
    errorToast: 'Couldn’t start the digest.',
    handles: ['blocklist_not_configured', 'ai_use_not_confirmed', 'ai_unavailable'],
  });
  return (
    <Subsection
      title="Digest"
      description="Weekly for the first three months, then monthly: how your posted text differed from the AI drafts, and suggested changes to the guide. Nothing changes until you accept."
      actions={
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
          <Play aria-hidden="true" />
          {run.isPending ? 'Starting…' : 'Run the digest now'}
        </Button>
      }
    >
      <div className="space-y-6">
        {run.error ? <ProblemAlert error={run.error} title="The digest can’t run yet." /> : null}
        {jobId ? (
          <JobPanel
            jobId={jobId}
            title="Running the digest"
            labels={DIGEST_STEPS}
            done="The new digest is below."
            onDismiss={() => {
              setJobId(undefined);
            }}
          />
        ) : null}
        <DigestsPanel />
      </div>
    </Subsection>
  );
}

/**
 * Library > Voice guide (`/library/voice`): the samples the guide is built from (with their AI-tell
 * flags), the LinkedIn-posts counter, the versioned guide with each change shown for approval,
 * and the digests of drafts against what you posted. Calls `/api/voice/samples*`,
 * `/api/voice/guide*`, `/api/voice/digests*` (contract section 10).
 */
export function VoiceSection() {
  const view = useApiQuery({ queryKey: GUIDE_VIEW_KEY, path: '/api/voice/guide' });

  return (
    <LibrarySection
      title="Voice guide"
      standfirst={
        <p>
          The guide is built from your own writing, and your unassisted words weigh most. Passages that read as AI-written inform topics only
          and are never used as examples.
        </p>
      }
      yourPart="Provide 5 to 10 LinkedIn posts you wrote yourself, and approve each change to the guide."
    >

      {view.isPending ? (
        <LoadingBlock label="Loading your voice guide" rows={3} />
      ) : view.isError ? (
        <LoadError error={view.error} title="Couldn’t load the voice guide." retryLabel="Try loading the voice guide again" onRetry={() => void view.refetch()} />
      ) : (
        <>
          <CorpusStatus view={view.data} />
          <Subsection title="The guide" description="What every draft follows: your tone and habits, the rules, and examples in your own words.">
            <GuidePanel view={view.data} />
          </Subsection>
        </>
      )}

      <Subsection
        title="Samples"
        description="Your portfolio writing, past LinkedIn posts and comments, interview answers and what you finally posted. Flagged passages are highlighted."
      >
        <SamplesPanel />
      </Subsection>

      <DigestBlock />
    </LibrarySection>
  );
}
