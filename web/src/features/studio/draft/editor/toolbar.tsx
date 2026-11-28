import { isNodeSelection, type Editor } from '@tiptap/core';
import { useEditorState } from '@tiptap/react';
import {
  BetweenHorizontalStart,
  BetweenVerticalStart,
  Bold,
  ChevronDown,
  Code,
  Italic,
  Link,
  List,
  ListChecks,
  ListOrdered,
  Plus,
  Redo2,
  Sparkles,
  Table,
  TextQuote,
  Trash2,
  Type,
  Undo2,
  type LucideIcon,
} from 'lucide-react';
import { Toolbar } from 'radix-ui';
import type { MouseEvent, ReactNode } from 'react';

import { buttonVariants } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { toggleVariants } from '@/components/ui/toggle-group';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/cn';
import { shortcutLabel } from '@/lib/platform';

import { AltTextPopover } from './alt-text-popover';
import type { EditorBus } from './bus';
import { BLOCK_COMMANDS, commandAvailable, type BlockCommandContext } from './commands';
import { LinkPopover } from './link-popover';

type BlockKind = 'paragraph' | 'heading-2' | 'heading-3' | 'heading-4' | 'other';

const BLOCK_LABELS: Readonly<Record<BlockKind, string>> = {
  paragraph: 'Text',
  'heading-2': 'Section heading',
  'heading-3': 'Subheading',
  'heading-4': 'Minor heading',
  other: 'Block',
};

interface ToolbarState {
  readonly editable: boolean;
  readonly block: BlockKind;
  readonly topLevel: boolean;
  readonly bold: boolean;
  readonly italic: boolean;
  readonly code: boolean;
  readonly link: boolean;
  readonly bulletList: boolean;
  readonly orderedList: boolean;
  readonly taskList: boolean;
  readonly blockquote: boolean;
  readonly inTable: boolean;
  readonly inCode: boolean;
  readonly image: boolean;
  readonly hasSelection: boolean;
  readonly canUndo: boolean;
  readonly canRedo: boolean;
}

function blockKind(editor: Editor): BlockKind {
  const parent = editor.state.selection.$from.parent;
  if (parent.type.name === 'paragraph') return 'paragraph';
  if (parent.type.name === 'heading') {
    const level: unknown = parent.attrs.level;
    if (level === 2 || level === 3 || level === 4) return `heading-${level}`;
  }
  return 'other';
}

function readState(editor: Editor): ToolbarState {
  const { selection } = editor.state;
  return {
    editable: editor.isEditable,
    block: blockKind(editor),
    topLevel: selection.$from.depth <= 1,
    bold: editor.isActive('bold'),
    italic: editor.isActive('italic'),
    code: editor.isActive('code'),
    link: editor.isActive('link'),
    bulletList: editor.isActive('bulletList'),
    orderedList: editor.isActive('orderedList'),
    taskList: editor.isActive('taskList'),
    blockquote: editor.isActive('blockquote'),
    inTable: editor.isActive('table'),
    inCode: selection.$from.parent.type.spec.code === true,
    image: isNodeSelection(selection) && selection.node.type.name === 'image',
    hasSelection: !selection.empty,
    canUndo: editor.can().undo(),
    canRedo: editor.can().redo(),
  };
}

/** Keeps the text's selection when a toolbar button is clicked with a pointer. */
function keepSelection(event: MouseEvent) {
  event.preventDefault();
}

interface ToolButtonProps {
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  pressed?: boolean;
  disabled?: boolean;
  shortcut?: string;
  children?: ReactNode;
}

function ToolButton({ label, icon: Icon, onClick, pressed, disabled, shortcut, children }: ToolButtonProps) {
  const button = (
    <Toolbar.Button
      aria-label={children ? undefined : label}
      aria-pressed={pressed}
      data-state={pressed ? 'on' : 'off'}
      disabled={disabled}
      onMouseDown={keepSelection}
      onClick={onClick}
      className={toggleVariants({ size: children ? 'sm' : 'icon' })}
    >
      <Icon aria-hidden="true" />
      {children}
    </Toolbar.Button>
  );
  if (children) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent>{shortcut ? `${label} (${shortcut})` : label}</TooltipContent>
    </Tooltip>
  );
}

