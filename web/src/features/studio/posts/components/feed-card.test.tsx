import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { DISPLAY_TEXT, HOOKS } from '../testing/fixtures';

import { FeedCard } from './feed-card';

const AUTHOR = { name: 'Alex Rivera', headline: 'Identity architect' };

describe('FeedCard', () => {
  it('marks the fold: the feed shows the text above it, then “see more”', () => {
    render(<FeedCard text={DISPLAY_TEXT} width="mobile" author={AUTHOR} hashtags={['Entra']} />);
    const card = screen.getByRole('figure', { name: 'LinkedIn preview, phone width' });
    const fold = within(card).getByRole('separator', { name: /The fold: the feed shows the text above/ });
    expect(fold).toBeInTheDocument();
    expect(within(card).getByText('Alex Rivera')).toBeInTheDocument();
    expect(within(card).getByText(HOOKS[0]?.text ?? '', { exact: false })).toBeInTheDocument();
    // The approved hashtag is set apart, as LinkedIn links it.
    expect(within(card).getByText('#Entra')).toHaveClass('font-semibold');
  });

  it('shows the post as the feed does, and opens it again with “see more”', async () => {
    const user = userEvent.setup();
    render(<FeedCard text={DISPLAY_TEXT} width="mobile" author={AUTHOR} />);
    await user.click(screen.getByRole('button', { name: 'Show it as the feed does' }));
    expect(screen.queryByRole('separator')).not.toBeInTheDocument();
    expect(screen.queryByText(/one dashboard everyone trusted/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show the whole post' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'see more' }));
    expect(screen.getByText(/one dashboard everyone trusted/)).toBeInTheDocument();
    expect(screen.getByRole('separator')).toBeInTheDocument();
  });

  it('cuts later on the desktop feed than on a phone', () => {
    const text = `${'word '.repeat(40)}end`;
    const { rerender } = render(<FeedCard text={text} width="mobile" author={AUTHOR} defaultCollapsed />);
    const phone = screen.getByRole('figure').textContent;
    rerender(<FeedCard key="desktop" text={text} width="desktop" author={AUTHOR} defaultCollapsed />);
    const desktop = screen.getByRole('figure', { name: 'LinkedIn preview, desktop width' }).textContent;
    expect(desktop.length).toBeGreaterThan(phone.length);
  });

  it('shows a short post whole, without a fold', () => {
    render(<FeedCard text="A short post." width="mobile" author={{ name: '' }} />);
    expect(screen.queryByRole('separator')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
  });

  it('shows the document’s first page, title and page count', () => {
    render(
      <FeedCard
        text="Hook."
        width="mobile"
        author={AUTHOR}
        media={{ kind: 'document', title: 'The wave plan', pageCount: 9, coverUrl: '/api/media/posts/x/a.png', coverAlt: 'Zero outages' }}
      />,
    );
    expect(screen.getByText('The wave plan')).toBeInTheDocument();
    expect(screen.getByText('9 pages')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Zero outages' })).toHaveAttribute('src', '/api/media/posts/x/a.png');
  });

  it('says when the media is not rendered yet', () => {
    render(<FeedCard text="Hook." width="mobile" author={AUTHOR} media={{ kind: 'image', url: null, alt: null }} />);
    expect(screen.getByText('The image shows here once it is rendered.')).toBeInTheDocument();
  });
});
