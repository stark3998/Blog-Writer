import type { components } from './schema';

/** `GET /api/health`: status and the server's build SHA. The only unauthenticated route. */
export type HealthResponse = components['schemas']['HealthResponse'];

/** `GET /api/me`: the signed-in owner. `name` is `null` when only the app cookie authenticated. */
export type MeResponse = components['schemas']['MeResponse'];
