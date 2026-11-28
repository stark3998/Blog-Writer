import type { QueryClient } from '@tanstack/react-query';
import type { Editor } from '@tiptap/core';

import { DialectError, EDITOR_MAX_BYTES, utf8ByteLength, type ContentLossWarning, type DialectErrorCode } from '@/features/editor';
import type { ApiClient } from '@/lib/api/client';
import { isEtagMismatch, preconditionHeaders, type ConflictInfo } from '@/lib/api/envelope';
import { ApiError } from '@/lib/api/errors';
import { fieldErrors, isProblem, readOnlyState } from '@/lib/api/problems';
import { apiRequest } from '@/lib/api/request';

import { draftKeys } from '../api/queries';
import { draftCodec } from '../editor/bus';
import type { DraftEnvironment } from '../environment';
import { draftFromMeta, metaForSave, metaKey, validateMetaDraft, type MetaDraft, type MetaErrors, type MetaField } from '../model/meta';
import type { Article, ArticleMeta, ArticleMetaIn, Finding, VersionedArticle } from '../model/types';

/**
 * One editing session of an article, outside React: loading it into the editor (through the
 * worker), autosave with If-Match plus `baseVersion` (contract 1.4), the 412 that opens the
 * conflict dialog, and the article details. The server is the record: the session never
 * overwrites a newer version unless the owner chooses "Keep mine".
 *
 * React reads `getSnapshot()` through `useSyncExternalStore` (`use-draft-session.ts`).
 */

export type LoadProblem =
  | { readonly kind: 'too-large'; readonly bytes: number }
  | { readonly kind: 'dialect'; readonly code: DialectErrorCode; readonly message: string };

export type SaveStatus =
  | { readonly kind: 'idle' }
  | { readonly kind: 'pending' }
  | { readonly kind: 'saving' }
  | { readonly kind: 'saved'; readonly at: string }
  | { readonly kind: 'offline' }
  | { readonly kind: 'retrying'; readonly error: unknown }
  | { readonly kind: 'failed'; readonly error: unknown }
  | { readonly kind: 'conflict' }
  | { readonly kind: 'read-only'; readonly state?: string }
  | { readonly kind: 'too-large' }
  | { readonly kind: 'invalid' };

export interface SaverSnapshot {
  /** The version in the editor, `null` until the article has loaded. */
  readonly loadedVersion: number | null;
  readonly loadProblem: LoadProblem | null;
  readonly status: SaveStatus;
  /** Unsaved changes in the editor or the details. */
  readonly dirty: boolean;
  readonly meta: MetaDraft;
  /** The server's field errors from the last save (`validation_failed`). */
  readonly serverErrors: MetaErrors;
  /** What the last save could not hold exactly (`ArticleCodec.check`). */
  readonly lossWarnings: readonly ContentLossWarning[];
  /** The normalizer's findings from the last save. */
  readonly saveFindings: readonly Finding[];
  /** The owner's text when a save hit a 412, for the conflict dialog. */
  readonly conflictMine: string | undefined;
  readonly resolving: boolean;
}

export interface SaverDeps {
  readonly editor: Editor;
  readonly api: ApiClient;
  readonly queryClient: QueryClient;
  readonly articleId: string;
  readonly env: DraftEnvironment;
}

/** What the session needs from React, which may change between renders. */
export interface SaverHandlers {
  readonly refetch: () => Promise<{ data?: VersionedArticle | undefined }>;
  readonly onConflict: (error: unknown) => void;
}

type Pause = 'conflict' | 'read-only' | 'too-large' | 'invalid';

interface Base {
  readonly version: number;
  readonly meta: ArticleMeta | undefined;
}

interface Sent {
  readonly markdown: string;
  readonly metaKey: string;
}

const META_FIELDS: readonly MetaField[] = ['title', 'slug', 'excerpt', 'pillar', 'format', 'tags', 'series.id', 'series.title', 'series.part', 'series.total'];

/** `validation_failed` paths (`meta.title`, `meta.series.part`, `meta.tags.2`) as detail fields. */
export function metaFieldErrors(fields: Readonly<Record<string, string>>): MetaErrors {
  const errors: MetaErrors = {};
  for (const [path, message] of Object.entries(fields)) {
    if (!path.startsWith('meta.')) continue;
    const name = path.slice('meta.'.length);
    const field = META_FIELDS.find((candidate) => name === candidate || name.startsWith(`${candidate}.`));
    if (field && !errors[field]) errors[field] = message;
  }
  return errors;
}

