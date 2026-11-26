import { Search } from 'lucide-react';
import { Link } from 'react-router';

import { APP_NAME } from '@/app/nav';
import { Button } from '@/components/ui/button';
import { useTheme } from '@/theme/theme-provider';

import { BrandMark } from './brand-mark';
import { ThemeMenu, themeOption } from './theme-menu';

/** Phone header: the mark, search and theme. Navigation lives in the bottom tab bar, under the thumb. */
export function MobileTopBar({ onOpenCommandPalette }: { onOpenCommandPalette: () => void }) {
  const { preference } = useTheme();
  const theme = themeOption(preference);
  const ThemeIcon = theme.icon;

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-sheet pt-[env(safe-area-inset-top)]">
      <div className="flex h-14 items-center justify-between gap-2 pl-3 pr-1.5">
        <Link to="/today" className="flex items-center gap-2.5 rounded-md px-1.5 py-1.5 text-ink">
          <BrandMark className="size-6" />
          <span className="font-serif text-xl font-medium">{APP_NAME}</span>
        </Link>
        <div className="flex items-center">
          <Button variant="ghost" size="icon" aria-label="Search" onClick={onOpenCommandPalette}>
            <Search className="size-5" aria-hidden="true" />
          </Button>
          <ThemeMenu align="end">
            <Button variant="ghost" size="icon" aria-label={`Theme: ${theme.label}`}>
              <ThemeIcon className="size-5" aria-hidden="true" />
            </Button>
          </ThemeMenu>
        </div>
      </div>
    </header>
  );
}
