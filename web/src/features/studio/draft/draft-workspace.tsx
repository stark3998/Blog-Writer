import { EditorContent } from '@tiptap/react';
import { Copy, Eye, FileDown, PencilLine, Play, RotateCw } from 'lucide-react';
import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { EmptyState } from '@/components/shell/empty-state';
import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ConflictDialog } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { ContentLossWarning } from '@/features/editor';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useLiveEvents } from '@/features/jobs/use-job';
import { missingFields, problemMessage } from '@/lib/api/problems';
import { toastError, toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { cn } from '@/lib/cn';
import { useMachineViews } from '@/lib/query/machines';
import { useFormatPacks, usePillars } from '@/features/studio/piece';

import { fetchMarkdownExport, saveTextFile } from './api/export';
import { useIllustrationCommands, useIllustrationRun, type EditorHold, type RunEnded } from './api/illustrations';
import { useBrief, useGatesView } from './api/queries';
import { CitationPicker } from './editor/citation-picker';
import type { BlockCommandContext } from './editor/commands';
import { revealRange } from './editor/finding-marks';
import { captureSelection, insertCitation, type CapturedSelection } from './editor/fragments';
import { ImageDialog } from './editor/image-dialog';
import { DraftNodeContext } from './editor/node-views';
import { SELECTION_MAX_CHARS, SelectionAi } from './editor/selection-ai';
import { SlashMenu } from './editor/slash-menu';
import { EditorToolbar } from './editor/toolbar';
import { useDraftEditor } from './editor/use-draft-editor';
import { useDraftEnvironment } from './environment';
import { ImagesPanel } from './images/images-panel';
import { runEndSentence } from './images/run-progress';
import { LiveDraft, useLiveText } from './live/live-draft';
import { ManuscriptFacts, ManuscriptPillar, threadClass } from './manuscript';
import { droppedSentence, heroOf, largeImagesInBody } from './model/illustrations';
import { metaForSave, metaKey } from './model/meta';
import { locateSpan } from './model/spans';
import { articleActions, lastErrorMessage, readOnlyReason, writerOf } from './model/status';
import type { Finding, VersionedArticle } from './model/types';
import { DetailsPanel } from './panels/details-panel';
import { GatesPanel } from './panels/gates-panel';
import { LiveChecks } from './panels/live-checks';
import { OutlinePanel } from './panels/outline-panel';
import { useLiveLint } from './panels/use-live-lint';
import { PreviewDialog } from './preview/preview-dialog';
import { ApproveDialog } from './review/approve-dialog';
import { PipelineDialog } from './review/pipeline-dialog';
import { ReviewBar } from './review/review-bar';
import { ReviseDialog } from './review/revise-dialog';
import { ClearanceNotice, PublishStanding, VetoNotice } from './review/standing';
import { useArticleCommands } from './review/use-article-commands';
import { SaveStatusLine } from './session/save-status';
import { useDraftSession, type LoadProblem } from './session/use-draft-session';
import { VersionsSheet } from './versions/versions-sheet';

/** Labels for the pipeline's steps (`pipeline.article`, `pipeline.revise`; contract W-B1). */
const PIPELINE_STEP_LABELS: Readonly<Record<string, string>> = {
  research: 'Research',
  interview_questions: 'Interview questions',
  interview: 'Your interview answers',
  outline: 'Outline',
  draft: 'Draft',
  normalize: 'Tidy the Markdown',
  critique_revise: 'Critique and revise',
  gates: 'Gates',
  assets: 'Diagrams and images',
  finalize: 'Hand back for review',
  revise: 'Revise',
};

const CAPTURE_NOTES = {
  empty: 'Select the text you want rewritten first.',
  code: 'Select prose to rewrite: code and diagrams aren’t rewritten.',
  sources: 'Select text in the article body: the sources list isn’t rewritten.',
} as const;

type Dialog = 'approve' | 'revise' | 'pipeline' | 'versions' | 'citation';

/** An image action rewriting the body: the editor waits for the request, then for the version it saved. */
interface ImageHold {
  readonly until: number | null;
}

export interface DraftWorkspaceProps {
  articleId: string;
  data: VersionedArticle;
  refetch: () => Promise<{ data?: VersionedArticle | undefined }>;
}

export function DraftWorkspace({ articleId, data, refetch }: DraftWorkspaceProps) {
  const env = useDraftEnvironment();
  const { api } = useAppData();
  const article = data.item;
  const machines = useMachineViews();
  const writer = writerOf(article.status, machines.data);
  const reason = readOnlyReason(article.status, writer);
  const actions = articleActions(article, machines.data);
  const { editor, bus, slashKeys, setEditable } = useDraftEditor({ label: 'Article text', kind: 'manuscript' });
  const session = useDraftSession({ articleId, article, refetch, editor, env });
  const commands = useArticleCommands(articleId, session.flush);
  const gates = useGatesView(articleId);
  const brief = useBrief(article.briefId);
  const pillars = usePillars();
  const formats = useFormatPacks();
  const live = useLiveText(writer === 'pipeline' ? article.pipelineJobId : null);
  useLiveEvents(writer === 'pipeline' || writer === 'publish');

  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [preview, setPreview] = useState<{ readonly version?: number } | null>(null);
  const [image, setImage] = useState<{ readonly id: number; readonly file?: File } | null>(null);
  const [selection, setSelection] = useState<CapturedSelection | null>(null);
  const [margin, setMargin] = useState('checks');
  const [writing, setWriting] = useState(false);
  const [imageHold, setImageHold] = useState<ImageHold | null>(null);
  const statusRef = useRef<HTMLDivElement>(null);

  const editorHold = useMemo<EditorHold>(
    () => ({
      begin: () => {
        setImageHold({ until: null });
      },
      end: (version) => {
        setImageHold(version === null ? null : { until: version });
      },
    }),
    [],
  );
  const imageCommands = useIllustrationCommands(articleId, session.flush, editorHold);
  const imageRun = useIllustrationRun(articleId, article.illustrationJobId, (ended: RunEnded) => {
    if (ended.status === 'succeeded') {
      const dropped = droppedSentence(ended.outcome);
      toast.success('The illustrations are ready.', {
        description: `${dropped ? `${dropped} ` : ''}Use one as the hero or place it in the text, under Images.`,
        action:
          margin === 'images'
            ? undefined
            : {
                label: 'Show them',
                onClick: () => {
                  setMargin('images');
                },
              },
      });
      return;
    }
    // A failure's reason comes with the job's record, which may still be on its way: Images shows it.
    toast.error('No new illustrations.', {
      description: ended.error ? runEndSentence(ended.status, ended.error, ended.outcome) : 'Images shows what happened.',
    });
  });
  // Held until the editor shows the version the image action saved (or can't show any).
  const imageHeld =
    imageHold !== null &&
    (imageHold.until === null ||
      (session.loadProblem === null && (session.loadedVersion === null || session.loadedVersion < imageHold.until)));

  const editable =
    writer === 'owner' &&
    session.loadedVersion !== null &&
    session.loadProblem === null &&
    !session.conflict.open &&
    session.status.kind !== 'read-only' &&
    !imageHeld;

  useEffect(() => {
    setEditable(editable);
  }, [setEditable, editable]);

  useEffect(() => {
    editor.view.dom.setAttribute('aria-readonly', String(!editable));
  }, [editor, editable]);

  const metaIn = metaForSave(session.meta, session.metaErrors, article.meta);
  const lint = useLiveLint({
    editor,
    enabled: editable,
    meta: metaIn,
    metaKey: metaKey(metaIn),
    briefId: article.briefId,
    delayMs: env.timings.lintMs,
  });

  const openAi = () => {
    if (!editor.isEditable) return;
    const captured = captureSelection(editor);
    if (typeof captured === 'string') {
      toast(CAPTURE_NOTES[captured]);
      return;
    }
    if (captured.markdown.length > SELECTION_MAX_CHARS) {
      toast('Select less text: a rewrite takes up to 8,000 characters.');
      return;
    }
    if (!captured.inline) editor.commands.setTextSelection(captured.range);
    setSelection(captured);
  };
  const onAiShortcut = useEffectEvent(openAi);

  useEffect(() => {
    const offAi = bus.on('ai', () => {
      onAiShortcut();
    });
    const offImages = bus.on('images', (files) => {
      setImage({ id: Date.now(), file: files[0] });
    });
    const offProblem = bus.on('problem', (message) => {
      toast.error(message);
    });
    return () => {
      offAi();
      offImages();
      offProblem();
    };
  }, [bus]);

  const blockContext = useMemo<BlockCommandContext>(
    () => ({
      openImageDialog: () => {
        setImage({ id: Date.now() });
      },
      openCitationPicker: () => {
        setDialog('citation');
      },
    }),
    [],
  );

  const citations = brief.data?.item.citations;
  const nodeContext = useMemo(
    () => ({
      citations: new Map((citations ?? []).map((citation) => [citation.id, citation])),
      briefReady: brief.isSuccess,
    }),
    [citations, brief.isSuccess],
  );

  const showReportFinding = (finding: Finding) => {
    if (typeof finding.start !== 'number' || typeof finding.end !== 'number') return;
    const range = locateSpan(editor.state.doc, article.markdown, finding.start, finding.end);
    if (range) revealRange(editor, range);
  };

  const showLintFinding = (finding: Finding) => {
    if (typeof finding.start !== 'number' || typeof finding.end !== 'number' || lint.source === undefined) return;
    const range = locateSpan(editor.state.doc, lint.source, finding.start, finding.end);
    if (range) revealRange(editor, range);
  };

  const showBlocks = (warning: ContentLossWarning) => {
    const blocks = warning.blocks;
    if (!blocks) return;
    let from: number | undefined;
    let to: number | undefined;
    editor.state.doc.forEach((node, offset, index) => {
      if (index === blocks.from) from = offset + 1;
      if (index === blocks.to - 1) to = offset + node.nodeSize - 1;
    });
    if (from !== undefined) revealRange(editor, { from, to: Math.max(from, to ?? from) });
  };

  const openPreview = async (version?: number) => {
    if (version === undefined) await session.flush();
    setPreview(version === undefined ? {} : { version });
  };

  const exportMarkdown = async () => {
    await session.flush();
    try {
      const file = await fetchMarkdownExport(api, articleId, `${article.meta?.slug ?? articleId}.md`);
      saveTextFile(file.name, file.text);
      toastSuccess('Exported the Markdown file.');
    } catch (error) {
      const missing = missingFields(error);
      toastError(error, {
        title: missing.length > 0 ? `Couldn’t export: missing ${missing.join(', ')}.` : 'Couldn’t export the article.',
      });
    }
  };

  const copyMyText = async () => {
    try {
      await navigator.clipboard.writeText(session.currentMarkdown());
      toastSuccess('Copied your text.');
    } catch {
      toast.error('Couldn’t copy your text.', { description: 'Select it in the editor and copy it yourself.' });
    }
  };

  const pillarList = pillars.data?.item.pillars ?? [];
  const pack = (formats.data?.item.packs ?? []).find((candidate) => candidate.id === article.meta?.format);
  const lastError = article.status === 'needs_attention' ? lastErrorMessage(article.lastError) : undefined;
  const emptyArticle = article.markdown.trim() === '';
  const showPlannedEmpty = article.status === 'planned' && emptyArticle && !writing && !session.dirty;
  const showPipelineEmpty = writer === 'pipeline' && emptyArticle && !live;
  const loading = session.loadedVersion === null && session.loadProblem === null;
  const hideEditor = live !== undefined || loading || session.loadProblem !== null || showPlannedEmpty || showPipelineEmpty;

  return (
    <div data-slot="studio-tab" data-piece-id={articleId} className="grid gap-6">
      <div ref={statusRef} tabIndex={-1} className="outline-none">
        <ReviewBar
          article={article}
          actions={actions}
          commands={commands}
          saveStatus={
            writer === 'owner' && session.loadedVersion !== null ? (
              <SaveStatusLine
                status={session.status}
                dirty={session.dirty}
                onRetry={session.retrySave}
                onResolve={() => {
                  session.conflict.onOpenChange(true);
                }}
              />
            ) : null
          }
          onApprove={() => {
            setDialog('approve');
          }}
          onRevise={() => {
            setDialog('revise');
          }}
          onStartPipeline={() => {
            setDialog('pipeline');
          }}
          onVersions={() => {
            setDialog('versions');
          }}
          onPreview={() => void openPreview()}
          onExport={() => void exportMarkdown()}
          returnFocus={statusRef}
        />
      </div>

      {machines.isError && !machines.data ? (
        <ProblemAlert
          error={machines.error}
          title="Couldn’t load what can be done with this article. The text stays read-only until it loads."
          onRetry={() => void machines.refetch()}
          retryLabel="Try loading the article’s actions again"
        />
      ) : null}

      {lastError ? (
        <Alert variant="warn">
          <AlertTitle>It needs you</AlertTitle>
          <AlertDescription>
            <p>{lastError}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {session.status.kind === 'read-only' ? (
        <Alert variant="error">
          <AlertTitle>Your latest changes weren’t saved</AlertTitle>
          <AlertDescription>
            <p>The article became read-only before they reached the server. Copy them before you discard them.</p>
          </AlertDescription>
          <AlertActions>
            <Button size="sm" variant="secondary" onClick={() => void copyMyText()}>
              <Copy aria-hidden="true" />
              Copy my text
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void session.reload()}>
              Discard my changes
            </Button>
          </AlertActions>
        </Alert>
      ) : null}
      {session.status.kind === 'too-large' ? (
        <Alert variant="error">
          <AlertTitle>Not saved: the draft is over 128 KB</AlertTitle>
          <AlertDescription>
            <p>That is the most the editor opens. Shorten it (or move a long table or diagram out) to save again.</p>
          </AlertDescription>
          <AlertActions>
            <Button size="sm" variant="secondary" onClick={session.retrySave}>
              Try saving again
            </Button>
          </AlertActions>
        </Alert>
      ) : null}
      {session.status.kind === 'invalid' ? (
        <Alert variant="error">
          <AlertTitle>Not saved: some article details aren’t valid</AlertTitle>
          <AlertDescription>
            <p>Fix the marked details; the text saves again as soon as you do.</p>
          </AlertDescription>
          <AlertActions>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setMargin('details');
              }}
            >
              Show the details
            </Button>
          </AlertActions>
        </Alert>
      ) : null}
      {session.status.kind === 'failed' ? (
        <Alert variant="error">
          <AlertTitle>Your changes aren’t saved</AlertTitle>
          <AlertDescription>
            <p>{problemMessage(session.status.error)}</p>
          </AlertDescription>
          <AlertActions>
            <Button size="sm" variant="secondary" onClick={session.retrySave}>
              <RotateCw aria-hidden="true" />
              Try again
            </Button>
          </AlertActions>
        </Alert>
      ) : null}
      {session.status.kind === 'offline' ? (
        <Alert variant="warn" role="status">
          <AlertTitle>Offline</AlertTitle>
          <AlertDescription>
            <p>Your changes are kept in this tab and saved when you’re back online. Don’t close it until then.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <ClearanceNotice article={article} commands={commands} canAttest={actions.ownerEdits} />
      <VetoNotice article={article} commands={commands} />
      <PublishStanding article={article} />

      {writer === 'pipeline' && article.pipelineJobId ? (
        <JobTimeline jobId={article.pipelineJobId} title="Pipeline" labels={PIPELINE_STEP_LABELS} />
      ) : null}

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem] xl:gap-14">
        <section aria-label="Manuscript" className="min-w-0">
          {hideEditor ? null : (
            <EditorToolbar
              editor={editor}
              bus={bus}
              commands={blockContext}
              onRewrite={() => {
                openAi();
              }}
              className="sticky top-[calc(3.5rem+env(safe-area-inset-top))] z-20 mb-6 md:top-0"
            />
          )}
          {reason && !live ? (
            <p className="mb-6 flex items-start gap-2 font-sans text-sm text-ink-muted">
              <PencilLine aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
              {reason}
            </p>
          ) : null}
          <div className="relative pl-5 md:pl-8">
            <span aria-hidden="true" className={cn('absolute inset-y-0 left-0 w-0.5 rounded-full', threadClass(article.meta?.pillar))} />
            <ManuscriptPillar pillarId={article.meta?.pillar} pillars={pillarList} />
            {session.loadProblem ? (
              <LoadProblemState
                problem={session.loadProblem}
                onRetry={session.retryLoad}
                onPreview={() => void openPreview()}
                onExport={() => void exportMarkdown()}
              />
            ) : showPlannedEmpty ? (
              <EmptyState
                title="No draft yet."
                yourPart="Start the pipeline, or write it yourself. Either way you review it before anything publishes."
              >
                <p>
                  The pipeline researches the topic, interviews you, drafts from the brief and your stories, critiques
                  and revises it at most twice, and runs the seven gates.
                </p>
                <div className="flex flex-wrap gap-2 pt-2">
                  {actions.startPipeline ? (
                    <Button
                      onClick={() => {
                        setDialog('pipeline');
                      }}
                    >
                      <Play aria-hidden="true" />
                      Start the pipeline
                    </Button>
                  ) : null}
                  <Button
                    variant="secondary"
                    disabled={!editable}
                    onClick={() => {
                      setWriting(true);
                      editor.commands.focus('end');
                    }}
                  >
                    <PencilLine aria-hidden="true" />
                    Write it yourself
                  </Button>
                </div>
              </EmptyState>
            ) : showPipelineEmpty ? (
              <p className="font-serif text-lg text-ink-muted">The draft appears here as the pipeline writes it.</p>
            ) : null}
            {live ? (
              <DraftNodeContext value={nodeContext}>
                <LiveDraft live={live} />
              </DraftNodeContext>
            ) : null}
            {loading ? (
              <div aria-busy="true" className="grid gap-4">
                <p role="status" className="sr-only">
                  Loading the draft
                </p>
                <Skeleton className="h-6 w-2/3" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-11/12" />
                <Skeleton className="h-4 w-4/5" />
              </div>
            ) : null}
            <div className={cn('max-w-[42rem]', hideEditor && 'hidden')}>
              <DraftNodeContext value={nodeContext}>
                <EditorContent editor={editor} />
              </DraftNodeContext>
              <ManuscriptFacts editor={editor} pack={pack} />
            </div>
          </div>
        </section>

        <aside aria-label="Review notes" className="min-w-0">
          <Tabs value={margin} onValueChange={setMargin} className="gap-6">
            <TabsList aria-label="Review notes">
              <TabsTrigger value="checks">Checks</TabsTrigger>
              <TabsTrigger value="outline">Outline</TabsTrigger>
              <TabsTrigger value="details">Details</TabsTrigger>
              <TabsTrigger value="images">Images</TabsTrigger>
            </TabsList>
            <TabsContent value="checks" className="grid gap-10">
              <GatesPanel
                articleId={articleId}
                actions={actions}
                view={gates.data}
                pending={gates.isPending}
                error={gates.error}
                onRetry={() => void gates.refetch()}
                ensureSaved={session.flush}
                onShow={showReportFinding}
                onStale={() => void refetch()}
              />
              {editable ? (
                <LiveChecks
                  lint={lint}
                  saveFindings={session.saveFindings}
                  lossWarnings={session.lossWarnings}
                  onShowLint={showLintFinding}
                  onShowBlock={showBlocks}
                />
              ) : null}
            </TabsContent>
            <TabsContent value="outline">
              <OutlinePanel editor={editor} />
            </TabsContent>
            <TabsContent value="details">
              <DetailsPanel
                meta={session.meta}
                errors={session.metaErrors}
                onChange={session.setMeta}
                readOnly={!editable}
                publishedSlug={article.publish?.slug ?? null}
              />
            </TabsContent>
            <TabsContent value="images">
              <ImagesPanel
                article={article}
                writer={writer}
                run={imageRun}
                commands={imageCommands}
                onReload={() => void refetch()}
                onPreview={() => void openPreview()}
              />
            </TabsContent>
          </Tabs>
        </aside>
      </div>

      <SlashMenu editor={editor} slashKeys={slashKeys} commands={blockContext} />
      <SelectionAi
        editor={editor}
        articleId={articleId}
        capture={selection}
        onClose={() => {
          setSelection(null);
        }}
      />
      <ConflictDialog
        {...session.conflict}
        subject="This draft"
        mine={session.conflictMine}
        theirs={session.conflictMine !== undefined ? article.markdown : undefined}
        saving={session.resolving}
        onKeepMine={session.keepMine}
        onTakeTheirs={session.takeTheirs}
      />
      <ApproveDialog
        open={dialog === 'approve'}
        onOpenChange={(open) => {
          setDialog(open ? 'approve' : null);
        }}
        article={article}
        gates={gates.data}
        returnFocus={statusRef}
        onApprove={async (request) => {
          await commands.approve(request);
          toastSuccess('Approved. It publishes at the time you chose.');
        }}
      />
      <ReviseDialog
        open={dialog === 'revise'}
        onOpenChange={(open) => {
          setDialog(open ? 'revise' : null);
        }}
        returnFocus={statusRef}
        onRevise={async (instruction) => {
          await commands.revise(instruction);
          toastSuccess('Revision started. Follow it here.');
        }}
      />
      <PipelineDialog
        open={dialog === 'pipeline'}
        onOpenChange={(open) => {
          setDialog(open ? 'pipeline' : null);
        }}
        article={article}
        returnFocus={statusRef}
        onStart={async (startFrom) => {
          await commands.startPipeline(startFrom);
          toastSuccess('Pipeline started. Follow it here.');
        }}
      />
      <VersionsSheet
        open={dialog === 'versions'}
        onOpenChange={(open) => {
          setDialog(open ? 'versions' : null);
        }}
        articleId={articleId}
        currentMarkdown={session.currentMarkdown}
        canRestore={actions.ownerEdits}
        onRestore={async (version) => {
          await commands.restore(version);
          await session.reload();
        }}
        onPreview={(version) => {
          setDialog(null);
          void openPreview(version);
        }}
      />
      <CitationPicker
        open={dialog === 'citation'}
        onOpenChange={(open) => {
          setDialog(open ? 'citation' : null);
        }}
        citations={citations}
        emptyReason={
          !article.briefId
            ? 'No research brief yet. Citations come from the brief’s sources.'
            : brief.isError
              ? 'The brief couldn’t be loaded. Try again in a moment.'
              : 'The brief has no sources.'
        }
        onPick={(citation) => {
          insertCitation(editor, citation);
        }}
      />
      {image ? (
        <ImageDialog
          key={image.id}
          editor={editor}
          open
          initialFile={image.file}
          onOpenChange={(open) => {
            if (!open) setImage(null);
          }}
        />
      ) : null}
      {preview ? (
        <PreviewDialog
          open
          onOpenChange={(open) => {
            if (!open) setPreview(null);
          }}
          articleId={articleId}
          version={preview.version}
          title={article.meta?.title ?? 'this article'}
          hero={heroOf(article).image}
          largeImages={preview.version === undefined ? largeImagesInBody(article) : 0}
        />
      ) : null}
    </div>
  );
}

interface LoadProblemStateProps {
  problem: LoadProblem;
  onRetry: () => void;
  onPreview: () => void;
  onExport: () => void;
}

function loadProblemText(problem: LoadProblem): { title: string; detail: string; retryable: boolean } {
  if (problem.kind === 'too-large') {
    return {
      title: 'This article is too large for the editor.',
      detail: `The editor opens articles up to 128 KB; this one is ${String(Math.ceil(problem.bytes / 1024))} KB. Preview or export it, or ask for a revision that shortens it.`,
      retryable: false,
    };
  }
  switch (problem.code) {
    case 'too-large':
      return {
        title: 'This article is too large for the editor.',
        detail: 'The editor opens articles up to 128 KB. Preview or export it, or ask for a revision that shortens it.',
        retryable: false,
      };
    case 'timeout':
      return { title: 'The editor took too long to open this article.', detail: problem.message, retryable: true };
    case 'worker-unavailable':
      return {
        title: 'The editor couldn’t start in this browser.',
        detail: 'It needs Web Workers, which this browser blocked. Try another browser, or preview and export the article instead.',
        retryable: false,
      };
    case 'worker-failed':
    case 'cancelled':
      return { title: 'The editor stopped while opening this article.', detail: problem.message, retryable: true };
    case 'too-deep':
    case 'invalid-frontmatter':
    case 'invalid-content':
    case 'internal':
      return { title: 'The editor can’t open this article.', detail: problem.message, retryable: false };
  }
}

function LoadProblemState({ problem, onRetry, onPreview, onExport }: LoadProblemStateProps) {
  const { title, detail, retryable } = loadProblemText(problem);
  return (
    <Alert variant="error" role="alert">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{detail}</p>
      </AlertDescription>
      <AlertActions>
        {retryable ? (
          <Button size="sm" variant="secondary" onClick={onRetry}>
            <RotateCw aria-hidden="true" />
            Try again
          </Button>
        ) : null}
        <Button size="sm" variant="secondary" onClick={onPreview}>
          <Eye aria-hidden="true" />
          Preview as portfolio page
        </Button>
        <Button size="sm" variant="ghost" onClick={onExport}>
          <FileDown aria-hidden="true" />
          Export Markdown
        </Button>
      </AlertActions>
    </Alert>
  );
}
