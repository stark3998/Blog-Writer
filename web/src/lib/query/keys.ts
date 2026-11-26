/**
 * Every query key in one place, so the event stream can invalidate exactly what an event changes.
 * Keys are prefixes of each other where invalidation should cascade: `jobs.all` covers every job
 * query.
 */
export const queryKeys = {
  me: ['me'] as const,
  health: ['health'] as const,
  jobs: {
    all: ['jobs'] as const,
    detail: (jobId: string) => ['jobs', 'detail', jobId] as const,
  },
};
