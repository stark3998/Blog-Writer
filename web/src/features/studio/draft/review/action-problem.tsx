import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { ApiError } from '@/lib/api/errors';
import { confidentialFindings, isProblem, missingFields, problemCode, problemMessage } from '@/lib/api/problems';

import { GATES } from '../model/gates';

import { UnsavedChangesError } from './use-article-commands';

/**
 * An action's refusal, explained in place (inside its dialog): the server's sentence, what to do
 * about it, and where to go when the fix lives elsewhere in the app.
 */

function gateName(gate: string): string {
  const info = (GATES as Partial<Record<string, { readonly name: string }>>)[gate];
  return info?.name ?? gate;
}

function extensionList(error: unknown, name: string): string[] {
  const value = error instanceof ApiError ? error.problem?.extensions?.[name] : undefined;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

const SETTINGS_FIXES: Readonly<Record<string, { to: string; label: string }>> = {
  ai_use_not_confirmed: { to: '/settings/policy', label: 'Open Blocklist & policy' },
  blocklist_not_configured: { to: '/settings/policy', label: 'Open Blocklist & policy' },
  voice_guide_missing: { to: '/library/voice', label: 'Open the voice guide' },
  portfolio_not_configured: { to: '/settings/integrations', label: 'Open Integrations' },
  publish_in_progress: { to: '/settings/system', label: 'See the running jobs in System' },
  images_disabled: { to: '/settings/models', label: 'Open Settings > Models' },
};

export function ActionProblem({ error, title }: { error: unknown; title: string }) {
  if (!(error instanceof ApiError) || error.kind === 'unauthenticated' || error.kind === 'forbidden') {
    return error ? (
      <Alert variant="error">
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>
          <p>{error instanceof UnsavedChangesError ? error.message : problemMessage(error)}</p>
        </AlertDescription>
      </Alert>
    ) : null;
  }
  const code = problemCode(error);
  const blocking = isProblem(error, 'gates_blocking') ? extensionList(error, 'blocking') : [];
  const missing = missingFields(error);
  const confidential = confidentialFindings(error);
  const fix = code ? SETTINGS_FIXES[code] : undefined;
  return (
    <Alert variant="error">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{problemMessage(error)}</p>
        {blocking.length > 0 ? (
          <p>{`Blocking: ${blocking.map(gateName).join(', ')}.`}</p>
        ) : null}
        {missing.length > 0 ? <p>{`Missing details: ${missing.join(', ')}.`}</p> : null}
        {confidential.length > 0 ? <p>Rephrase the marked text; the blocklisted term itself is never shown.</p> : null}
        {fix ? (
          <p>
            <Link to={fix.to} className="font-medium text-ink underline underline-offset-4">
              {fix.label}
            </Link>
          </p>
        ) : null}
        {error.requestId ? (
          <p className="text-xs">
            Request ID <code className="font-mono">{error.requestId}</code>
          </p>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
