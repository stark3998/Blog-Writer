import { codePointOffsets, findingSpan, type TextSpan } from '@/features/intake';
import type { ConfidentialFinding } from '@/lib/api/problems';

/**
 * The owner's own text with the spans a `confidential_text` refusal points at marked (the
 * server never names the term; the owner sees where it is in what they wrote). The server counts
 * code points; `findingSpan` (the intake's conversion) turns them into string offsets, so an
 * emoji before the term does not shift the mark.
 */
export function MarkedText({ text, findings, label }: { text: string; findings: readonly ConfidentialFinding[]; label: string }) {
  const offsets = codePointOffsets(text);
  const spans = findings
    .map((finding) => findingSpan(text, finding, offsets))
    .filter((span): span is TextSpan => span !== undefined)
    .sort((a, b) => a.start - b.start);
  if (spans.length === 0) return null;
  const parts: { text: string; marked: boolean }[] = [];
  let cursor = 0;
  for (const span of spans) {
    const start = Math.max(span.start, cursor);
    const end = Math.min(span.end, text.length);
    if (end <= start) continue;
    if (start > cursor) parts.push({ text: text.slice(cursor, start), marked: false });
    parts.push({ text: text.slice(start, end), marked: true });
    cursor = end;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), marked: false });
  return (
    <p className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-md border border-line bg-sunken px-3 py-2 text-sm text-ink-muted">
      <span className="sr-only">{`${label}, with the blocklisted part marked: `}</span>
      {parts.map((part, index) =>
        part.marked ? (
          <mark key={index} className="rounded-sm bg-highlight px-0.5 text-ink underline decoration-danger decoration-wavy">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </p>
  );
}
