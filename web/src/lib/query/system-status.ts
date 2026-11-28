import { useApiQuery } from './api-query';
import { queryKeys } from './keys';

/**
 * `GET /api/system/status`: safe mode, the runner, the spending brake, the services and token
 * expiries. One cache entry for Today's notices and Settings, under the jobs prefix
 * (`queryKeys.jobs.systemStatus`) because they move as jobs run; a Settings change that affects
 * them invalidates that key, and Today sees it too.
 */
export function useSystemStatus(options: { refetchOnWindowFocus?: boolean } = {}) {
  return useApiQuery({ queryKey: queryKeys.jobs.systemStatus, path: '/api/system/status' }, options);
}
