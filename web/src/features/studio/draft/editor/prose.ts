/**
 * The manuscript's look, as static class names on the editor element (Tailwind sees them here;
 * no style tag is ever injected, so the strict CSP holds). The editor runs with
 * `injectCSS: false`; the first group copies ProseMirror's own required rules.
 *
 * The text is set like a proof: Newsreader at reading size on a single measure, section heads
 * in the same face, code and tables in the tool's faces.
 */
const PROSEMIRROR_BASE = [
  'relative whitespace-break-spaces break-words outline-none',
  '[font-variant-ligatures:none] [font-feature-settings:"liga"_0]',
  '[&_pre]:whitespace-pre-wrap',
  '[&_[contenteditable=false]]:whitespace-normal [&_[contenteditable=false]_[contenteditable=true]]:whitespace-pre-wrap',
  '[&_img.ProseMirror-separator]:inline! [&_img.ProseMirror-separator]:m-0! [&_img.ProseMirror-separator]:size-0! [&_img.ProseMirror-separator]:border-0!',
  '[&_.ProseMirror-gapcursor]:pointer-events-none [&_.ProseMirror-gapcursor]:absolute [&_.ProseMirror-gapcursor]:hidden',
  '[&.ProseMirror-focused_.ProseMirror-gapcursor]:block',
  "[&_.ProseMirror-gapcursor]:after:absolute [&_.ProseMirror-gapcursor]:after:-top-0.5 [&_.ProseMirror-gapcursor]:after:block [&_.ProseMirror-gapcursor]:after:w-5 [&_.ProseMirror-gapcursor]:after:border-t [&_.ProseMirror-gapcursor]:after:border-ink [&_.ProseMirror-gapcursor]:after:content-['']",
  '[&.ProseMirror-hideselection]:caret-transparent [&.ProseMirror-hideselection]:selection:bg-transparent',
  '[&_.ProseMirror-selectednode]:rounded-sm [&_.ProseMirror-selectednode]:outline-2 [&_.ProseMirror-selectednode]:outline-offset-2 [&_.ProseMirror-selectednode]:outline-focus',
  '[&_.bw-selection]:bg-highlight',
];

