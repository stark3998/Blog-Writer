import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { cn } from '@/lib/cn';
import { readProjectFile } from '@/test/files';

import { Badge } from './badge';
import { Button } from './button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from './card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from './dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './dropdown-menu';
import { Input } from './input';
import { Kbd } from './kbd';
import { ScrollArea } from './scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './select';
import { Separator } from './separator';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from './sheet';
import { Skeleton } from './skeleton';
import { Switch } from './switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './tabs';
import { Textarea } from './textarea';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip';

describe('cn', () => {
  it('lets the last conflicting class win, including custom tokens', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4');
    expect(cn('text-ink-muted', 'text-ink')).toBe('text-ink');
    expect(cn('text-sm', 'text-ink')).toBe('text-sm text-ink');
    expect(cn('w-sidebar', 'w-rail')).toBe('w-rail');
    expect(cn('shadow-overlay', 'shadow-none')).toBe('shadow-none');
    expect(cn('max-w-measure', 'max-w-page')).toBe('max-w-page');
  });
});

describe('Button', () => {
  it('is a plain button by default, never a form submit by accident', () => {
    render(<Button>Approve</Button>);
    const button = screen.getByRole('button', { name: 'Approve' });
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveClass('bg-ink', 'text-ink-inverse');
  });

  it('has variants and sizes, with larger targets on touch screens', () => {
    render(
      <Button variant="secondary" size="lg">
        Later
      </Button>,
    );
    expect(screen.getByRole('button', { name: 'Later' })).toHaveClass('border-line-strong', 'h-12');
    render(<Button size="icon" aria-label="Close" />);
    expect(screen.getByRole('button', { name: 'Close' })).toHaveClass('size-10', 'pointer-coarse:size-11');
  });

  it('renders its child with button styles when asChild is set', () => {
    render(
      <Button asChild>
        <a href="/today">Open Today</a>
      </Button>,
    );
    const link = screen.getByRole('link', { name: 'Open Today' });
    expect(link).toHaveClass('bg-ink');
    expect(link).not.toHaveAttribute('type');
  });
});

describe('Badge', () => {
  it('pairs each pillar colour with its name', () => {
    render(<Badge variant="identity">Identity modernization</Badge>);
    expect(screen.getByText('Identity modernization')).toHaveClass('bg-identity-soft', 'text-identity');
  });
});

describe('Card', () => {
  it('gives its title a real heading', () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Plan for next week</CardTitle>
          <CardDescription>Ready since Sunday 18:00</CardDescription>
        </CardHeader>
        <CardContent>Three posts and the flagship.</CardContent>
        <CardFooter>
          <Button>Approve</Button>
        </CardFooter>
      </Card>,
    );
    expect(screen.getByRole('heading', { level: 3, name: 'Plan for next week' })).toBeInTheDocument();
  });
});

describe('Dialog', () => {
  it('opens with a title and description, traps focus and returns it on close', async () => {
    const user = userEvent.setup();
    render(
      <Dialog>
        <DialogTrigger asChild>
          <Button>Discard draft</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Discard this draft?</DialogTitle>
            <DialogDescription>The research and interview stay.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="danger">Discard</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>,
    );
    const trigger = screen.getByRole('button', { name: 'Discard draft' });
    await user.click(trigger);
    const dialog = await screen.findByRole('dialog', { name: 'Discard this draft?' });
    expect(dialog).toHaveAccessibleDescription('The research and interview stay.');
    expect(document.body).toHaveAttribute('data-scroll-locked');

    // Focus starts inside and Tab cycles through the dialog's controls only, both ways.
    const discard = screen.getByRole('button', { name: 'Discard' });
    const close = screen.getByRole('button', { name: 'Close' });
    expect(discard).toHaveFocus();
    const order: (Element | null)[] = [];
    for (const keys of ['{Tab}', '{Tab}', '{Shift>}{Tab}{/Shift}', '{Shift>}{Tab}{/Shift}']) {
      await user.keyboard(keys);
      order.push(document.activeElement);
    }
    expect(order).toEqual([close, discard, close, discard]);
    expect(trigger).not.toHaveFocus();

    await user.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(trigger).toHaveFocus();
    });
  });
});

