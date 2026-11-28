import type { components } from '@/lib/api/schema';

import { useApiQuery } from './api-query';
import { queryKeys } from './keys';

/**
 * `GET /api/meta/machines`: every status machine (contract section 2) with the owner's part in
 * it: `edges` (every edge), `ownerEdges` (the edges the owner asks for; every other edge is the
 * system's: jobs, schedules, a publish attempt's answer), `ownerWrites` (the states the owner
 * edits the content in) and, for articles, `writers` (who writes in each state).
 */
export type MachineView = components['schemas']['MachineView'];
export type MachineViews = Readonly<Record<string, MachineView>>;

/** Every machine's edges as `{machine: {state: [targets]}}` (what most screens need). */
export type Machines = Readonly<Record<string, Readonly<Record<string, readonly string[]>>>>;

/** The edges of every machine (`edges`), keyed like the machines. */
export function edgesOf(views: MachineViews): Machines {
  return Object.fromEntries(Object.entries(views).map(([name, view]) => [name, view.edges]));
}

const MACHINE_OPTIONS = { staleTime: Number.POSITIVE_INFINITY, gcTime: Number.POSITIVE_INFINITY } as const;

/**
 * `GET /api/meta/machines` in full (`MachineViews`). The machines are code on the server, the
 * same for a whole deployment, and no event changes them: one read per session, one cache entry
 * for every area (`queryKeys.meta.machines`).
 */
export function useMachineViews() {
  return useApiQuery({ queryKey: queryKeys.meta.machines, path: '/api/meta/machines' }, MACHINE_OPTIONS);
}

/** The machines' edges only (`edgesOf`), from the same cache entry as `useMachineViews`. */
export function useMachines() {
  return useApiQuery(
    { queryKey: queryKeys.meta.machines, path: '/api/meta/machines' },
    { ...MACHINE_OPTIONS, select: (data): Machines => edgesOf(data) },
  );
}

/**
 * Whether `machine` has the edge `from -> to`. `undefined` while the machines are unknown (loading,
 * failed, or a machine the table lacks): callers decide what unknown means for their action
 * (hide it, or leave it to the route), and the route stays the judge.
 */
export function machineAllows(machines: Machines | undefined, machine: string, from: string, to: string): boolean | undefined {
  const table = machines?.[machine];
  if (!table) return undefined;
  return table[from]?.includes(to) ?? false;
}

/**
 * Whether the owner takes `from -> to` (an edge of `ownerEdges`: a route of the owner's moves it
 * there). `undefined` while the machines are unknown.
 */
export function ownerTakes(views: MachineViews | undefined, machine: string, from: string, to: string): boolean | undefined {
  const view = views?.[machine];
  if (!view) return undefined;
  return view.ownerEdges[from]?.includes(to) ?? false;
}

/** Whether the owner edits the content of a `machine` document in `state` (`ownerWrites`). */
export function ownerEditsIn(views: MachineViews | undefined, machine: string, state: string): boolean | undefined {
  const view = views?.[machine];
  if (!view) return undefined;
  return view.ownerWrites.includes(state);
}

/** Who writes a `machine` document's content in `state` (`writers`: `owner`, `pipeline`, ...). */
export function writerIn(views: MachineViews | undefined, machine: string, state: string): string | undefined {
  return views?.[machine]?.writers?.[state];
}
