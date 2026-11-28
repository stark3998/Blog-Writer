import { act, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, describe, expect, it } from 'vitest';

import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import type { GuideView, StyleGuide, VoiceDigest, VoiceSample } from './lib/types';
import { digest, guideView, page, pillarSettings, styleGuide, T2, versioned, voiceSample } from './testing/fixtures';
import { getJson, machinesHandler, problemWith, record, type RecordedRequest } from './testing/handlers';

afterEach(() => {
  act(() => {
    toast.dismiss();
  });
});

interface VoiceOptions {
  view?: GuideView;
  guides?: Record<string, StyleGuide>;
  samples?: VoiceSample[];
  digests?: VoiceDigest[];
  onBuild?: () => Response;
  onCreateSample?: () => Response | undefined;
}

function setup({ view = guideView(), guides = {}, samples = [voiceSample()], digests = [], onBuild, onCreateSample }: VoiceOptions = {}) {
  const requests: RecordedRequest[] = [];
  let currentView = view;
  server.use(
    machinesHandler,
    getJson('/api/kb/pillars', versioned(pillarSettings(), '"pillars-1"')),
    http.get(apiUrl('/api/voice/guide'), () => HttpResponse.json(currentView)),
    http.get(apiUrl('/api/voice/guide/:id'), ({ params }) => {
      const guide = guides[String(params.id)];
      return guide ? HttpResponse.json(versioned(guide, `"${guide.id}-etag"`)) : problemWith(404, 'not_found');
    }),
    http.post(apiUrl('/api/voice/guide/build'), async ({ request }) => {
      await record(requests, request);
      return onBuild?.() ?? HttpResponse.json({ jobId: 'voice-build-1', status: 'queued' }, { status: 202 });
    }),
    http.post(apiUrl('/api/voice/guide/:id/activate'), async ({ request, params }) => {
      await record(requests, request);
      const guide = guides[String(params.id)];
      if (!guide) return problemWith(404, 'not_found');
      const active = { ...guide, status: 'active' as const, approvedAt: T2 };
      currentView = { ...currentView, active: versioned(active, '"active-etag"'), versions: currentView.versions.map((v) => (v.id === guide.id ? { ...v, status: 'active' as const } : v)) };
      return HttpResponse.json(versioned(active, '"active-etag"'));
    }),
    http.post(apiUrl('/api/voice/guide/:id/versions'), async ({ request, params }) => {
      const recorded = await record(requests, request);
      const base = guides[String(params.id)] ?? styleGuide();
      const created = { ...base, ...(recorded.body as object), id: 'style-guide-0003', version: 3, status: 'proposed' as const, editedByOwner: true, basedOnVersion: base.version };
      guides[created.id] = created;
      currentView = { ...currentView, versions: [{ id: created.id, version: 3, status: 'proposed', createdAt: T2 }, ...currentView.versions] };
      return HttpResponse.json(versioned(created, '"v3-etag"'), { status: 201 });
    }),
    http.get(apiUrl('/api/voice/samples'), () => HttpResponse.json(page(samples.map((sample) => versioned(sample, `"${sample.id}-etag"`))))),
    http.post(apiUrl('/api/voice/samples'), async ({ request }) => {
      const recorded = await record(requests, request);
      return onCreateSample?.() ?? HttpResponse.json(versioned(voiceSample({ id: 'sample-0002', ...(recorded.body as object) })), { status: 201 });
    }),
    http.post(apiUrl('/api/voice/samples/:id/status'), async ({ request }) => {
      const recorded = await record(requests, request);
      return HttpResponse.json(versioned(voiceSample({ status: (recorded.body as { status: 'active' | 'excluded' }).status }), '"sample-0001-etag-2"'));
    }),
    http.get(apiUrl('/api/voice/digests'), () => HttpResponse.json(page(digests.map((item) => versioned(item, `"${item.id}-etag"`))))),
    http.post(apiUrl('/api/voice/digests/:digest/suggestions/:suggestion'), async ({ request }) => {
      await record(requests, request);
      return HttpResponse.json(versioned(digest(), '"digest-etag-2"'));
    }),
  );
  return { requests };
}

