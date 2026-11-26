/**
 * A controllable matchMedia for jsdom, which has none. Tests flip the viewport,
 * colour scheme or display mode and every subscribed list fires `change`.
 */
export interface MediaState {
  desktop: boolean;
  dark: boolean;
  standalone: boolean;
  reducedMotion: boolean;
}

const DEFAULTS: MediaState = { desktop: true, dark: false, standalone: false, reducedMotion: false };

let state: MediaState = { ...DEFAULTS };
const lists = new Set<FakeMediaQueryList>();

function evaluate(query: string): boolean {
  if (query.includes('min-width: 48rem')) return state.desktop;
  if (query.includes('prefers-color-scheme: dark')) return state.dark;
  if (query.includes('display-mode: standalone')) return state.standalone;
  if (query.includes('prefers-reduced-motion: reduce')) return state.reducedMotion;
  return false;
}

type LegacyListener = (event: Event) => void;

class FakeMediaQueryList extends EventTarget {
  readonly media: string;
  onchange: LegacyListener | null = null;

  constructor(media: string) {
    super();
    this.media = media;
  }

  get matches(): boolean {
    return evaluate(this.media);
  }

  /** The pre-2019 API, still called by some libraries. */
  addListener(listener: LegacyListener): void {
    this.addEventListener('change', listener);
  }

  removeListener(listener: LegacyListener): void {
    this.removeEventListener('change', listener);
  }
}

export function installMatchMedia(): void {
  window.matchMedia = (query: string) => {
    const list = new FakeMediaQueryList(query);
    lists.add(list);
    return list;
  };
}

/** Changes the simulated environment and notifies the lists whose answer changed. */
export function setMedia(next: Partial<MediaState>): void {
  const before = new Map([...lists].map((list) => [list, list.matches]));
  state = { ...state, ...next };
  for (const list of lists) {
    if (before.get(list) === list.matches) continue;
    const event = new Event('change');
    list.dispatchEvent(event);
    list.onchange?.(event);
  }
}

export function resetMedia(): void {
  state = { ...DEFAULTS };
  lists.clear();
}
