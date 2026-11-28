import { CircleAlert, Plus } from 'lucide-react';
import { Tabs as TabsPrimitive } from 'radix-ui';
import { useMemo, useRef, useState } from 'react';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { focusFirstInvalid } from '@/components/ui/field';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { cn } from '@/lib/cn';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';
import { PillarThread } from '@/components/ui/pillar-badge';

import { SaveBar } from './components/save-bar';
import { LibrarySection, LoadError, LoadingBlock, Subsection } from './components/section-intro';
import { UnsavedChangesGuard } from './components/unsaved-changes-guard';
import { usePillarsQuery } from './hooks/use-pillars';
import { useSettingsDraft } from './hooks/use-settings-draft';
import { errorsUnder, hasErrors, mergeErrors, NO_ERRORS, serverFieldErrors, type ErrorMap } from './lib/errors';
import { formatShare, listOf, plural } from './lib/format';
import { REFERENCE_KIND_LABEL } from './lib/labels';
import { parsePercent } from './lib/rules';
import { LanesForm } from './pillars/lanes-form';
import { describeLanes, lanesToBody, lanesToDraft, validateLanes, type LanesDraft } from './pillars/lanes-model';
import {
  describePillars,
  draftToBody,
  MAX_PILLARS,
  newPillar,
  pillarsToDraft,
  pillarToDraft,
  validatePillars,
  type PillarDraft,
  type PillarsDraft,
} from './pillars/model';
import { PillarForm } from './pillars/pillar-form';
import { PillarMix } from './pillars/pillar-mix';

/** The extension members of a `pillar_in_use` (409) answer: which removed ids are still used, and by what. */
function pillarsInUse(error: unknown): { id: string; kinds: string[] }[] {
  if (!isProblem(error, 'pillar_in_use')) return [];
  const references = error.problem?.extensions?.references;
  if (typeof references !== 'object' || references === null || Array.isArray(references)) return [];
  return Object.entries(references as Record<string, unknown>).map(([id, kinds]) => ({
    id,
    kinds: Array.isArray(kinds) ? kinds.filter((kind): kind is string => typeof kind === 'string') : [],
  }));
}

function errorCount(errors: ErrorMap): number {
  return Object.keys(errors).length;
}

/**
 * Library > Pillars (`/library/pillars`): each pillar's description, audience, vocabulary and
 * renamed terms, hashtags, trusted research sites, feeds and target share; the series and the
 * weekly lane rules. Calls `GET/PUT /api/kb/pillars`, `GET/PUT /api/kb/lanes`, `GET/POST
 * /api/sources` (contract section 10). Saves send If-Match (If-None-Match: * the first time).
 */
