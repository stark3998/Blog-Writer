import {
  infiniteQueryOptions,
  mutationOptions,
  queryOptions,
  useInfiniteQuery,
  useMutation,
  useQuery,
  type QueryKey,
  type UseInfiniteQueryOptions,
  type UseMutationOptions,
  type UseQueryOptions,
} from '@tanstack/react-query';
import type { Client } from 'openapi-fetch';

import { preconditionHeaders, type Etag } from '@/lib/api/envelope';
import { isProblem } from '@/lib/api/problems';
import {
  apiRequest,
  withHeaders,
  type ApiData,
  type ApiInit,
  type ApiInitArgs,
  type ApiPath,
  type ApiWriteMethod,
  type RequiredKeys,
} from '@/lib/api/request';
import type { paths } from '@/lib/api/schema';
import { toastError, toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';

import { clampPageSize, flattenPages, nextCursorOf, type PageEntry } from './pagination';

/**
 * TanStack Query over the typed API client. Three shapes, each as an options factory (for
 * `queryClient.fetchQuery`, `prefetchQuery`, `useQueries`, loaders) and as a hook bound to the
 * app's client:
 *
 * - `apiQueryOptions` / `useApiQuery`: one GET.
 * - `apiInfiniteQueryOptions` / `useApiInfiniteQuery`: a paged list (contract 1.5).
 * - `apiMutationOptions` / `useApiMutation`: a write, with If-Match, cache updates, invalidation
 *   and toasts.
 *
 * Errors are `ApiError`s (`lib/api/errors`); read them with `lib/api/problems`.
 */

/** `init` is required exactly when the route needs parameters or a body. */
type InitField<I> = RequiredKeys<I> extends never ? { readonly init?: I } : { readonly init: I };

export type ApiQueryArgs<P, K extends QueryKey = QueryKey, Paths extends object = paths> = {
  /** A contract key from `queryKeys` (`queryKeys.articles.detail(id)`), so events refresh it. */
  readonly queryKey: K;
  readonly path: P;
} & InitField<ApiInit<'get', P, Paths>>;

type LooseInit = Record<string, unknown> & { params?: Record<string, unknown> };

function initOf(args: object): LooseInit | undefined {
  return (args as { init?: LooseInit }).init;
}

/** The query function of one GET; the query's `signal` cancels the request. */
function getFn<Paths extends object, P extends ApiPath<'get', Paths>>(
  client: Client<Paths>,
  path: P,
  init: LooseInit | undefined,
): (context: { signal: AbortSignal }) => Promise<ApiData<'get', P, Paths>> {
  return ({ signal }) => apiRequest(client, 'get', path, ...([{ ...init, signal }] as ApiInitArgs<'get', P, Paths>));
}

/** Options for one GET: `useQuery(apiQueryOptions(api, {queryKey, path, init}))`. */
export function apiQueryOptions<Paths extends object, P extends ApiPath<'get', Paths>, K extends QueryKey>(
  client: Client<Paths>,
  args: ApiQueryArgs<P, K, Paths>,
) {
  return queryOptions({ queryKey: args.queryKey, queryFn: getFn(client, args.path, initOf(args)) });
}

export type ApiQueryHookOptions<TQueryFnData, TData, K extends QueryKey> = Omit<
  UseQueryOptions<TQueryFnData, Error, TData, K>,
  'queryKey' | 'queryFn'
>;

/**
 * One GET with the app's client.
 *
 * ```tsx
 * const article = useApiQuery({
 *   queryKey: queryKeys.articles.detail(id),
 *   path: '/api/articles/{id}',
 *   init: { params: { path: { id } } },
 * });
 * ```
 */
export function useApiQuery<P extends ApiPath<'get'>, K extends QueryKey, TData = ApiData<'get', P>>(
  args: ApiQueryArgs<P, K>,
  options?: ApiQueryHookOptions<ApiData<'get', P>, TData, K>,
) {
  const { api } = useAppData();
  return useQuery<ApiData<'get', P>, Error, TData, K>({
    ...options,
    queryKey: args.queryKey,
    queryFn: getFn(api, args.path, initOf(args)),
  });
}

export type ApiInfiniteArgs<P, K extends QueryKey = QueryKey, Paths extends object = paths> = ApiQueryArgs<
  P,
  K,
  Paths
> & {
  /** Items per page, 1-100 (default 50; lists of large documents use 20). */
  readonly pageSize?: number;
};

/**
 * Options for a paged list: each page is `GET path?limit=&cursor=` and the next cursor comes
 * from the page's `nextCursor`. Use with `useInfiniteQuery`; the list's key is
 * `queryKeys.<kind>.list(filters)`.
 */
export function apiInfiniteQueryOptions<Paths extends object, P extends ApiPath<'get', Paths>, K extends QueryKey>(
  client: Client<Paths>,
  args: ApiInfiniteArgs<P, K, Paths>,
) {
  const init = initOf(args);
  const limit = clampPageSize(args.pageSize);
  return infiniteQueryOptions({
    queryKey: args.queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) => {
      const query = { ...(init?.params?.query as object | undefined), limit, ...(pageParam ? { cursor: pageParam } : {}) };
      const pageInit: LooseInit = { ...init, params: { ...init?.params, query }, signal };
      return apiRequest(client, 'get', args.path, ...([pageInit] as ApiInitArgs<'get', P, Paths>));
    },
    getNextPageParam: (lastPage) => nextCursorOf(lastPage),
  });
}

