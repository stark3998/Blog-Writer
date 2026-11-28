import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useRef } from 'react';
import { useLocation, useNavigate } from 'react-router';

import { AREAS } from '@/app/nav';
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from '@/components/ui/command';
import { useTheme } from '@/theme/theme-provider';

import { focusPageHeading } from './focus';
import { THEME_OPTIONS } from './theme-menu';

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present on desktop, where the sidebar can collapse. */
  sidebar?: { collapsed: boolean; onCollapsedChange: (collapsed: boolean) => void };
}

const THEME_COMMAND_LABEL = {
  system: 'Use the system theme',
  light: 'Use the light theme',
  dark: 'Use the dark theme',
} as const;

/** Cmd/Ctrl+K: jump to any page or section, switch theme, collapse the sidebar. */
export function CommandPalette({ open, onOpenChange, sidebar }: CommandPaletteProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { preference, setPreference } = useTheme();
  // Set when a command navigates, so focus lands on the new page instead of the old trigger.
  const navigated = useRef(false);

  const go = (path: string) => {
    navigated.current = location.pathname !== path;
    onOpenChange(false);
    void navigate(path);
  };

  const run = (action: () => void) => {
    navigated.current = false;
    onOpenChange(false);
    action();
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Command palette"
      description="Go to a page or section, or run a command."
      onCloseAutoFocus={(event) => {
        if (!navigated.current) return;
        navigated.current = false;
        event.preventDefault();
        focusPageHeading();
      }}
    >
      <CommandInput placeholder="Go to a page or run a command" />
      <CommandList>
        <CommandEmpty>No matches. Try a page name, such as Slots or Voice guide.</CommandEmpty>

        <CommandGroup heading="Go to">
          {AREAS.map(({ id, label, path, icon: Icon }) => (
            <CommandItem
              key={id}
              value={label}
              onSelect={() => {
                go(path);
              }}
            >
              <Icon aria-hidden="true" />
              {label}
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandGroup heading="Sections">
          {AREAS.flatMap((area) =>
            area.sections.map((section) => (
              <CommandItem
                key={`${area.id}/${section.id}`}
                value={`${section.label} ${area.label}`}
                keywords={section.keywords ? [...section.keywords] : undefined}
                onSelect={() => {
                  go(`${area.path}/${section.id}`);
                }}
              >
                <area.icon aria-hidden="true" />
                <span>{section.label}</span>
                <CommandShortcut>{area.label}</CommandShortcut>
              </CommandItem>
            )),
          )}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Theme">
          {THEME_OPTIONS.map(({ value, icon: Icon }) => (
            <CommandItem
              key={value}
              value={THEME_COMMAND_LABEL[value]}
              keywords={['theme', 'appearance', value === 'dark' ? 'night' : value === 'light' ? 'day' : 'auto']}
              onSelect={() => {
                run(() => {
                  setPreference(value);
                });
              }}
            >
              <Icon aria-hidden="true" />
              {THEME_COMMAND_LABEL[value]}
              {preference === value ? <CommandShortcut>Current</CommandShortcut> : null}
            </CommandItem>
          ))}
        </CommandGroup>

        {sidebar ? (
          <CommandGroup heading="View">
            <CommandItem
              value={sidebar.collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              keywords={['sidebar', 'navigation', 'rail']}
              onSelect={() => {
                run(() => {
                  sidebar.onCollapsedChange(!sidebar.collapsed);
                });
              }}
            >
              {sidebar.collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
              {sidebar.collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            </CommandItem>
          </CommandGroup>
        ) : null}
      </CommandList>
    </CommandDialog>
  );
}
