import { DataProvider } from '@/features/data/data-provider';
import { TodayPage } from '@/features/today/today-page';

/** `/today`: a thin shell; the page lives in `features/today/` (this route is already its own chunk). */
export function Component() {
  // DataProvider passes through when the app already provides one around the router.
  return (
    <DataProvider>
      <TodayPage />
    </DataProvider>
  );
}
