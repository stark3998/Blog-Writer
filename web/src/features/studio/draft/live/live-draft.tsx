import { EditorContent } from '@tiptap/react';
import { PencilLine } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { useJobStepText, useJobSteps } from '@/features/jobs/use-job';
import type { StepText } from '@/lib/events/job-store';

import { useDraftEditor } from '../editor/use-draft-editor';
import { useDraftEnvironment } from '../environment';

/**
 * The draft as the pipeline writes it (contract section 9, `delta`): read-only, re-parsed in
 * the worker a few times a second while text streams in. A `draft.reset` clears the step's
 * text in the job store, and the view falls back to the stored article.
 */

/** The pipeline steps that stream text: drafting, the critique loop's revisions, owner revisions. */
const STREAMING_STEPS = ['draft', 'critique_revise', 'revise'] as const;
type StreamingStep = (typeof STREAMING_STEPS)[number];

const STEP_WORDS: Readonly<Record<StreamingStep, string>> = {
  draft: 'Writing the draft',
  critique_revise: 'Revising after the critique',
  revise: 'Revising as you asked',
};

export interface LiveText extends StepText {
  readonly step: StreamingStep;
}

/**
 * The text a running pipeline step is streaming for `jobId`, or `undefined` when none is: the
 * stored article is the better source then (a finished step's stream is older than what it wrote).
 */
export function useLiveText(jobId: string | null | undefined): LiveText | undefined {
  const id = jobId ?? '';
  const steps = useJobSteps(id);
  const draft = useJobStepText(id, 'draft');
  const critique = useJobStepText(id, 'critique_revise');
  const revise = useJobStepText(id, 'revise');
  if (!jobId) return undefined;
  const texts: Readonly<Record<StreamingStep, StepText | undefined>> = {
    draft,
    critique_revise: critique,
    revise,
  };
  const known = steps.length > 0;
  for (const step of [...STREAMING_STEPS].reverse()) {
    const text = texts[step];
    if (!text || text.text === '') continue;
    const running = steps.some((entry) => entry.name === step && entry.status === 'running');
    if (running || !known) return { step, ...text };
  }
  return undefined;
}

export function LiveDraft({ live }: { live: LiveText }) {
  const env = useDraftEnvironment();
  const { editor } = useDraftEditor({ label: 'The draft as it is written (read-only)', kind: 'live' });
  const [unreadable, setUnreadable] = useState(false);
  const pending = useRef<string | null>(null);
  const busy = useRef(false);
  const lastRun = useRef(0);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    pending.current = live.text;
    // Read through functions: both refs change while a parse is awaited.
    const queued = () => pending.current !== null;
    const idle = () => timer.current === undefined;
    const run = async () => {
      timer.current = undefined;
      const text = pending.current;
      if (text === null || busy.current) return;
      pending.current = null;
      busy.current = true;
      lastRun.current = Date.now();
      try {
        const parsed = await env.dialect.parse(text, { timeoutMs: env.timings.parseTimeoutMs });
        if (!editor.isDestroyed) {
          editor.commands.setContent(parsed.doc, { emitUpdate: false });
          setUnreadable(false);
        }
      } catch {
        setUnreadable(true);
      } finally {
        busy.current = false;
        if (queued() && idle()) {
          timer.current = window.setTimeout(() => void run(), env.timings.liveParseMs);
        }
      }
    };
    if (!busy.current && idle()) {
      const wait = Math.max(0, env.timings.liveParseMs - (Date.now() - lastRun.current));
      timer.current = window.setTimeout(() => void run(), wait);
    }
  }, [live.text, editor, env]);

  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
    },
    [],
  );

  return (
    <div>
      <p className="mb-6 flex items-center gap-2 font-sans text-sm font-medium text-ink">
        <PencilLine aria-hidden="true" className="size-4 animate-pulse-soft text-ink-subtle" />
        {`${STEP_WORDS[live.step]}…`}
        <span className="font-normal text-ink-muted">It appears here as it’s written and can’t be edited yet.</span>
      </p>
      {live.gap ? (
        <p className="mb-4 font-sans text-sm text-ink-muted">
          Part of the streamed text was missed. The full draft loads when this step finishes.
        </p>
      ) : null}
      {unreadable ? (
        <div className="whitespace-pre-wrap font-serif text-lg leading-prose text-ink md:text-xl">{live.text}</div>
      ) : null}
      <div className={unreadable ? 'hidden' : undefined}>
        <EditorContent editor={editor} />
      </div>
    </div>
  );
}