function serialize(editor: Editor): { markdown: string; warnings: ContentLossWarning[]; tooLarge: boolean } {
  try {
    const check = draftCodec().check({ frontmatter: null, doc: editor.getJSON() });
    return { markdown: check.markdown, warnings: check.warnings, tooLarge: false };
  } catch (error) {
    if (error instanceof DialectError && error.code === 'too-large') return { markdown: '', warnings: [], tooLarge: true };
    // The check itself failed: save what the editor writes, without the loss report.
    return { markdown: editor.getMarkdown(), warnings: [], tooLarge: false };
  }
}

export class DraftSaver {
  private readonly deps: SaverDeps;
  private readonly listeners = new Set<() => void>();
  private snapshot: SaverSnapshot;
  private handlers: SaverHandlers | null = null;
  private base: Base | null = null;
  private sent: Sent | null = null;
  private metaDraft: MetaDraft;
  private editSeq = 0;
  private savedSeq = 0;
  private inFlight: Promise<boolean> | null = null;
  private again = false;
  private paused: Pause | null = null;
  private quietTimer: number | undefined;
  private maxTimer: number | undefined;
  private retryTimer: number | undefined;
  private retries = 0;
  private loadToken = 0;

  constructor(deps: SaverDeps, initialMeta: ArticleMeta | undefined) {
    this.deps = deps;
    this.metaDraft = draftFromMeta(initialMeta);
    this.snapshot = {
      loadedVersion: null,
      loadProblem: null,
      status: { kind: 'idle' },
      dirty: false,
      meta: this.metaDraft,
      serverErrors: {},
      lossWarnings: [],
      saveFindings: [],
      conflictMine: undefined,
      resolving: false,
    };
  }

  // ---- React's view -----------------------------------------------------------------------

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getSnapshot = (): SaverSnapshot => this.snapshot;

  private update(patch: Partial<SaverSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }

  connect(handlers: SaverHandlers): void {
    this.handlers = handlers;
  }

  private get unsaved(): boolean {
    return this.editSeq !== this.savedSeq;
  }

  private get destroyed(): boolean {
    return this.deps.editor.isDestroyed;
  }

  private etag(): string | undefined {
    return this.deps.queryClient.getQueryData<VersionedArticle>(draftKeys.article(this.deps.articleId))?.etag ?? undefined;
  }

  readonly currentMarkdown = (): string => (this.destroyed ? '' : this.deps.editor.getMarkdown());

  // ---- Loading ----------------------------------------------------------------------------

  /** A version of the article arrived (first load, a refetch, an event): load it when it is newer and nothing is unsaved. */
  receive(article: Article): void {
    const base = this.base;
    if (base && article.version <= base.version) return;
    if (base && (this.unsaved || this.inFlight)) return;
    void this.load(article);
  }

  /** Loads `source` into the editor (parsed in the worker) and makes it the session's base. */
  async load(source: Article): Promise<void> {
    this.loadToken += 1;
    const token = this.loadToken;
    const bytes = utf8ByteLength(source.markdown);
    if (bytes > EDITOR_MAX_BYTES) {
      this.update({ loadProblem: { kind: 'too-large', bytes } });
      return;
    }
    const { editor, env } = this.deps;
    try {
      // An empty body needs no parser: it is one empty paragraph (the schema's minimum).
      const parsed =
        source.markdown.trim() === ''
          ? { frontmatter: null, doc: { type: 'doc', content: [{ type: 'paragraph' }] } }
          : await env.dialect.parse(source.markdown, { timeoutMs: env.timings.parseTimeoutMs });
      if (token !== this.loadToken || this.destroyed) return;
      const anchor = editor.isFocused ? editor.state.selection.from : null;
      editor
        .chain()
        .command(({ tr }) => {
          tr.setMeta('addToHistory', false);
          return true;
        })
        .setContent(parsed.doc, { emitUpdate: false, errorOnInvalidContent: true })
        .run();
      // Keep the cursor near where it was; a first load starts at the top.
      editor.commands.setTextSelection(anchor === null ? 0 : Math.min(anchor, editor.state.doc.content.size));
      this.metaDraft = draftFromMeta(source.meta);
      this.base = { version: source.version, meta: source.meta };
      this.sent = { markdown: source.markdown, metaKey: metaKey(metaForSave(this.metaDraft, {}, source.meta)) };
      this.savedSeq = this.editSeq;
      this.paused = null;
      this.update({
        loadedVersion: source.version,
        loadProblem: null,
        meta: this.metaDraft,
        serverErrors: {},
        dirty: false,
        status: { kind: 'idle' },
      });
    } catch (error) {
      if (token !== this.loadToken) return;
      this.update({
        loadProblem:
          error instanceof DialectError
            ? { kind: 'dialect', code: error.code, message: error.message }
            : { kind: 'dialect', code: 'invalid-content', message: 'The article has content the editor can’t show.' },
      });
    }
  }

