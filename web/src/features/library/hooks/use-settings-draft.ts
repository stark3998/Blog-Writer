import { useCallback, useMemo, useState } from 'react';

import type { Entry } from '../lib/types';

/** Drafts are plain data built the same way every time, so their JSON is a fair equality test. */
export function sameDraft(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

interface State<S, D> {
  /** The latest version of the document the server gave us. */
  readonly source: Entry<S> | undefined;
  /** The saved version the draft started from, and its etag (what a save sends as If-Match). */
  readonly baseline: D | undefined;
  readonly baseEtag: string | null | undefined;
  readonly draft: D | undefined;
}

function fresh<S, D>(source: Entry<S> | undefined, toDraft: (item: S) => D): State<S, D> {
  if (!source) return { source, baseline: undefined, baseEtag: undefined, draft: undefined };
  const baseline = toDraft(source.item);
  return { source, baseline, baseEtag: source.etag, draft: baseline };
}

/**
 * A newer server version arrived (a refetch, an event, a save). Without local changes the draft
 * follows it. With local changes the draft and the etag it was based on stay, so the next save
 * is refused with 412 and the owner chooses in the conflict dialog: nothing is overwritten
 * silently in either direction.
 */
function follow<S, D>(state: State<S, D>, source: Entry<S> | undefined, toDraft: (item: S) => D): State<S, D> {
  if (!source) return { ...state, source };
  if (state.draft === undefined || sameDraft(state.draft, state.baseline)) return fresh(source, toDraft);
  return { ...state, source };
}

export interface SettingsDraft<S, D> {
  /** The owner's working copy; `undefined` until the document has loaded. */
  readonly draft: D | undefined;
  readonly baseline: D | undefined;
  /** The etag of the version the draft started from (`null`: a singleton never saved). */
  readonly baseEtag: string | null | undefined;
  /** The latest version the server gave (for "theirs" in the conflict dialog). */
  readonly source: Entry<S> | undefined;
  readonly dirty: boolean;
  readonly update: (change: (draft: D) => D) => void;
  /** Back to the saved version. */
  readonly discard: () => void;
  /** Drop the changes and start again from the latest server version ("Take theirs"). */
  readonly takeServer: () => void;
  /** A save succeeded: `response` is the new saved version; `sent` the draft that was saved. */
  readonly saved: (sent: D, response: Entry<S>) => void;
}

/**
 * The working copy of a settings document (a kb singleton): it follows the server while the
 * owner has no changes, keeps their changes otherwise, and remembers which version they started
 * from so a save can be refused when someone else saved in between (contract 1.4).
 *
 * `toDraft` must be a stable function (declare it at module level).
 */
export function useSettingsDraft<S, D>(source: Entry<S> | undefined, toDraft: (item: S) => D): SettingsDraft<S, D> {
  const [state, setState] = useState<State<S, D>>(() => fresh(source, toDraft));
  let current = state;
  if (source !== state.source) {
    current = follow(state, source, toDraft);
    setState(current);
  }

  const { draft, baseline } = current;
  const dirty = useMemo(() => draft !== undefined && !sameDraft(draft, baseline), [draft, baseline]);

  const update = useCallback((change: (draft: D) => D) => {
    setState((previous) => (previous.draft === undefined ? previous : { ...previous, draft: change(previous.draft) }));
  }, []);
  const discard = useCallback(() => {
    setState((previous) => ({ ...previous, draft: previous.baseline }));
  }, []);
  const takeServer = useCallback(() => {
    setState((previous) => fresh(previous.source, toDraft));
  }, [toDraft]);
  const saved = useCallback(
    (sent: D, response: Entry<S>) => {
      setState((previous) => {
        const next = toDraft(response.item);
        // Edits made while the save was on its way stay; otherwise the draft is the saved version.
        const unchanged = previous.draft === undefined || sameDraft(previous.draft, sent);
        return { source: previous.source, baseline: next, baseEtag: response.etag, draft: unchanged ? next : previous.draft };
      });
    },
    [toDraft],
  );

  return {
    draft,
    baseline,
    baseEtag: current.baseEtag,
    source: current.source,
    dirty,
    update,
    discard,
    takeServer,
    saved,
  };
}
