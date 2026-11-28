import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiUrl, problem } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import type * as Navigation from './integrations/navigation';
import { leaveForLinkedIn } from './integrations/navigation';
import { connection, jobView, page, profileChecklist, systemStatus } from './testing/fixtures';
import { serveSettings } from './testing/handlers';

vi.mock('./integrations/navigation', async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, leaveForLinkedIn: vi.fn() };
});

const AUTHORIZE = 'https://www.linkedin.com/oauth/v2/authorization?response_type=code&state=s';

async function open() {
  const view = renderApp('/settings/integrations');
  await findPageHeading('Settings');
  return view;
}

function linkedinSection() {
  return screen.getByRole('region', { name: 'LinkedIn' });
}

afterEach(() => {
  vi.mocked(leaveForLinkedIn).mockReset();
  Reflect.deleteProperty(window.navigator, 'serviceWorker');
});

describe('Settings > Integrations: LinkedIn', () => {
  it('shows a connected token on its 60-day ruler', async () => {
    serveSettings();
    await open();
    const section = linkedinSection();
    expect(await within(section).findByText('Connected')).toBeInTheDocument();
    const ruler = within(section).getByRole('meter', { name: 'LinkedIn token age' });
    expect(ruler).toHaveAttribute('aria-valuenow', '12');
    expect(ruler).toHaveAttribute('aria-valuetext', 'Day 12 of 60. 48 days left.');
    expect(within(section).getByRole('button', { name: 'Check the token now' })).toBeInTheDocument();
  });

  it('shows the day-50 banner with a reconnect', async () => {
    serveSettings(
      http.get(apiUrl('/api/linkedin/connection'), () =>
        HttpResponse.json(connection({ tokenDay: 52, bannerDue: true, expiresAt: null })),
      ),
    );
    await open();
    const section = linkedinSection();
    expect(await within(section).findByText('Your LinkedIn connection ends in 8 days.')).toBeInTheDocument();
    expect(within(section).getByRole('meter', { name: 'LinkedIn token age' })).toHaveAttribute(
      'aria-valuetext',
      'Day 52 of 60. 8 days left: reconnect before then.',
    );
    expect(within(section).getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
  });

  it('leaves for LinkedIn’s own sign-in page, and only for it', async () => {
    let answer = AUTHORIZE;
    serveSettings(
      http.get(apiUrl('/api/linkedin/connection'), () =>
        HttpResponse.json(connection({ status: 'disconnected', connectedAt: null, expiresAt: null, tokenDay: null })),
      ),
      http.post(apiUrl('/api/linkedin/oauth/start'), () => HttpResponse.json({ authorizeUrl: answer })),
    );
    const error = vi.spyOn(toast, 'error');
    const { user } = await open();
    const connect = await within(linkedinSection()).findByRole('button', { name: 'Connect LinkedIn' });
    await user.click(connect);
    await waitFor(() => {
      expect(leaveForLinkedIn).toHaveBeenCalledWith(AUTHORIZE);
    });

    answer = 'https://evil.example.com/oauth?state=s';
    await user.click(connect);
    await waitFor(() => {
      expect(error).toHaveBeenCalledWith('Couldn’t start connecting LinkedIn.', expect.anything());
    });
    expect(leaveForLinkedIn).toHaveBeenCalledTimes(1);
  });

  it('says why the sign-in did not connect, once, on the way back', async () => {
    window.sessionStorage.setItem('bw:linkedin-connect-started', new Date().toISOString());
    serveSettings(
      http.get(apiUrl('/api/linkedin/connection'), () =>
        HttpResponse.json(
          connection({ status: 'disconnected', connectedAt: null, expiresAt: null, tokenDay: null, lastError: 'oauth_denied' }),
        ),
      ),
    );
    await open();
    const section = linkedinSection();
    expect(await within(section).findByText('LinkedIn didn’t connect.')).toBeInTheDocument();
    expect(within(section).getByText(/You cancelled on LinkedIn’s page/)).toBeInTheDocument();
    expect(window.sessionStorage.getItem('bw:linkedin-connect-started')).toBeNull();
  });

  it('asks before disconnecting', async () => {
    let disconnected = false;
    serveSettings(
      http.post(apiUrl('/api/linkedin/disconnect'), () => {
        disconnected = true;
        return HttpResponse.json(connection({ status: 'disconnected', connectedAt: null, expiresAt: null, tokenDay: null }));
      }),
    );
    const { user } = await open();
    await user.click(await within(linkedinSection()).findByRole('button', { name: 'Disconnect' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Disconnect LinkedIn?' });
    expect(within(confirm).getByRole('button', { name: 'Stay connected' })).toHaveFocus();
    await user.click(within(confirm).getByRole('button', { name: 'Disconnect' }));
    expect(await within(linkedinSection()).findByRole('button', { name: 'Connect LinkedIn' })).toBeInTheDocument();
    expect(disconnected).toBe(true);
  });

  it('explains when the server has no LinkedIn app', async () => {
    serveSettings(
      http.get(apiUrl('/api/linkedin/connection'), () =>
        HttpResponse.json(connection({ status: 'disconnected', configured: false, connectedAt: null, tokenDay: null })),
      ),
    );
    await open();
    expect(await within(linkedinSection()).findByText('Not set up')).toBeInTheDocument();
    expect(within(linkedinSection()).queryByRole('button', { name: /Connect/ })).not.toBeInTheDocument();
  });
});

/** A valid uncompressed P-256 point, base64url (65 bytes, first 0x04). */
const SERVER_KEY = btoa(String.fromCharCode(4, ...Array.from({ length: 64 }, () => 7)))
  .replace(/\+/g, '-')
  .replace(/\//g, '_')
  .replace(/=+$/, '');

function fakePush() {
  const endpoint = 'https://fcm.googleapis.com/fcm/send/device-1';
  let current: PushSubscription | null = null;
  const subscription = {
    endpoint,
    options: { applicationServerKey: null, userVisibleOnly: true },
    toJSON: () => ({ endpoint, expirationTime: null, keys: { p256dh: 'BPublicKey', auth: 'AuthSecret' } }),
    unsubscribe: vi.fn(() => {
      current = null;
      return Promise.resolve(true);
    }),
  } as unknown as PushSubscription;
  const pushManager = {
    getSubscription: vi.fn(() => Promise.resolve(current)),
    subscribe: vi.fn(() => {
      current = subscription;
      return Promise.resolve(subscription);
    }),
  };
  const registration = { pushManager } as unknown as ServiceWorkerRegistration;
  const notification = {
    permission: 'default' as NotificationPermission,
    requestPermission: vi.fn(() => {
      notification.permission = 'granted';
      return Promise.resolve('granted' as NotificationPermission);
    }),
  };
  vi.stubGlobal('PushManager', function PushManager() {
    return undefined;
  });
  vi.stubGlobal('Notification', notification);
  Object.defineProperty(window.navigator, 'serviceWorker', {
    configurable: true,
    value: { getRegistration: () => Promise.resolve(registration), controller: null, addEventListener: vi.fn(), removeEventListener: vi.fn() },
  });
  return { endpoint, pushManager, notification };
}

async function idFor(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 32);
}

describe('Settings > Integrations: push', () => {
  it('says when this browser cannot receive notifications', async () => {
    serveSettings();
    await open();
    const section = screen.getByRole('region', { name: 'Notifications on this device' });
    expect(await within(section).findByText('Not available here')).toBeInTheDocument();
    expect(within(section).getByText(/can’t receive notifications for the app/)).toBeInTheDocument();
  });

  it('asks for permission on the tap, subscribes with the server key and registers the device', async () => {
    const { endpoint, pushManager, notification } = fakePush();
    let registered: unknown;
    let removed: string | undefined;
    serveSettings(
      http.get(apiUrl('/api/push/key'), () => HttpResponse.json({ applicationServerKey: SERVER_KEY })),
      http.post(apiUrl('/api/push/subscriptions'), async ({ request }) => {
        registered = await request.json();
        return HttpResponse.json({ id: 'sub-1', active: true, createdAt: '2026-11-27T15:00:00.000000Z' }, { status: 201 });
      }),
      http.delete(apiUrl('/api/push/subscriptions/:id'), ({ params }) => {
        removed = String(params.id);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = await open();
    const section = screen.getByRole('region', { name: 'Notifications on this device' });
    await user.click(await within(section).findByRole('button', { name: 'Turn on notifications' }));

    expect(await within(section).findByText('On for this device')).toBeInTheDocument();
    expect(notification.requestPermission).toHaveBeenCalledTimes(1);
    expect(pushManager.subscribe).toHaveBeenCalledWith(expect.objectContaining({ userVisibleOnly: true }));
    expect(registered).toEqual({ endpoint, expirationTime: null, keys: { p256Dh: 'BPublicKey', auth: 'AuthSecret' } });

    await user.click(within(section).getByRole('button', { name: 'Turn off' }));
    expect(await within(section).findByText('Off on this device')).toBeInTheDocument();
    expect(removed).toBe(await idFor(endpoint));
  });

  it('explains a blocked permission', async () => {
    const { notification } = fakePush();
    notification.permission = 'denied';
    serveSettings();
    await open();
    const section = screen.getByRole('region', { name: 'Notifications on this device' });
    expect(await within(section).findByText('Blocked')).toBeInTheDocument();
    expect(within(section).getByText(/Notifications are blocked for this site/)).toBeInTheDocument();
  });

  it('says when the server has no push key', async () => {
    fakePush();
    serveSettings(http.get(apiUrl('/api/push/key'), () => problem(503, 'push_unavailable')));
    await open();
    const section = screen.getByRole('region', { name: 'Notifications on this device' });
    expect(await within(section).findByText(/Push isn’t set up on the server yet/)).toBeInTheDocument();
  });
});

describe('Settings > Integrations: services and the checklist', () => {
  it('shows the portfolio with its last publish and Web IQ’s state', async () => {
    serveSettings(
      http.get(apiUrl('/api/jobs'), ({ request }) => {
        const type = new URL(request.url).searchParams.get('type');
        const job = jobView({
          jobId: 'job-publish-1',
          type: 'publish.article',
          status: 'failed',
          steps: [{ name: 'wait_build', status: 'failed', runs: 1, startedAt: null, finishedAt: null }],
          error: { code: 'build_failed', step: 'wait_build', detail: 'The portfolio build failed.', at: '2026-11-27T14:00:00.000000Z' },
        });
        return HttpResponse.json(page(type === 'publish.article' ? [{ item: job, etag: '"j"' }] : []));
      }),
      http.get(apiUrl('/api/system/status'), () => HttpResponse.json(systemStatus())),
    );
    await open();
    const services = screen.getByRole('region', { name: 'Publishing and research services' });
    expect(await within(services).findByText('The portfolio build failed.')).toBeInTheDocument();
    expect(within(services).getByText('Step: Wait for the build.')).toBeInTheDocument();
    expect(within(services).getByText('Failing for now. Requests use the direct fetcher until it recovers.')).toBeInTheDocument();
  });

  it('saves a tick on the profile checklist with its version', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    serveSettings(
      http.put(apiUrl('/api/engagement/profile-checklist'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        const next = profileChecklist('"checklist-2"');
        return HttpResponse.json(next);
      }),
    );
    const { user } = await open();
    const list = await screen.findByRole('list', { name: 'Profile checklist' });
    await user.click(within(list).getByRole('checkbox', { name: 'About section matches the pillars and your voice' }));
    await waitFor(() => {
      expect(sent?.ifMatch).toBe('"checklist-1"');
    });
    expect(sent?.body).toEqual({
      items: [
        { id: 'headline', label: 'Headline names your three pillars', done: true, note: null },
        { id: 'about', label: 'About section matches the pillars and your voice', done: true, note: null },
        { id: 'featured', label: 'Featured section shows your best flagship pieces', done: false, note: null },
      ],
    });
  });

  it('reloads the checklist when another tab changed it', async () => {
    let reads = 0;
    serveSettings(
      http.get(apiUrl('/api/engagement/profile-checklist'), () => {
        reads += 1;
        return HttpResponse.json(profileChecklist(`"checklist-${String(reads)}"`));
      }),
      http.put(apiUrl('/api/engagement/profile-checklist'), () => problem(412, 'etag_mismatch')),
    );
    const error = vi.spyOn(toast, 'error');
    const { user } = await open();
    const list = await screen.findByRole('list', { name: 'Profile checklist' });
    await user.click(within(list).getByRole('checkbox', { name: 'Featured section shows your best flagship pieces' }));
    await waitFor(() => {
      expect(error).toHaveBeenCalledWith('The checklist changed in another tab.', expect.anything());
    });
    await waitFor(() => {
      expect(reads).toBe(2);
    });
  });
});