async function openVoice(options?: VoiceOptions) {
  const state = setup(options);
  const view = renderApp('/library/voice');
  await findPageHeading('Library');
  await screen.findByText(/LinkedIn posts you wrote by hand/);
  return { ...state, ...view };
}

const ACTIVE = styleGuide();
const PROPOSED = styleGuide({
  id: 'style-guide-0003',
  version: 3,
  status: 'proposed',
  approvedAt: null,
  basedOnVersion: 2,
  body: 'Short sentences. Lead with the outcome.\n\nNo hype words.',
  rules: { prefer: ['Open with the outcome'], avoid: ['Rhetorical questions'], bannedPhrases: ['game changer'], vocabulary: {} },
});

describe('Library > Voice guide', () => {
  it('counts the hand-written LinkedIn posts and waits for enough to build', async () => {
    await openVoice();
    expect(screen.getByText('3 of 5')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: /LinkedIn posts you wrote by hand/ })).toHaveAttribute('aria-valuenow', '3');
    expect(screen.getByText(/Add 2 more posts to build your guide/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Build your guide' })).toBeDisabled();
    expect(screen.getByText(/No guide in use yet/)).toBeInTheDocument();
  });

  it('shows each sample with its AI tells highlighted', async () => {
    const { user } = await openVoice();
    const row = await screen.findByRole('button', { name: /Agent identity first/ });
    expect(row).toHaveTextContent('LinkedIn post, 16 words, 1 flag, 6.3 per 100 words');
    await user.click(row);
    expect(await screen.findByText('delve', { selector: 'mark' })).toBeInTheDocument();
    const flags = screen.getByRole('complementary', { name: 'AI tells in this sample' });
    expect(within(flags).getByText('AI tell: ‘delve’.')).toBeInTheDocument();
    expect(within(flags).getByText(/never quoted as examples/)).toBeInTheDocument();
  });

  it('excludes a sample with its etag', async () => {
    const { requests, user } = await openVoice();
    await user.click(await screen.findByRole('button', { name: /Agent identity first/ }));
    await user.click(await screen.findByRole('button', { name: 'Exclude from the guide' }));
    expect(await screen.findByText('Sample excluded from the guide.')).toBeInTheDocument();
    const status = requests.find((request) => request.path.endsWith('/status'));
    expect(status?.ifMatch).toBe('"sample-0001-etag"');
    expect(status?.body).toEqual({ status: 'excluded' });
  });

  it('adds a sample you wrote without AI help', async () => {
    const { requests, user } = await openVoice();
    await user.click(screen.getByRole('button', { name: 'Add a sample' }));
    await user.type(screen.getByRole('textbox', { name: /^Text/ }), 'I keep agents on a short leash.');
    await user.click(screen.getByRole('checkbox', { name: /I wrote this without AI help/ }));
    await user.click(screen.getByRole('button', { name: 'Add sample' }));
    expect(await screen.findByText('Sample added.')).toBeInTheDocument();
    expect(requests.find((request) => request.path === '/api/voice/samples')?.body).toEqual({
      sourceType: 'linkedin_post',
      text: 'I keep agents on a short leash.',
      url: null,
      title: null,
      postedAt: null,
      pillar: null,
      ownerUnassisted: true,
    });
  });

  it('marks blocklisted text in a new sample', async () => {
    const { user } = await openVoice({
      onCreateSample: () =>
        problemWith(422, 'confidential_text', {
          detail: 'Some text matches the blocklist.',
          findings: [{ field: 'text', code: 'blocklist', severity: 'block', message: 'Matches a blocklisted client name.', ref: 't-1' }],
        }),
    });
    await user.click(screen.getByRole('button', { name: 'Add a sample' }));
    const text = screen.getByRole('textbox', { name: /^Text/ });
    await user.type(text, 'Our work for a client.');
    await user.click(screen.getByRole('button', { name: 'Add sample' }));
    expect(await screen.findByText('Matches a blocklisted client name.')).toBeInTheDocument();
    expect(text).toHaveAttribute('aria-invalid', 'true');
  });

  it('shows a proposed version against the one in use and approves it', async () => {
    const { requests, user } = await openVoice({
      view: guideView({
        active: versioned(ACTIVE, '"active-etag"'),
        versions: [
          { id: PROPOSED.id, version: 3, status: 'proposed', createdAt: T2 },
          { id: ACTIVE.id, version: 2, status: 'active', createdAt: ACTIVE.createdAt, approvedAt: ACTIVE.approvedAt ?? null },
        ],
        linkedinSamples: 6,
        canBuild: true,
      }),
      guides: { [PROPOSED.id]: PROPOSED, [ACTIVE.id]: ACTIVE },
    });
    const card = await screen.findByRole('region', { name: 'Version 3 is waiting for your approval' });
    expect(await within(card).findByRole('group', { name: /Changes to the guide's text in version 3/ })).toBeInTheDocument();
    expect(within(card).getByText('Open with the outcome', { selector: 'ins' })).toBeInTheDocument();
    expect(within(card).getByText('Lead with the decision', { selector: 'del' })).toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: 'Approve version 3' }));
    expect(await screen.findByText('Version 3 is now your guide.')).toBeInTheDocument();
    const activate = requests.find((request) => request.path.endsWith('/activate'));
    expect(activate?.path).toBe('/api/voice/guide/style-guide-0003/activate');
    expect(activate?.ifMatch).toBe('"style-guide-0003-etag"');
    expect(await screen.findByRole('heading', { name: 'Version 3, in use' })).toBeInTheDocument();
  });

  it('saves an edit as a new version that waits for approval', async () => {
    const { requests, user } = await openVoice({
      view: guideView({
        active: versioned(ACTIVE, '"active-etag"'),
        versions: [{ id: ACTIVE.id, version: 2, status: 'active', createdAt: ACTIVE.createdAt }],
        linkedinSamples: 6,
        canBuild: true,
      }),
      guides: { [ACTIVE.id]: ACTIVE },
    });
    await user.click(await screen.findByRole('button', { name: 'Edit the guide' }));
    const body = screen.getByRole('textbox', { name: /^The guide/ });
    await user.type(body, '\n\nName the trade-off.');
    await user.click(screen.getByRole('button', { name: 'Save as a new version' }));
    expect(await screen.findByText('Saved as version 3. Approve it to start using it.')).toBeInTheDocument();
    const created = requests.find((request) => request.path.endsWith('/versions'));
    expect(created?.path).toBe('/api/voice/guide/style-guide-0002/versions');
    expect((created?.body as { body: string }).body).toMatch(/Name the trade-off\.$/);
    expect(await screen.findByRole('region', { name: 'Version 3 is waiting for your approval' })).toBeInTheDocument();
  });

  it('starts a build and follows it', async () => {
    const { requests, user } = await openVoice({ view: guideView({ linkedinSamples: 5, canBuild: true }) });
    await user.click(screen.getByRole('button', { name: 'Build your guide' }));
    expect(await screen.findByRole('heading', { name: 'Building your guide' })).toBeInTheDocument();
    expect(requests.some((request) => request.path === '/api/voice/guide/build')).toBe(true);
  });

  it('says why a build cannot start', async () => {
    const { user } = await openVoice({
      view: guideView({ linkedinSamples: 5, canBuild: true }),
      onBuild: () => problemWith(409, 'ai_use_not_confirmed'),
    });
    await user.click(screen.getByRole('button', { name: 'Build your guide' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The guide can’t be built yet.');
    expect(alert).toHaveTextContent('Confirm your employer’s AI-use policy');
  });

  it('accepts a digest suggestion with the digest’s etag', async () => {
    const { requests, user } = await openVoice({ digests: [digest()] });
    expect(await screen.findByText('Open with the outcome, not the question.')).toBeInTheDocument();
    expect(screen.getByText(/you changed 34% of the AI draft/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Accept: Open with the outcome, not the question.' }));
    expect(await screen.findByText(/Added to a new version of your guide/)).toBeInTheDocument();
    const decision = requests.find((request) => request.path.includes('/suggestions/'));
    expect(decision?.path).toBe('/api/voice/digests/digest-2026-11-16/suggestions/sug-1');
    expect(decision?.ifMatch).toBe('"digest-2026-11-16-etag"');
    expect(decision?.body).toEqual({ decision: 'accept' });
  });
});
