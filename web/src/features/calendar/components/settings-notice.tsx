import { Alert, AlertActions, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

import type { CalendarSettings } from '../data';

/** Says which settings could not be loaded (the views use the defaults meanwhile), with a retry. */
export function SettingsNotice({ settings }: { settings: CalendarSettings }) {
  if (settings.failed.length === 0) return null;
  return (
    <Alert variant="warn">
      <AlertDescription>
        <p>{`Couldn’t load your ${settings.failed.join(', ')}. The calendar uses the defaults until they load.`}</p>
      </AlertDescription>
      <AlertActions>
        <Button size="sm" variant="secondary" onClick={settings.retry}>
          Try again
        </Button>
      </AlertActions>
    </Alert>
  );
}
