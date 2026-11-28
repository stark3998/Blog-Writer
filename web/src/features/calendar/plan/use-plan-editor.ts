import { useState } from 'react';

import { useConflict, type ConflictState } from '@/features/data/conflict-dialog';
import type { components } from '@/lib/api/schema';
import { toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import type { WeekPlan } from '../lib/plan';

type PlanEditIn = components['schemas']['PlanEditIn'];
type PlanEnvelope = components['schemas']['Versioned_WeekPlan_'];

/** One change to a week plan (`PUT /api/plan/weeks/{weekStart}`). */
export interface PlanEdit {
  readonly body: PlanEditIn;
  /** The plan as it will look, shown at once (a move lands where it was dropped). */
  readonly preview?: WeekPlan;
  /** The toast after the save: "Moved to Wed 2 Dec, 10:00." */
  readonly success: string;
}

export interface PlanEditor {
  /** Saves an edit against the plan the owner sees (If-Match its etag). */
  readonly apply: (edit: PlanEdit) => void;
  readonly saving: boolean;
  /** The 412 answer: who changed the plan meanwhile. */
  readonly conflict: ConflictState;
  /** The edit the 412 refused, for the comparison and "Keep mine". */
  readonly refused: PlanEdit | null;
  readonly keepMine: () => void;
  readonly takeTheirs: () => void;
}

/**
 * Edits of one week plan with If-Match, an instant preview that rolls back on failure, and the
 * conflict dialog for a stale plan (another tab, or the planner regenerating it). Errors other
 * than 412 are toasted with the server's sentence (`slot_conflict`, `slot_has_post`, …).
 */
export function usePlanEditor(plan: WeekPlan, etag: string | null): PlanEditor {
  const { queryClient } = useAppData();
  const conflict = useConflict();
  const [refused, setRefused] = useState<PlanEdit | null>(null);
  const key = queryKeys.plans.detail(plan.id);
  const save = useApiMutation('put', '/api/plan/weeks/{week_start}', {
    updates: () => key,
    // Moved posts follow their slots; Today lists the plan.
    invalidates: [queryKeys.posts.lists(), queryKeys.plans.lists(), queryKeys.today],
    errorToast: 'Couldn’t save the plan.',
    handles: ['etag_mismatch'],
  });

  const send = (edit: PlanEdit, ifMatch: string) => {
    const previous = queryClient.getQueryData<PlanEnvelope>(key);
    if (edit.preview && previous) {
      void queryClient.cancelQueries({ queryKey: key });
      queryClient.setQueryData<PlanEnvelope>(key, { ...previous, item: edit.preview });
    }
    save.mutate(
      { params: { path: { week_start: plan.weekStart } }, body: edit.body, ifMatch },
      {
        onSuccess: () => {
          toastSuccess(edit.success);
          setRefused(null);
          conflict.close();
        },
        onError: (error) => {
          if (previous && edit.preview) queryClient.setQueryData(key, previous);
          if (conflict.show(error)) {
            setRefused(edit);
            // Load the plan as it is now, for the comparison.
            void queryClient.invalidateQueries({ queryKey: key });
          }
        },
      },
    );
  };

  return {
    apply: (edit) => {
      if (etag !== null) send(edit, etag);
    },
    saving: save.isPending,
    conflict,
    refused,
    keepMine: () => {
      const current = conflict.conflict?.etag ?? queryClient.getQueryData<PlanEnvelope>(key)?.etag ?? undefined;
      if (refused && current) send({ ...refused, preview: undefined }, current);
    },
    takeTheirs: () => {
      setRefused(null);
      conflict.close();
      void queryClient.invalidateQueries({ queryKey: key });
    },
  };
}
