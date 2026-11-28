import type { Editor } from '@tiptap/core';
import { useCallback, useEffect, useRef, useState } from 'react';

import { utf8ByteLength } from '@/features/editor';
import { apiRequest } from '@/lib/api/request';
import { useAppData } from '@/lib/app-data';

import { setFindingMarks, type MarkedFinding } from '../editor/finding-marks';
import { hasSpan, sortFindings } from '../model/gates';
import { locateSpan } from '../model/spans';
import type { ArticleMetaIn, Finding } from '../model/types';

/**
 * The live checks (`POST /api/gates/lint`): the deterministic part of the gates (dialect,
 * outbound links, owner signal, blocklist, verbatim budget, slop, standards ids) over the text
 * as it is in the editor, a moment after the owner stops typing. No AI, no network fetches.
 * Findings are underlined in the text.
 */

/** The server's body cap for lint (512 KiB, like a save). */
const LINT_MAX_BYTES = 512 * 1024;

export interface LiveLintState {
  /** A check is running (the last findings stay on screen meanwhile). */
  readonly checking: boolean;
  /** The last findings, or `undefined` before the first check. */
  readonly findings: readonly Finding[] | undefined;
  /** The Markdown those findings index (for "Show in text"). */
  readonly source: string | undefined;
  /** The last check failed. */
  readonly error: unknown;
}

export interface LiveLint extends LiveLintState {
  readonly recheck: () => void;
}

const INITIAL: LiveLintState = { checking: false, findings: undefined, source: undefined, error: undefined };

export interface LiveLintOptions {
  readonly editor: Editor;
  readonly enabled: boolean;
  readonly meta: ArticleMetaIn;
  readonly metaKey: string;
  readonly briefId: string | null | undefined;
  readonly delayMs: number;
}

export function useLiveLint({ editor, enabled, meta, metaKey, briefId, delayMs }: LiveLintOptions): LiveLint {
  const { api } = useAppData();
  const [state, setState] = useState<LiveLintState>(INITIAL);
  const [revision, setRevision] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const metaRef = useRef(meta);

  useEffect(() => {
    metaRef.current = meta;
  }, [meta]);

  // A new revision for every change of the text.
  useEffect(() => {
    const onUpdate = () => {
      setRevision((current) => current + 1);
    };
    editor.on('update', onUpdate);
    return () => {
      editor.off('update', onUpdate);
    };
  }, [editor]);

  const check = useCallback(async () => {
    abort.current?.abort();
    const gone = () => editor.isDestroyed;
    if (gone()) return;
    const markdown = editor.getMarkdown();
    if (utf8ByteLength(markdown) > LINT_MAX_BYTES) return;
    const controller = new AbortController();
    abort.current = controller;
    setState((previous) => ({ ...previous, checking: true }));
    try {
      const result = await apiRequest(api, 'post', '/api/gates/lint', {
        body: { meta: metaRef.current, markdown, ...(briefId ? { briefId } : {}) },
        signal: controller.signal,
      });
      if (controller.signal.aborted || gone()) return;
      const findings = sortFindings(result.findings);
      const doc = editor.state.doc;
      const marks: MarkedFinding[] = findings.flatMap((finding, index) => {
        if (!hasSpan(finding)) return [];
        const range = locateSpan(doc, markdown, finding.start, finding.end);
        return range ? [{ ...range, severity: finding.severity, id: `lint-${String(index)}` }] : [];
      });
      setFindingMarks(editor, marks);
      setState({ checking: false, findings, source: markdown, error: undefined });
    } catch (error) {
      if (controller.signal.aborted) return;
      setState((previous) => ({ ...previous, checking: false, error }));
    }
  }, [api, briefId, editor]);

  // Check a moment after the text or the details stop changing.
  useEffect(() => {
    if (!enabled) return undefined;
    const timer = window.setTimeout(() => {
      void check();
    }, delayMs);
    return () => {
      window.clearTimeout(timer);
    };
  }, [enabled, revision, metaKey, delayMs, check]);

  // Leaving edit mode clears the marks; the gates report speaks for read-only text.
  useEffect(() => {
    if (enabled) return undefined;
    abort.current?.abort();
    if (!editor.isDestroyed) setFindingMarks(editor, []);
    return undefined;
  }, [enabled, editor]);

  useEffect(
    () => () => {
      abort.current?.abort();
    },
    [],
  );

  const recheck = useCallback(() => {
    void check();
  }, [check]);

  return { ...state, recheck };
}
