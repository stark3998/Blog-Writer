import { useCallback, useState, useSyncExternalStore, type SetStateAction } from 'react';

import { EMPTY_INTAKE, type IntakeState } from '@/features/intake';
import { useAppData, type AppData } from '@/lib/app-data';

/**
 * Interview drafts, autosaved **in memory only**: every keystroke and every redaction lands
 * here, so moving between questions or tabs loses nothing, while nothing is ever written to
 * web storage, IndexedDB, the query cache or a service-worker cache (contract 1.9 rule 9).
 *
 * The store belongs to one app instance (`AppData`). Reloading the page, closing the tab or a
 * session-expiry redirect (a full navigation) drops it, and so does any sign-in or "not
 * authorised" screen: the drafts are cleared as soon as the session stops being `ok`.
 */

/** One interview's drafts by question id. */
export type InterviewDrafts = ReadonlyMap<string, IntakeState>;

const NO_DRAFTS: InterviewDrafts = new Map();

export interface DraftStore {
  /** The drafts of one interview; the same object until they change (a `useSyncExternalStore` snapshot). */
  readonly get: (interviewId: string) => InterviewDrafts;
  /** Sets or updates one question's draft (`setState` style: a value or an updater). */
  readonly update: (interviewId: string, questionId: string, action: SetStateAction<IntakeState>) => void;
  /** Forgets one interview's drafts (sent, skipped, or the owner took the saved version). */
  readonly clear: (interviewId: string) => void;
  /** Forgets everything (the session ended). */
  readonly clearAll: () => void;
  readonly subscribe: (listener: () => void) => () => void;
}

export function createDraftStore(): DraftStore {
  let drafts = new Map<string, InterviewDrafts>();
  const listeners = new Set<() => void>();
  const notify = () => {
    for (const listener of listeners) listener();
  };
  return {
    get: (interviewId) => drafts.get(interviewId) ?? NO_DRAFTS,
    update: (interviewId, questionId, action) => {
      const current = drafts.get(interviewId) ?? NO_DRAFTS;
      const previous = current.get(questionId) ?? EMPTY_INTAKE;
      const next = typeof action === 'function' ? action(previous) : action;
      if (next === previous) return;
      const updated = new Map(current);
      updated.set(questionId, next);
      drafts = new Map(drafts);
      drafts.set(interviewId, updated);
      notify();
    },
    clear: (interviewId) => {
      if (!drafts.has(interviewId)) return;
      drafts = new Map(drafts);
      drafts.delete(interviewId);
      notify();
    },
    clearAll: () => {
      if (drafts.size === 0) return;
      drafts = new Map();
      notify();
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

const stores = new WeakMap<AppData, DraftStore>();

/** The draft store of one app instance, emptied whenever its session stops being `ok`. */
export function draftStoreFor(appData: AppData): DraftStore {
  const existing = stores.get(appData);
  if (existing) return existing;
  const store = createDraftStore();
  appData.auth.subscribe(() => {
    if (appData.auth.getState().status !== 'ok') store.clearAll();
  });
  stores.set(appData, store);
  return store;
}

export interface InterviewDraftsHandle {
  readonly drafts: InterviewDrafts;
  /** The drafts as they are right now (an update made in this event handler included). */
  readonly latest: () => InterviewDrafts;
  readonly update: (questionId: string, action: SetStateAction<IntakeState>) => void;
  readonly clear: () => void;
}

/** One interview's in-memory drafts, re-rendering when they change. */
export function useInterviewDrafts(interviewId: string): InterviewDraftsHandle {
  const appData = useAppData();
  const [store] = useState(() => draftStoreFor(appData));
  const drafts = useSyncExternalStore(
    store.subscribe,
    () => store.get(interviewId),
    () => NO_DRAFTS,
  );
  const latest = useCallback(() => store.get(interviewId), [store, interviewId]);
  const update = useCallback(
    (questionId: string, action: SetStateAction<IntakeState>) => {
      store.update(interviewId, questionId, action);
    },
    [store, interviewId],
  );
  const clear = useCallback(() => {
    store.clear(interviewId);
  }, [store, interviewId]);
  return { drafts, latest, update, clear };
}
