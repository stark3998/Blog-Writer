/**
 * Highlighting findings in text. The server's spans (`Finding.start`/`end`) are code-point
 * offsets (Python string indexes); JavaScript strings index UTF-16 code units, so an emoji
 * before a match would shift every highlight. Everything here counts code points.
 */

export interface SpanLike {
  readonly start?: number | null;
  readonly end?: number | null;
}

export interface TextSegment {
  readonly text: string;
  /** Indexes (into the spans given) of the spans that cover this segment; empty when plain. */
  readonly spans: readonly number[];
}

/**
 * Splits `text` into runs of plain and marked text. Spans outside the text are clipped; spans
 * without both offsets are ignored (a finding about the whole text marks nothing).
 */
export function markSegments(text: string, spans: readonly SpanLike[]): TextSegment[] {
  const points = Array.from(text);
  const cover: number[][] = points.map(() => []);
  spans.forEach((span, index) => {
    if (typeof span.start !== 'number' || typeof span.end !== 'number') return;
    const start = Math.max(0, Math.trunc(span.start));
    const end = Math.min(points.length, Math.trunc(span.end));
    for (let position = start; position < end; position += 1) cover[position]?.push(index);
  });
  const segments: TextSegment[] = [];
  let run: string[] = [];
  let runSpans: number[] = [];
  const key = (list: readonly number[]) => list.join(',');
  points.forEach((point, position) => {
    const here = cover[position] ?? [];
    if (run.length > 0 && key(here) !== key(runSpans)) {
      segments.push({ text: run.join(''), spans: runSpans });
      run = [];
    }
    if (run.length === 0) runSpans = here;
    run.push(point);
  });
  if (run.length > 0) segments.push({ text: run.join(''), spans: runSpans });
  return segments;
}

/** The text a span covers (code points), for showing what a finding points at. */
export function spanText(text: string, span: SpanLike): string | undefined {
  if (typeof span.start !== 'number' || typeof span.end !== 'number' || span.end <= span.start) return undefined;
  return Array.from(text).slice(span.start, span.end).join('');
}

/** The number of characters a person would count (code points, not UTF-16 units). */
export function charCount(text: string): number {
  return Array.from(text).length;
}
