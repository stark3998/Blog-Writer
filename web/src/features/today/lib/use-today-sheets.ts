import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { hasLaunchParams, LAUNCH_PARAMS, readLaunch, withoutLaunchParams, type TodayLaunch } from './launch';

/** What the Engage panel opens with. `serial` changes for every request, so the panel starts fresh. */
export type EngageRequest =
  | { readonly kind: 'share'; readonly text: string; readonly url: string | null; readonly serial: number }
  | { readonly kind: 'reply'; readonly postId: string | null; readonly serial: number };

export interface TodaySheets {
  readonly engageOpen: boolean;
  readonly engageRequest: EngageRequest | null;
  readonly setEngageOpen: (open: boolean) => void;
  /** Opens the Engage panel replying on a post (a reply reminder). */
  readonly openReply: (postId: string | null) => void;
  /** The post whose numbers the snapshot form is open for. */
  readonly snapshotPostId: string | null;
  readonly openSnapshot: (postId: string) => void;
  readonly closeSnapshot: () => void;
}

interface State {
  /** The launch parameters last read from the URL, so each is handled once. */
  readonly seen: string;
  readonly serial: number;
  readonly engageOpen: boolean;
  readonly engageRequest: EngageRequest | null;
  readonly snapshotPostId: string | null;
}

const INITIAL: State = { seen: '', serial: 0, engageOpen: false, engageRequest: null, snapshotPostId: null };

function launchKey(params: URLSearchParams): string {
  const own = new URLSearchParams();
  for (const name of LAUNCH_PARAMS) {
    for (const value of params.getAll(name)) own.append(name, value);
  }
  return own.toString();
}

function applyLaunch(state: State, launch: TodayLaunch | null): State {
  if (launch === null) return state;
  const serial = state.serial + 1;
  switch (launch.kind) {
    case 'share':
      return {
        ...state,
        serial,
        engageOpen: true,
        engageRequest: { kind: 'share', text: launch.text, url: launch.url, serial },
        snapshotPostId: null,
      };
    case 'engage':
      return {
        ...state,
        serial,
        engageOpen: true,
        engageRequest: { kind: 'reply', postId: launch.postId, serial },
        snapshotPostId: null,
      };
    case 'snapshot':
      return { ...state, serial, engageOpen: false, snapshotPostId: launch.postId };
  }
}

/**
 * The two sheets Today opens in place (the Engage panel and the snapshot form), and the links that
 * open them (`?share=`, `?engage=`, `?snapshot=`): each is read once, then removed from the URL
 * with a replace, so shared text leaves the address bar and the history.
 */
export function useTodaySheets(): TodaySheets {
  const [params, setParams] = useSearchParams();
  const key = launchKey(params);
  const [state, setState] = useState<State>(INITIAL);

  if (key !== state.seen) {
    // Read the launch during render (once per new URL), not in an effect, so the sheet opens
    // with the first paint.
    setState(applyLaunch({ ...state, seen: key }, key === '' ? null : readLaunch(params)));
  }

  useEffect(() => {
    if (hasLaunchParams(params)) setParams((current) => withoutLaunchParams(current), { replace: true });
  }, [params, setParams]);

  const setEngageOpen = useCallback((open: boolean) => {
    setState((current) => ({ ...current, engageOpen: open }));
  }, [setState]);
  const openReply = useCallback((postId: string | null) => {
    setState((current) => {
      const serial = current.serial + 1;
      return { ...current, serial, engageOpen: true, engageRequest: { kind: 'reply', postId, serial } };
    });
  }, [setState]);
  const openSnapshot = useCallback((postId: string) => {
    setState((current) => ({ ...current, snapshotPostId: postId }));
  }, [setState]);
  const closeSnapshot = useCallback(() => {
    setState((current) => ({ ...current, snapshotPostId: null }));
  }, [setState]);

  return {
    engageOpen: state.engageOpen,
    engageRequest: state.engageRequest,
    setEngageOpen,
    openReply,
    snapshotPostId: state.snapshotPostId,
    openSnapshot,
    closeSnapshot,
  };
}
