import type { Editor } from '@tiptap/core';
import { useEditorState } from '@tiptap/react';
import { useId } from 'react';

import { cn } from '@/lib/cn';

import { revealRange } from '../editor/finding-marks';
import { outlineOf, sameOutline } from '../model/document';

const INDENT: Readonly<Record<number, string>> = {
  1: 'pl-0',
  2: 'pl-0',
  3: 'pl-4',
  4: 'pl-8',
};

/** The article's headings, as a way around it: choosing one goes to it in the text. */
export function OutlinePanel({ editor }: { editor: Editor }) {
  const headingId = useId();
  const outline = useEditorState({
    editor,
    selector: ({ editor: current }) => outlineOf(current.state.doc),
    equalityFn: sameOutline,
  });
  return (
    <nav aria-labelledby={headingId} className="grid gap-3">
      <h2 id={headingId} className="font-serif text-xl font-medium text-ink">
        Outline
      </h2>
      {outline.length === 0 ? (
        <p className="text-sm text-ink-muted">No headings yet. Section headings (level 2) appear here as you add them.</p>
      ) : (
        <ol className="grid gap-0.5 border-l border-line">
          {outline.map((entry) => (
            <li key={entry.pos} className={cn(INDENT[entry.level] ?? 'pl-8')}>
              <button
                type="button"
                className={cn(
                  '-ml-px w-full border-l-2 border-transparent py-1.5 pl-3 pr-2 text-left text-sm text-ink-muted',
                  'hover:border-ink hover:text-ink focus-visible:border-ink pointer-coarse:min-h-11',
                  entry.level <= 2 && 'font-medium text-ink',
                )}
                onClick={() => {
                  const node = editor.state.doc.nodeAt(entry.pos);
                  if (!node) return;
                  revealRange(editor, { from: entry.pos + 1, to: entry.pos + 1 + node.content.size });
                }}
              >
                {entry.text === '' ? <span className="italic text-ink-subtle">Empty heading</span> : entry.text}
              </button>
            </li>
          ))}
        </ol>
      )}
    </nav>
  );
}
