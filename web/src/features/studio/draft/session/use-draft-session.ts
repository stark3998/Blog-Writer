import type { Editor } from '@tiptap/core';
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { useConflict, type ConflictState } from '@/features/data/conflict-dialog';
import type { ContentLossWarning } from '@/features/editor';
import { useAppData } from '@/lib/app-data';

import type { DraftEnvironment } from '../environment';
import { validateMetaDraft, type MetaDraft, type MetaErrors } from '../model/meta';
import type { Article, Finding, VersionedArticle } from '../model/types';

import { DraftSaver, type LoadProblem, type SaveStatus } from './draft-saver';

export type { LoadProblem, SaveStatus } from './draft-saver';

export interface DraftSession {
  /** The version in the editor, `null` until the article has loaded. */
  readonly loadedVersion: number | null;
  readonly loadProblem: LoadProblem | null;
  readonly retryLoad: () => void;
  readonly status: SaveStatus;
  /** Unsaved changes in the editor or the details. */
  readonly dirty: boolean;
  readonly meta: MetaDraft;
  /** The dialect's rules on the details, plus the server's answer to the last save. */
  readonly metaErrors: MetaErrors;
  readonly setMeta: (next: MetaDraft) => void;
  readonly lossWarnings: readonly ContentLossWarning[];
  readonly saveFindings: readonly Finding[];
  /** Saves now; resolves `true` once everything is saved (actions call it before they run). */
  readonly flush: () => Promise<boolean>;
  readonly retrySave: () => void;
  /** Drops local changes and loads the server's version. */
  readonly reload: () => Promise<void>;
  /** The editor's text as Markdown, now. */
  readonly currentMarkdown: () => string;
  readonly conflict: ConflictState;
  readonly conflictMine: string | undefined;
  readonly keepMine: () => void;
  readonly takeTheirs: () => void;
  readonly resolving: boolean;
}

export interface DraftSessionOptions {
  readonly articleId: string;
  readonly article: Article | undefined;
  readonly refetch: () => Promise<{ data?: VersionedArticle | undefined }>;
  readonly editor: Editor;
  readonly env: DraftEnvironment;
}

/** The editing session of one article (`DraftSaver`), wired to React and the browser. */
export function useDraftSession({ articleId, article, refetch, editor, env }: DraftSessionOptions): DraftSession {
  const { api, queryClient } = useAppData();
  const conflict = useConflict();
  const [saver] = useState(() => new DraftSaver({ editor, api, queryClient, articleId, env }, article?.meta));
  const snapshot = useSyncExternalStore(saver.subscribe, saver.getSnapshot, saver.getSnapshot);
  const showConflict = conflict.show;

  useEffect(() => {
    saver.connect({
      refetch,
      onConflict: (error) => {
        showConflict(error);
      },
    });
  }, [saver, refetch, showConflict]);

  useEffect(() => {
    if (article) saver.receive(article);
  }, [saver, article]);

  useEffect(() => {
    const onUpdate = () => {
      saver.noteEdit();
    };
    editor.on('update', onUpdate);
    return () => {
      editor.off('update', onUpdate);
    };
  }, [saver, editor]);

  useEffect(() => {
    const onOnline = () => {
      saver.online();
    };
    // Warn before the page closes with unsaved changes.
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (saver.hasUnsaved()) event.preventDefault();
    };
    window.addEventListener('online', onOnline);
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [saver]);

  // Leaving the tab (another Studio tab, another page) sends what is unsaved.
  useEffect(() => saver.leave, [saver]);

  const metaErrors = useMemo(
    () => ({ ...validateMetaDraft(snapshot.meta), ...snapshot.serverErrors }),
    [snapshot.meta, snapshot.serverErrors],
  );
  const closeConflict = conflict.close;
  const conflictInfo = conflict.conflict;

  return {
    loadedVersion: snapshot.loadedVersion,
    loadProblem: snapshot.loadProblem,
    retryLoad: () => {
      if (article) void saver.load(article);
    },
    status: snapshot.status,
    dirty: snapshot.dirty,
    meta: snapshot.meta,
    metaErrors,
    setMeta: saver.setMeta,
    lossWarnings: snapshot.lossWarnings,
    saveFindings: snapshot.saveFindings,
    flush: saver.flush,
    retrySave: saver.retry,
    reload: saver.reload,
    currentMarkdown: saver.currentMarkdown,
    conflict,
    conflictMine: snapshot.conflictMine,
    keepMine: () => {
      void saver.keepMine(conflictInfo).then((ok) => {
        if (ok) closeConflict();
      });
    },
    takeTheirs: () => {
      void saver.takeTheirs().then(closeConflict);
    },
    resolving: snapshot.resolving,
  };
}
