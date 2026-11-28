import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { createTestAppData } from '@/test/app-data';
import { machinesResponse, SERVER_MACHINES } from '@/test/machines';
import { apiUrl, server } from '@/test/msw';

import { queryKeys } from './keys';
import {
  edgesOf,
  machineAllows,
  ownerEditsIn,
  ownerTakes,
  useMachines,
  useMachineViews,
  writerIn,
  type MachineViews,
} from './machines';

const TABLE = { article: { planned: ['researching', 'archived'], archived: [] } };
const VIEWS = machinesResponse(TABLE);

describe('useMachines', () => {
  it('reads the machines once for every screen, under the shared key', async () => {
    let reads = 0;
    server.use(
      http.get(apiUrl('/api/meta/machines'), () => {
        reads += 1;
        return HttpResponse.json(VIEWS);
      }),
    );
    const appData = createTestAppData();
    const wrapper = ({ children }: { children: ReactNode }) => <DataProvider appData={appData}>{children}</DataProvider>;
    const first = renderHook(() => useMachines(), { wrapper });
    await waitFor(() => {
      expect(first.result.current.data).toEqual(TABLE);
    });
    const second = renderHook(() => useMachineViews(), { wrapper });
    expect(second.result.current.data).toEqual(VIEWS);
    expect(reads).toBe(1);
    expect(appData.queryClient.getQueryData(queryKeys.meta.machines)).toEqual(VIEWS);
  });
});

describe('machineAllows', () => {
  it('answers from the table, and does not guess while it is unknown', () => {
    expect(machineAllows(TABLE, 'article', 'planned', 'researching')).toBe(true);
    expect(machineAllows(TABLE, 'article', 'planned', 'published')).toBe(false);
    expect(machineAllows(TABLE, 'article', 'unknown', 'archived')).toBe(false);
    expect(machineAllows(TABLE, 'post', 'draft', 'approved')).toBeUndefined();
    expect(machineAllows(undefined, 'article', 'planned', 'researching')).toBeUndefined();
  });
});

describe('the owner’s part', () => {
  const views = { article: SERVER_MACHINES.article, post: SERVER_MACHINES.post };

  it('says which edges the owner takes and which are the system’s', () => {
    expect(edgesOf(views).article).toEqual(SERVER_MACHINES.article.edges);
    expect(ownerTakes(views, 'article', 'approved', 'in_review')).toBe(true);
    expect(ownerTakes(views, 'article', 'publish_failed', 'in_review')).toBe(true);
    expect(ownerTakes(views, 'article', 'publishing', 'published')).toBe(false);
    expect(ownerTakes(views, 'post', 'approved', 'staged')).toBe(false);
    expect(ownerTakes(views, 'idea', 'new', 'accepted')).toBeUndefined();
    expect(ownerTakes(undefined, 'article', 'approved', 'in_review')).toBeUndefined();
  });

  it('says where the owner edits and who writes', () => {
    expect(ownerEditsIn(views, 'article', 'in_review')).toBe(true);
    expect(ownerEditsIn(views, 'article', 'approved')).toBe(false);
    expect(ownerEditsIn(views, 'post', 'expired')).toBe(true);
    expect(ownerEditsIn(undefined, 'article', 'in_review')).toBeUndefined();
    expect(writerIn(views, 'article', 'gating')).toBe('pipeline');
    expect(writerIn(views, 'article', 'unpublishing')).toBe('publish');
    expect(writerIn(views, 'post', 'draft')).toBeUndefined();
  });

  it('serves owner edges that are always edges of the machine', () => {
    const all: MachineViews = SERVER_MACHINES;
    for (const [name, view] of Object.entries(all)) {
      for (const [state, targets] of Object.entries(view.ownerEdges)) {
        for (const target of targets) expect(view.edges[state], `${name}: ${state} -> ${target}`).toContain(target);
      }
      for (const state of view.ownerWrites) expect(view.states, `${name}: ${state}`).toContain(state);
    }
  });
});
