import { PanelLeftClose, PanelLeftOpen, Search } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, NavLink } from 'react-router';

import { APP_NAME, AREAS, type Area } from '@/app/nav';
import { Kbd } from '@/components/ui/kbd';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/cn';
import { shortcutLabel } from '@/lib/platform';
import { useTheme } from '@/theme/theme-provider';

import { BrandMark } from './brand-mark';
import { ThemeMenu, themeOption } from './theme-menu';

interface SidebarProps {
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  onOpenCommandPalette: () => void;
}

const itemBase = cn(
  'flex h-10 w-full items-center gap-3 rounded-md px-2.5 text-sm font-medium transition-colors duration-150',
  'pointer-coarse:h-11 [&_svg]:size-[1.125rem] [&_svg]:shrink-0',
);

/** Shows a tooltip with the label only when the rail hides it. */
function RailTooltip({ label, show, children }: { label: string; show: boolean; children: ReactNode }) {
  if (!show) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

/*
 * NavLink sets aria-current="page" on the active link; styling keys off that.
 * The className stays a plain string because the tooltip trigger (a Radix Slot)
 * joins class names as strings and would mangle NavLink's function form.
 */
const linkClass = cn(
  itemBase,
  'text-ink-muted hover:bg-wash hover:text-ink',
  // The current page looks like a sheet of paper on the desk.
  'aria-[current=page]:bg-sheet aria-[current=page]:font-semibold aria-[current=page]:text-ink',
  'aria-[current=page]:shadow-[0_0_0_1px_var(--color-line)] aria-[current=page]:hover:bg-sheet',
);

function SidebarLink({ area, collapsed }: { area: Area; collapsed: boolean }) {
  const Icon = area.icon;
  return (
    <RailTooltip label={area.label} show={collapsed}>
      <NavLink to={area.path} className={cn(linkClass, collapsed && 'justify-center px-0')}>
        <Icon aria-hidden="true" />
        <span className={cn(collapsed && 'sr-only')}>{area.label}</span>
      </NavLink>
    </RailTooltip>
  );
}

/** Desktop navigation, on the desk beside the sheet. Collapses to an icon rail. */
export function Sidebar({ collapsed, onCollapsedChange, onOpenCommandPalette }: SidebarProps) {
  const { preference } = useTheme();
  const theme = themeOption(preference);
  const ThemeIcon = theme.icon;
  const CollapseIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
  const collapseLabel = collapsed ? 'Expand sidebar' : 'Collapse sidebar';

  const themeButton = (
    <button
      type="button"
      aria-label={`Theme: ${theme.label}`}
      className={cn(itemBase, 'text-ink-muted hover:bg-wash hover:text-ink', collapsed && 'justify-center px-0')}
    >
      <ThemeIcon aria-hidden="true" />
      {collapsed ? null : (
        <>
          <span aria-hidden="true">Theme</span>
          <span className="ml-auto text-xs text-ink-subtle" aria-hidden="true">
            {theme.label}
          </span>
        </>
      )}
    </button>
  );

  return (
    <div
      id="sidebar"
      data-collapsed={collapsed}
      className={cn(
        'sticky top-0 flex h-dvh shrink-0 flex-col px-3 pb-3 pt-4 transition-[width] duration-200 ease-standard',
        collapsed ? 'w-rail' : 'w-sidebar',
      )}
    >
      <Link
        to="/today"
        className={cn(
          'mb-7 flex h-10 items-center gap-2.5 rounded-md px-2 text-ink',
          collapsed && 'justify-center px-0',
        )}
      >
        <BrandMark className="size-6" />
        <span className={cn('font-serif text-xl font-medium', collapsed && 'sr-only')}>{APP_NAME}</span>
      </Link>

      <nav aria-label="Main">
        <ul className="flex flex-col gap-1">
          {AREAS.map((area) => (
            <li key={area.id}>
              <SidebarLink area={area} collapsed={collapsed} />
            </li>
          ))}
        </ul>
      </nav>

      <div className="mt-auto flex flex-col gap-1">
        <RailTooltip label={`Search (${shortcutLabel('K')})`} show={collapsed}>
          <button
            type="button"
            onClick={onOpenCommandPalette}
            aria-keyshortcuts="Meta+K Control+K"
            className={cn(itemBase, 'text-ink-muted hover:bg-wash hover:text-ink', collapsed && 'justify-center px-0')}
          >
            <Search aria-hidden="true" />
            <span className={cn(collapsed && 'sr-only')}>Search</span>
            {collapsed ? null : (
              <Kbd className="ml-auto" aria-hidden="true">
                {shortcutLabel('K')}
              </Kbd>
            )}
          </button>
        </RailTooltip>

        {collapsed ? (
          // The tooltip trigger sits inside the menu trigger so both get the button's events.
          <Tooltip>
            <ThemeMenu side="right" align="end">
              <TooltipTrigger asChild>{themeButton}</TooltipTrigger>
            </ThemeMenu>
            <TooltipContent side="right">{`Theme: ${theme.label}`}</TooltipContent>
          </Tooltip>
        ) : (
          <ThemeMenu side="top" align="start">
            {themeButton}
          </ThemeMenu>
        )}

        <RailTooltip label={collapseLabel} show={collapsed}>
          <button
            type="button"
            aria-controls="sidebar"
            aria-expanded={!collapsed}
            onClick={() => {
              onCollapsedChange(!collapsed);
            }}
            className={cn(itemBase, 'text-ink-muted hover:bg-wash hover:text-ink', collapsed && 'justify-center px-0')}
          >
            <CollapseIcon aria-hidden="true" />
            <span className={cn(collapsed && 'sr-only')}>{collapseLabel}</span>
          </button>
        </RailTooltip>
      </div>
    </div>
  );
}
