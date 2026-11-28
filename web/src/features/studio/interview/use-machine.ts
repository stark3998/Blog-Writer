import { useMachines } from '@/lib/query/machines';

import { INTERVIEW_MACHINE, type InterviewStatus } from './interview-model';

/**
 * Where an interview in `status` can go. Until the table loads, or if it cannot, the contract's
 * machine answers, so the buttons never wait on it.
 */
export function useInterviewTargets(status: InterviewStatus): readonly string[] {
  const machines = useMachines();
  return machines.data?.interview?.[status] ?? INTERVIEW_MACHINE[status];
}
