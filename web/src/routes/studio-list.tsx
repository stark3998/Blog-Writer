import { StudioListPage } from '@/features/studio/list/studio-list-page';

/** `/studio`: a thin shell; the page lives in `features/studio/list/` (this route is already its own chunk). */
export function Component() {
  return <StudioListPage />;
}
