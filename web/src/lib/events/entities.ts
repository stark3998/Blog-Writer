import type { QueryKey } from '@tanstack/react-query';

import { ENTITY_PREFIXES, entityKeys, isEntityName, queryKeys, type EntityName } from '@/lib/query/keys';

import { JOB_EVENT, type StreamEvent } from './events';

/** A document a job reported as changed (`entity.changed`, ids only). */
export interface EntityChange {
  readonly entity: EntityName;
  readonly id: string;
  /** The job that changed it. */
  readonly jobId: string;
}

/** The change an event reports, or `undefined` for every other event (and unknown entities). */
export function entityChangeOf(event: StreamEvent): EntityChange | undefined {
  if (event.type !== 'job' || event.event.type !== JOB_EVENT.entityChanged) return undefined;
  const { entity, id } = event.event.data;
  if (!isEntityName(entity) || typeof id !== 'string' || id === '') return undefined;
  return { entity, id, jobId: event.jobId };
}

/**
 * What a change makes stale (contract section 9): the document (`[<plural>, 'detail', id]`, with
 * its sub-resources), every list of its kind (`[<plural>, 'list']`) and Today.
 */
export function entityInvalidations({ entity, id }: Pick<EntityChange, 'entity' | 'id'>): readonly QueryKey[] {
  const keys = entityKeys(ENTITY_PREFIXES[entity]);
  return [keys.detail(id), keys.lists(), queryKeys.today];
}
