import { Archive, CircleAlert, EllipsisVertical, FileText, ListChecks, Repeat2, Save, Sparkles, Undo2 } from 'lucide-react';
import { useId, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useJobStatus, useLiveEvents } from '@/features/jobs/use-job';
import { ApiError } from '@/lib/api/errors';
import { fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import { toastSuccess } from '@/lib/api/toast';
import { isLiveJobStatus } from '@/lib/events/events';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { canRepurpose, usePillars, usePost, useRepurpose } from '@/features/studio/piece';

import { CarouselBuilder, focusSlide } from '../carousel/carousel-builder';
import { feedAuthor, feedMedia } from '../components/feed-parts';
import { PostStatusBadge } from '../components/status-badge';
import { postErrorSentence, reviewOnlyReason } from '../model/labels';
import { canMove } from '../model/machines';
import { readSpec, slideTexts, specErrors, specJson, type CarouselSpec, type SpecError } from '../model/slides';
import { addHashtag, hashtagsInText, removeHashtag, replaceHook, utf16Offset } from '../model/text';
import {
  goLivePath,
  hasSlides,
  isApproved,
  isEditable,
  mediaWithRole,
  truncatedCount,
  type Finding,
  type Post,
  type Versioned,
} from '../model/types';
import { useBrand, useCadence, useLiveLint, useMachines } from '../queries';

import { ApprovalPanel } from './approval-panel';
import { ChecksPanel } from './checks-panel';
import { HookPicker } from './hook-picker';
import { LeaveGuard } from './leave-guard';
import { LintPanel } from './lint-panel';
import { PostChips } from './post-chips';
import { PreviewPanel } from './preview-panel';
import { RewriteDialog, type RewriteWhat } from './rewrite-dialog';
import { TextEditor, type LiveMeasure } from './text-editor';
import { editBody, MAX_HASHTAGS, usePostDraft } from './use-post-draft';

/** What a read-only post says under its text: why, and where to go instead. */
function readOnlyReason(post: Post): string {
  switch (post.status) {
    case 'generating':
      return 'The writer is working on it. It opens for editing when it is done.';
    case 'publishing':
      return 'It is being published right now.';
    case 'posted':
      return 'It is live on LinkedIn. Edit the live post on the go-live page.';
    case 'kit':
      return 'It is in the ready-to-post kit. Switch it back to one-click on the go-live page to edit it.';
    case 'needs_attention':
      return 'A publish attempt needs your check on the go-live page first.';
    case 'missed':
      return 'Its slot passed. Pick a new one on the go-live page, then edit it.';
    case 'deleted':
      return 'It was deleted from LinkedIn.';
    case 'archived':
      return 'It is archived.';
    case 'draft':
    case 'approved':
    case 'staged':
    case 'expired':
      return '';
  }
}

/** The codes the save shows in place (fields, slides, notices) instead of a toast. */
const SAVE_HANDLES = [
  'etag_mismatch',
  'validation_failed',
  'invalid_text',
  'hashtag_not_approved',
  'unknown_hook',
  'unknown_pillar',
  'invalid_post',
  'invalid_spec',
  'read_only_state',
  'not_a_carousel',
  'format_disabled',
  'unsupported_format',
] as const;

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="grid min-w-0 gap-4 lg:col-start-1">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function ComposerSkeleton() {
  return (
    <div className="grid gap-6">
      <p role="status" className="sr-only">
        Loading the post
      </p>
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-24 w-full max-w-measure" />
      <Skeleton className="h-56 w-full max-w-measure" />
    </div>
  );
}

/** The LinkedIn composer for one post: loads it, then edits it. */
export function PostComposer({ postId }: { postId: string }) {
  const post = usePost(postId);
  if (post.isPending) return <ComposerSkeleton />;
  if (post.isError) {
    return (
      <ProblemAlert
        error={post.error}
        title="Couldn’t load the post."
        onRetry={() => void post.refetch()}
        retryLabel="Try loading the post again"
      />
    );
  }
  return (
    <ComposerBody
      key={post.data.item.id}
      versioned={post.data}
      onReload={() => {
        void post.refetch();
      }}
    />
  );
}

function ComposerBody({ versioned, onReload }: { versioned: Versioned<Post>; onReload: () => void }) {
  const post = versioned.item;
  const postId = post.id;
  const detail = queryKeys.posts.detail(postId);
  const draft = usePostDraft(versioned);
  const machines = useMachines();
  const pillars = usePillars();
  const brand = useBrand();
  const cadence = useCadence();
  const editable = isEditable(post);
  const lint = useLiveLint(postId, draft.text.displayText, { enabled: editable });
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const conflict = useConflict();
  const [conflictPart, setConflictPart] = useState<'text' | 'slides'>('text');
  const [saveError, setSaveError] = useState<unknown>(null);
  const [failing, setFailing] = useState<readonly string[]>([]);
  const [checkJob, setCheckJob] = useState<string | null>(null);
  const [renderJob, setRenderJob] = useState<string | null>(null);
  const [rewriteJob, setRewriteJob] = useState<string | null>(null);
  const [rewrite, setRewrite] = useState<RewriteWhat | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);

  const checkStatus = useJobStatus(checkJob ?? '');
  const renderStatus = useJobStatus(renderJob ?? '');
  const checkRunning = checkJob !== null && isLiveJobStatus(checkStatus);
  const renderRunning = renderJob !== null && isLiveJobStatus(renderStatus);
  // While the writer works on a post we did not start here, keep the event stream open so its
  // `entity.changed` refreshes the post.
  useLiveEvents(post.status === 'generating');

  const pillarList = pillars.data?.item.pillars ?? [];
  const pillar = pillarList.find((candidate) => candidate.id === draft.text.pillar);
  const approvedHashtags = pillar?.hashtags ?? (draft.text.pillar === post.pillar ? (post.hashtags ?? []) : []);
  const hashtags = hashtagsInText(draft.text.displayText, approvedHashtags);

  // ---- writes ----------------------------------------------------------------------------
  const savePost = useApiMutation('put', '/api/posts/{post_id}', {
    updates: () => detail,
    invalidates: [queryKeys.posts.lists()],
    handles: SAVE_HANDLES,
    errorToast: 'Couldn’t save the post.',
  });
  const saveSlides = useApiMutation('put', '/api/posts/{post_id}/carousel', {
    updates: () => detail,
    invalidates: [queryKeys.posts.lists()],
    handles: SAVE_HANDLES,
    errorToast: 'Couldn’t save the slides.',
  });
  const runChecks = useApiMutation('post', '/api/posts/{post_id}/check', {
    errorToast: 'Couldn’t start the checks.',
    onSuccess: (accepted) => {
      setCheckJob(accepted.jobId);
    },
  });
  const render = useApiMutation('post', '/api/posts/{post_id}/render', {
    handles: ['nothing_to_render'],
    errorToast: 'Couldn’t start the render.',
    onSuccess: (accepted) => {
      setRenderJob(accepted.jobId);
    },
  });
  const regenerate = useApiMutation('post', '/api/posts/{post_id}/regenerate', {
    invalidates: [detail, queryKeys.posts.lists()],
    // The dialog shows every refusal itself, next to the instruction.
    errorToast: false,
    onSuccess: (accepted) => {
      setRewriteJob(accepted.jobId);
      setRewrite(null);
      toastSuccess('The writer is on it.');
    },
  });
  // A post that went out (a derivative too: it lives under its article's header) can become an idea.
  const repurpose = useRepurpose();
  const repurposable = canRepurpose({ kind: 'post', status: post.status });
  const archive = useApiMutation('post', '/api/posts/{post_id}/archive', {
    updates: () => detail,
    invalidates: [queryKeys.posts.lists(), queryKeys.today],
    successToast: 'Post archived.',
    errorToast: 'Couldn’t archive the post.',
  });

  const saving = savePost.isPending || saveSlides.isPending;

  async function save(etagOverride?: string | null) {
    setSaveError(null);
    let etag = etagOverride === undefined ? draft.baseEtag : etagOverride;
    const params = { path: { post_id: postId } };
    try {
      if (draft.textDirty) {
        setConflictPart('text');
        const saved = await savePost.mutateAsync({
          params,
          body: editBody(draft.text, draft.serverText, approvedHashtags),
          ifMatch: etag,
        });
        etag = saved.etag;
        draft.textSaved(saved.etag);
      }
      if (draft.specDirty && draft.spec) {
        setConflictPart('slides');
        await saveSlides.mutateAsync({ params, body: { spec: specJson(draft.spec) }, ifMatch: etag });
      }
      draft.reset();
      conflict.close();
      toastSuccess(isApproved(post.status) ? 'Post saved. It is a draft again: approve it when ready.' : 'Post saved.');
    } catch (error) {
      if (conflict.show(error)) {
        onReload();
        return;
      }
      setSaveError(error);
    }
  }

  function startRender() {
    render.mutate({ params: { path: { post_id: postId } } });
  }
  function startChecks() {
    runChecks.mutate({ params: { path: { post_id: postId } } });
  }

  // ---- what the panels read --------------------------------------------------------------
  const liveFindings = editable && lint.data ? lint.data.findings : undefined;
  const findings: readonly Finding[] = liveFindings ?? post.lint?.findings ?? [];
  const truncated = liveFindings ? 0 : truncatedCount(post.lint);
  const blocking = findings.filter((finding) => finding.severity === 'block').length;
  const measure: LiveMeasure =
    lint.current && lint.data ? { payloadUnits: lint.data.payloadUnits, foldIndex: lint.data.foldIndex } : {};
  const slidesFormat = hasSlides(draft.text.format);
  const rendered =
    draft.text.format === 'image_card' ? mediaWithRole(post, 'image_card') : mediaWithRole(post, 'carousel_pdf');
  const specErrorList: SpecError[] = isProblem(saveError, 'invalid_spec')
    ? specErrors(saveError instanceof ApiError ? saveError.problem?.extensions?.errors : undefined)
    : [];
  const textError = fieldErrors(saveError).displayText;

  const blockedBy: string[] = [];
  if (draft.dirty) blockedBy.push('Save your changes first.');
  if (lint.isFetching && !lint.current) blockedBy.push('Wait for the lint to finish.');
  else if (blocking > 0) blockedBy.push('Fix the problems that block approval.');
  if (slidesFormat && !rendered && !draft.dirty) blockedBy.push('Render the slides first.');

  const author = feedAuthor(brand.data?.item);
  const media = feedMedia({ format: draft.text.format, media: post.media, documentTitle: draft.spec?.title ?? post.documentTitle });

  function showInText(finding: Finding) {
    const element = textareaRef.current;
    if (!element) return;
    const text = draft.text.displayText;
    const start = utf16Offset(text, finding.start ?? 0);
    const end = utf16Offset(text, finding.end ?? finding.start ?? 0);
    element.focus();
    element.setSelectionRange(start, Math.max(start, end));
  }

  // A rewrite first returns an approved post to Draft, then Draft -> Writing.
  const canRewrite = editable && canMove(machines, 'post', 'draft', 'generating');
  const openRewrite = (what: RewriteWhat) => {
    regenerate.reset();
    setRewrite(what);
  };

  return (
    <div className="grid gap-10" data-slot="post-composer">
      <LeaveGuard dirty={draft.dirty} />

      <div className="grid gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <PostStatusBadge status={post.status} />
            <PostChips
              lane={post.lane}
              pillar={draft.text.pillar}
              format={draft.text.format}
              pillars={pillarList}
              disabled={!editable}
              onPillar={(next) => {
                const nextTags = pillarList.find((candidate) => candidate.id === next)?.hashtags ?? [];
                draft.editText((current) => {
                  let text = current.displayText;
                  for (const tag of hashtagsInText(text, approvedHashtags)) {
                    if (!nextTags.some((candidate) => candidate.toLowerCase() === tag.toLowerCase())) text = removeHashtag(text, tag);
                  }
                  return { ...current, pillar: next, displayText: text };
                });
              }}
              onFormat={(format) => {
                draft.editText((current) => ({ ...current, format }));
              }}
            />
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More actions for this post">
                <EllipsisVertical aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                disabled={!canRewrite || draft.dirty}
                onSelect={() => {
                  openRewrite('text');
                }}
              >
                <Sparkles aria-hidden="true" />
                Rewrite with the writer…
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={checkRunning || post.status === 'generating'}
                onSelect={startChecks}
              >
                <ListChecks aria-hidden="true" />
                Run the checks
              </DropdownMenuItem>
              {repurposable ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    disabled={repurpose.pending}
                    onSelect={() => {
                      repurpose.repurpose(post.id, 'recut');
                    }}
                  >
                    <Repeat2 aria-hidden="true" />
                    Repurpose as a new post
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    disabled={repurpose.pending}
                    onSelect={() => {
                      repurpose.repurpose(post.id, 'flagship');
                    }}
                  >
                    <FileText aria-hidden="true" />
                    Turn it into an article idea
                  </DropdownMenuItem>
                </>
              ) : null}
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="danger"
                disabled={!canMove(machines, 'post', post.status, 'archived')}
                onSelect={() => {
                  setArchiveOpen(true);
                }}
              >
                <Archive aria-hidden="true" />
                Archive the post…
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {post.status === 'generating' ? (
          <Alert role="status">
            <AlertTitle>The writer is working on this post.</AlertTitle>
            <AlertDescription>
              <p>It opens for editing when it is done; this page updates by itself.</p>
            </AlertDescription>
          </Alert>
        ) : null}
        {post.reviewOnly ? (
          <Alert variant="warn">
            <AlertTitle>Read this one closely before you approve it.</AlertTitle>
            <AlertDescription>
              <ul className="grid gap-1">
                {(post.reviewOnlyReasons?.length ? post.reviewOnlyReasons : ['review']).map((reason) => (
                  <li key={reason}>{reviewOnlyReason(reason)}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
        {post.lastError && post.status !== 'generating' && post.status !== 'posted' ? (
          <Alert variant="warn">
            <AlertTitle>{post.status === 'needs_attention' ? 'This post needs you.' : 'The last attempt hit a problem.'}</AlertTitle>
            <AlertDescription>
              <p>{postErrorSentence(post.lastError)}</p>
            </AlertDescription>
            {isApproved(post.status) || post.status === 'needs_attention' ? (
              <AlertActions>
                <Button asChild size="sm" variant="secondary">
                  <Link to={goLivePath(post)}>Open go-live</Link>
                </Button>
              </AlertActions>
            ) : null}
          </Alert>
        ) : null}
        {draft.serverMoved ? (
          <Alert variant="warn">
            <AlertDescription>
              <p>This post changed on the server while you were editing. Saving asks which version to keep.</p>
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,23rem)] lg:gap-x-12">
        <Section title="Hook and text">
          {rewriteJob ? (
            <JobTimeline jobId={rewriteJob} title="Rewriting" labels={{ generate: 'Write', check: 'Check the result' }} />
          ) : null}
          {post.hookVariants && post.hookVariants.length > 0 ? (
            <HookPicker
              variants={post.hookVariants}
              selectedId={draft.text.selectedHookId}
              text={draft.text.displayText}
              disabled={!editable}
              onPick={(variant) => {
                draft.editText((current) => ({
                  ...current,
                  selectedHookId: variant.id,
                  displayText: replaceHook(current.displayText, variant.text),
                }));
              }}
              onRewrite={canRewrite && !draft.dirty ? () => {
                openRewrite('hooks');
              } : undefined}
            />
          ) : null}
          <TextEditor
            value={draft.text.displayText}
            onChange={(displayText) => {
              draft.editText((current) => ({ ...current, displayText }));
            }}
            readOnly={!editable}
            readOnlyReason={readOnlyReason(post)}
            measure={measure}
            approvedHashtags={approvedHashtags}
            hashtags={hashtags}
            onToggleHashtag={(tag, on) => {
              draft.editText((current) => ({
                ...current,
                displayText:
                  on && hashtagsInText(current.displayText, approvedHashtags).length < MAX_HASHTAGS
                    ? addHashtag(current.displayText, tag)
                    : removeHashtag(current.displayText, tag),
              }));
            }}
            textareaRef={textareaRef}
            error={textError}
          />
        </Section>

        <aside
          aria-label="Preview"
          className="grid min-w-0 gap-4 self-start lg:sticky lg:top-6 lg:col-start-2 lg:row-span-4 lg:row-start-1"
        >
          <h2 className="font-serif text-2xl font-medium text-ink">Preview</h2>
          <PreviewPanel
            text={draft.text.displayText}
            foldIndex={measure.foldIndex}
            hashtags={hashtags}
            author={author}
            media={media}
          />
        </aside>

        {slidesFormat ? (
          <Section title={draft.text.format === 'image_card' ? 'Image card' : 'Slides'}>
            {draft.textDirty && draft.text.format !== post.format ? (
              <p className="text-sm text-ink-muted">Save the post to switch its format; then the slides open here.</p>
            ) : (
              <CarouselBuilder
                post={post}
                etag={versioned.etag}
                spec={draft.spec}
                onChange={(update) => {
                  draft.editSpec(update);
                }}
                dirty={draft.specDirty}
                readOnly={!editable}
                errors={specErrorList}
                findings={findings}
                hook={draft.text.displayText.split('\n')[0] ?? ''}
                onRender={startRender}
                renderJobId={renderJob}
                renderBusy={render.isPending || renderRunning}
                renderError={
                  render.error ? <ProblemAlert error={render.error} title="Couldn’t start the render." /> : null
                }
                onAskWriter={canRewrite && !draft.dirty ? () => {
                  openRewrite('carousel');
                } : undefined}
              />
            )}
          </Section>
        ) : null}

        <Section title="Checks">
          <LintPanel
            findings={findings}
            truncated={truncated}
            checking={editable && (!lint.current || lint.isFetching)}
            error={editable ? lint.error : undefined}
            onRetry={() => void lint.refetch()}
            onShowInText={showInText}
            onShowSlide={(index) => {
              focusSlide(postId, index);
            }}
          />
          <ChecksPanel
            post={post}
            failing={failing}
            running={runChecks.isPending || checkRunning}
            onRun={post.status === 'generating' ? undefined : startChecks}
            disabledReason={draft.dirty ? 'Save first: the checks run on the saved post.' : undefined}
          />
          {checkJob ? <JobTimeline jobId={checkJob} title="Running the checks" labels={{ check: 'Lint, claims and near-duplicates' }} /> : null}
        </Section>

        <Section title="Approval">
          <ApprovalPanel
            post={post}
            etag={versioned.etag}
            machines={machines}
            timeZone={post.slot?.timezone ?? cadence.data?.item.timezone}
            blockedBy={blockedBy}
            onFailingChecks={setFailing}
            onRunChecks={startChecks}
            onRender={slidesFormat ? startRender : undefined}
            onReload={onReload}
          />
        </Section>
      </div>

      {draft.dirty || saveError ? (
        <div
          role="region"
          aria-label="Unsaved changes"
          className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 -mx-5 grid gap-3 border-t border-line bg-sheet px-5 py-3 sm:-mx-8 sm:px-8 md:bottom-0 lg:-mx-12 lg:px-12"
        >
          {saveError && !isProblem(saveError, 'etag_mismatch') ? (
            <p role="alert" className="flex items-start gap-2 text-sm font-medium text-danger">
              <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {isProblem(saveError, 'invalid_spec')
                ? 'Some slides need a fix: see the notes on each slide.'
                : problemMessage(saveError)}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-ink-muted">
              {draft.dirty
                ? isApproved(post.status)
                  ? 'Unsaved changes. Saving moves the post back to Draft.'
                  : 'Unsaved changes.'
                : 'Saved.'}
            </p>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                disabled={saving || !draft.dirty}
                onClick={() => {
                  draft.reset();
                  setSaveError(null);
                }}
              >
                <Undo2 aria-hidden="true" />
                Discard
              </Button>
              <Button disabled={saving || !draft.dirty || !editable} onClick={() => void save()}>
                <Save aria-hidden="true" />
                {saving ? 'Saving…' : 'Save changes'}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      <ConflictDialog
        {...conflict}
        subject={conflictPart === 'text' ? 'This post' : 'These slides'}
        mine={conflictPart === 'text' ? draft.text.displayText : slidesText(draft.spec)}
        theirs={conflictPart === 'text' ? post.displayText : slidesText(readSpec(post.carouselSpec))}
        saving={saving}
        onKeepMine={() => {
          const current = conflict.conflict;
          if (current?.etag) {
            draft.rebase(current.etag);
            void save(current.etag);
          } else {
            onReload();
          }
        }}
        onTakeTheirs={() => {
          draft.reset();
          setSaveError(null);
          conflict.close();
          onReload();
        }}
      />

      {rewrite ? (
        <RewriteDialog
          open
          onOpenChange={(open) => {
            if (!open) setRewrite(null);
          }}
          initialWhat={rewrite}
          slides={slidesFormat}
          approved={isApproved(post.status)}
          pending={regenerate.isPending}
          error={regenerate.error}
          onSubmit={(what, instruction) => {
            regenerate.mutate({
              params: { path: { post_id: postId } },
              body: { what, ...(instruction ? { instruction } : {}) },
              ifMatch: versioned.etag,
            });
          }}
        />
      ) : null}

      <AlertDialog open={archiveOpen} onOpenChange={setArchiveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Archive this post?</AlertDialogTitle>
            <AlertDialogDescription>
              It leaves the plan and Today, and its slot is freed. Nothing changes on LinkedIn.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              onClick={() => {
                archive.mutate({ params: { path: { post_id: postId } }, ifMatch: versioned.etag });
              }}
            >
              Archive
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** Slides as plain lines, for the conflict dialog's comparison. */
function slidesText(spec: CarouselSpec | undefined): string {
  if (!spec) return '';
  return [spec.title, ...spec.slides.map((slide, index) => `Slide ${String(index + 1)}: ${slideTexts(slide).join(' / ')}`)].join(
    '\n\n',
  );
}
