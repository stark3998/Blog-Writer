import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MarkedText } from './marked-text';

const finding = (start: number, end: number) => ({
  field: 'instruction',
  code: 'blocklisted-term',
  severity: 'block',
  message: 'A blocklisted term.',
  start,
  end,
});

describe('MarkedText', () => {
  it('marks the span the server points at, counting code points like the server', () => {
    // "🙂" is one code point but two UTF-16 units: the server's 2..9 is "Contoso".
    render(<MarkedText text="🙂 Contoso rocks" findings={[finding(2, 9)]} label="Your instruction" />);
    expect(screen.getByText('Contoso', { selector: 'mark' })).toBeInTheDocument();
  });

  it('shows nothing when no finding has a span in the text', () => {
    const { container } = render(<MarkedText text="Short" findings={[finding(10, 12)]} label="Your instruction" />);
    expect(container).toBeEmptyDOMElement();
  });
});
