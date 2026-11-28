import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiUrl } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { termsToAdd } from './policy/blocklist-editor';
import { autonomyView, blocklist, policy } from './testing/fixtures';
import { serveSettings } from './testing/handlers';

async function open(options: { viewport?: 'desktop' | 'mobile' } = {}) {
  const view = renderApp('/settings/policy', options);
  await findPageHeading('Settings');
  return view;
}

describe('Settings > Blocklist & policy', () => {
  it('pauses all autonomy with one tap on a phone, without a version', async () => {
    let headers: Headers | undefined;
    serveSettings(
      http.post(apiUrl('/api/kb/autonomy/pause'), ({ request }) => {
        headers = request.headers;
        return HttpResponse.json(autonomyView({ paused: true, pausedAt: '2026-11-27T15:00:00.000000Z' }));
      }),
    );
    const { user } = await open({ viewport: 'mobile' });
    const pause = await screen.findByRole('switch', { name: 'Pause all autonomy' });
    expect(pause).not.toBeChecked();
    await user.click(pause);
    await waitFor(() => {
      expect(pause).toBeChecked();
    });
    expect(headers?.get('If-Match')).toBeNull();
    expect(screen.getByText('Paused')).toBeInTheDocument();
  });

  it('lists what the app still needs, with links to where it is done', async () => {
    serveSettings();
    await open();
    const list = await screen.findByRole('list', { name: 'Owner inputs' });
    expect(within(list).getByText('2 of 5')).toBeInTheDocument();
    expect(within(list).getByRole('link', { name: 'Do it: Past LinkedIn posts you wrote by hand' })).toHaveAttribute(
      'href',
      '/library/voice',
    );
    expect(within(list).queryByRole('link', { name: /Blocklist of client/ })).not.toBeInTheDocument();
  });

  it('adds pasted terms and saves the list with its version', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    serveSettings(
      http.put(apiUrl('/api/kb/blocklist'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(blocklist([{ id: 't-1', term: 'Contoso', category: 'client', match: 'word' }]));
      }),
    );
    const { user } = await open();
    const form = await screen.findByRole('form', { name: 'Blocklist' });
    const entry = within(form).getByRole('textbox', { name: /Add terms/ });
    await user.click(entry);
    await user.paste('Fabrikam\nProject Nightjar\ncontoso');
    await user.click(within(form).getByRole('button', { name: 'Add to the list' }));
    expect(within(form).getByRole('status')).toHaveTextContent('Added 2 terms. Skipped: “contoso” is already on the list.');
    expect(within(within(form).getByRole('list', { name: 'Blocklist terms' })).getAllByRole('listitem')).toHaveLength(3);

    await user.click(within(form).getByRole('button', { name: 'Save the blocklist' }));
    await waitFor(() => {
      expect(sent?.ifMatch).toBe('"blocklist-1"');
    });
    expect(sent?.body).toEqual({
      terms: [
        { id: 't-1', term: 'Contoso', category: 'client', match: 'word' },
        { id: null, term: 'Fabrikam', category: 'client', match: 'word' },
        { id: null, term: 'Project Nightjar', category: 'client', match: 'word' },
      ],
    });
  });

  it('lets the owner save an empty list the first time', async () => {
    let header: string | null = null;
    serveSettings(
      http.get(apiUrl('/api/kb/blocklist'), () => HttpResponse.json(blocklist([], false))),
      http.put(apiUrl('/api/kb/blocklist'), ({ request }) => {
        header = request.headers.get('If-None-Match');
        return HttpResponse.json(blocklist([], true));
      }),
    );
    const { user } = await open();
    const form = await screen.findByRole('form', { name: 'Blocklist' });
    expect(within(form).getByText('The blocklist isn’t saved yet.')).toBeInTheDocument();
    await user.click(within(form).getByRole('button', { name: 'Save an empty blocklist' }));
    await waitFor(() => {
      expect(header).toBe('*');
    });
  });

  it('marks a forbidden topic that names a blocklisted term, without repeating it', async () => {
    serveSettings(
      http.put(apiUrl('/api/kb/policy'), () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Unprocessable',
            status: 422,
            code: 'confidential_text',
            findings: [{ field: 'forbiddenTopics.0', code: 'blocklist', severity: 'block', message: 'Blocked', ref: 't-1' }],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const { user } = await open();
    const form = await screen.findByRole('form', { name: 'Employer policy' });
    await user.click(within(form).getByRole('checkbox', { name: /My employer’s AI-use policy covers/ }));
    await user.click(within(form).getByRole('button', { name: 'Save the policy' }));
    expect(await within(form).findByText('This names something on your blocklist.')).toBeInTheDocument();
    expect(within(form).getByText('Some of this names something on your blocklist.')).toBeInTheDocument();
  });

  it('saves the AI-use confirmation with the rest of the policy', async () => {
    let body: unknown;
    serveSettings(
      http.put(apiUrl('/api/kb/policy'), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(policy({ aiUseConfirmed: true, aiUseConfirmedAt: '2026-11-27T15:00:00.000000Z' }, '"policy-2"'));
      }),
    );
    const { user } = await open();
    const form = await screen.findByRole('form', { name: 'Employer policy' });
    await user.click(within(form).getByRole('checkbox', { name: /My employer’s AI-use policy covers/ }));
    await user.click(within(form).getByRole('button', { name: 'Save the policy' }));
    await waitFor(() => {
      expect(body).toEqual({
        disclaimerShort: 'Views are my own.',
        disclaimerLong: null,
        forbiddenTopics: ['Unreleased products'],
        noEmployerBranding: true,
        aiUseConfirmed: true,
        clearanceStatement: 'I confirm this piece is anonymized and cleared for publication.',
      });
    });
    expect(await within(form).findByText(/Confirmed /)).toBeInTheDocument();
  });

  it('explains why auto-publish is held and switches it with the version', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    serveSettings(
      http.put(apiUrl('/api/kb/autonomy'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(autonomyView({ autoPublishEnabled: true }, { allowed: false, reasons: ['calibrating'] }));
      }),
    );
    const { user } = await open();
    const section = await screen.findByRole('region', { name: 'Auto-publish' });
    expect(await within(section).findByText('4 more clean reviews in a row needed.')).toBeInTheDocument();
    expect(within(section).getByRole('meter', { name: 'Clean reviews in a row' })).toHaveAttribute('aria-valuenow', '2');
    await user.click(within(section).getByRole('switch', { name: 'Allow auto-publish for articles' }));
    await waitFor(() => {
      expect(sent).toEqual({ ifMatch: '"autonomy-1"', body: { autoPublishEnabled: true } });
    });
  });
});

describe('termsToAdd', () => {
  it('takes one term per line, skipping blanks, repeats and lengths outside 2-100', () => {
    expect(termsToAdd('Fabrikam\n\n  Tailspin  \nX\nfabrikam', [{ term: 'Contoso' }])).toEqual({
      terms: ['Fabrikam', 'Tailspin'],
      skipped: ['“X” is shorter than 2 characters', '“fabrikam” is already on the list'],
    });
    expect(termsToAdd('a'.repeat(101), []).skipped).toEqual(['a term is longer than 100 characters']);
  });
});
