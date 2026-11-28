import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiError, kindForStatus, parseProblem } from '@/lib/api/errors';

import { ProblemAlert } from './problem-alert';

function problemError(status: number, code: string, requestId?: string): ApiError {
  return new ApiError({ kind: kindForStatus(status, code), status, problem: parseProblem({ status, code }), requestId });
}

describe('ProblemAlert', () => {
  it('says what failed and why, with the request id and a named retry', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <ProblemAlert
        error={problemError(503, 'store_unavailable', 'req-77')}
        title="Couldn’t load the ideas."
        onRetry={onRetry}
        retryLabel="Try loading the ideas again"
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the ideas.');
    expect(alert).toHaveTextContent('The database is busy. Try again in a moment.');
    expect(alert).toHaveTextContent('Request ID req-77');
    await user.click(screen.getByRole('button', { name: 'Try loading the ideas again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('shows nothing for errors the session screens already cover', () => {
    const { container } = render(
      <>
        <ProblemAlert error={problemError(401, 'unauthenticated')} />
        <ProblemAlert error={problemError(403, 'not_owner')} />
        <ProblemAlert error={Object.assign(new Error('aborted'), { name: 'AbortError' })} />
      </>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
