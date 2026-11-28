import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { Checkbox } from './checkbox';
import { Field, Fieldset, focusFirstInvalid } from './field';
import { Input } from './input';
import { Label } from './label';
import { RadioGroup, RadioGroupItem } from './radio-group';
import { SegmentedControl, SegmentedControlItem } from './segmented-control';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from './select';
import { Switch } from './switch';
import { Textarea } from './textarea';
import { ToggleGroup, ToggleGroupItem } from './toggle-group';

describe('Label and Checkbox', () => {
  it('names the checkbox, toggles from the label and from Space, and shows the mixed state', async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(
      <div className="flex items-center gap-2">
        <Checkbox id="agents" onCheckedChange={onCheckedChange} />
        <Label htmlFor="agents">AI agents & security</Label>
        <Checkbox id="all" aria-label="All pillars" checked="indeterminate" />
      </div>,
    );
    const box = screen.getByRole('checkbox', { name: 'AI agents & security' });
    expect(box).toHaveAttribute('aria-checked', 'false');
    await user.click(screen.getByText('AI agents & security'));
    expect(box).toHaveAttribute('aria-checked', 'true');
    expect(box).toHaveClass('data-[state=checked]:bg-ink', 'pointer-coarse:after:-inset-3');
    box.focus();
    await user.keyboard(' ');
    expect(box).toHaveAttribute('aria-checked', 'false');
    expect(onCheckedChange).toHaveBeenLastCalledWith(false);
    expect(screen.getByRole('checkbox', { name: 'All pillars' })).toHaveAttribute('aria-checked', 'mixed');
  });
});

describe('RadioGroup', () => {
  it('is a named group whose arrow keys move the choice', async () => {
    const user = userEvent.setup();
    render(
      <RadioGroup aria-label="Interview" defaultValue="micro">
        <div className="flex items-center gap-2">
          <RadioGroupItem value="micro" id="micro" />
          <Label htmlFor="micro">Micro</Label>
        </div>
        <div className="flex items-center gap-2">
          <RadioGroupItem value="full" id="full" />
          <Label htmlFor="full">Full</Label>
        </div>
      </RadioGroup>,
    );
    const group = screen.getByRole('radiogroup', { name: 'Interview' });
    const micro = within(group).getByRole('radio', { name: 'Micro' });
    const full = within(group).getByRole('radio', { name: 'Full' });
    expect(micro).toHaveAttribute('aria-checked', 'true');
    micro.focus();
    // Radix moves focus a tick after keydown and checks the item focus lands on while the arrow
    // is held, so hold it until focus has moved (a person's keyup always comes later).
    await user.keyboard('{ArrowDown>}');
    await waitFor(() => {
      expect(full).toHaveFocus();
    });
    await user.keyboard('{/ArrowDown}');
    expect(full).toHaveAttribute('aria-checked', 'true');
    expect(micro).toHaveAttribute('aria-checked', 'false');
  });
});

describe('Select', () => {
  function PillarSelect({ onValueChange }: { onValueChange: (value: string) => void }) {
    const [value, setValue] = useState<string | undefined>(undefined);
    return (
      <>
        <Label htmlFor="pillar">Pillar</Label>
        <Select
          value={value}
          onValueChange={(next) => {
            setValue(next);
            onValueChange(next);
          }}
        >
          <SelectTrigger id="pillar">
            <SelectValue placeholder="Choose a pillar" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectLabel>Pillars</SelectLabel>
              <SelectItem value="agents">AI agents & security</SelectItem>
              <SelectItem value="identity">Identity modernization</SelectItem>
              <SelectItem value="governance">AI governance</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
      </>
    );
  }

  it('opens from the keyboard, picks an option and shows it in the named trigger', async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<PillarSelect onValueChange={onValueChange} />);
    const trigger = screen.getByRole('combobox', { name: 'Pillar' });
    expect(trigger).toHaveTextContent('Choose a pillar');
    expect(trigger).toHaveClass('text-base', 'pointer-coarse:h-11');

    trigger.focus();
    await user.keyboard('{Enter}');
    const listbox = await screen.findByRole('listbox');
    expect(within(listbox).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'AI agents & security',
      'Identity modernization',
      'AI governance',
    ]);
    await user.keyboard('{ArrowDown}{Enter}');
    await waitFor(() => {
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    });
    expect(onValueChange).toHaveBeenCalledExactlyOnceWith('identity');
    expect(trigger).toHaveTextContent('Identity modernization');
    expect(trigger).toHaveFocus();
  });

  it('closes with Escape without changing the value', async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<PillarSelect onValueChange={onValueChange} />);
    const trigger = screen.getByRole('combobox', { name: 'Pillar' });
    trigger.focus();
    await user.keyboard('{ArrowDown}');
    expect(await screen.findByRole('listbox')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    });
    expect(onValueChange).not.toHaveBeenCalled();
    expect(trigger).toHaveTextContent('Choose a pillar');
  });
});

