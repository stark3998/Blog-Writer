import { ArrowLeft, Plus } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';

import { useAngles, useOpenItems, useWatchlist, type ItemEntry } from './lib/queries';
import { OpenItems } from './open-items';
import { EMPTY_PREFILL, ReplyComposer, type ComposePrefill } from './reply-composer';
import { ReplyItem } from './reply-item';

export type ReplyView =
  | { readonly name: 'list' }
  | { readonly name: 'compose'; readonly prefill: ComposePrefill; readonly key: number }
  | { readonly name: 'item'; readonly itemId: string };

interface ReplyHelperProps {
  view: ReplyView;
  onViewChange: (view: ReplyView) => void;
  /** Drafts by item id, kept by the panel while Today is open. */
  drafts: Readonly<Record<string, string>>;
  /** `null` drops the draft (the item was saved or dismissed). */
  onDraftChange: (itemId: string, text: string | null) => void;
}

/**
 * The Reply tab: what is waiting for a reply, a new one (paste, screenshot or share), and one
 * item at a time. Saving a new item asks for angles at once.
 */
export function ReplyHelper({ view, onViewChange, drafts, onDraftChange }: ReplyHelperProps) {
  const items = useOpenItems();
  const watchlist = useWatchlist();
  const angles = useAngles();
  /** The latest answer for each item this session wrote, for when the list cannot be read. */
  const [known, setKnown] = useState<Readonly<Record<string, ItemEntry>>>({});
  const rootRef = useRef<HTMLDivElement>(null);
  const moved = useRef(false);

  const viewKey = view.name === 'item' ? `item:${view.itemId}` : view.name === 'compose' ? `compose:${String(view.key)}` : 'list';
  useEffect(() => {
    // After the owner moves between the list and an item, start reading at the new view.
    if (!moved.current) return;
    moved.current = false;
    rootRef.current?.focus({ preventScroll: false });
  }, [viewKey]);

  function go(next: ReplyView) {
    moved.current = true;
    onViewChange(next);
  }

  function remember(entry: ItemEntry) {
    setKnown((current) => ({ ...current, [entry.item.id]: entry }));
  }

  function requestAngles(itemId: string) {
    angles.mutate({ params: { path: { item_id: itemId } } }, { onSuccess: remember });
  }

  const voices = (watchlist.data ?? []).filter((entry) => entry.item.active);
  let body: ReactNode;

  if (view.name === 'compose') {
    body = (
      <ReplyComposer
        key={view.key}
        prefill={view.prefill}
        onCreated={(entry) => {
          remember(entry);
          go({ name: 'item', itemId: entry.item.id });
          requestAngles(entry.item.id);
        }}
        onCancel={() => {
          go({ name: 'list' });
        }}
      />
    );
  } else if (view.name === 'item') {
    const { itemId } = view;
    const entry = items.data?.find((candidate) => candidate.item.id === itemId) ?? known[itemId];
    const forThis = angles.variables?.params.path.item_id === itemId;
    body = entry ? (
      <ReplyItem
        key={itemId}
        entry={entry}
        draft={drafts[itemId] ?? ''}
        onDraftChange={(text) => {
          onDraftChange(itemId, text);
        }}
        angles={{
          pending: forThis && angles.isPending,
          error: forThis ? angles.error : null,
          run: () => {
            requestAngles(itemId);
          },
        }}
        voices={voices}
        onBack={() => {
          go({ name: 'list' });
        }}
        onFinished={() => {
          onDraftChange(itemId, null);
          go({ name: 'list' });
        }}
      />
    ) : (
      <div className="grid gap-4">
        <p className="text-sm text-ink-muted">
          {items.isPending ? 'Loading…' : 'This one isn’t here any more: it was saved, dismissed or expired.'}
        </p>
        <Button
          variant="secondary"
          className="w-fit"
          onClick={() => {
            go({ name: 'list' });
          }}
        >
          <ArrowLeft aria-hidden="true" />
          All replies
        </Button>
      </div>
    );
  } else {
    body = (
      <div className="grid gap-8">
        <div className="grid gap-3">
          <p className="text-sm text-ink-muted">
            Paste a comment, read your own screenshot, or share from the LinkedIn app. You get up to three angles drawn
            from your stories and positions, then write the reply yourself.
          </p>
          <Button
            className="w-fit"
            onClick={() => {
              go({ name: 'compose', prefill: EMPTY_PREFILL, key: Date.now() });
            }}
          >
            <Plus aria-hidden="true" />
            New reply
          </Button>
        </div>
        <section aria-labelledby="engage-waiting" className="grid gap-3">
          <h3 id="engage-waiting" className="text-sm font-semibold text-ink">
            Waiting for a reply
          </h3>
          <OpenItems
            drafted={new Set(Object.keys(drafts).filter((id) => (drafts[id] ?? '').trim() !== ''))}
            onOpen={(entry) => {
              remember(entry);
              go({ name: 'item', itemId: entry.item.id });
            }}
          />
        </section>
      </div>
    );
  }

  return (
    <div ref={rootRef} tabIndex={-1} className="outline-none">
      {body}
    </div>
  );
}
