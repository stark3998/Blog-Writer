import { act, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, describe, expect, it } from 'vitest';

import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { laneSettings, page, pillarSettings, source, versioned } from './testing/fixtures';
import { getJson, machinesHandler, problemWith, record, singleton, type RecordedRequest } from './testing/handlers';

// Toasts live in a module-level store that replays them to the next test's toaster.
afterEach(() => {
  act(() => {
    toast.dismiss();
  });
});

function setup({ pillarsEtag = null as string | null, onPillarsPut = undefined as ((body: unknown) => Response | undefined) | undefined } = {}) {
  const pillars = singleton('/api/kb/pillars', versioned(pillarSettings(), pillarsEtag), { onPut: onPillarsPut });
  const lanes = singleton('/api/kb/lanes', versioned(laneSettings(), '"lanes-1"'));
  const sources: RecordedRequest[] = [];
  server.use(
    ...pillars.handlers,
    ...lanes.handlers,
    machinesHandler,
    getJson('/api/sources', page([versioned(source())])),
    http.post(apiUrl('/api/sources'), async ({ request }) => {
      const recorded = await record(sources, request);
      return HttpResponse.json(versioned(source({ id: 'feed-0002', ...(recorded.body as object) })), { status: 201 });
    }),
  );
  return { pillars, lanes, sources };
}

/** The selected pillar's panel (inside the Library's own tab panel). */
function pillarPanel(): HTMLElement {
  const panels = screen.getAllByRole('tabpanel');
  const inner = panels.at(-1);
  if (!inner) throw new Error('no pillar panel');
  return inner;
}

async function openPillars(options?: Parameters<typeof setup>[0]) {
  const state = setup(options);
  const view = renderApp('/library/pillars');
  await findPageHeading('Library');
  await screen.findByRole('tab', { name: /AI agents & security/ });
  return { ...state, ...view };
}

