import { mergeHeaders, type Client, type ClientPathsWithMethod, type FetchOptions, type MethodResponse } from 'openapi-fetch';

import { unwrap } from './client';
import type { paths } from './schema';

/**
 * Typed calls for any route in the generated schema (`./schema`, `npm run gen:api`).
 *
 * Every type here is computed from `Paths`, so a route the schema does not list yet is a compile
 * error, and a route the schema gains is typed the moment `schema.ts` is regenerated: nothing in
 * this file names a route.
 */

/** The HTTP methods the app's API uses. */
export type ApiMethod = 'get' | 'put' | 'post' | 'delete' | 'patch';

/** The methods that change something. */
export type ApiWriteMethod = Exclude<ApiMethod, 'get'>;

/** The paths of `Paths` that answer `M`, for example `ApiPath<'get'>` is every route you can GET. */
export type ApiPath<M extends ApiMethod, Paths extends object = paths> = ClientPathsWithMethod<Client<Paths>, M>;

/** The operation object (parameters, request body, responses) of `M` on `P`. */
type Operation<M extends ApiMethod, P, Paths> = P extends keyof Paths
  ? M extends keyof Paths[P]
    ? Paths[P][M]
    : never
  : never;

/**
 * The request options of `M P`: `params` (`path`, `query`, `header`), `body`, `headers`, `signal`
 * and the other `RequestInit` fields, exactly as openapi-fetch takes them.
 */
export type ApiInit<M extends ApiMethod, P, Paths extends object = paths> = FetchOptions<Operation<M, P, Paths>>;

/** The keys of `T` a caller must give. */
export type RequiredKeys<T> = {
  [K in keyof T]-?: object extends Pick<T, K> ? never : K;
}[keyof T];

/** `[init]` when the route needs parameters or a body, `[init?]` when it needs none. */
export type ApiInitArgs<M extends ApiMethod, P, Paths extends object = paths> =
  RequiredKeys<ApiInit<M, P, Paths>> extends never
    ? [init?: ApiInit<M, P, Paths>]
    : [init: ApiInit<M, P, Paths>];

/** What a successful `M P` resolves with (the JSON body of its 2xx response). */
export type ApiData<M extends ApiMethod, P extends ApiPath<M, Paths>, Paths extends object = paths> = MethodResponse<
  Client<Paths>,
  M,
  P
>;

/** What openapi-fetch resolves every call with. */
interface RawResult {
  readonly data?: unknown;
  readonly error?: unknown;
  readonly response: Response;
}

/** `client.request` without its per-route overloads, for code that is generic over the route. */
type LooseRequest = (method: string, path: string, init?: object) => Promise<RawResult>;

function looseRequest(client: object): LooseRequest {
  // openapi-fetch's `request(method, path, init)` is the same call as `GET(path, init)` and the
  // others; its overloads cannot be resolved for a path type that is still generic.
  return (client as { request: LooseRequest }).request;
}

/**
 * Calls `M P` and resolves with the data of the 2xx response, or rejects with an `ApiError` (status,
 * problem `code` and extensions, request id). The client's session handling (sign-in redirects,
 * the CSRF retry, "not authorised") applies as for any other call.
 *
 * ```ts
 * const page = await apiRequest(api, 'get', '/api/ideas', { params: { query: { limit: 20 } } });
 * ```
 */
export async function apiRequest<Paths extends object, M extends ApiMethod, P extends ApiPath<M, Paths>>(
  client: Client<Paths>,
  method: M,
  path: P,
  ...[init]: ApiInitArgs<M, P, Paths>
): Promise<ApiData<M, P, Paths>> {
  const { data } = await apiRequestWithResponse(client, method, path, ...([init] as ApiInitArgs<M, P, Paths>));
  return data;
}

/**
 * Like `apiRequest`, with the `Response` as well, for the headers (`ETag`, `Location`,
 * `X-Request-Id`). The body has already been read.
 */
export async function apiRequestWithResponse<Paths extends object, M extends ApiMethod, P extends ApiPath<M, Paths>>(
  client: Client<Paths>,
  method: M,
  path: P,
  ...[init]: ApiInitArgs<M, P, Paths>
): Promise<{ data: ApiData<M, P, Paths>; response: Response }> {
  const pending = looseRequest(client)(method, String(path), init);
  const result = await pending;
  // `unwrap` throws the ApiError for anything but a 2xx; the data is the success branch then.
  const data = (await unwrap(Promise.resolve(result))) as ApiData<M, P, Paths>;
  return { data, response: result.response };
}

/** Adds headers to a request's own (`init.headers` can be a record, an array or `Headers`). */
export function withHeaders<I extends { headers?: FetchOptions<unknown>['headers'] }>(
  init: I | undefined,
  headers: Readonly<Record<string, string>>,
): I & { headers: Headers } {
  return { ...(init ?? ({} as I)), headers: mergeHeaders(init?.headers, headers) };
}
