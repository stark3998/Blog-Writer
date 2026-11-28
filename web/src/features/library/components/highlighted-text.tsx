import { useMemo } from 'react';

import { cn } from '@/lib/cn';

import { markSegments, type SpanLike } from '../lib/spans';

export interface HighlightedTextProps {
  text: string;
  /** Findings with code-point spans; the ones without a span mark nothing. */
  spans: readonly (SpanLike & { readonly severity?: string })[];
  className?: string;
}

/**
 * Text with the passages findings point at marked, like a proof-reader's highlighter: the
 * `highlight` token behind the words and a wavy underline, so it never relies on colour alone.
 * Blocking findings get a red underline.
 */
export function HighlightedText({ text, spans, className }: HighlightedTextProps) {
  const segments = useMemo(() => markSegments(text, spans), [text, spans]);
  return (
    <p className={cn('whitespace-pre-wrap break-words', className)}>
      {segments.map((segment, index) => {
        if (segment.spans.length === 0) return <span key={index}>{segment.text}</span>;
        const blocking = segment.spans.some((spanIndex) => spans[spanIndex]?.severity === 'block');
        return (
          <mark
            key={index}
            className={cn(
              'rounded-[0.1875rem] bg-highlight text-ink underline decoration-wavy decoration-1 underline-offset-4',
              blocking ? 'decoration-danger' : 'decoration-warn',
            )}
          >
            {segment.text}
          </mark>
        );
      })}
    </p>
  );
}
