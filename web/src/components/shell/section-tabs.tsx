import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { useNavigate } from 'react-router';

import type { Section } from '@/app/nav';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

interface SectionTabsProps<S extends Section> {
  /** Accessible name for the tab list, for example "Settings sections". */
  label: string;
  sections: readonly S[];
  active: S;
  /** Each tab lives at `${basePath}/${section.id}`, so sections can be linked and bookmarked. */
  basePath: string;
  children: (section: S) => ReactNode;
}

/**
 * Keeps the selected tab visible when the list scrolls sideways on a phone.
 * Sets scrollLeft directly, so the page itself never jumps vertically.
 */
function useActiveTabInView(activeId: string) {
  const listRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const list = listRef.current;
    const tab = list?.querySelector<HTMLElement>('[role="tab"][data-state="active"]');
    if (!list || !tab || list.scrollWidth <= list.clientWidth) return;
    // The list is `relative`, so offsetLeft is measured in its scrolled content.
    const start = tab.offsetLeft;
    const end = start + tab.offsetWidth;
    if (start < list.scrollLeft || end > list.scrollLeft + list.clientWidth) {
      list.scrollLeft = start - (list.clientWidth - tab.offsetWidth) / 2;
    }
  }, [activeId]);
  return listRef;
}

/**
 * Tabs backed by the URL. Switching tabs replaces the history entry (Back
 * leaves the page rather than stepping through tabs) and keeps the scroll position.
 */
export function SectionTabs<S extends Section>({ label, sections, active, basePath, children }: SectionTabsProps<S>) {
  const navigate = useNavigate();
  const listRef = useActiveTabInView(active.id);
  return (
    <Tabs
      value={active.id}
      onValueChange={(id) => {
        void navigate(`${basePath}/${id}`, { replace: true, preventScrollReset: true });
      }}
    >
      <TabsList ref={listRef} aria-label={label}>
        {sections.map((section) => (
          <TabsTrigger key={section.id} value={section.id}>
            {section.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {sections.map((section) => (
        <TabsContent key={section.id} value={section.id}>
          {children(section)}
        </TabsContent>
      ))}
    </Tabs>
  );
}
