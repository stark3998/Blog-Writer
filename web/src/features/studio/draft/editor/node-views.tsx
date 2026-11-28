import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { NodeViewContent, NodeViewWrapper, useEditorState, type ReactNodeViewProps } from '@tiptap/react';
import { ChevronDown, Info, Lightbulb, TriangleAlert, Workflow, type LucideIcon } from 'lucide-react';
import { createContext, use, useEffect, useId, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { CALLOUT_TYPES, isCalloutType, type CalloutType } from '@/features/editor';
import { ApiError } from '@/lib/api/errors';
import { problemMessage } from '@/lib/api/problems';
import { useAppData } from '@/lib/app-data';
import { cn } from '@/lib/cn';

import { draftKeys } from '../api/queries';
import { isRenderUnavailable, MERMAID_PREVIEW_MAX_CHARS, renderMermaid, sourceHash, svgImageSrc } from '../api/render-mermaid';
import { useDraftEnvironment } from '../environment';
import type { BriefCitation } from '../model/types';

import type { MermaidViewOptions } from './extensions';

/**
 * React views for the dialect nodes that need more than their HTML: diagrams (source and a
 * server-rendered preview), callouts (a type picker) and citations (the brief source they
 * point at). They render inside `EditorContent`, so the tab's contexts reach them.
 */

export interface DraftNodeContextValue {
  /** The brief's sources by id. */
  readonly citations: ReadonlyMap<string, BriefCitation>;
  /** False until the brief has loaded (or when there is none): citations are not judged then. */
  readonly briefReady: boolean;
}

export const DraftNodeContext = createContext<DraftNodeContextValue>({ citations: new Map(), briefReady: false });

function useEditable(editor: ReactNodeViewProps['editor']): boolean {
  return useEditorState({ editor, selector: ({ editor: current }) => current.isEditable });
}

/** `value`, once it has stopped changing for `ms`. */
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSettled(value);
    }, ms);
    return () => {
      window.clearTimeout(timer);
    };
  }, [value, ms]);
  return settled;
}

function DiagramPreview({ source, enabled }: { source: string; enabled: boolean }) {
  const { api } = useAppData();
  const { timings } = useDraftEnvironment();
  const settled = useSettled(source, timings.diagramMs);
  const trimmed = settled.trim();
  const tooLong = settled.length > MERMAID_PREVIEW_MAX_CHARS;
  const render = useQuery({
    queryKey: draftKeys.diagram(sourceHash(settled)),
    queryFn: ({ signal }) => renderMermaid(api, settled, signal),
    enabled: enabled && trimmed !== '' && !tooLong,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 10 * 60_000,
    placeholderData: keepPreviousData,
    retry: (count, error) => error instanceof ApiError && error.transient && !isRenderUnavailable(error) && count < 2,
  });
  const image = useMemo(() => (render.data ? svgImageSrc(render.data.svg) : undefined), [render.data]);

  if (!enabled) {
    return <p className="text-sm text-ink-muted">The diagram renders once the pipeline has finished writing.</p>;
  }
  if (trimmed === '') {
    return <p className="text-sm text-ink-muted">Write the diagram’s Mermaid source below; the preview follows.</p>;
  }
  if (tooLong) {
    return (
      <p className="text-sm text-ink-muted">
        {`Diagrams are limited to ${MERMAID_PREVIEW_MAX_CHARS.toLocaleString('en-US')} characters, so this one can’t be drawn. Shorten it.`}
      </p>
    );
  }
  if (render.isError && !render.data) {
    return isRenderUnavailable(render.error) ? (
      <p className="text-sm text-ink-muted">
        Live diagram previews aren’t available on this server right now. Rendering is off or its browser is missing.
      </p>
    ) : (
      <p role="alert" className="flex items-start gap-2 text-sm text-danger">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <span>{`The diagram didn’t render: ${problemMessage(render.error)}`}</span>
      </p>
    );
  }
  if (!image) {
    return (
      <div className="grid gap-2">
        <Skeleton className="h-32 w-full" />
        <p className="text-sm text-ink-subtle">Rendering the diagram…</p>
      </div>
    );
  }
  const updating = render.isFetching || settled !== source;
  return (
    <div className="grid gap-2">
      <img
        src={image}
        alt="Rendered preview of the diagram"
        className={cn('mx-auto max-h-[28rem] max-w-full transition-opacity', updating && 'opacity-60')}
        draggable={false}
      />
      {updating ? <p className="text-xs text-ink-subtle">Updating the preview…</p> : null}
    </div>
  );
}