export function PillarsSection() {
  const pillarsQuery = usePillarsQuery();
  const lanesQuery = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.lanes), path: '/api/kb/lanes' });
  const pillars = useSettingsDraft(pillarsQuery.data, pillarsToDraft);
  const lanes = useSettingsDraft(lanesQuery.data, lanesToDraft);
  const pillarsConflict = useConflict();
  const lanesConflict = useConflict();
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const editorRef = useRef<HTMLDivElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<{ key: string | undefined; index: number }>({ key: undefined, index: 0 });
  const [showErrors, setShowErrors] = useState(false);

  const savePillars = useApiMutation('put', '/api/kb/pillars', {
    updates: () => queryKeys.kb.detail(KB_SINGLETONS.pillars),
    invalidates: [queryKeys.today],
    successToast: 'Pillars saved.',
    errorToast: 'Couldn’t save the pillars.',
    handles: ['etag_mismatch', 'validation_failed', 'confidential_text', 'pillar_in_use'],
  });
  const saveLanes = useApiMutation('put', '/api/kb/lanes', {
    updates: () => queryKeys.kb.detail(KB_SINGLETONS.lanes),
    successToast: 'Series and lane rules saved.',
    errorToast: 'Couldn’t save the series and lane rules.',
    handles: ['etag_mismatch', 'validation_failed', 'confidential_text'],
  });

  const pillarDraft = pillars.draft;
  const lanesDraft = lanes.draft;
  const savedPillars = useMemo(() => (pillarsQuery.data?.item.pillars ?? []).map((pillar) => ({ id: pillar.id, name: pillar.name })), [pillarsQuery.data]);

  const pillarClientErrors = useMemo(() => (pillarDraft ? validatePillars(pillarDraft) : NO_ERRORS), [pillarDraft]);
  const lanesClientErrors = useMemo(
    () => (lanesDraft ? validateLanes(lanesDraft, new Set(savedPillars.map((pillar) => pillar.id))) : NO_ERRORS),
    [lanesDraft, savedPillars],
  );
  const pillarErrors = mergeErrors(showErrors ? pillarClientErrors : NO_ERRORS, serverFieldErrors(savePillars.error));
  const laneErrors = mergeErrors(showErrors ? lanesClientErrors : NO_ERRORS, serverFieldErrors(saveLanes.error));
  const inUse = pillarsInUse(savePillars.error);

  const keys = pillarDraft?.pillars.map((pillar) => pillar.key) ?? [];
  const activeIndex = selection.key !== undefined && keys.includes(selection.key) ? keys.indexOf(selection.key) : Math.min(selection.index, Math.max(0, keys.length - 1));
  const activeKey = keys[activeIndex];

  const select = (index: number) => {
    setSelection({ key: keys[index], index });
  };

  const updatePillar = (index: number, change: (pillar: PillarDraft) => PillarDraft) => {
    pillars.update((draft) => ({ pillars: draft.pillars.map((pillar, position) => (position === index ? change(pillar) : pillar)) }));
  };

  const removePillar = (index: number) => {
    pillars.update((draft) => ({ pillars: draft.pillars.filter((_, position) => position !== index) }));
    const next = Math.max(0, index - 1);
    setSelection({ key: keys.filter((_, position) => position !== index)[next], index: next });
  };

  const addPillar = () => {
    const added = newPillar();
    pillars.update((draft) => ({ pillars: [...draft.pillars, added] }));
    setSelection({ key: added.key, index: keys.length });
    requestAnimationFrame(() => {
      editorRef.current?.querySelector<HTMLInputElement>('[role="tabpanel"][data-state="active"] input')?.focus();
    });
  };

  /** Puts pillars the server refused to remove back, from the saved version. */
  const keepPillars = (ids: readonly string[]) => {
    const saved = pillarsQuery.data?.item.pillars ?? [];
    pillars.update((draft) => {
      const present = new Set(draft.pillars.map((pillar) => pillar.id));
      const restored = saved.filter((pillar) => ids.includes(pillar.id) && !present.has(pillar.id)).map(pillarToDraft);
      return { pillars: [...draft.pillars, ...restored] };
    });
    savePillars.reset();
  };

  const sendPillars = async (draft: PillarsDraft, ifMatch: string | null): Promise<boolean> => {
    try {
      const data = await savePillars.mutateAsync({ body: draftToBody(draft), ifMatch });
      pillars.saved(draft, data);
      pillarsConflict.close();
      return true;
    } catch (error) {
      if (pillarsConflict.show(error)) void pillarsQuery.refetch();
      return false;
    }
  };

  const sendLanes = async (draft: LanesDraft, ifMatch: string | null): Promise<boolean> => {
    try {
      const data = await saveLanes.mutateAsync({ body: lanesToBody(draft), ifMatch });
      lanes.saved(draft, data);
      lanesConflict.close();
      return true;
    } catch (error) {
      if (lanesConflict.show(error)) void lanesQuery.refetch();
      return false;
    }
  };

  const showFirstProblem = () => {
    const firstPillar = Object.keys(pillarClientErrors)
      .map((path) => /^pillars\.(\d+)/.exec(path)?.[1])
      .find((index) => index !== undefined);
    if (firstPillar !== undefined) select(Number(firstPillar));
    requestAnimationFrame(() => {
      if (!focusFirstInvalid(editorRef.current)) summaryRef.current?.focus();
    });
  };

  const save = async () => {
    setShowErrors(true);
    if (hasErrors(pillarClientErrors) || hasErrors(lanesClientErrors)) {
      showFirstProblem();
      return;
    }
    const savePillarsFirst = !(pillarDraft && pillars.baseline && removesPillars(pillars.baseline, pillarDraft));
    const steps: (() => Promise<boolean>)[] = [];
    const pillarStep = () => (pillarDraft && pillars.dirty ? sendPillars(pillarDraft, pillars.baseEtag ?? null) : Promise.resolve(true));
    const laneStep = () => (lanesDraft && lanes.dirty ? sendLanes(lanesDraft, lanes.baseEtag ?? null) : Promise.resolve(true));
    // A series on a new pillar needs the pillar saved first; removing a pillar needs its series gone first.
    if (savePillarsFirst) steps.push(pillarStep, laneStep);
    else steps.push(laneStep, pillarStep);
    for (const step of steps) {
      if (!(await step())) {
        requestAnimationFrame(() => {
          if (!focusFirstInvalid(editorRef.current)) summaryRef.current?.focus();
        });
        return;
      }
    }
    setShowErrors(false);
  };

  const discard = () => {
    pillars.discard();
    lanes.discard();
    savePillars.reset();
    saveLanes.reset();
    setShowErrors(false);
  };

  const dirty = pillars.dirty || lanes.dirty;
  const saving = savePillars.isPending || saveLanes.isPending;
  const totalErrors = errorCount(pillarErrors) + errorCount(laneErrors);
  const genericPillarError =
    savePillars.error && !isProblem(savePillars.error, ['etag_mismatch', 'validation_failed', 'confidential_text', 'pillar_in_use'])
      ? savePillars.error
      : undefined;
  const genericLaneError =
    saveLanes.error && !isProblem(saveLanes.error, ['etag_mismatch', 'validation_failed', 'confidential_text']) ? saveLanes.error : undefined;
  const nameOf = (id: string) => pillarsQuery.data?.item.pillars?.find((pillar) => pillar.id === id)?.name ?? id;
  const onPillarShare = lanesDraft ? parsePercent(lanesDraft.onPillarMinShare) : undefined;

  return (
    <LibrarySection
      title="Your pillars"
      standfirst={
        <p>
          {`Each pillar holds its audience, its words and the sources research trusts. The planner keeps at least ${
            onPillarShare === undefined ? '80%' : formatShare(onPillarShare)
          } of each week’s posts on a pillar.`}
        </p>
      }
      yourPart="Keep each pillar’s audience, vocabulary and sources current. Planning, research and drafts follow these settings."
    >

      {pillarsQuery.isPending ? (
        <LoadingBlock label="Loading your pillars" rows={4} />
      ) : pillarsQuery.isError ? (
        <LoadError
          error={pillarsQuery.error}
          title="Couldn’t load the pillars."
          retryLabel="Try loading the pillars again"
          onRetry={() => void pillarsQuery.refetch()}
        />
      ) : pillarDraft ? (
        <div ref={editorRef} className="space-y-8">
          {pillars.baseEtag === null ? (
            <Alert variant="info">
              <AlertTitle>These are the plan’s starting pillars.</AlertTitle>
              <AlertDescription>
                <p>Change what doesn’t fit and save. Until then the app works from these defaults.</p>
              </AlertDescription>
            </Alert>
          ) : null}

          <PillarMix
            entries={pillarDraft.pillars.map((pillar) => ({
              key: pillar.key,
              id: pillar.id,
              name: pillar.name,
              share: parsePercent(pillar.share),
            }))}
          />

          {(showErrors && totalErrors > 0) || inUse.length > 0 || genericPillarError || genericLaneError ? (
            <div ref={summaryRef} tabIndex={-1} className="space-y-3 outline-none">
              {inUse.length > 0 ? (
                <Alert variant="error">
                  <AlertTitle>{inUse.length === 1 ? 'A pillar you removed is still in use.' : 'Pillars you removed are still in use.'}</AlertTitle>
                  <AlertDescription>
                    <ul className="list-disc space-y-1 pl-5">
                      {inUse.map((entry) => (
                        <li key={entry.id}>
                          {`${nameOf(entry.id)}: used by ${listOf(entry.kinds.map((kind) => REFERENCE_KIND_LABEL[kind] ?? kind))}.`}
                        </li>
                      ))}
                    </ul>
                    <p>Move what uses it to another pillar first, or keep the pillar.</p>
                  </AlertDescription>
                  <AlertActions>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        keepPillars(inUse.map((entry) => entry.id));
                      }}
                    >
                      {inUse.length === 1 ? 'Keep the pillar' : 'Keep the pillars'}
                    </Button>
                  </AlertActions>
                </Alert>
              ) : null}
              {showErrors && totalErrors > 0 ? (
                <Alert variant="error">
                  <AlertTitle>{`${plural(totalErrors, 'field needs', 'fields need')} a fix before saving.`}</AlertTitle>
                  <AlertDescription>
                    <p>Each one is marked where it is.</p>
                  </AlertDescription>
                </Alert>
              ) : null}
              {genericPillarError ? <ProblemAlert error={genericPillarError} title="Couldn’t save the pillars." /> : null}
              {genericLaneError ? <ProblemAlert error={genericLaneError} title="Couldn’t save the series and lane rules." /> : null}
            </div>
          ) : null}

          <TabsPrimitive.Root
            value={activeKey ?? ''}
            onValueChange={(key) => {
              setSelection({ key, index: keys.indexOf(key) });
            }}
            orientation={isDesktop ? 'vertical' : 'horizontal'}
            className="grid gap-6 md:grid-cols-[15rem_minmax(0,1fr)] md:gap-10"
          >
            <div className="space-y-3">
              <TabsPrimitive.List
                aria-label="Pillars"
                className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1 md:mx-0 md:flex-col md:overflow-visible md:px-0"
              >
                {pillarDraft.pillars.map((pillar, index) => {
                  const broken = hasErrors(errorsUnder(pillarErrors, `pillars.${String(index)}`));
                  const share = parsePercent(pillar.share);
                  return (
                    <TabsPrimitive.Trigger
                      key={pillar.key}
                      value={pillar.key}
                      className={cn(
                        'flex min-h-11 shrink-0 items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm text-ink-muted',
                        'transition-colors duration-150 hover:bg-wash hover:text-ink',
                        'data-[state=active]:bg-wash-strong data-[state=active]:font-semibold data-[state=active]:text-ink',
                      )}
                    >
                      <PillarThread id={pillar.id} />
                      <span className="min-w-0 flex-1 truncate">{pillar.name.trim() || 'New pillar'}</span>
                      <span className="tabular-nums text-ink-subtle">{share === undefined ? '' : formatShare(share)}</span>
                      {broken ? (
                        <>
                          <CircleAlert aria-hidden="true" className="size-4 shrink-0 text-danger" />
                          <span className="sr-only">, needs a fix</span>
                        </>
                      ) : null}
                    </TabsPrimitive.Trigger>
                  );
                })}
              </TabsPrimitive.List>
              <Button variant="secondary" size="sm" disabled={pillarDraft.pillars.length >= MAX_PILLARS} onClick={addPillar}>
                <Plus aria-hidden="true" />
                Add a pillar
              </Button>
              {pillarDraft.pillars.length >= MAX_PILLARS ? (
                <p className="text-sm text-ink-muted">{`Five pillars is the most. Remove one to add another.`}</p>
              ) : null}
              {pillarErrors.pillars ? (
                <p className="flex items-start gap-1.5 text-sm font-medium text-danger">
                  <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                  {pillarErrors.pillars}
                </p>
              ) : null}
            </div>
            {pillarDraft.pillars.map((pillar, index) => (
              <TabsPrimitive.Content key={pillar.key} value={pillar.key} className="min-w-0 outline-offset-4">
                <h3 className="mb-6 flex items-center gap-3 font-serif text-xl font-medium text-ink">
                  <PillarThread id={pillar.id} className="h-6" />
                  {pillar.name.trim() || 'New pillar'}
                </h3>
                <PillarForm
                  pillar={pillar}
                  errors={errorsUnder(pillarErrors, `pillars.${String(index)}`)}
                  onChange={(change) => {
                    updatePillar(index, change);
                  }}
                  onRemove={() => {
                    removePillar(index);
                  }}
                  canRemove={pillarDraft.pillars.length > 1}
                />
              </TabsPrimitive.Content>
            ))}
          </TabsPrimitive.Root>
        </div>
      ) : null}

      <Subsection
        title="Series and weekly rules"
        description="How the planner fills a week: derivatives of the flagship, native posts from your banks, quick takes, formats and re-cuts."
      >
        {lanesQuery.isPending ? (
          <LoadingBlock label="Loading the lane rules" rows={3} />
        ) : lanesQuery.isError ? (
          <LoadError
            error={lanesQuery.error}
            title="Couldn’t load the series and lane rules."
            retryLabel="Try loading the lane rules again"
            onRetry={() => void lanesQuery.refetch()}
          />
        ) : lanesDraft ? (
          <LanesForm draft={lanesDraft} errors={laneErrors} pillars={savedPillars} onChange={lanes.update} />
        ) : null}
      </Subsection>

      <SaveBar
        dirty={dirty}
        saving={saving}
        saveLabel="Save changes"
        onSave={() => void save()}
        onDiscard={discard}
        blockedReason={showErrors && totalErrors > 0 ? `${plural(totalErrors, 'field needs', 'fields need')} a fix.` : undefined}
      />

      <ConflictDialog
        {...pillarsConflict}
        subject="The pillars"
        mine={pillarDraft ? describePillars(pillarDraft) : undefined}
        theirs={
          pillarsQuery.data && pillarsQuery.data.etag !== pillars.baseEtag ? describePillars(pillarsToDraft(pillarsQuery.data.item)) : undefined
        }
        saving={savePillars.isPending}
        onKeepMine={() => {
          if (pillarDraft) void sendPillars(pillarDraft, pillarsConflict.conflict?.etag ?? pillarsQuery.data?.etag ?? null);
        }}
        onTakeTheirs={() => {
          void pillarsQuery.refetch().then(() => {
            pillars.takeServer();
          });
          savePillars.reset();
          pillarsConflict.close();
        }}
      />
      <ConflictDialog
        {...lanesConflict}
        subject="The series and lane rules"
        mine={lanesDraft ? describeLanes(lanesDraft) : undefined}
        theirs={lanesQuery.data && lanesQuery.data.etag !== lanes.baseEtag ? describeLanes(lanesToDraft(lanesQuery.data.item)) : undefined}
        saving={saveLanes.isPending}
        onKeepMine={() => {
          if (lanesDraft) void sendLanes(lanesDraft, lanesConflict.conflict?.etag ?? lanesQuery.data?.etag ?? null);
        }}
        onTakeTheirs={() => {
          void lanesQuery.refetch().then(() => {
            lanes.takeServer();
          });
          saveLanes.reset();
          lanesConflict.close();
        }}
      />

      <UnsavedChangesGuard when={dirty} subject="your pillars and lane rules" />
    </LibrarySection>
  );
}

/** True when the draft drops a pillar the saved version has. */
function removesPillars(baseline: PillarsDraft, draft: PillarsDraft): boolean {
  const kept = new Set(draft.pillars.map((pillar) => pillar.id));
  return baseline.pillars.some((pillar) => pillar.saved && !kept.has(pillar.id));
}