describe('Sheet', () => {
  it('slides in as a labelled dialog', async () => {
    const user = userEvent.setup();
    render(
      <Sheet>
        <SheetTrigger asChild>
          <Button>Sources</Button>
        </SheetTrigger>
        <SheetContent side="bottom">
          <SheetHeader>
            <SheetTitle>Sources</SheetTitle>
            <SheetDescription>Everything this brief cites.</SheetDescription>
          </SheetHeader>
        </SheetContent>
      </Sheet>,
    );
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    const sheet = await screen.findByRole('dialog', { name: 'Sources' });
    expect(sheet).toHaveClass('bottom-0', 'data-[state=open]:animate-slide-in-bottom');
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});

describe('DropdownMenu', () => {
  function ModeMenu() {
    const [mode, setMode] = useState('normal');
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button>Mode: {mode}</Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>Availability</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={mode} onValueChange={setMode}>
            <DropdownMenuRadioItem value="normal">Normal</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="away">Away</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="danger">Reset</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  it('opens from the keyboard and picks a radio item', async () => {
    const user = userEvent.setup();
    render(<ModeMenu />);
    screen.getByRole('button', { name: 'Mode: normal' }).focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('menu')).toBeInTheDocument();
    expect(screen.getByRole('menuitemradio', { name: 'Normal' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('menuitem', { name: 'Reset' })).toHaveClass('text-danger');
    await user.click(screen.getByRole('menuitemradio', { name: 'Away' }));
    expect(screen.getByRole('button', { name: 'Mode: away' })).toBeInTheDocument();
  });
});

describe('Tabs', () => {
  it('moves selection with the arrow keys', async () => {
    const user = userEvent.setup();
    render(
      <Tabs defaultValue="brief">
        <TabsList aria-label="Stages">
          <TabsTrigger value="brief">Brief</TabsTrigger>
          <TabsTrigger value="draft">Draft</TabsTrigger>
        </TabsList>
        <TabsContent value="brief">The brief</TabsContent>
        <TabsContent value="draft">The draft</TabsContent>
      </Tabs>,
    );
    screen.getByRole('tab', { name: 'Brief' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Draft' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('The draft');
  });
});

describe('Tooltip', () => {
  it('names an icon button on hover and on keyboard focus', async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button size="icon" aria-label="Snooze 30 minutes" />
          </TooltipTrigger>
          <TooltipContent>Snooze 30 minutes</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    await user.tab();
    expect(await screen.findByRole('tooltip', { name: 'Snooze 30 minutes' })).toBeInTheDocument();
  });
});

describe('form controls', () => {
  it('Switch toggles and reports its state', async () => {
    const user = userEvent.setup();
    render(
      <label>
        <Switch /> Pause all autonomy
      </label>,
    );
    const toggle = screen.getByRole('switch', { name: 'Pause all autonomy' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });

  it('Input and Textarea use 16px text and flag invalid values', () => {
    render(
      <>
        <Input aria-label="Post URL" aria-invalid="true" />
        <Textarea aria-label="Final text" />
      </>,
    );
    expect(screen.getByRole('textbox', { name: 'Post URL' })).toHaveClass('text-base', 'aria-invalid:border-danger');
    expect(screen.getByRole('textbox', { name: 'Final text' })).toHaveClass('text-base', 'field-sizing-content');
  });
});

describe('small parts', () => {
  it('Separator is decorative unless told otherwise', () => {
    const { container } = render(
      <>
        <Separator />
        <Separator decorative={false} orientation="vertical" />
      </>,
    );
    expect(container.querySelector('[data-slot="separator"][role="none"]')).not.toBeNull();
    expect(screen.getByRole('separator')).toHaveAttribute('aria-orientation', 'vertical');
  });

  it('Skeleton is hidden from assistive technology', () => {
    const { container } = render(<Skeleton className="h-4" />);
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
  });

  it('ScrollArea and Kbd render their content', () => {
    render(
      <ScrollArea className="h-20">
        <p>Long list</p>
        <Kbd>⌘K</Kbd>
      </ScrollArea>,
    );
    expect(screen.getByText('Long list')).toBeInTheDocument();
    expect(screen.getByText('⌘K').tagName).toBe('KBD');
  });
});

describe('styles under the CSP (style-src self blocks style tags added at runtime)', () => {
  /** Declarations that only ever mattered to browsers the app does not support. */
  const OBSOLETE = new Set(['-ms-overflow-style', '-webkit-overflow-scrolling']);

  /** `selector -> declarations` for each innermost `selector { … }` block, comments removed. */
  function rules(css: string): Map<string, Set<string>> {
    const found = new Map<string, Set<string>>();
    for (const [, selector = '', body = ''] of css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const declarations = body
        .split(';')
        .map((declaration) => declaration.replace(/\s*:\s*/, ':').trim().toLowerCase())
        .filter((declaration) => declaration !== '');
      found.set(selector.trim(), new Set(declarations));
    }
    return found;
  }

  it('ships the rules Radix ScrollArea injects, so the native scrollbar stays hidden', () => {
    const { container } = render(
      <ScrollArea className="h-20">
        <p>Long list</p>
      </ScrollArea>,
    );
    const injected = rules([...container.querySelectorAll('style')].map((style) => style.textContent).join('\n'));
    expect(injected.size).toBeGreaterThan(0);

    const shipped = rules(readProjectFile('src/styles/index.css'));
    for (const [selector, declarations] of injected) {
      const ours = shipped.get(selector);
      expect(ours, `index.css has no "${selector}" rule`).toBeDefined();
      for (const declaration of declarations) {
        if (OBSOLETE.has(declaration.split(':', 1)[0] ?? '')) continue;
        expect(ours?.has(declaration), `${selector} { ${declaration} }`).toBe(true);
      }
    }
  });

  it('ships the rules Radix Select injects for its list, so the native scrollbar stays hidden there too', () => {
    render(
      <Select open>
        <SelectTrigger aria-label="Pillar">
          <SelectValue placeholder="Choose" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="agents">AI agents & security</SelectItem>
        </SelectContent>
      </Select>,
    );
    // The list renders in a portal, so its style tag is outside the render container.
    const injected = rules([...document.querySelectorAll('style')].map((style) => style.textContent).join('\n'));
    const selectRules = [...injected].filter(([selector]) => selector.includes('data-radix-select-viewport'));
    expect(selectRules.length).toBeGreaterThan(0);

    const shipped = rules(readProjectFile('src/styles/index.css'));
    for (const [selector, declarations] of selectRules) {
      const ours = shipped.get(selector);
      expect(ours, `index.css has no "${selector}" rule`).toBeDefined();
      for (const declaration of declarations) {
        if (OBSOLETE.has(declaration.split(':', 1)[0] ?? '')) continue;
        expect(ours?.has(declaration), `${selector} { ${declaration} }`).toBe(true);
      }
    }
  });

  it('ships the scroll lock Radix dialogs inject', () => {
    const shipped = rules(readProjectFile('src/styles/index.css'));
    expect(shipped.get('body[data-scroll-locked]')).toEqual(new Set(['overflow:hidden', 'overscroll-behavior:contain']));
  });
});
