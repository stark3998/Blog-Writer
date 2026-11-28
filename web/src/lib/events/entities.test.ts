// @vitest-environment node
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { ENTITY_PREFIXES, queryKeys } from '@/lib/query/keys';
import { jobEventPayload } from '@/test/job-events';

import { entityChangeOf, entityInvalidations } from './entities';
import { createEventRouter, invalidationsFor } from './event-router';
import { JOB_EVENT, parseStreamEvent, type StreamEvent } from './events';
import { createJobStore } from './job-store';

function jobEvent(seq: number, type: string, data: Record<string, unknown> = {}, jobId = 'job-1'): StreamEvent {
  const event = parseStreamEvent('job', JSON.stringify(jobEventPayload(seq, type, { step: 'save', data, jobId })));
  if (!event) throw new Error('fixture does not parse');
  return event;
}

const changed = (entity: string, id: string, seq = 4) => jobEvent(seq, JOB_EVENT.entityChanged, { entity, id });

describe('entity.changed (contract section 9)', () => {
  it('maps every entity the server names to its query-key prefix', () => {
    expect(ENTITY_PREFIXES).toEqual({
      article: 'articles',
      post: 'posts',
      idea: 'ideas',
      research_run: 'researchRuns',
      brief: 'briefs',
      quick_search: 'quickSearches',
      story: 'stories',
      pov: 'povs',
      interview: 'interviews',
      voice_sample: 'voiceSamples',
      style_guide: 'styleGuides',
      voice_digest: 'voiceDigests',
      week_plan: 'plans',
      reminder: 'reminders',
      source: 'sources',
      engagement_item: 'engagementItems',
      experiment: 'experiments',
      insight_suggestion: 'suggestions',
      eval_result: 'evalResults',
      backup_status: 'backups',
      linkedin_connection: 'linkedin',
      settings: 'kb',
    });
  });

  it('reads the change from a job event, and nothing else', () => {
    expect(entityChangeOf(changed('article', 'art-1'))).toEqual({ entity: 'article', id: 'art-1', jobId: 'job-1' });
    expect(entityChangeOf(changed('nonsense', 'x'))).toBeUndefined();
    expect(entityChangeOf(changed('article', ''))).toBeUndefined();
    expect(entityChangeOf(jobEvent(4, 'step.succeeded'))).toBeUndefined();
  });

  it('invalidates the document, every list of its kind and Today, plus the job', () => {
    expect(entityInvalidations({ entity: 'week_plan', id: 'plan-2026-11-30' })).toEqual([
      ['plans', 'detail', 'plan-2026-11-30'],
      ['plans', 'list'],
      ['today'],
    ]);
    expect(invalidationsFor(changed('settings', 'pillars'))).toEqual([
      ['jobs', 'detail', 'job-1'],
      ['kb', 'detail', 'pillars'],
      ['kb', 'list'],
      ['today'],
    ]);
    // An entity the web does not know only refreshes its job.
    expect(invalidationsFor(changed('nonsense', 'x'))).toEqual([['jobs', 'detail', 'job-1']]);
  });

  it('batches a burst so each key is invalidated once, with the article queries under the prefix', async () => {
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const route = createEventRouter(createJobStore(), queryClient);
    route(changed('article', 'art-1', 4));
    route(changed('post', 'post-1', 5));
    route(changed('article', 'art-1', 6));
    await Promise.resolve();
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
      ['today'],
      ['articles', 'list'],
      ['posts', 'list'],
      ['jobs', 'detail', 'job-1'],
      ['articles', 'detail', 'art-1'],
      ['posts', 'detail', 'post-1'],
    ]);
    // `['articles', 'detail', 'art-1']` covers the article's sub-resources too.
    expect([...queryKeys.articles.detail('art-1'), 'versions'].slice(0, 3)).toEqual(['articles', 'detail', 'art-1']);
  });
});
