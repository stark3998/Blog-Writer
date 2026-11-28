import { useSyncExternalStore } from 'react';

import { createPersistentStore } from '@/lib/persistent-store';

export const SIDEBAR_STORAGE_KEY = 'bw:sidebar';

/** Whether the desktop sidebar is collapsed to an icon rail. Saved on this device. */
export const sidebarStore = createPersistentStore<boolean>({
  key: SIDEBAR_STORAGE_KEY,
  fallback: false,
  parse: (raw) => (raw === 'collapsed' ? true : raw === 'expanded' ? false : undefined),
  serialize: (collapsed) => (collapsed ? 'collapsed' : 'expanded'),
});

const serverSnapshot = () => false;

export function useSidebarCollapsed(): readonly [boolean, (collapsed: boolean) => void] {
  const collapsed = useSyncExternalStore(sidebarStore.subscribe, sidebarStore.get, serverSnapshot);
  return [collapsed, sidebarStore.set] as const;
}
