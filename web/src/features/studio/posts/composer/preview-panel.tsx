import { Monitor, Smartphone } from 'lucide-react';
import { useState } from 'react';

import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';

import { FeedCard, type FeedAuthor, type FeedMedia, type FeedWidth } from '../components/feed-card';

export interface PreviewPanelProps {
  text: string;
  /** The phone fold from the server's lint for this text, when it has answered. */
  foldIndex?: number;
  hashtags: readonly string[];
  author: FeedAuthor;
  media: FeedMedia | null;
}

/**
 * The feed card at a phone's width or a desktop feed's, with the fold marked. The card sits on
 * the sunken paper, as a post sits in the feed.
 */
export function PreviewPanel({ text, foldIndex, hashtags, author, media }: PreviewPanelProps) {
  const [width, setWidth] = useState<FeedWidth>('mobile');
  return (
    <div className="grid gap-4">
      <SegmentedControl
        aria-label="Preview width"
        value={width}
        onValueChange={(value) => {
          if (value === 'mobile' || value === 'desktop') setWidth(value);
        }}
        fullWidth
      >
        <SegmentedControlItem value="mobile">
          <Smartphone aria-hidden="true" />
          Phone
        </SegmentedControlItem>
        <SegmentedControlItem value="desktop">
          <Monitor aria-hidden="true" />
          Desktop
        </SegmentedControlItem>
      </SegmentedControl>
      <div className="flex justify-center rounded-xl bg-sunken px-3 py-5 shadow-[inset_0_0_0_1px_var(--color-line)]">
        <FeedCard
          key={width}
          text={text}
          width={width}
          foldIndex={foldIndex}
          hashtags={hashtags}
          author={author}
          media={media}
        />
      </div>
      <p className="text-sm text-ink-muted">
        {width === 'mobile'
          ? 'The phone fold is the one the lint checks: about 140 characters or three lines.'
          : 'Desktop shows a little more before “see more”; the phone fold is the stricter one.'}
      </p>
    </div>
  );
}