export type ApiInfiniteHookOptions<TPage, K extends QueryKey> = Omit<
  UseInfiniteQueryOptions<TPage, Error, PageEntry<TPage>[], K, string | undefined>,
  'queryKey' | 'queryFn' | 'initialPageParam' | 'getNextPageParam' | 'select'
>;

/**
 * A paged list with the app's client. `data` is every loaded entry (`{item, etag}`) in order;
 * `hasNextPage`, `fetchNextPage` and `isFetchingNextPage` drive "Show more".
 *
 * ```tsx
 * const ideas = useApiInfiniteQuery({
 *   queryKey: queryKeys.ideas.list({ status: 'new' }),
 *   path: '/api/ideas',
 *   init: { params: { query: { status: 'new' } } },
 *   pageSize: 20,
 * });
 * ```
 */
export function useApiInfiniteQuery<P extends ApiPath<'get'>, K extends QueryKey>(
  args: ApiInfiniteArgs<P, K>,
  options?: ApiInfiniteHookOptions<ApiData<'get', P>, K>,
) {
  const { api } = useAppData();
  return useInfiniteQuery<ApiData<'get', P>, Error, PageEntry<ApiData<'get', P>>[], K, string | undefined>({
    ...apiInfiniteQueryOptions(api, args),
    ...options,
    select: flattenPages<ApiData<'get', P>>,
  });
}

/** A write's request options, plus `ifMatch`. */
export type ApiWriteInit<M extends ApiWriteMethod, P, Paths extends object = paths> = ApiInit<M, P, Paths> & {
  /**
   * The etag you read: sends `If-Match` (contract 1.4). `null` for a never-saved singleton
   * (sends `If-None-Match: *`). Leave it out for creates and idempotent one-tap actions.
   */
  readonly ifMatch?: Etag | null;
};

/** What `mutate` takes: nothing at all when the route needs no parameters or body. */
export type ApiMutationVariables<M extends ApiWriteMethod, P, Paths extends object = paths> =
  RequiredKeys<ApiInit<M, P, Paths>> extends never
    ? // eslint-disable-next-line @typescript-eslint/no-invalid-void-type -- `void` lets `mutate()` be called without an argument (TanStack's own "no variables").
      ApiWriteInit<M, P, Paths> | void
    : ApiWriteInit<M, P, Paths>;