describe('Field', () => {
  it('wires the label, the hint and the error to the control', () => {
    render(
      <Field label="Post URL" description="Copy it from the post’s menu on LinkedIn." error="Paste a LinkedIn post link." required>
        <Input type="url" />
      </Field>,
    );
    const input = screen.getByRole('textbox', { name: 'Post URL' });
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAttribute('aria-required', 'true');
    expect(input).toHaveAccessibleDescription('Copy it from the post’s menu on LinkedIn. Paste a LinkedIn post link.');
    // The visible "(required)" is for sighted users; screen readers get aria-required instead.
    expect(screen.getByText('(required)')).toHaveAttribute('aria-hidden', 'true');
  });

  it('describes nothing that is not there, and keeps an id given to the field', () => {
    render(
      <Field label="Final text" id="final-text">
        <Textarea />
      </Field>,
    );
    const textarea = screen.getByRole('textbox', { name: 'Final text' });
    expect(textarea).toHaveAttribute('id', 'final-text');
    expect(textarea).not.toHaveAttribute('aria-describedby');
    expect(textarea).not.toHaveAttribute('aria-invalid');
  });

  it('hands the wiring to composite controls through a function', async () => {
    const user = userEvent.setup();
    render(
      <Field label="Lane" error="Pick a lane.">
        {(control) => (
          <Select>
            <SelectTrigger {...control}>
              <SelectValue placeholder="Choose" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="native">Native</SelectItem>
            </SelectContent>
          </Select>
        )}
      </Field>,
    );
    const trigger = screen.getByRole('combobox', { name: 'Lane' });
    expect(trigger).toHaveAttribute('aria-invalid', 'true');
    expect(trigger).toHaveAccessibleDescription('Pick a lane.');
    trigger.focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('listbox')).toBeInTheDocument();
  });

  it('puts a switch before its label in a horizontal row', async () => {
    const user = userEvent.setup();
    render(
      <Field label="Pause all autonomy" description="Scheduled AI work waits; your own actions still run." orientation="horizontal">
        <Switch />
      </Field>,
    );
    const toggle = screen.getByRole('switch', { name: 'Pause all autonomy' });
    expect(toggle).toHaveAccessibleDescription('Scheduled AI work waits; your own actions still run.');
    await user.click(screen.getByText('Pause all autonomy'));
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(toggle.closest('[data-slot="field"]')).toHaveAttribute('data-orientation', 'horizontal');
  });

  it('moves focus to the first invalid control', () => {
    const { container } = render(
      <form>
        <Field label="Title">
          <Input />
        </Field>
        <Field label="Slug" error="Use lowercase letters, digits and hyphens.">
          <Input />
        </Field>
      </form>,
    );
    expect(focusFirstInvalid(container)).toBe(true);
    expect(screen.getByRole('textbox', { name: 'Slug' })).toHaveFocus();
    expect(focusFirstInvalid(null)).toBe(false);
  });
});

