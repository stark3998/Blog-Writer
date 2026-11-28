import { Siren } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field } from '@/components/ui/field';
import { useApiMutation } from '@/lib/query/api-query';

/**
 * The out-of-band alert check (contract I-2): one `test` alert event, which Azure Monitor turns
 * into an e-mail, and, when asked, a test notification to every subscribed device.
 */
export function AlertsPanel() {
  const [push, setPush] = useState(true);
  const send = useApiMutation('post', '/api/system/test-alert', {
    successToast: 'Test alert sent.',
    errorToast: 'Couldn’t send the test alert.',
  });
  const result = send.data;
  return (
    <div className="grid max-w-measure justify-items-start gap-4">
      <Field
        orientation="horizontal"
        label="Also send a test notification to my devices"
        description="It shows a title only and opens this page when tapped."
      >
        <Checkbox
          checked={push}
          onCheckedChange={(checked) => {
            setPush(checked === true);
          }}
        />
      </Field>
      <Button
        variant="secondary"
        disabled={send.isPending}
        onClick={() => {
          send.mutate({ body: { push } });
        }}
      >
        <Siren aria-hidden="true" />
        {send.isPending ? 'Sending…' : 'Send a test alert'}
      </Button>
      {result ? (
        <p role="status" className="text-sm text-ink-muted">
          The alert is on its way: expect the e-mail within a few minutes.
          {result.push
            ? ` Notifications: ${String(result.push.delivered)} delivered, ${String(result.push.failed)} failed${
                result.push.expired > 0 ? `, ${String(result.push.expired)} no longer subscribed` : ''
              }.`
            : null}
        </p>
      ) : null}
    </div>
  );
}
