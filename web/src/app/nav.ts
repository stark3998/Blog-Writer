import { CalendarDays, Inbox, Library, PenLine, Settings, type LucideIcon } from 'lucide-react';

export interface Section {
  readonly id: string;
  readonly label: string;
  /** Extra words the command palette matches on. */
  readonly keywords?: readonly string[];
}

export const CALENDAR_VIEWS = [
  { id: 'plan', label: 'Plan', keywords: ['week', 'schedule', 'calendar'] },
  { id: 'ideas', label: 'Ideas', keywords: ['backlog', 'feeds', 'signals'] },
  { id: 'availability', label: 'Availability', keywords: ['mode', 'away', 'light', 'normal'] },
] as const satisfies readonly Section[];

export const STUDIO_TABS = [
  { id: 'brief', label: 'Brief & research' },
  { id: 'interview', label: 'Interview' },
  { id: 'draft', label: 'Draft' },
  { id: 'posts', label: 'Posts & carousel' },
  { id: 'go-live', label: 'Go-live' },
] as const satisfies readonly Section[];

export const LIBRARY_SECTIONS = [
  { id: 'pillars', label: 'Pillars', keywords: ['topics', 'hashtags'] },
  { id: 'voice', label: 'Voice guide', keywords: ['style', 'tone'] },
  { id: 'stories', label: 'Stories & POV', keywords: ['takes', 'positions', 'experience'] },
  { id: 'brand', label: 'Brand kit', keywords: ['colours', 'colors', 'fonts', 'headshot', 'carousel'] },
  { id: 'formats', label: 'Format packs', keywords: ['case study', 'framework', 'explainer', 'news'] },
] as const satisfies readonly Section[];

export const SETTINGS_SECTIONS = [
  { id: 'models', label: 'Models', keywords: ['ai', 'routing', 'fallback'] },
  { id: 'slots', label: 'Slots', keywords: ['schedule', 'times', 'post slots'] },
  { id: 'sources', label: 'Sources', keywords: ['feeds', 'rss', 'scouting'] },
  { id: 'integrations', label: 'Integrations', keywords: ['linkedin', 'portfolio', 'connect'] },
  { id: 'policy', label: 'Blocklist & policy', keywords: ['confidentiality', 'auto-publish', 'autonomy'] },
  { id: 'insights', label: 'Insights', keywords: ['analytics', 'metrics', 'snapshots', 'spend'] },
  { id: 'system', label: 'System', keywords: ['jobs', 'evals', 'export', 'version'] },
] as const satisfies readonly Section[];

export type AreaId = 'today' | 'calendar' | 'studio' | 'library' | 'settings';

export interface Area {
  readonly id: AreaId;
  readonly label: string;
  readonly path: `/${string}`;
  readonly icon: LucideIcon;
  /** Sub-sections reachable as `${path}/${section.id}`. */
  readonly sections: readonly Section[];
}

/** The five areas, in navigation order. */
export const AREAS: readonly Area[] = [
  { id: 'today', label: 'Today', path: '/today', icon: Inbox, sections: [] },
  { id: 'calendar', label: 'Calendar', path: '/calendar', icon: CalendarDays, sections: CALENDAR_VIEWS },
  { id: 'studio', label: 'Studio', path: '/studio', icon: PenLine, sections: [] },
  { id: 'library', label: 'Library', path: '/library', icon: Library, sections: LIBRARY_SECTIONS },
  { id: 'settings', label: 'Settings', path: '/settings', icon: Settings, sections: SETTINGS_SECTIONS },
];

/** Finds a section by id; `undefined` id means the first section. */
export function findSection<const S extends Section>(sections: readonly S[], id: string | undefined): S | undefined {
  if (id === undefined) return sections[0];
  return sections.find((section) => section.id === id);
}

export const APP_NAME = 'Blog Writer';

/** The document title for a page, for example "Today – Blog Writer". */
export function documentTitle(page: string): string {
  return `${page} – ${APP_NAME}`;
}
