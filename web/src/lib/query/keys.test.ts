// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { entityKeys, isEntityName, KB_SINGLETONS, queryKeys } from './keys';

describe('query keys (contract sections 9 and 10)', () => {
  it('nest lists and details under each kind, so prefixes cascade', () => {
    const keys = entityKeys('ideas');
    expect(keys.all).toEqual(['ideas']);
    expect(keys.lists()).toEqual(['ideas', 'list']);
    expect(keys.list({ status: 'new' })).toEqual(['ideas', 'list', { status: 'new' }]);
    expect(keys.list()).toEqual(['ideas', 'list', {}]);
    expect(keys.details()).toEqual(['ideas', 'detail']);
    expect(keys.detail('idea-1')).toEqual(['ideas', 'detail', 'idea-1']);
  });

  it('keep the Phase 1 keys and add Today and every entity', () => {
    expect(queryKeys.me).toEqual(['me']);
    expect(queryKeys.jobs.detail('job-1')).toEqual(['jobs', 'detail', 'job-1']);
    expect(queryKeys.jobs.list({ status: 'failed' })).toEqual(['jobs', 'list', { status: 'failed' }]);
    expect(queryKeys.today).toEqual(['today']);
    // Reads that jobs change sit under the jobs prefix, so job events refresh them.
    expect(queryKeys.jobs.systemStatus).toEqual(['jobs', 'system-status']);
    expect(queryKeys.jobs.spend(7)).toEqual(['jobs', 'spend', { days: 7 }]);
    // Fixed for a deployment: outside every entity prefix, so no event refetches them.
    expect(queryKeys.meta.machines).toEqual(['meta', 'machines']);
    expect(queryKeys.plans.detail('plan-2026-11-30')).toEqual(['plans', 'detail', 'plan-2026-11-30']);
    expect(queryKeys.kb.detail(KB_SINGLETONS.policy)).toEqual(['kb', 'detail', 'employer-policy']);
  });

  it('knows the entity names the server sends', () => {
    expect(isEntityName('voice_sample')).toBe(true);
    expect(isEntityName('toString')).toBe(false);
    expect(isEntityName(3)).toBe(false);
  });
});
