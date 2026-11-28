import { useState } from 'react';

import { readSpec, sameSpec, type CarouselSpec } from '../model/slides';
import { hashtagsInText } from '../model/text';
import type { Post, PostEditIn, PostFormat, Versioned } from '../model/types';

/**
 * The fields of a post the composer edits as text (`PUT /api/posts/{id}`). Hashtags are not a
 * separate field: the approved hashtags that appear in the text are the post's hashtags, so the
 * text is the one source of truth (`editBody` derives the list).
 */
export interface TextDraft {
  readonly displayText: string;
  readonly selectedHookId: string | null;
  readonly pillar: string;
  readonly format: PostFormat;
}

export function textDraftOf(post: Post): TextDraft {
  return {
    displayText: post.displayText,
    selectedHookId: post.selectedHookId ?? null,
    pillar: post.pillar,
    format: post.format,
  };
}

export function sameText(a: TextDraft, b: TextDraft): boolean {
  return (
    a.displayText === b.displayText &&
    a.selectedHookId === b.selectedHookId &&
    a.pillar === b.pillar &&
    a.format === b.format
  );
}

/** At most three hashtags go out (`linkedin.lint.MAX_HASHTAGS`). */
export const MAX_HASHTAGS = 3;

/**
 * The owner's edit as `PUT /api/posts/{id}` takes it: the text, the hashtags it uses (approved
 * ones only, at most three), and the hook, pillar and format when they changed.
 */
export function editBody(draft: TextDraft, server: TextDraft, approvedHashtags: readonly string[]): PostEditIn {
  return {
    displayText: draft.displayText,
    hashtags: hashtagsInText(draft.displayText, approvedHashtags).slice(0, MAX_HASHTAGS),
    ...(draft.selectedHookId !== null && draft.selectedHookId !== server.selectedHookId
      ? { selectedHookId: draft.selectedHookId }
      : {}),
    ...(draft.pillar !== server.pillar ? { pillar: draft.pillar } : {}),
    ...(draft.format !== server.format ? { format: draft.format } : {}),
  };
}

interface Base {
  readonly etag: string | null;
  readonly text: TextDraft;
  readonly spec: CarouselSpec | undefined;
}

interface Local {
  readonly text: TextDraft;
  readonly spec: CarouselSpec | undefined;
  /** The server version the edits started from: saves send its etag, so a newer save is a 412. */
  readonly base: Base;
}

export interface PostDraft {
  /** What the composer shows: the owner's edits while there are any, else the server's copy. */
  readonly text: TextDraft;
  /** The server's copy of the text fields. */
  readonly serverText: TextDraft;
  readonly spec: CarouselSpec | undefined;
  readonly textDirty: boolean;
  readonly specDirty: boolean;
  readonly dirty: boolean;
  /** The etag a save must send (the version the edits started from). */
  readonly baseEtag: string | null;
  /** True when the server saved a newer version while the owner was editing. */
  readonly serverMoved: boolean;
  readonly editText: (update: (current: TextDraft) => TextDraft) => void;
  readonly editSpec: (update: (current: CarouselSpec | undefined) => CarouselSpec | undefined) => void;
  /** After the text saved: it is the new base, the slides may still be unsaved. */
  readonly textSaved: (etag: string | null) => void;
  /** Drops every edit: the composer follows the server again. */
  readonly reset: () => void;
  /** Keeps the edits but rebases them on `etag` ("Keep mine" after a conflict). */
  readonly rebase: (etag: string | null) => void;
}

/**
 * The composer's edits over the server's post. Nothing is copied while the owner is not
 * editing, so a refetch (an event after a job, another tab) shows at once; once the owner edits,
 * the edits win on screen and the save carries the etag they started from, so a newer server
 * version answers 412 and the conflict dialog decides (contract 1.4), never a silent overwrite.
 */
export function usePostDraft(versioned: Versioned<Post>): PostDraft {
  const server = textDraftOf(versioned.item);
  const serverSpec = readSpec(versioned.item.carouselSpec);
  const [local, setLocal] = useState<Local | null>(null);

  const textDirty = local !== null && !sameText(local.text, local.base.text);
  const specDirty = local !== null && !sameSpec(local.spec, local.base.spec);
  const dirty = textDirty || specDirty;

  const fresh = (): Local => {
    const base: Base = { etag: versioned.etag, text: server, spec: serverSpec };
    return { text: server, spec: serverSpec, base };
  };
  const started = (current: Local | null): Local => {
    if (current === null) return fresh();
    const changed = !sameText(current.text, current.base.text) || !sameSpec(current.spec, current.base.spec);
    return changed ? current : fresh();
  };

  return {
    text: dirty ? local.text : server,
    serverText: server,
    spec: dirty ? local.spec : serverSpec,
    textDirty,
    specDirty,
    dirty,
    baseEtag: dirty ? local.base.etag : versioned.etag,
    serverMoved: dirty && local.base.etag !== versioned.etag,
    editText: (update) => {
      setLocal((current) => {
        const start = started(current);
        return { ...start, text: update(start.text) };
      });
    },
    editSpec: (update) => {
      setLocal((current) => {
        const start = started(current);
        return { ...start, spec: update(start.spec) };
      });
    },
    textSaved: (etag) => {
      setLocal((current) =>
        current === null ? null : { ...current, base: { ...current.base, etag, text: current.text } },
      );
    },
    reset: () => {
      setLocal(null);
    },
    rebase: (etag) => {
      setLocal((current) => (current === null ? null : { ...current, base: { ...current.base, etag } }));
    },
  };
}
