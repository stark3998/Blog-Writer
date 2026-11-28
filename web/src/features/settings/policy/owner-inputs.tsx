import { Circle, CircleCheck } from 'lucide-react';
import { Link } from 'react-router';

import { Progress } from '@/components/ui/progress';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiQuery } from '@/lib/query/api-query';
import { cn } from '@/lib/cn';

import { BlockLoading } from '../components/settings-block';
import { settingsKeys } from '../queries';

/** Only same-app paths become links (the server sends paths such as `/library/voice`). */
function appPath(href: string): string | undefined {
  return /^\/(?![/\\])/.test(href) ? href : undefined;
}

/**
 * What only the owner can provide (`GET /api/kb/inputs`, contract section 12): each with its count
 * where it is counted and a link to where it is done. Today shows the open ones as banners.
 */
export function OwnerInputsPanel() {
  const inputs = useApiQuery({ queryKey: settingsKeys.ownerInputs, path: '/api/kb/inputs' });
  if (inputs.isPending) return <BlockLoading label="Loading what the app still needs from you" lines={5} />;
  if (inputs.isError) {
    return (
      <ProblemAlert
        error={inputs.error}
        title="Couldn’t load the owner inputs."
        onRetry={() => void inputs.refetch()}
        retryLabel="Try loading the owner inputs again"
      />
    );
  }
  const items = inputs.data.items;
  const done = items.filter((item) => item.done).length;
  return (
    <div className="max-w-measure">
      <div className="flex items-center gap-4">
        <Progress
          value={done}
          max={Math.max(items.length, 1)}
          tone={done === items.length ? 'success' : 'ink'}
          aria-label="Owner inputs in place"
          getValueLabel={() => `${String(done)} of ${String(items.length)} in place`}
          className="max-w-48"
        />
        <p className="text-sm text-ink-muted">{`${String(done)} of ${String(items.length)} in place`}</p>
      </div>
      <ul aria-label="Owner inputs" className="mt-4 border-t border-line">
        {items.map((item) => {
          const href = appPath(item.href);
          const counted = typeof item.have === 'number' && typeof item.need === 'number';
          return (
            <li key={item.code} className="flex items-start gap-3 border-b border-line py-3">
              {item.done ? (
                <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-success" />
              ) : (
                <Circle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
              )}
              <div className="min-w-0 flex-1">
                <p className={cn(item.done ? 'text-ink-muted' : 'font-medium text-ink')}>
                  {item.label}
                  <span className="sr-only">{item.done ? ': in place' : ': still needed'}</span>
                </p>
                {counted ? (
                  <p className="text-sm text-ink-subtle">{`${String(item.have)} of ${String(item.need)}`}</p>
                ) : null}
              </div>
              {href && !item.done ? (
                <Link
                  to={href}
                  className="inline-flex min-h-11 items-center text-sm font-medium text-ink underline decoration-line-strong underline-offset-4 md:min-h-0"
                  aria-label={`Do it: ${item.label}`}
                >
                  Do it
                </Link>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