export interface ApiMutationExtras<TData, TVariables> {
  /** Query keys to invalidate after a success. Active queries refetch, and the mutation stays pending until they have. */
  readonly invalidates?: readonly QueryKey[] | ((data: TData, variables: TVariables) => readonly QueryKey[]);
  /**
   * The detail query to put the response in (same `Versioned` shape), so the next save already
   * has the new etag. Return `undefined` to skip.
   */
  readonly updates?: (data: TData, variables: TVariables) => QueryKey | undefined;
  /** A success toast in the action's words ("Pillars saved."). */
  readonly successToast?: string | ((data: TData, variables: TVariables) => string | undefined);
  /** The error toast's title ("Couldn’t save the pillars."), or `false` for none. Default: the problem's sentence. */
  readonly errorToast?: string | false;
  /** Problem codes this screen shows itself (`etag_mismatch` with the conflict dialog, `confidential_text` inline): no toast for them. */
  readonly handles?: readonly string[];
}

export type ApiMutationOptions<TData, TVariables> = ApiMutationExtras<TData, TVariables> &
  Omit<UseMutationOptions<TData, Error, TVariables>, 'mutationFn'>;

/** Options for a write: `useMutation(apiMutationOptions(api, 'put', '/api/kb/pillars', {...}))`. */
export function apiMutationOptions<Paths extends object, M extends ApiWriteMethod, P extends ApiPath<M, Paths>>(
  client: Client<Paths>,
  method: M,
  path: P,
  options: ApiMutationOptions<ApiData<M, P, Paths>, ApiMutationVariables<M, P, Paths>> = {},
) {
  const { invalidates, updates, successToast, errorToast, handles = [], onSuccess, onError, ...rest } = options;
  type Data = ApiData<M, P, Paths>;
  type Variables = ApiMutationVariables<M, P, Paths>;
  return mutationOptions<Data, Error, Variables>({
    ...rest,
    mutationFn: (variables) => {
      const { ifMatch, ...init } = (variables ?? {}) as ApiWriteInit<M, P, Paths>;
      const sent = withHeaders(init, preconditionHeaders(ifMatch));
      return apiRequest(client, method, path, ...([sent] as ApiInitArgs<M, P, Paths>));
    },
    onSuccess: async (data, variables, onMutateResult, context) => {
      const key = updates?.(data, variables);
      if (key) context.client.setQueryData(key, data);
      const message = typeof successToast === 'function' ? successToast(data, variables) : successToast;
      if (message) toastSuccess(message);
      const keys = typeof invalidates === 'function' ? invalidates(data, variables) : (invalidates ?? []);
      await Promise.all([
        ...keys.map((queryKey) => context.client.invalidateQueries({ queryKey })),
        onSuccess?.(data, variables, onMutateResult, context),
      ]);
    },
    onError: (error, variables, onMutateResult, context) => {
      if (errorToast !== false && !isProblem(error, handles)) {
        toastError(error, errorToast === undefined ? {} : { title: errorToast });
      }
      return onError?.(error, variables, onMutateResult, context);
    },
  });
}

/**
 * A write with the app's client.
 *
 * ```tsx
 * const save = useApiMutation('put', '/api/kb/pillars', {
 *   updates: () => queryKeys.kb.detail(KB_SINGLETONS.pillars),
 *   successToast: 'Pillars saved.',
 *   errorToast: 'Couldn’t save the pillars.',
 *   handles: ['etag_mismatch'],
 * });
 * save.mutate({ body: pillars, ifMatch: etag }, { onError: (error) => conflict.show(error) });
 * ```
 */
export function useApiMutation<M extends ApiWriteMethod, P extends ApiPath<M>>(
  method: M,
  path: P,
  options?: ApiMutationOptions<ApiData<M, P>, ApiMutationVariables<M, P>>,
) {
  const { api } = useAppData();
  return useMutation(apiMutationOptions(api, method, path, options));
}
