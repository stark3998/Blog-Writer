import type { ReactNode } from 'react';

/** The id of the go-live page's heading: focus moves there when the post's state changes. */
export function goLiveHeadingId(postId: string): string {
  return `go-live-${postId}-heading`;
}

/**
 * The heading of a go-live panel ("Post at 09:00 today", "Posted on LinkedIn"). After an action
 * replaces the panel (the tap published, the kit was marked posted), focus lands here, so the
 * new state is read out and the next action is a tab away.
 */
export function PanelHeading({ postId, children }: { postId: string; children: ReactNode }) {
  return (
    <h2 id={goLiveHeadingId(postId)} tabIndex={-1} className="font-serif text-3xl font-medium text-ink outline-none">
      {children}
    </h2>
  );
}
