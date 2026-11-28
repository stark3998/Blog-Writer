import type { Editor } from '@tiptap/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { DataProvider } from '@/features/data/data-provider';
import { ArticleCodec, DialectError, utf8ByteLength, EDITOR_MAX_BYTES } from '@/features/editor';
import { createTestAppData, seedSession, type TestAppData } from '@/test/app-data';
import { ThemeProvider } from '@/theme/theme-provider';

import { DraftEnvironmentProvider, type DraftEnvironment, type DialectService } from '../environment';

/**
 * The Draft tab in tests: an in-thread parser instead of the worker (jsdom has none), short
 * timings, and the ProseMirror layout calls jsdom lacks.
 */

export const TEST_TIMINGS: DraftEnvironment['timings'] = {
  autosaveMs: 25,
  autosaveMaxWaitMs: 400,
  retryMs: [20],
  lintMs: 30,
  liveParseMs: 5,
  parseTimeoutMs: 2000,
  diagramMs: 5,
};

/** Parses with the real codec on the main thread, or fails the way the worker would. */
export function testDialect(failure?: DialectError): DialectService {
  let codec: ArticleCodec | undefined;
  return {
    parse: (markdown) => {
      if (failure) return Promise.reject(failure);
      if (utf8ByteLength(markdown) > EDITOR_MAX_BYTES) {
        return Promise.reject(new DialectError('too-large', 'The article is larger than 128 KB, the most the editor opens.'));
      }
      codec ??= new ArticleCodec();
      return Promise.resolve(codec.parse(markdown));
    },
  };
}

export function testEnvironment(overrides: Partial<DraftEnvironment> = {}): DraftEnvironment {
  return { dialect: testDialect(), timings: TEST_TIMINGS, ...overrides };
}

/** ProseMirror measures text ranges; jsdom has no layout, so every rectangle is empty. */
export function installEditorLayout(): void {
  const rect = () => new DOMRect(0, 0, 0, 0);
  const rects = () => Object.assign([rect()], { item: () => rect() }) as unknown as DOMRectList;
  for (const proto of [Range.prototype, Element.prototype] as { getClientRects?: unknown; getBoundingClientRect?: unknown }[]) {
    if (typeof proto.getClientRects !== 'function' || proto === Range.prototype) {
      Object.defineProperty(proto, 'getClientRects', { value: rects, configurable: true, writable: true });
    }
    if (typeof proto.getBoundingClientRect !== 'function' || proto === Range.prototype) {
      Object.defineProperty(proto, 'getBoundingClientRect', { value: rect, configurable: true, writable: true });
    }
  }
  if (typeof document.elementFromPoint !== 'function') {
    Object.defineProperty(document, 'elementFromPoint', { value: () => null, configurable: true, writable: true });
  }
}

export interface RenderDraftOptions {
  readonly env?: DraftEnvironment;
  readonly appData?: TestAppData;
  readonly path?: string;
}

/** Renders `ui` (the Draft tab) at `/studio/:id/draft` with the data layer, tooltips and toasts. */
export function renderDraft(ui: ReactElement, { env = testEnvironment(), appData = createTestAppData(), path = '/studio/art-1/draft' }: RenderDraftOptions = {}) {
  seedSession(appData);
  const router = createMemoryRouter(
    [
      {
        path: '/studio/:pieceId/draft',
        element: (
          <DraftEnvironmentProvider value={env}>
            <TooltipProvider>{ui}</TooltipProvider>
          </DraftEnvironmentProvider>
        ),
      },
      { path: '*', element: <p>Another page</p> },
    ],
    { initialEntries: [path] },
  );
  const user = userEvent.setup();
  const view = render(
    <ThemeProvider>
      <DataProvider appData={appData}>
        <RouterProvider router={router} useTransitions={false} />
        <Toaster />
      </DataProvider>
    </ThemeProvider>,
  );
  return { ...view, user, router, appData };
}

/** The TipTap editor behind the article text box (TipTap keeps it on its element). */
export async function findArticleEditor(name: string | RegExp = 'Article text'): Promise<Editor> {
  const element = await screen.findByRole('textbox', { name });
  const editor = (element as HTMLElement & { editor?: Editor }).editor;
  if (!editor) throw new Error('the text box has no editor');
  return editor;
}