describe('Library > Pillars', () => {
  it('shows each pillar’s settings and the target mix', async () => {
    await openPillars();
    expect(screen.getByRole('heading', { level: 2, name: 'Your pillars' })).toBeInTheDocument();
    expect(screen.getByText('These are the plan’s starting pillars.')).toBeInTheDocument();
    const mix = screen.getByRole('figure', { name: /^Target share of each week’s posts/ });
    expect(within(mix).getByText('Identity modernization')).toBeInTheDocument();
    expect(within(mix).getByText('Every post has a pillar')).toBeInTheDocument();
    const panel = pillarPanel();
    expect(within(panel).getByRole('textbox', { name: /^Audience/ })).toHaveValue(
      'Security architects and platform engineers connecting agents to production.',
    );
    expect(within(panel).getByRole('button', { name: 'Remove the hashtag #AISecurity' })).toBeInTheDocument();
    expect(await within(panel).findByText('OWASP GenAI blog')).toBeInTheDocument();
    expect(within(panel).getByText('Active')).toBeInTheDocument();
  });

  it('saves the plan’s pillars the first time with If-None-Match: *', async () => {
    const { pillars, user } = await openPillars();
    const audience = within(pillarPanel()).getByRole('textbox', { name: /^Audience/ });
    await user.clear(audience);
    await user.type(audience, 'CISOs and AppSec leads.');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText('Pillars saved.')).toBeInTheDocument();
    const put = pillars.requests.find((request) => request.method === 'PUT');
    expect(put?.ifNoneMatch).toBe('*');
    expect(put?.ifMatch).toBeNull();
    const body = put?.body as { pillars: { id: string; audience: string; targetShare: number }[] };
    expect(body.pillars.map((item) => item.id)).toEqual(['agents', 'identity', 'governance']);
    expect(body.pillars[0]).toMatchObject({ audience: 'CISOs and AppSec leads.', targetShare: 0.34 });
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
    });
  });

  it('adds hashtags as typed, without #, up to three', async () => {
    const { pillars, user } = await openPillars({ pillarsEtag: '"pillars-1"' });
    const panel = pillarPanel();
    const add = within(panel).getByRole('textbox', { name: 'Add a hashtag to AI agents & security' });
    await user.type(add, '#MCP{Enter}');
    await user.type(add, 'Agent Identity{Enter}');
    expect(within(panel).getByText('Use letters and digits only, starting with a letter.')).toBeInTheDocument();
    await user.clear(add);
    await user.type(add, 'AgentIdentity{Enter}');
    expect(within(panel).getByRole('button', { name: 'Remove the hashtag #MCP' })).toBeInTheDocument();
    expect(within(panel).queryByRole('textbox', { name: 'Add a hashtag to AI agents & security' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText('Pillars saved.');
    const put = pillars.requests.find((request) => request.method === 'PUT');
    expect(put?.ifMatch).toBe('"pillars-1"');
    expect((put?.body as { pillars: { hashtags: string[] }[] }).pillars[0]?.hashtags).toEqual(['AISecurity', 'MCP', 'AgentIdentity']);
  });

  it('refuses shares above 100% before anything is sent', async () => {
    const { pillars, user } = await openPillars({ pillarsEtag: '"pillars-1"' });
    const share = within(pillarPanel()).getByRole('textbox', { name: /^Target share/ });
    await user.clear(share);
    await user.type(share, '90');
    expect(screen.getByText('The shares add up to 156%. Lower them to 100% or less.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByText('1 field needs a fix before saving.')).toBeInTheDocument();
    expect(screen.getAllByText('The target shares add up to more than 100%.').length).toBeGreaterThan(0);
    expect(pillars.requests.some((request) => request.method === 'PUT')).toBe(false);
  });

  it('shows who saved first and keeps mine with the new etag', async () => {
    const { pillars, user } = await openPillars({ pillarsEtag: '"pillars-1"' });
    const topics = within(pillarPanel()).getByRole('textbox', { name: /^Topics/ });
    await user.type(topics, ', agent sandboxes');
    pillars.replace(versioned(pillarSettings(), '"pillars-2"'));
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    const dialog = await screen.findByRole('alertdialog', { name: 'The pillars changed since you opened it' });
    expect(within(dialog).getByText(/You saved a newer version in another tab or on another device/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    await screen.findByText('Pillars saved.');
    const puts = pillars.requests.filter((request) => request.method === 'PUT');
    expect(puts.map((request) => request.ifMatch)).toEqual(['"pillars-1"', '"pillars-2"']);
    expect((puts[1]?.body as { pillars: { topics: string }[] }).pillars[0]?.topics).toMatch(/agent sandboxes$/);
  });

  it('explains a removal the server refuses and puts the pillar back', async () => {
    const { user } = await openPillars({
      pillarsEtag: '"pillars-1"',
      onPillarsPut: () =>
        problemWith(409, 'pillar_in_use', {
          detail: 'A pillar you removed or renamed is still in use. Move what uses it first.',
          pillars: ['governance'],
          references: { governance: ['idea', 'post'] },
        }),
    });
    await user.click(screen.getByRole('tab', { name: /AI governance/ }));
    await user.click(within(pillarPanel()).getByRole('button', { name: 'Remove AI governance' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Remove pillar' }));
    expect(screen.queryByRole('tab', { name: /AI governance/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    const title = await screen.findByText('A pillar you removed is still in use.');
    const alert = title.closest<HTMLElement>('[role="alert"]');
    if (!alert) throw new Error('the refusal is not an alert');
    expect(within(alert).getByText('AI governance: used by ideas and posts.')).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Keep the pillar' }));
    expect(screen.getByRole('tab', { name: /AI governance/ })).toBeInTheDocument();
  });

  it('saves the lane rules with their own etag', async () => {
    const { lanes, pillars, user } = await openPillars({ pillarsEtag: '"pillars-1"' });
    const hooks = await screen.findByRole('textbox', { name: /^Hook variants per post/ });
    await user.clear(hooks);
    await user.type(hooks, '4');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText('Series and lane rules saved.');
    const put = lanes.requests.find((request) => request.method === 'PUT');
    expect(put?.ifMatch).toBe('"lanes-1"');
    expect(put?.body).toMatchObject({ hookVariants: 4, series: [{ id: 'okta-to-entra', pillar: 'identity' }] });
    expect(pillars.requests.some((request) => request.method === 'PUT')).toBe(false);
  });

  it('adds a feed to the pillar it belongs to', async () => {
    const { sources, user } = await openPillars({ pillarsEtag: '"pillars-1"' });
    const panel = pillarPanel();
    await within(panel).findByText('OWASP GenAI blog');
    await user.type(within(panel).getByRole('textbox', { name: 'Feed name' }), 'MCP changelog');
    await user.type(within(panel).getByRole('textbox', { name: 'Feed address' }), 'https://modelcontextprotocol.io/feed.xml');
    await user.click(within(panel).getByRole('button', { name: 'Add feed' }));
    expect(await screen.findByText('Feed added.')).toBeInTheDocument();
    expect(sources[0]?.body).toEqual({ type: 'feed', title: 'MCP changelog', url: 'https://modelcontextprotocol.io/feed.xml', pillar: 'agents' });
  });

  it('asks before leaving with unsaved changes', async () => {
    const { user, router } = await openPillars({ pillarsEtag: '"pillars-1"' });
    await user.type(within(pillarPanel()).getByRole('textbox', { name: /^Topics/ }), ' and more');
    await user.click(screen.getByRole('tab', { name: 'Voice guide' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Leave without saving?' });
    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
    expect(router.state.location.pathname).toBe('/library/pillars');
    expect(within(pillarPanel()).getByRole('textbox', { name: /^Topics/ })).toHaveValue(
      'MCP, multi-agent SecOps, prompt injection, agent identity and more',
    );
  });
});