export interface EditorToolbarProps {
  editor: Editor;
  bus: EditorBus;
  commands: BlockCommandContext;
  /** Opens the selection AI for the current selection. */
  onRewrite: () => void;
  className?: string;
}

/**
 * The formatting toolbar (an ARIA toolbar: one tab stop, arrow keys between buttons). Every
 * control acts on the text's own selection, which pointer clicks never take away.
 */
export function EditorToolbar({ editor, bus, commands, onRewrite, className }: EditorToolbarProps) {
  const state = useEditorState({ editor, selector: ({ editor: current }) => readState(current) });

  if (!state.editable) return null;
  const run = (action: (chain: ReturnType<Editor['chain']>) => ReturnType<Editor['chain']>) => {
    action(editor.chain().focus()).run();
  };
  const insertable = BLOCK_COMMANDS.filter((command) => command.id !== 'paragraph');

  return (
    <Toolbar.Root
      aria-label="Formatting"
      className={cn(
        'flex min-h-12 items-center gap-1 overflow-x-auto border-b border-line bg-sheet px-1 py-1 [scrollbar-width:none]',
        className,
      )}
    >
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Toolbar.Button
            onMouseDown={keepSelection}
            disabled={state.inCode}
            aria-label={`Text style: ${BLOCK_LABELS[state.block]}`}
            className={cn(toggleVariants({ size: 'sm' }), 'w-40 shrink-0 justify-between')}
          >
            <span className="flex items-center gap-2">
              <Type aria-hidden="true" />
              {BLOCK_LABELS[state.block]}
            </span>
            <ChevronDown aria-hidden="true" className="text-ink-subtle" />
          </Toolbar.Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuLabel>Text style</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={state.block}
            onValueChange={(value) => {
              if (value === 'paragraph') run((chain) => chain.setParagraph());
              else if (value === 'heading-2') run((chain) => chain.setHeading({ level: 2 }));
              else if (value === 'heading-3') run((chain) => chain.setHeading({ level: 3 }));
              else if (value === 'heading-4') run((chain) => chain.setHeading({ level: 4 }));
            }}
          >
            <DropdownMenuRadioItem value="paragraph">Text</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="heading-2" disabled={!state.topLevel}>
              Section heading
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="heading-3" disabled={!state.topLevel}>
              Subheading
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="heading-4" disabled={!state.topLevel}>
              Minor heading
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>

      <Toolbar.Separator className="mx-1 h-6 w-px shrink-0 bg-line" />

      <ToolButton
        label="Bold"
        icon={Bold}
        shortcut={shortcutLabel('B')}
        pressed={state.bold}
        disabled={state.inCode}
        onClick={() => {
          run((chain) => chain.toggleBold());
        }}
      />
      <ToolButton
        label="Italic"
        icon={Italic}
        shortcut={shortcutLabel('I')}
        pressed={state.italic}
        disabled={state.inCode}
        onClick={() => {
          run((chain) => chain.toggleItalic());
        }}
      />
      <ToolButton
        label="Inline code"
        icon={Code}
        shortcut={shortcutLabel('E')}
        pressed={state.code}
        disabled={state.inCode}
        onClick={() => {
          run((chain) => chain.toggleCode());
        }}
      />
      <LinkPopover editor={editor} bus={bus}>
        <Toolbar.Button
          aria-label={state.link ? 'Edit link' : 'Add link'}
          aria-pressed={state.link}
          data-state={state.link ? 'on' : 'off'}
          disabled={state.inCode}
          onMouseDown={keepSelection}
          className={toggleVariants({ size: 'icon' })}
        >
          <Link aria-hidden="true" />
        </Toolbar.Button>
      </LinkPopover>

      <Toolbar.Separator className="mx-1 h-6 w-px shrink-0 bg-line" />

      <ToolButton
        label="Bulleted list"
        icon={List}
        pressed={state.bulletList}
        disabled={state.inCode}
        onClick={() => {
          run((chain) => chain.toggleBulletList());
        }}
      />
      <ToolButton
        label="Numbered list"
        icon={ListOrdered}
        pressed={state.orderedList}
        disabled={state.inCode}
        onClick={() => {
          run((chain) => chain.toggleOrderedList());
        }}
      />
      <ToolButton
        label="Checklist"
        icon={ListChecks}
        pressed={state.taskList}
        disabled={state.inCode}
        onClick={() => {
          run((chain) => chain.toggleTaskList());
        }}
      />
      <ToolButton
        label="Quote"
        icon={TextQuote}
        pressed={state.blockquote}
        disabled={state.inCode}
        onClick={() => {
          run((chain) => chain.toggleBlockquote());
        }}
      />

      <Toolbar.Separator className="mx-1 h-6 w-px shrink-0 bg-line" />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Toolbar.Button
            onMouseDown={keepSelection}
            disabled={state.inCode}
            className={cn(toggleVariants({ size: 'sm' }), 'shrink-0')}
          >
            <Plus aria-hidden="true" />
            Insert
          </Toolbar.Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <DropdownMenuLabel>Insert a block</DropdownMenuLabel>
          {insertable.map((command) => {
            const Icon = command.icon;
            return (
              <DropdownMenuItem
                key={command.id}
                disabled={!commandAvailable(command, editor)}
                onSelect={() => {
                  command.run(editor, commands);
                }}
              >
                <Icon aria-hidden="true" />
                <span className="grid">
                  <span>{command.label}</span>
                  <span className="text-xs text-ink-subtle">{command.hint}</span>
                </span>
              </DropdownMenuItem>
            );
          })}
          <DropdownMenuSeparator />
          <p className="px-2.5 py-1.5 text-xs text-ink-subtle">Or type / in an empty line.</p>
        </DropdownMenuContent>
      </DropdownMenu>

      {state.inTable ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Toolbar.Button onMouseDown={keepSelection} className={cn(toggleVariants({ size: 'sm' }), 'shrink-0')}>
              <Table aria-hidden="true" />
              Table
              <ChevronDown aria-hidden="true" className="text-ink-subtle" />
            </Toolbar.Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onSelect={() => {
              run((chain) => chain.addRowAfter());
            }}>
              <BetweenHorizontalStart aria-hidden="true" />
              Add a row below
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => {
              run((chain) => chain.addColumnAfter());
            }}>
              <BetweenVerticalStart aria-hidden="true" />
              Add a column to the right
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => {
              run((chain) => chain.deleteRow());
            }}>Delete this row</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => {
              run((chain) => chain.deleteColumn());
            }}>Delete this column</DropdownMenuItem>
            <DropdownMenuItem variant="danger" onSelect={() => {
              run((chain) => chain.deleteTable());
            }}>
              <Trash2 aria-hidden="true" />
              Delete the table
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}

      {state.image ? <AltTextPopover editor={editor} /> : null}

      <Toolbar.Separator className="mx-1 h-6 w-px shrink-0 bg-line" />

      <Toolbar.Button
        onMouseDown={keepSelection}
        disabled={!state.hasSelection || state.inCode}
        onClick={onRewrite}
        className={cn(buttonVariants({ variant: 'ghost', size: 'sm' }), 'shrink-0 text-ink')}
      >
        <Sparkles aria-hidden="true" />
        Rewrite selection
        <DropdownMenuShortcut className="ml-1 hidden lg:inline">{shortcutLabel('⇧E')}</DropdownMenuShortcut>
      </Toolbar.Button>

      <div className="ml-auto flex shrink-0 items-center gap-1">
        <ToolButton
          label="Undo"
          icon={Undo2}
          shortcut={shortcutLabel('Z')}
          disabled={!state.canUndo}
          onClick={() => {
            run((chain) => chain.undo());
          }}
        />
        <ToolButton
          label="Redo"
          icon={Redo2}
          shortcut={shortcutLabel('⇧Z')}
          disabled={!state.canRedo}
          onClick={() => {
            run((chain) => chain.redo());
          }}
        />
      </div>
    </Toolbar.Root>
  );
}
