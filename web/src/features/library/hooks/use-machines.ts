import { useMachines as useMachineTable } from '@/lib/query/machines';

import type { MachineTable } from '../lib/machines';

/**
 * `GET /api/meta/machines`: the state machines action buttons are enabled from (contract
 * section 10). `undefined` while it loads or when it failed (callers fall back to the contract's
 * table, and the server still refuses a wrong edge). The shared query: one entry for every area.
 */
export function useMachines(): MachineTable | undefined {
  return useMachineTable().data;
}
