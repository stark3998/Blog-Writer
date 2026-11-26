import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react';
import type { ReactElement } from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { isThemePreference, type ThemePreference } from '@/theme/theme';
import { useTheme } from '@/theme/theme-provider';

interface ThemeOption {
  value: ThemePreference;
  label: string;
  icon: LucideIcon;
}

const SYSTEM_OPTION: ThemeOption = { value: 'system', label: 'System', icon: Monitor };

export const THEME_OPTIONS: readonly ThemeOption[] = [
  SYSTEM_OPTION,
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
];

export function themeOption(preference: ThemePreference): ThemeOption {
  return THEME_OPTIONS.find((option) => option.value === preference) ?? SYSTEM_OPTION;
}

interface ThemeMenuProps {
  /** The trigger button. Its accessible name should include the current theme. */
  children: ReactElement;
  side?: 'top' | 'right' | 'bottom' | 'left';
  align?: 'start' | 'center' | 'end';
}

/** Theme choice: follow the system (default), or fix light or dark. Saved on this device. */
export function ThemeMenu({ children, side = 'bottom', align = 'end' }: ThemeMenuProps) {
  const { preference, setPreference } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent side={side} align={align} className="min-w-44">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={preference}
          onValueChange={(value) => {
            if (isThemePreference(value)) setPreference(value);
          }}
        >
          {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
            <DropdownMenuRadioItem key={value} value={value}>
              <Icon className="text-ink-subtle" aria-hidden="true" />
              {label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
