import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { describe, expect, it, vi } from 'vitest';

import { ApiError, kindForStatus, parseProblem } from '@/lib/api/errors';

import { conflictAuthor, ConflictDialog, describeConflict, useConflict } from './conflict-dialog';

const CONFLICT = { etag: '"9"', updatedAt: '2026-11-26T15:04:05.123456Z', updatedBy: 'pipeline', version: 4 };

function stale(current?: Record<string, unknown>): ApiError {
  const problem = parseProblem({ status: 412, code: 'etag_mismatch', ...(current ? { current } : {}) });
  return new ApiError({ kind: kindForStatus(412, 'etag_mismatch'), status: 412, problem });
}

describe('ConflictDialog', () => {
  function renderDialog(overrides: Partial<Parameters<typeof ConflictDialog>[0]> = {}) {
    const props = {
      open: true,
      onOpenChange: vi.fn(),
      conflict: CONFLICT,
      subject: 'This draft',
      mine: 'Intro.\n\nMy new paragraph.',
      theirs: 'Intro.\n\nThe pipeline’s paragraph.',
      onKeepMine: vi.fn(),
      onTakeTheirs: vi.fn(),
      ...overrides,
    };
    render(<ConflictDialog {...props} />);
    return props;
  }

  it('says who changed it and when, shows the difference and starts on the answer that loses nothing', async () => {
    renderDialog();
    const dialog = await screen.findByRole('alertdialog', { name: 'This draft changed since you opened it' });
    expect(dialog).toHaveAccessibleDescription(
      `${describeConflict(CONFLICT)} Keep mine saves your version over it. Take theirs loads the saved version and drops your changes.`,
    );
    expect(describeConflict(CONFLICT)).toMatch(/^The pipeline saved a newer version at .+\.$/);
    const diff = within(dialog).getByRole('group', { name: 'Your version compared with the saved one' });
    expect(diff).toHaveTextContent('1 paragraph changed.');
    await waitFor(() => {
      expect(within(dialog).getByRole('button', { name: 'Copy mine' })).toHaveFocus();
    });
  });

  it('keeps mine, takes theirs or copies mine, and waits while saving', async () => {
    const user = userEvent.setup();
    const success = vi.spyOn(toast, 'success').mockReturnValue(1);
    const props = renderDialog();
    await user.click(await screen.findByRole('button', { name: 'Keep mine' }));
    expect(props.onKeepMine).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Take theirs' }));
    expect(props.onTakeTheirs).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Copy mine' }));
    await waitFor(() => {
      expect(success).toHaveBeenCalledWith('Copied your version.', {});
    });
    await expect(navigator.clipboard.readText()).resolves.toBe('Intro.\n\nMy new paragraph.');
    // The dialog decides nothing by itself: it is still open.
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  });

  it('disables both answers while "Keep mine" saves, and closes only through the owner', async () => {
    const user = userEvent.setup();
    const props = renderDialog({ saving: true, mine: undefined, theirs: undefined, conflict: {} });
    const dialog = await screen.findByRole('alertdialog');
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Take theirs' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Copy mine' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('group')).not.toBeInTheDocument();
    expect(dialog).toHaveAccessibleDescription(/^A newer version was saved\. /);
    await user.keyboard('{Escape}');
    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('names who saved it', () => {
    expect(conflictAuthor('owner')).toBe('You saved a newer version in another tab or on another device');
    expect(conflictAuthor('system')).toBe('The app saved a newer version');
    expect(conflictAuthor('someone-new')).toBe('A newer version was saved');
    expect(describeConflict({ updatedAt: 'not a date' })).toBe('A newer version was saved.');
  });
});

describe('useConflict', () => {
  it('opens for a 412 with what it says, and ignores other errors', () => {
    const { result } = renderHook(() => useConflict());
    expect(result.current.open).toBe(false);
    let shown = true;
    act(() => {
      shown = result.current.show(new ApiError({ kind: 'server', status: 500 }));
    });
    expect(shown).toBe(false);
    expect(result.current.open).toBe(false);

    act(() => {
      shown = result.current.show(stale(CONFLICT));
    });
    expect(shown).toBe(true);
    expect(result.current.open).toBe(true);
    expect(result.current.conflict).toEqual(CONFLICT);

    act(() => {
      result.current.close();
    });
    expect(result.current.open).toBe(false);
    expect(result.current.conflict).toEqual(CONFLICT);
  });
});
