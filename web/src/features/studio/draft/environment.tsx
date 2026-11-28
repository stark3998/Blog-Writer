import { createContext, use, type ReactNode } from 'react';

import { DEFAULT_WORKER_TIMEOUT_MS, parseInWorker, type Article as DialectArticle } from '@/features/editor';

/**
 * What the Draft tab needs from outside React, in one place: the dialect parser (the normalize
 * Web Worker, with its timeout) and the editor's timings. Tests swap in an in-thread parser and
 * short delays; the app uses the defaults.
 */

export interface DialectService {
  /**
   * Parses an article body into the editor's document. Everything that comes from outside the
   * editor goes through here: the stored article, the live pipeline text, Markdown pastes and
   * AI proposals. Rejects with a `DialectError` (`too-large`, `timeout`, ...).
   */
  readonly parse: (markdown: string, options?: { timeoutMs?: number }) => Promise<DialectArticle>;
}

export interface DraftTimings {
  /** Quiet time after the last edit before the draft saves. */
  readonly autosaveMs: number;
  /** The longest the draft waits to save while the owner keeps typing. */
  readonly autosaveMaxWaitMs: number;
  /** Waits between save retries after a server error (then the owner retries by hand). */
  readonly retryMs: readonly number[];
  /** Quiet time before the live checks run on the text. */
  readonly lintMs: number;
  /** How often the live pipeline text is parsed again while it streams. */
  readonly liveParseMs: number;
  /** How long the worker may take to parse a document. */
  readonly parseTimeoutMs: number;
  /** Quiet time before a changed diagram is rendered again. */
  readonly diagramMs: number;
}

export interface DraftEnvironment {
  readonly dialect: DialectService;
  readonly timings: DraftTimings;
}

export const DEFAULT_DRAFT_ENVIRONMENT: DraftEnvironment = {
  dialect: { parse: (markdown, options) => parseInWorker(markdown, options) },
  timings: {
    autosaveMs: 1200,
    autosaveMaxWaitMs: 8000,
    retryMs: [2000, 5000, 15000],
    lintMs: 2500,
    liveParseMs: 400,
    parseTimeoutMs: Math.max(DEFAULT_WORKER_TIMEOUT_MS, 8000),
    diagramMs: 900,
  },
};

const DraftEnvironmentContext = createContext<DraftEnvironment>(DEFAULT_DRAFT_ENVIRONMENT);

export function DraftEnvironmentProvider({ value, children }: { value: DraftEnvironment; children: ReactNode }) {
  return <DraftEnvironmentContext value={value}>{children}</DraftEnvironmentContext>;
}

export function useDraftEnvironment(): DraftEnvironment {
  return use(DraftEnvironmentContext);
}
