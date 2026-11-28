import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from './accordion';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from './alert-dialog';
import { Button } from './button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './collapsible';
import { Popover, PopoverClose, PopoverContent, PopoverTrigger } from './popover';

describe('Popover', () => {
  it('opens from its trigger as a labelled panel and gives focus back on Escape', async () => {
    const user = userEvent.setup();
    render(
      <Popover>
        <PopoverTrigger asChild>
          <Button>Rewrite</Button>
        </PopoverTrigger>
        <PopoverContent aria-label="Proposal">
          <p>The proposal</p>
          <PopoverClose asChild>
            <Button variant="secondary">Discard</Button>
          </PopoverClose>
        </PopoverContent>
      </Popover>,
    );
    const trigger = screen.getByRole('button', { name: 'Rewrite' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await user.click(trigger);
    const panel = await screen.findByRole('dialog', { name: 'Proposal' });
    expect(panel).toHaveClass('bg-raised', 'shadow-overlay');
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(trigger).toHaveFocus();
  });

  it('closes from a close button inside', async () => {
    const user = userEvent.setup();
    render(
      <Popover defaultOpen>
        <PopoverTrigger>Open</PopoverTrigger>
        <PopoverContent aria-label="Proposal">
          <PopoverClose>Discard</PopoverClose>
        </PopoverContent>
      </Popover>,
    );
    await user.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});

describe('AlertDialog', () => {
  function UnpublishDialog({ onUnpublish }: { onUnpublish: () => void }) {
    return (
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button variant="danger">Unpublish</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Unpublish this article?</AlertDialogTitle>
            <AlertDialogDescription>It comes off your site within a few minutes.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it live</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={onUnpublish}>
              Unpublish
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    );
  }

  it('asks with a named, described alert dialog and starts on the safe answer', async () => {
    const user = userEvent.setup();
    render(<UnpublishDialog onUnpublish={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Unpublish' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Unpublish this article?' });
    expect(dialog).toHaveAccessibleDescription('It comes off your site within a few minutes.');
    expect(screen.getByRole('button', { name: 'Keep it live' })).toHaveFocus();
  });

  it('acts on its action and closes; Escape answers with cancel', async () => {
    const user = userEvent.setup();
    const onUnpublish = vi.fn();
    render(<UnpublishDialog onUnpublish={onUnpublish} />);
    const trigger = screen.getByRole('button', { name: 'Unpublish' });

    await user.click(trigger);
    await screen.findByRole('alertdialog');
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
    expect(onUnpublish).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(trigger).toHaveFocus();
    });

    await user.click(trigger);
    const dialog = await screen.findByRole('alertdialog');
    const action = screen.getAllByRole('button', { name: 'Unpublish' }).find((button) => dialog.contains(button));
    if (!action) throw new Error('no action button in the dialog');
    expect(action).toHaveClass('bg-danger');
    await user.click(action);
    expect(onUnpublish).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
  });
});

describe('Accordion', () => {
  it('puts each trigger in a heading and opens one section at a time from the keyboard', async () => {
    const user = userEvent.setup();
    render(
      <Accordion type="single" collapsible>
        <AccordionItem value="facts">
          <AccordionTrigger headingLevel={4}>Fact-check</AccordionTrigger>
          <AccordionContent>Two claims need a second source.</AccordionContent>
        </AccordionItem>
        <AccordionItem value="links">
          <AccordionTrigger>Outbound links</AccordionTrigger>
          <AccordionContent>All links answer.</AccordionContent>
        </AccordionItem>
      </Accordion>,
    );
    expect(screen.getByRole('heading', { level: 4, name: 'Fact-check' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: 'Outbound links' })).toBeInTheDocument();
    const facts = screen.getByRole('button', { name: 'Fact-check' });
    const links = screen.getByRole('button', { name: 'Outbound links' });
    expect(facts).toHaveAttribute('aria-expanded', 'false');

    facts.focus();
    await user.keyboard('{Enter}');
    expect(facts).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('region', { name: 'Fact-check' })).toHaveTextContent('Two claims need a second source.');

    await user.keyboard('{ArrowDown}');
    expect(links).toHaveFocus();
    await user.keyboard(' ');
    expect(links).toHaveAttribute('aria-expanded', 'true');
    expect(facts).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('Collapsible', () => {
  it('shows and hides its content from a button that says whether it is open', async () => {
    const user = userEvent.setup();
    render(
      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="ghost">Show the 12 unchanged sources</Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <p>Unchanged sources</p>
        </CollapsibleContent>
      </Collapsible>,
    );
    const trigger = screen.getByRole('button', { name: 'Show the 12 unchanged sources' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Unchanged sources')).not.toBeInTheDocument();
    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const content = screen.getByText('Unchanged sources').closest('[data-slot="collapsible-content"]');
    expect(content).toHaveClass('data-[state=open]:animate-expand');
    expect(trigger).toHaveAttribute('aria-controls', content?.id);
  });
});
