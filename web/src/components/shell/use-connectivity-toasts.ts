import { useEffect } from 'react';
import { toast } from 'sonner';

const TOAST_ID = 'connectivity';

/** Tells the owner when the phone or laptop drops offline, and when it is back. */
export function useConnectivityToasts(): void {
  useEffect(() => {
    const offline = () => {
      toast('You’re offline', {
        id: TOAST_ID,
        description: 'The app reconnects on its own when your connection is back.',
        duration: Number.POSITIVE_INFINITY,
      });
    };
    const online = () => {
      toast.success('Back online', { id: TOAST_ID, duration: 3000 });
    };

    window.addEventListener('offline', offline);
    window.addEventListener('online', online);
    if (!navigator.onLine) offline();
    return () => {
      window.removeEventListener('offline', offline);
      window.removeEventListener('online', online);
    };
  }, []);
}