describe('Fieldset', () => {
  it('names a group of fields with its legend and describes it', () => {
    render(
      <Fieldset legend="Pillars" description="At least one." error="Pick a pillar.">
        <Field label="AI governance" orientation="horizontal">
          <Checkbox />
        </Field>
      </Fieldset>,
    );
    const group = screen.getByRole('group', { name: 'Pillars' });
    expect(group).toHaveAccessibleDescription('At least one. Pick a pillar.');
    expect(within(group).getByRole('checkbox', { name: 'AI governance' })).toBeInTheDocument();
  });

  it('names a single group control through the function form', () => {
    render(
      <Fieldset legend="Mode for this week" required>
        {(group) => (
          <SegmentedControl {...group} defaultValue="normal">
            <SegmentedControlItem value="normal">Normal</SegmentedControlItem>
            <SegmentedControlItem value="away">Away</SegmentedControlItem>
          </SegmentedControl>
        )}
      </Fieldset>,
    );
    const control = screen.getByRole('radiogroup', { name: 'Mode for this week' });
    expect(control).toHaveAttribute('aria-required', 'true');
  });
});

describe('ToggleGroup', () => {
  it('toggles several options with pressed states and arrow-key focus', async () => {
    const user = userEvent.setup();
    render(
      <ToggleGroup type="multiple" aria-label="Marks" variant="outline" size="icon">
        <ToggleGroupItem value="bold" aria-label="Bold">
          B
        </ToggleGroupItem>
        <ToggleGroupItem value="italic" aria-label="Italic">
          I
        </ToggleGroupItem>
      </ToggleGroup>,
    );
    // Radix makes a multiple toggle group a toolbar of toggle buttons.
    expect(screen.getByRole('toolbar', { name: 'Marks' })).toBeInTheDocument();
    const bold = screen.getByRole('button', { name: 'Bold' });
    expect(bold).toHaveClass('border-line-strong', 'size-10');
    await user.click(bold);
    expect(bold).toHaveAttribute('aria-pressed', 'true');
    expect(bold).toHaveAttribute('data-state', 'on');
    await user.keyboard('{ArrowRight}');
    const italic = screen.getByRole('button', { name: 'Italic' });
    await waitFor(() => {
      expect(italic).toHaveFocus();
    });
    await user.keyboard(' ');
    expect(italic).toHaveAttribute('aria-pressed', 'true');
    expect(bold).toHaveAttribute('aria-pressed', 'true');
  });

  it('acts as one choice that can be cleared when single', async () => {
    const user = userEvent.setup();
    render(
      <ToggleGroup type="single" aria-label="Status">
        <ToggleGroupItem value="new">New</ToggleGroupItem>
        <ToggleGroupItem value="snoozed">Snoozed</ToggleGroupItem>
      </ToggleGroup>,
    );
    expect(screen.getByRole('radiogroup', { name: 'Status' })).toBeInTheDocument();
    const fresh = screen.getByRole('radio', { name: 'New' });
    await user.click(fresh);
    expect(fresh).toHaveAttribute('aria-checked', 'true');
    await user.click(fresh);
    expect(fresh).toHaveAttribute('aria-checked', 'false');
  });
});

describe('SegmentedControl', () => {
  it('is a radio group that arrow keys move and that can never be cleared', async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(
      <SegmentedControl aria-label="Preview width" defaultValue="mobile" onValueChange={onValueChange} fullWidth>
        <SegmentedControlItem value="mobile">Mobile</SegmentedControlItem>
        <SegmentedControlItem value="desktop">Desktop</SegmentedControlItem>
      </SegmentedControl>,
    );
    const group = screen.getByRole('radiogroup', { name: 'Preview width' });
    expect(group).toHaveClass('flex', 'w-full');
    const mobile = within(group).getByRole('radio', { name: 'Mobile' });
    expect(mobile).toHaveAttribute('aria-checked', 'true');
    expect(mobile).toHaveClass('data-[state=checked]:bg-sheet', 'pointer-coarse:h-11');

    await user.click(mobile);
    expect(mobile).toHaveAttribute('aria-checked', 'true');
    expect(onValueChange).not.toHaveBeenCalled();

    mobile.focus();
    const desktop = within(group).getByRole('radio', { name: 'Desktop' });
    await user.keyboard('{ArrowRight>}');
    await waitFor(() => {
      expect(desktop).toHaveFocus();
    });
    await user.keyboard('{/ArrowRight}');
    expect(desktop).toHaveAttribute('aria-checked', 'true');
    expect(onValueChange).toHaveBeenCalledExactlyOnceWith('desktop');
  });
});
