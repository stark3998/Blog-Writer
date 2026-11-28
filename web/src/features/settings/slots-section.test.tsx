import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiUrl } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { cadence, schedule } from './testing/fixtures';
import { serveSettings } from './testing/handlers';

async function open() {
  const view = renderApp('/settings/slots');
  await findPageHeading('Settings');
  await screen.findByRole('form', { name: 'Cadence' });
  return view;
}

describe('Settings > Slots', () => {
  it('lists the post slots, the calendar anchors and the schedules', async () => {
    serveSettings();
    await open();
    const slots = screen.getByRole('list', { name: 'Post slots' });
    expect(within(slots).getAllByRole('listitem')).toHaveLength(5);
    expect(within(slots).getByRole('group', { name: 'Slot 1' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /Time zone/ })).toHaveValue('America/New_York');
    expect(screen.getByRole('group', { name: 'Veto notice goes out' })).toBeInTheDocument();

    const list = await screen.findByRole('list', { name: 'Schedules' });
    expect(within(list).getByText('Every 6 hours', { exact: false })).toBeInTheDocument();
    expect(within(list).getByText('Every day at 03:30', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('1 schedule is waiting for you.')).toBeInTheDocument();
  });

  it('saves a changed slot time with the version it read, and refreshes the schedules', async () => {
    let body: unknown;
    let ifMatch: string | null = null;
    let scheduleReads = 0;
    serveSettings(
      http.put(apiUrl('/api/kb/cadence'), async ({ request }) => {
        ifMatch = request.headers.get('If-Match');
        body = await request.json();
        return HttpResponse.json({ ...cadence(), etag: '"cadence-2"' });
      }),
      http.get(apiUrl('/api/schedules'), () => {
        scheduleReads += 1;
        return HttpResponse.json([schedule()]);
      }),
    );
    const { user } = await open();
    await waitFor(() => {
      expect(scheduleReads).toBe(1);
    });

    fireEvent.change(screen.getByLabelText('Slot 1: time'), { target: { value: '09:30' } });
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save the cadence' }));

    await waitFor(() => {
      expect(ifMatch).toBe('"cadence-1"');
    });
    expect((body as { postSlots: unknown[] }).postSlots[0]).toEqual({ day: 'tue', time: '09:30', kind: 'primary', windowMinutes: 120 });
    await waitFor(() => {
      expect(scheduleReads).toBe(2);
    });
  });

  it('stops a second slot on a busy day before sending, and focuses it', async () => {
    let calls = 0;
    serveSettings(
      http.put(apiUrl('/api/kb/cadence'), () => {
        calls += 1;
        return HttpResponse.json(cadence());
      }),
    );
    const { user } = await open();
    // All five weekdays already have a slot: the new one starts on Saturday. Move it to Tuesday.
    await user.click(screen.getByRole('button', { name: 'Add a slot' }));
    const added = screen.getByRole('group', { name: 'Slot 6' });
    fireEvent.change(within(added).getByLabelText('Slot 6: time'), { target: { value: '16:00' } });
    await user.click(within(added).getByRole('combobox', { name: 'Slot 6: day' }));
    await user.click(await screen.findByRole('option', { name: 'Tuesday' }));
    await user.click(screen.getByRole('button', { name: 'Save the cadence' }));

    const day = within(added).getByRole('combobox', { name: 'Slot 6: day' });
    expect(day).toHaveAttribute('aria-invalid', 'true');
    expect(day).toHaveFocus();
    expect(within(added).getByText('Two slots on one day while two posts on a day are blocked.')).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it('marks a slot listed twice at its time', async () => {
    serveSettings();
    const { user } = await open();
    await user.click(screen.getByRole('button', { name: 'Add a slot' }));
    const added = screen.getByRole('group', { name: 'Slot 6' });
    await user.click(within(added).getByRole('combobox', { name: 'Slot 6: day' }));
    await user.click(await screen.findByRole('option', { name: 'Tuesday' }));
    await user.click(screen.getByRole('button', { name: 'Save the cadence' }));
    const time = within(added).getByLabelText('Slot 6: time');
    expect(time).toHaveAttribute('aria-invalid', 'true');
    expect(time).toHaveFocus();
    expect(within(added).getByText('Listed twice.')).toBeInTheDocument();
  });

  it('removes a slot and moves focus to “Add a slot”', async () => {
    serveSettings();
    const { user } = await open();
    await user.click(screen.getByRole('button', { name: 'Remove Slot 5 (Friday 15:00)' }));
    expect(within(screen.getByRole('list', { name: 'Post slots' })).getAllByRole('listitem')).toHaveLength(4);
    expect(screen.getByRole('button', { name: 'Add a slot' })).toHaveFocus();
  });

  it('shows the server’s field errors where they belong', async () => {
    serveSettings(
      http.put(apiUrl('/api/kb/cadence'), () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Unprocessable',
            status: 422,
            code: 'validation_failed',
            errors: [{ loc: ['body', 'stageLeadMinutes'], msg: 'Too short for uploads.', type: 'value_error' }],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const { user } = await open();
    fireEvent.change(screen.getByRole('spinbutton', { name: /Get posts ready before the slot/ }), { target: { value: '20' } });
    await user.click(screen.getByRole('button', { name: 'Save the cadence' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Staging: Too short for uploads.');
    const field = screen.getByRole('spinbutton', { name: /Get posts ready before the slot/ });
    await waitFor(() => {
      expect(field).toHaveAttribute('aria-invalid', 'true');
    });
  });

  it('saves the defaults the first time with If-None-Match', async () => {
    let header: string | null = null;
    serveSettings(
      http.get(apiUrl('/api/kb/cadence'), () => HttpResponse.json(cadence(null))),
      http.put(apiUrl('/api/kb/cadence'), ({ request }) => {
        header = request.headers.get('If-None-Match');
        return HttpResponse.json(cadence('"cadence-1"'));
      }),
    );
    const { user } = await open();
    expect(screen.getByText('The defaults apply until you save.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save the cadence' }));
    await waitFor(() => {
      expect(header).toBe('*');
    });
  });

  it('pauses a schedule and settles a missed run', async () => {
    const calls: string[] = [];
    serveSettings(
      http.post(apiUrl('/api/schedules/:id/pause'), ({ params }) => {
        calls.push(`pause ${String(params.id)}`);
        return HttpResponse.json(schedule({ status: 'paused' }));
      }),
      http.post(apiUrl('/api/schedules/:id/confirm'), async ({ params, request }) => {
        calls.push(`confirm ${String(params.id)} ${JSON.stringify(await request.json())}`);
        return HttpResponse.json(schedule({ id: String(params.id) }));
      }),
    );
    const { user } = await open();
    await user.click(await screen.findByRole('button', { name: 'Pause “Crawl feeds and triage new items”' }));
    await user.click(screen.getByRole('button', { name: 'Run the missed “Daily backup of the durable containers” now' }));
    await waitFor(() => {
      expect(calls).toEqual(['pause sched-signals-crawl', 'confirm sched-backups-dump {"run":true}']);
    });
  });
});