const MANUSCRIPT = [
  'min-h-[50dvh] font-serif text-lg leading-prose text-ink md:text-xl md:leading-prose',
  // Preflight zeroes block margins; blocks are spaced from the one before. Diagrams and callouts get more room.
  '[&>*+*]:mt-5 [&>*+figure]:mt-8 [&>figure+*]:mt-8 [&>*+aside]:mt-8 [&>aside+*]:mt-8 [&>*:first-child]:mt-0',
  // Section heads: the article's title is the page's h1, so the body starts at h2.
  '[&_h1]:mt-12 [&_h1]:text-3xl [&_h1]:font-medium [&_h1]:leading-tight',
  '[&_h2]:mt-12 [&_h2]:text-2xl [&_h2]:font-medium [&_h2]:leading-snug [&_h2]:tracking-tight md:[&_h2]:text-3xl',
  '[&_h3]:mt-10 [&_h3]:text-xl [&_h3]:font-semibold [&_h3]:leading-snug md:[&_h3]:text-2xl',
  '[&_h4]:mt-8 [&_h4]:text-lg [&_h4]:font-semibold md:[&_h4]:text-xl',
  // Lists and checklists.
  '[&_ul]:list-disc [&_ul]:pl-6 [&_ol]:list-decimal [&_ol]:pl-7 [&_li]:my-1.5 [&_li]:pl-1 [&_li>p]:my-0 [&_li>*+*]:mt-2',
  '[&_li::marker]:text-ink-subtle',
  '[&_ul[data-type=taskList]]:list-none [&_ul[data-type=taskList]]:pl-0',
  '[&_li[data-type=taskItem]]:flex [&_li[data-type=taskItem]]:items-start [&_li[data-type=taskItem]]:gap-3 [&_li[data-type=taskItem]]:pl-0',
  '[&_li[data-type=taskItem]>label]:mt-[0.45em] [&_li[data-type=taskItem]>label]:flex [&_li[data-type=taskItem]>label]:shrink-0',
  '[&_li[data-type=taskItem]_input]:size-4 [&_li[data-type=taskItem]_input]:accent-ink',
  '[&_li[data-type=taskItem]>div]:min-w-0 [&_li[data-type=taskItem]>div]:flex-1',
  '[&_li[data-checked=true]>div]:text-ink-muted [&_li[data-checked=true]>div]:line-through [&_li[data-checked=true]>div]:decoration-line-strong',
  // Quotes, rules, links and images.
  '[&_blockquote]:border-l-2 [&_blockquote]:border-ink [&_blockquote]:pl-5 [&_blockquote]:italic [&_blockquote]:text-ink-muted',
  '[&_hr]:my-10 [&_hr]:border-line-strong',
  '[&_a]:underline [&_a]:decoration-line-strong [&_a]:underline-offset-4 [&_a:hover]:decoration-ink',
  '[&_p_img]:inline-block [&_p_img]:max-h-[32rem] [&_p_img]:max-w-full [&_p_img]:rounded-md [&_p_img]:border [&_p_img]:border-line [&_p_img]:align-top',
  // Code: inline, blocks and lowlight's token classes.
  '[&_code]:rounded-sm [&_code]:bg-sunken [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em]',
  '[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-line [&_pre]:bg-sunken [&_pre]:px-4 [&_pre]:py-3 [&_pre]:font-mono [&_pre]:text-sm [&_pre]:leading-relaxed',
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-sm',
  '[&_.hljs-comment]:italic [&_.hljs-comment]:text-ink-subtle [&_.hljs-quote]:italic [&_.hljs-quote]:text-ink-subtle',
  '[&_.hljs-keyword]:font-semibold [&_.hljs-selector-tag]:font-semibold [&_.hljs-section]:font-semibold',
  '[&_.hljs-string]:text-success [&_.hljs-regexp]:text-success [&_.hljs-addition]:text-success',
  '[&_.hljs-number]:text-warn [&_.hljs-literal]:text-warn [&_.hljs-symbol]:text-warn',
  '[&_.hljs-title]:text-agents [&_.hljs-built_in]:text-identity [&_.hljs-type]:text-identity',
  '[&_.hljs-attr]:text-governance [&_.hljs-attribute]:text-governance [&_.hljs-meta]:text-ink-subtle',
  '[&_.hljs-deletion]:text-danger',
  // Tables: compact rows in the tool's face.
  '[&_.tableWrapper]:overflow-x-auto',
  '[&_table]:w-full [&_table]:border-collapse [&_table]:font-sans [&_table]:text-base [&_table]:leading-normal',
  '[&_th]:border-b-2 [&_th]:border-ink [&_th]:px-2 [&_th]:py-2 [&_th]:text-left [&_th]:align-bottom [&_th]:font-semibold',
  '[&_td]:border-b [&_td]:border-line [&_td]:px-2 [&_td]:py-2 [&_td]:align-top',
  '[&_td>p]:my-0 [&_th>p]:my-0 [&_.selectedCell]:bg-wash-strong',
  // The sources list (citation definitions), always last.
  '[&_section.footnotes]:mt-12 [&_section.footnotes]:border-t [&_section.footnotes]:border-line [&_section.footnotes]:pt-4',
  '[&_section.footnotes]:font-sans [&_section.footnotes]:text-sm [&_section.footnotes]:leading-normal [&_section.footnotes]:text-ink-muted',
  "[&_section.footnotes]:before:mb-2 [&_section.footnotes]:before:block [&_section.footnotes]:before:font-semibold [&_section.footnotes]:before:text-ink [&_section.footnotes]:before:content-['Sources']",
  '[&_section.footnotes_ol]:list-none [&_section.footnotes_ol]:pl-0 [&_section.footnotes_li]:my-1.5',
  '[&_li[data-footnote]]:before:mr-2 [&_li[data-footnote]]:before:rounded-sm [&_li[data-footnote]]:before:bg-sunken [&_li[data-footnote]]:before:px-1 [&_li[data-footnote]]:before:font-mono [&_li[data-footnote]]:before:text-xs [&_li[data-footnote]]:before:text-ink-subtle [&_li[data-footnote]]:before:content-[attr(data-footnote)]',
  // Placeholders (only while editable).
  '[&_.is-editor-empty:first-child]:before:pointer-events-none [&_.is-editor-empty:first-child]:before:float-left [&_.is-editor-empty:first-child]:before:h-0 [&_.is-editor-empty:first-child]:before:text-ink-subtle [&_.is-editor-empty:first-child]:before:content-[attr(data-placeholder)]',
  '[&_p.is-empty]:before:pointer-events-none [&_p.is-empty]:before:float-left [&_p.is-empty]:before:h-0 [&_p.is-empty]:before:text-ink-subtle [&_p.is-empty]:before:content-[attr(data-placeholder)]',
];

/** Class names for the editor element (`editorProps.attributes.class`). */
export const MANUSCRIPT_CLASS = [...PROSEMIRROR_BASE, ...MANUSCRIPT].join(' ');

/** Live checks marked in the text: a wavy underline by severity (the checks list says the same in words). */
export const FINDING_MARK_CLASS = {
  block: 'underline decoration-wavy decoration-2 decoration-danger underline-offset-4',
  warn: 'underline decoration-wavy decoration-2 decoration-warn underline-offset-4',
  info: 'underline decoration-dotted decoration-2 decoration-ink-subtle underline-offset-4',
} as const;

/** A span the owner jumped to ("Show in text"): marked like a highlighter pen. */
export const FLASH_CLASS = 'rounded-sm bg-highlight';