  /** Drops local changes and loads the server's version (after a read-only refusal, "Take theirs", a restore). */
  readonly reload = async (): Promise<void> => {
    this.clearTimers();
    const fresh = await this.handlers?.refetch();
    const item = fresh?.data?.item;
    if (!item) return;
    this.base = null;
    this.savedSeq = this.editSeq;
    this.paused = null;
    await this.load(item);
  };

  // ---- Editing ----------------------------------------------------------------------------

  /** The text changed (an `update` from the editor). */
  readonly noteEdit = (): void => {
    this.editSeq += 1;
    this.schedule();
  };

  readonly setMeta = (next: MetaDraft): void => {
    this.metaDraft = next;
    this.editSeq += 1;
    if (this.paused === 'invalid') {
      this.paused = null;
      this.update({ meta: next, serverErrors: {} });
    } else {
      this.update({ meta: next });
    }
    this.schedule();
  };

  private schedule(): void {
    if (this.paused) {
      this.update({ dirty: true });
      return;
    }
    this.update({ dirty: true, status: this.snapshot.status.kind === 'saving' ? this.snapshot.status : { kind: 'pending' } });
    const { autosaveMs, autosaveMaxWaitMs } = this.deps.env.timings;
    window.clearTimeout(this.quietTimer);
    this.quietTimer = window.setTimeout(() => void this.saveNow(), autosaveMs);
    this.maxTimer ??= window.setTimeout(() => void this.saveNow(), autosaveMaxWaitMs);
  }

  private clearTimers(): void {
    window.clearTimeout(this.quietTimer);
    window.clearTimeout(this.maxTimer);
    window.clearTimeout(this.retryTimer);
    this.quietTimer = undefined;
    this.maxTimer = undefined;
    this.retryTimer = undefined;
  }

  // ---- Saving -----------------------------------------------------------------------------

  /** Saves now, or right after the save that is running. */
  private async saveNow(): Promise<boolean> {
    window.clearTimeout(this.quietTimer);
    window.clearTimeout(this.maxTimer);
    this.quietTimer = undefined;
    this.maxTimer = undefined;
    if (this.inFlight) {
      this.again = true;
      return this.inFlight;
    }
    const run = this.saveOnce();
    this.inFlight = run;
    try {
      return await run;
    } finally {
      this.inFlight = null;
      if (this.again) {
        this.again = false;
        if (this.unsaved && !this.paused) void this.saveNow();
      }
    }
  }

  /** One save of what the editor and the details hold now. */
  private async saveOnce(override?: { etag: string; version: number }): Promise<boolean> {
    const base = this.base;
    if (!base || this.destroyed) return false;
    if (this.paused && !override) return false;
    const { editor, api, queryClient, articleId } = this.deps;
    const seq = this.editSeq;
    const { markdown, warnings, tooLarge } = serialize(editor);
    if (tooLarge || utf8ByteLength(markdown) > EDITOR_MAX_BYTES) {
      this.paused = 'too-large';
      this.update({ status: { kind: 'too-large' } });
      return false;
    }
    const details: ArticleMetaIn = metaForSave(this.metaDraft, validateMetaDraft(this.metaDraft), base.meta);
    const key = metaKey(details);
    if (!override && this.sent?.markdown === markdown && this.sent.metaKey === key) {
      this.savedSeq = seq;
      this.update({
        lossWarnings: warnings,
        dirty: this.unsaved,
        status: this.snapshot.status.kind === 'saved' ? this.snapshot.status : { kind: 'idle' },
      });
      return true;
    }
    const etag = override?.etag ?? this.etag();
    if (!etag) {
      this.update({ status: { kind: 'failed', error: new Error('missing etag') } });
      return false;
    }
    this.update({ status: { kind: 'saving' } });
    try {
      const saved = await apiRequest(api, 'put', '/api/articles/{article_id}', {
        params: { path: { article_id: articleId } },
        body: { meta: details, markdown, baseVersion: override?.version ?? base.version },
        headers: preconditionHeaders(etag),
      });
      this.base = { version: saved.item.version, meta: saved.item.meta };
      this.sent = { markdown, metaKey: key };
      this.savedSeq = seq;
      this.retries = 0;
      this.paused = null;
      // The answer becomes the cache: the next save already has the new etag.
      queryClient.setQueryData(draftKeys.article(articleId), saved);
      this.update({
        loadedVersion: saved.item.version,
        lossWarnings: warnings,
        saveFindings: saved.findings,
        serverErrors: {},
        dirty: this.unsaved,
        status: { kind: 'saved', at: saved.item.updatedAt },
      });
      if (this.unsaved) this.schedule();
      return true;
    } catch (error) {
      return this.failed(error);
    }
  }