export function MermaidNodeView({ node, editor, extension }: ReactNodeViewProps) {
  const editable = useEditable(editor);
  const [showSource, setShowSource] = useState(false);
  const sourceId = useId();
  const options = extension.options as Partial<MermaidViewOptions>;
  const sourceVisible = editable || showSource;
  return (
    <NodeViewWrapper as="figure" data-type="mermaid" className="rounded-lg border border-line bg-sheet font-sans">
      <figcaption
        contentEditable={false}
        className="flex select-none items-center justify-between gap-3 border-b border-line px-3 py-2 text-sm"
      >
        <span className="flex items-center gap-2 font-medium text-ink">
          <Workflow aria-hidden="true" className="size-4 text-ink-subtle" />
          Diagram
        </span>
        {editable ? (
          <span className="text-xs text-ink-subtle">Mermaid source below</span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={showSource}
            aria-controls={sourceId}
            onClick={() => {
              setShowSource((shown) => !shown);
            }}
          >
            {showSource ? 'Hide source' : 'Show source'}
          </Button>
        )}
      </figcaption>
      <div contentEditable={false} className="px-4 py-4">
        <DiagramPreview source={node.textContent} enabled={options.renderDiagrams ?? true} />
      </div>
      <div id={sourceId} className={cn('border-t border-line', !sourceVisible && 'hidden')}>
        <pre className="m-0! rounded-none! border-0! bg-sunken">
          <NodeViewContent<'code'> as="code" aria-label="Mermaid source" />
        </pre>
      </div>
    </NodeViewWrapper>
  );
}

const CALLOUT_STYLES: Readonly<Record<CalloutType, { label: string; icon: LucideIcon; className: string; iconClass: string }>> = {
  note: { label: 'Note', icon: Info, className: 'border-ink bg-sunken', iconClass: 'text-ink' },
  tip: { label: 'Tip', icon: Lightbulb, className: 'border-success bg-success-soft', iconClass: 'text-success' },
  warning: { label: 'Warning', icon: TriangleAlert, className: 'border-warn bg-warn-soft', iconClass: 'text-warn' },
};

export function CalloutNodeView({ node, editor, updateAttributes }: ReactNodeViewProps) {
  const editable = useEditable(editor);
  const raw: unknown = node.attrs.type;
  const type: CalloutType = typeof raw === 'string' && isCalloutType(raw) ? raw : 'note';
  const style = CALLOUT_STYLES[type];
  const Icon = style.icon;
  return (
    <NodeViewWrapper as="aside" data-callout={type} className={cn('rounded-r-lg border-l-4 px-4 py-3', style.className)}>
      <div contentEditable={false} className="mb-1.5 flex select-none items-center gap-2 font-sans text-sm font-semibold text-ink">
        <Icon aria-hidden="true" className={cn('size-4', style.iconClass)} />
        {editable ? (
          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label={`Callout type: ${style.label}`}
              className="inline-flex items-center gap-1 rounded-sm px-1 hover:bg-wash pointer-coarse:min-h-11"
            >
              {style.label}
              <ChevronDown aria-hidden="true" className="size-3.5 text-ink-subtle" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuLabel>Callout type</DropdownMenuLabel>
              <DropdownMenuRadioGroup
                value={type}
                onValueChange={(value) => {
                  if (isCalloutType(value)) updateAttributes({ type: value });
                }}
              >
                {CALLOUT_TYPES.map((option) => (
                  <DropdownMenuRadioItem key={option} value={option}>
                    {CALLOUT_STYLES[option].label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <span>{style.label}</span>
        )}
      </div>
      <NodeViewContent className="[&>*]:my-0 [&>*+*]:mt-3" />
    </NodeViewWrapper>
  );
}

export function CitationNodeView({ node, selected }: ReactNodeViewProps) {
  const { citations, briefReady } = use(DraftNodeContext);
  const raw: unknown = node.attrs.id;
  const id = typeof raw === 'string' ? raw : '';
  const source = citations.get(id);
  const unknown = briefReady && !source;
  const label = source ? (source.publisher?.trim() ?? '') || source.domain : id;
  const description = source
    ? `Citation: ${source.title}, ${source.domain}`
    : unknown
      ? `Citation ${id}: not a source in the brief`
      : `Citation ${id}`;
  return (
    <NodeViewWrapper as="span" data-citation={id} className="whitespace-nowrap">
      <sup
        aria-label={description}
        title={source ? `${source.title} (${source.domain})` : description}
        className={cn(
          'mx-0.5 inline-flex items-center gap-1 rounded-sm px-1 py-px font-sans text-xs font-medium not-italic',
          unknown ? 'bg-warn-soft text-warn' : 'bg-sunken text-ink-muted ring-1 ring-inset ring-line',
          selected && 'outline-2 outline-offset-1 outline-focus',
        )}
      >
        {unknown ? <TriangleAlert aria-hidden="true" className="size-3" /> : null}
        {label}
      </sup>
    </NodeViewWrapper>
  );
}