  private failed(error: unknown): boolean {
    if (isEtagMismatch(error)) {
      this.paused = 'conflict';
      this.update({ status: { kind: 'conflict' }, conflictMine: this.currentMarkdown() });
      this.handlers?.onConflict(error);
      void this.handlers?.refetch();
      return false;
    }
    if (isProblem(error, 'read_only_state')) {
      this.paused = 'read-only';
      this.update({ status: { kind: 'read-only', state: readOnlyState(error)?.state } });
      void this.handlers?.refetch();
      return false;
    }
    if (isProblem(error, 'markdown_too_large')) {
      this.paused = 'too-large';
      this.update({ status: { kind: 'too-large' } });
      return false;
    }
    if (isProblem(error, 'validation_failed')) {
      this.paused = 'invalid';
      this.update({ status: { kind: 'invalid' }, serverErrors: metaFieldErrors(fieldErrors(error)) });
      return false;
    }
    if (error instanceof ApiError && error.kind === 'network') {
      this.update({ status: { kind: 'offline' } });
      return false;
    }
    const wait = this.deps.env.timings.retryMs[this.retries];
    if (error instanceof ApiError && error.transient && wait !== undefined) {
      this.retries += 1;
      this.update({ status: { kind: 'retrying', error } });
      this.retryTimer = window.setTimeout(() => void this.saveNow(), wait);
      return false;
    }
    this.update({ status: { kind: 'failed', error } });
    return false;
  }

  /** Saves pending edits; resolves `true` once everything is saved (actions call it first). */
  readonly flush = async (): Promise<boolean> => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (this.inFlight) await this.inFlight;
      if (this.paused) return false;
      if (!this.unsaved) return true;
      if (!(await this.saveNow())) return false;
    }
    return !this.unsaved;
  };

  /** Tries again after a failed or paused save (never past a conflict: the dialog decides that). */
  readonly retry = (): void => {
    if (this.paused === 'conflict') return;
    this.paused = null;
    this.retries = 0;
    void this.saveNow();
  };

  /** Back online: send what waited. */
  readonly online = (): void => {
    if (this.unsaved && !this.paused) void this.saveNow();
  };

  /** True while there is something the server does not have. */
  readonly hasUnsaved = (): boolean => this.unsaved;

  // ---- Conflicts --------------------------------------------------------------------------

  /** "Keep mine": save the owner's version over the current one, with its etag and version. */
  async keepMine(conflict: ConflictInfo | undefined): Promise<boolean> {
    this.update({ resolving: true });
    try {
      let etag = conflict?.etag;
      let version = conflict?.version;
      if (etag === undefined || version === undefined) {
        const fresh = await this.handlers?.refetch();
        etag = fresh?.data?.etag ?? undefined;
        version = fresh?.data?.item.version;
      }
      if (etag === undefined || version === undefined) return false;
      this.paused = null;
      const ok = await this.saveOnce({ etag, version });
      if (ok) this.update({ conflictMine: undefined });
      return ok;
    } finally {
      this.update({ resolving: false });
    }
  }

  /** "Take theirs": drop the owner's changes and load the saved version. */
  async takeTheirs(): Promise<void> {
    this.update({ resolving: true });
    try {
      await this.reload();
      this.update({ conflictMine: undefined });
    } finally {
      this.update({ resolving: false });
    }
  }

  // ---- Leaving ----------------------------------------------------------------------------

  /** The tab is going away: send what is unsaved, once, and stop the timers. */
  readonly leave = (): void => {
    this.clearTimers();
    const base = this.base;
    if (!base || this.paused || !this.unsaved || this.inFlight || this.destroyed) return;
    const { editor, api, queryClient, articleId } = this.deps;
    const { markdown, tooLarge } = serialize(editor);
    const etag = this.etag();
    if (tooLarge || !etag) return;
    const details = metaForSave(this.metaDraft, validateMetaDraft(this.metaDraft), base.meta);
    this.savedSeq = this.editSeq;
    void apiRequest(api, 'put', '/api/articles/{article_id}', {
      params: { path: { article_id: articleId } },
      body: { meta: details, markdown, baseVersion: base.version },
      headers: preconditionHeaders(etag),
    }).then(
      (saved) => {
        queryClient.setQueryData(draftKeys.article(articleId), saved);
      },
      () => {
        // The tab is gone; the next visit loads the last saved version.
      },
    );
  };
}
