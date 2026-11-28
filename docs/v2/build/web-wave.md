# Blog Writer v2: web feature wave

The rules for the parallel web wave that builds the UI areas once the server is integrated: who owns
which files, the shared building blocks and data helpers, and the testing, phone-first and
accessibility rules every feature keeps. Binding inputs: `docs/v2/build/contract.md` (sections 1,
9 and 10 above all), `docs/v2/phase1/web.md` (design system) and `docs/v2/phase1/web-data.md` (data
layer, session rules, event stream).

Everything here lives in `web/`. Run every check from `web/`.

---

## 1. How the wave works

- Every area's route module in `src/routes/` is a **thin shell**: page title, tabs and the URL.
  Each section or tab is its own lazy chunk at a **pre-agreed path** in `src/features/`, loaded by
  the route's `loader` before the page (or the tab switch) commits, so a tab switch never flashes
  a fallback and focus stays on the tab list (`src/app/lazy-sections.tsx`).
- The files at those paths exist today as **placeholders** that render the Phase 1 empty states.
  Replace a placeholder's body; keep its file name and named export.
- Agents work in the same tree at the same time, without commits. Touch only your own paths
  (section 2). Everything else is shared and frozen for the wave (section 3).
- The server is integrated first, so `src/lib/api/schema.ts` lists the real routes. If a route
  you need is missing or wrong in it, do not edit `schema.ts` (it is generated): say so in your
  report and work against the contract.

## 2. Ownership map

| Agent | Owns (create anything inside) | Route / tab | Phone-first |
|---|---|---|---|
| **A1 Today** | `src/features/today/**` (`today-page.tsx`: `TodayPage`), `src/features/engagement/**` (`engagement-panel.tsx`: `EngagementPanel`), the `share_target` entry of `public/manifest.webmanifest` (+ its test) | `/today` (and `/today?share=`) | yes |
| **A2 Calendar** | `src/features/calendar/**`: `week-view.tsx` (`WeekView`, the Plan tab), `ideas-view.tsx` (`IdeasView`), `availability-view.tsx` (`AvailabilityView`) | `/calendar/plan`, `/ideas`, `/availability` | approve, triage and availability yes; drag and drop desktop |
| **A3 Studio core** | `src/features/studio/list/**` (`studio-list-page.tsx`: `StudioListPage`), `src/features/studio/piece/**` (`piece-header.tsx`: `PieceHeader`; piece queries shared with the other Studio agents), `src/features/studio/brief/**` (`brief-tab.tsx`: `BriefTab`) | `/studio`, the piece header, `/studio/:id/brief` | no |
| **A4 Interview & intake** | `src/features/studio/interview/**` (`interview-tab.tsx`: `InterviewTab`), `src/features/intake/**` (new: the shared raw text → redact → check → confirm flow, exported from `src/features/intake/index.ts`) | `/studio/:id/interview` | yes |
| **A5 Draft** | `src/features/studio/draft/**` (`draft-tab.tsx`: `DraftTab`; the editor UI, gates panel, selection AI, versions, preview, publish actions) | `/studio/art-…/draft` | no |
| **A6 Posts & go-live** | `src/features/studio/posts/**` (`posts-tab.tsx`: `PostsTab`), `src/features/studio/go-live/**` (`go-live-tab.tsx`: `GoLiveTab`) | `/studio/:id/posts`, `/studio/:id/go-live?post=` | go-live **yes**; approve on phone |
| **A7 Library** | `src/features/library/**`: `pillars-section.tsx` (`PillarsSection`), `voice-section.tsx` (`VoiceSection`), `stories-section.tsx` (`StoriesSection`), `brand-section.tsx` (`BrandSection`), `formats-section.tsx` (`FormatsSection`) | `/library/<section>` | stories review queue yes |
| **A8 Settings & insights** | `src/features/settings/**`: `models-`, `slots-`, `sources-`, `integrations-`, `policy-`, `system-section.tsx` (`ModelsSection` … `SystemSection`); `src/features/insights/**` (`insights-section.tsx`: `InsightsSection`); push: `src/pwa/push*.ts` (new) and the `push`/`notificationclick` handlers in `public/sw.js` | `/settings/<section>` | connect, push setup, pause switch, snapshot form yes |

Notes:

- **Studio tabs** receive `{ pieceId }` (`PieceTabProps` in `src/features/studio/piece/piece-tab.ts`,
  frozen). A piece is an article (`art-…`: five tabs) or a standalone post (`post-…`: no Draft tab,
  `studioTabsFor` in `src/app/nav.ts`). Read other URL state (`?post=`) with `useSearchParams`.
  A3 owns the piece header and the piece queries; the other Studio agents fetch what they show
  with the same keys (`queryKeys.articles.detail(id)`, `queryKeys.posts.detail(id)`), so the
  caches are shared without importing each other's code.
- **Insights** is a Settings tab until about eight weeks of data exist, then gets its own area;
  keep it self-contained in `src/features/insights/` so the move is a route change only. Split it
  out to a ninth agent if A8 is too large.
- **No new top-level routes.** Contract section 10 keeps the existing routes. Push and Today links
  land on `/studio/<id>/interview`, `/studio/<id>/go-live?post=<id>` and `/calendar/plan`; the
  LinkedIn OAuth callback always answers 303 to `/settings/integrations` (read the outcome from the
  connection's `lastError`); the share target is `/today?share=`, handled in the client. A route
  the wave really needs goes through the integrator (section 3).
- **Shared feature components.** When one area needs another's component (intake from A4, the
  piece queries from A3), import it from the owner's `index.ts` only. If it does not exist yet, do
  not write into the owner's folder: build the smallest local version in yours, and name it in
  your report so the integrator merges the two.

## 3. Shared files: nobody in the wave edits them

Ask the integrator (your report) for any change here.

| Path (`web/`) | What |
|---|---|
| `src/app/**` | Route table, navigation config (`nav.ts`: areas, sections, keywords, `studioTabsFor`), `lazy-sections.tsx` |
| `src/routes/**` | The thin route shells |
| `src/components/ui/**`, `src/components/shell/**` | Primitives and the app shell |
| `src/lib/**` | API client, generated `schema.ts`, problems, envelopes, query helpers and keys, event stream, auth |
| `src/features/data/**`, `jobs/**`, `auth/**`, `system/**` | Data provider, conflict dialog, problem alert, job timeline and hooks, auth screens, `/api/me` and `/api/health` |
| `src/features/editor/**` | The article dialect for TipTap (`README.md` there). Import from `@/features/editor`; a change to the dialect is a contract change with goldens |
| `src/features/studio/piece/piece-tab.ts` | The shell-to-tab props |
| `src/styles/**`, `src/theme/**`, `src/main.tsx`, `index.html` | Tokens (`index.css`), theme, entry |
| `src/test/**` | The shared test harness (msw server, `renderApp`, `MockEventSource`, event payloads, `TestPaths`) |
| `src/pwa/**`, `public/**` | Except the two entries given to A1 and A8 above |
| `package.json`, `package-lock.json`, `eslint.config.js`, `tsconfig*.json`, `vite.config.ts`, `scripts/**` | Tooling. Dependencies are pinned: ask for a new one in your report |

**A primitive you need is missing?** Build it in your feature folder
(`src/features/<area>/components/<name>.tsx`), never in `src/components/ui/`. Follow the same
conventions (`data-slot`, `cn()`, cva variants, tokens only, `pointer-coarse:` sizes, Radix from
`radix-ui`) and test it. If it is generally useful, name it in your report; the integrator
promotes it.

## 4. Building blocks

### UI primitives (`@/components/ui/*`)

Phase 1: `button`, `badge`, `card`, `dialog`, `dropdown-menu`, `tabs`, `tooltip`, `sheet`, `input`,
`textarea`, `switch`, `separator`, `scroll-area`, `skeleton`, `command`, `sonner` (`Toaster`), `kbd`.

Added for the wave:

| File | Exports | Use it for |
|---|---|---|
| `label` | `Label` | Naming a control (`htmlFor`) |
| `field` | `Field`, `Fieldset`, `focusFirstInvalid` | Every form control: label, hint, error and `aria-*` wiring (below) |
| `select` | `Select`, `SelectTrigger`, `SelectValue`, `SelectContent`, `SelectItem`, `SelectGroup`, `SelectLabel`, `SelectSeparator` | A choice from a list too long for radios |
| `checkbox` | `Checkbox` (`checked`: `true`, `false`, `'indeterminate'`) | Independent yes/no choices |
| `radio-group` | `RadioGroup`, `RadioGroupItem` | One choice, all options visible |
| `segmented-control` | `SegmentedControl`, `SegmentedControlItem` (`fullWidth` on phones) | One required choice of 2-5 short options (week mode, preview width). A radio group: never empty |
| `toggle-group` | `ToggleGroup` (`type="single" \| "multiple"`), `ToggleGroupItem`, `toggleVariants` | Toggles: editor marks (multiple, a toolbar), clearable filters (single) |
| `popover` | `Popover`, `PopoverTrigger`, `PopoverAnchor`, `PopoverContent`, `PopoverClose` | A panel tied to a trigger or anchor (the selection-AI proposal) |
| `alert-dialog` | `AlertDialog` + `Trigger`, `Content`, `Header`, `Footer`, `Title`, `Description`, `Action` (`variant`), `Cancel` | A question that must be answered ("Unpublish this article?"); focus starts on Cancel |
| `alert` | `Alert` (`variant`: `info`, `success`, `warn`, `error`), `AlertTitle`, `AlertDescription`, `AlertActions` | A message in the page; `error` is `role="alert"` |
| `progress` | `Progress` (`value` or none for indeterminate, `max`, `tone`, `size`) | A named progress bar |
| `table` | `Table`, `TableHeader`, `TableBody`, `TableFooter`, `TableRow`, `TableHead`, `TableCell` (`numeric`), `TableCaption` | Facts in columns (jobs, snapshots, sources) |
| `accordion` | `Accordion`, `AccordionItem`, `AccordionTrigger` (`headingLevel`), `AccordionContent` | Sections that open in place (gate findings) |
| `collapsible` | `Collapsible`, `CollapsibleTrigger`, `CollapsibleContent` | One section that opens |
| `stat-tile` | `StatTile` (`label`, `value`, `unit`, `delta`, `caption`), `formatStatValue` | A headline number, a KPI row (Insights, the week's scorecard) |
| `diff-view` | `DiffView` (`before`, `after`, `context`, `label`), `diffBlocks`, `describeDiff` | The selection-AI proposal and the conflict dialog: a block-level jsdiff with word edits |

```tsx
<Field label="Post URL" description="Copy it from the post’s menu on LinkedIn." error={errors.url} required>
  <Input type="url" value={url} onChange={(event) => setUrl(event.target.value)} />
</Field>

<Field label="Pillar" error={errors.pillar}>
  {(control) => (
    <Select value={pillar} onValueChange={setPillar}>
      <SelectTrigger {...control}><SelectValue placeholder="Choose a pillar" /></SelectTrigger>
      <SelectContent>{/* SelectItem … */}</SelectContent>
    </Select>
  )}
</Field>

<Fieldset legend="Mode for this week">
  {(group) => (
    <SegmentedControl {...group} value={mode} onValueChange={setMode} fullWidth>
      <SegmentedControlItem value="normal">Normal</SegmentedControlItem>
      <SegmentedControlItem value="light">Light</SegmentedControlItem>
      <SegmentedControlItem value="away">Away</SegmentedControlItem>
    </SegmentedControl>
  )}
</Fieldset>
```

### Shell parts (`@/components/shell/*`)

`Page`, `PageHeader` (the page's only `h1`; sets the document title), `EmptyState` (what will
appear, plus the "Your part" note), `DetailList` and `Timeline`, `SectionTabs`, `NotFound`. Pages
start inside the shell: never render another `main`, `nav` or `h1`.

### Shared data components

- `ConflictDialog` and `useConflict` (`@/features/data/conflict-dialog`): the 412 answer of
  contract 1.4: who saved what and when, a diff of mine against theirs, **Keep mine**, **Take
  theirs**, **Copy mine**. It never decides by itself.
- `ProblemAlert` (`@/features/data/problem-alert`): an inline error with the owner-facing
  sentence, the request id and "Try again". Renders nothing for sign-in and "not authorised".
- `JobTimeline` and the job hooks (`@/features/jobs/*`): a job's steps live from the event stream,
  with a progress bar for steps that report `progress` events.

## 5. Data helpers

### Reading: `useApiQuery`, `useApiInfiniteQuery` (`@/lib/query/api-query`)

Typed from `schema.ts` (paths, parameters, bodies, responses). Errors are `ApiError`s
(`kind`, `status`, `code`, `requestId`, `problem`). Keys come from `queryKeys` so the event stream
can refresh them.

```tsx
import { useApiInfiniteQuery, useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';

const article = useApiQuery({
  queryKey: queryKeys.articles.detail(id),
  path: '/api/articles/{id}',
  init: { params: { path: { id } } },
});                                              // data: Versioned<Article> = { item, etag }

const pillars = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.pillars), path: '/api/kb/pillars' });

const ideas = useApiInfiniteQuery({
  queryKey: queryKeys.ideas.list({ status: 'new' }),
  path: '/api/ideas',
  init: { params: { query: { status: 'new' } } },
  pageSize: 20,
});                                              // data: every loaded { item, etag }, in order
// ideas.hasNextPage, ideas.fetchNextPage(), ideas.isFetchingNextPage drive "Show more".
```

- Options factories for loaders, `useQueries` and prefetching: `apiQueryOptions(api, args)`,
  `apiInfiniteQueryOptions(api, args)`; plain calls: `apiRequest(api, method, path, init)`
  (`@/lib/api/request`; `apiRequestWithResponse` also gives the headers). `api` is
  `useAppData().api`.
- **Keys** (`@/lib/query/keys`): `[<plural>, 'list', filters]` and `[<plural>, 'detail', id]` for
  every entity of contract section 9 (`queryKeys.articles`, `.posts`, `.ideas`, `.plans`, … `.kb`),
  plus `queryKeys.today`, `queryKeys.jobs`. Settings singletons are `queryKeys.kb.detail(id)` with
  the ids in `KB_SINGLETONS`; the LinkedIn connection is `queryKeys.linkedin.detail(LINKEDIN_CONNECTION_ID)`.
  Put sub-resources under their document (`[...queryKeys.articles.detail(id), 'versions']`) so they
  refresh with it. A key of your own must start with an entity prefix for the same reason.
- **Lists are infinite queries** (contract 1.5): never use a list key with `useQuery` and with
  `useInfiniteQuery` at once (different data shapes). A screen that needs only the first page
  still uses `useApiInfiniteQuery` and simply never asks for more.
- Defaults (Phase 1): 30 s `staleTime`, retries only for transient failures, no refetch on focus
  except Today's queries (`refetchOnWindowFocus: true`).

### Writing: `useApiMutation`

```tsx
import { preconditionHeaders } from '@/lib/api/envelope';  // only for plain apiRequest calls
import { useApiMutation } from '@/lib/query/api-query';

const save = useApiMutation('put', '/api/kb/pillars', {
  updates: () => queryKeys.kb.detail(KB_SINGLETONS.pillars), // the response becomes the cache: next save has the new etag
  invalidates: [queryKeys.today],                             // prefixes to refetch after a success
  successToast: 'Pillars saved.',                             // the action's words
  errorToast: 'Couldn’t save the pillars.',                   // title; the reason and request id follow
  handles: ['etag_mismatch', 'validation_failed'],            // codes this screen shows itself: no toast
});
save.mutate({ body: draft, ifMatch: pillars.data?.etag });   // etag: If-Match; null: If-None-Match: *
```

- `ifMatch` (contract 1.4): the etag you read sends `If-Match`; `null` (a singleton never saved)
  sends `If-None-Match: *`; leave it out for creates and the idempotent one-tap actions.
- Writes never retry. Errors toast by default with `problemMessage(error)` and
  "Request ID …"; sign-in and "not authorised" never toast (the session screens show them).
- Owner edits invalidate their own queries (`updates`, `invalidates`); changes made by jobs arrive
  as events (below).
- A route that starts a job answers 202 `JobAccepted` (`{jobId, status}`): show
  `<JobTimeline jobId={...} />` or read `useJobStatus(jobId)`.

### Problems (`@/lib/api/problems`, `@/lib/api/toast`, `@/features/data/problem-alert`)

| Helper | Gives |
|---|---|
| `problemMessage(error)` | One owner-facing sentence: the server's `detail`, else a sentence for the `code` (`PROBLEM_MESSAGES`), else one for the status |
| `isProblem(error, code \| codes)`, `problemCode(error)` | Branch on a code (`slot_conflict`, `gates_blocking`, `ai_use_not_confirmed`, …) |
| `fieldErrors(error)` | `validation_failed`: `{ 'title': 'Field required', 'meta.description': … }` for `Field error={…}` |
| `confidentialFindings(error)` | `confidential_text`: `{field, start, end, message, ref}`; highlight by field and span; the term is never sent |
| `missingFields`, `refusedTransition`, `readOnlyState` | `meta_incomplete`, `invalid_transition` (`from`, `to`, `allowed`), `read_only_state` (`state`, `writer`) |
| `toastError(error, { title, action })`, `toastSuccess(message)` | Toasts with the request id; offline errors collapse into one |
| `<ProblemAlert error={query.error} title="Couldn’t load the ideas." onRetry={() => void query.refetch()} />` | The inline error state |

### ETags and conflicts (`@/lib/api/envelope`, `@/features/data/conflict-dialog`)

`Versioned<T>` (`{item, etag}`), `Page<T>`, `JobAccepted`, `etagFromResponse`,
`preconditionHeaders(etag)`, `isEtagMismatch(error)`, `conflictOf(error)` (the 412's `current`:
`etag`, `updatedAt`, `updatedBy`, `version`, `status`), `itemsOf(page)`.

```tsx
const conflict = useConflict();
const save = useApiMutation('put', '/api/articles/{id}', {
  updates: () => queryKeys.articles.detail(id),      // the new etag is in the cache at once
  handles: ['etag_mismatch'],                        // the dialog shows it, not a toast
});

function send(etag: string, baseVersion: number) {
  save.mutate(
    { params: { path: { id } }, body: { markdown, meta, baseVersion }, ifMatch: etag },
    { onError: (error) => { if (conflict.show(error)) pauseAutosave(); }, onSuccess: () => conflict.close() },
  );
}

<ConflictDialog
  {...conflict}
  subject="This draft"
  mine={markdown}
  theirs={serverMarkdown}                          // from a refetch, or leave both out to skip the diff
  saving={save.isPending}
  onKeepMine={() => {
    const current = conflict.conflict;             // { etag, updatedAt, updatedBy, version, status }
    if (current?.etag && current.version !== undefined) send(current.etag, current.version);
    else void article.refetch();                   // the route did not know it: reload, then save again
  }}
  onTakeTheirs={() => { void article.refetch(); conflict.close(); }}
/>
```

Article autosave sends `baseVersion` with If-Match: a stale etag with an unchanged `version` is
merged by the server (200); only a changed `version` is a 412 (contract 1.4). Never retry a 412
silently.

### Live updates (`@/lib/events/*`, `@/features/jobs/use-job`)

- The router already invalidates on events (contract 9): `snapshot` and `status` refresh
  `['jobs']`; other job events refresh `['jobs', 'detail', jobId]`; **`entity.changed`** refreshes
  `[<plural>, 'detail', id]`, `[<plural>, 'list']` and `['today']`; `draft.reset` clears the
  step's streamed text in the job store; `progress` feeds `useJobProgress`.
- The stream is open only while something retains it: a `JobTimeline`, or `useLiveEvents(true)`
  while your screen shows running work. Pages refetch on mount; Today also on focus.
- For side effects beyond a refetch: `useEntityChanged({ entity: 'article', id }, (change) => …)`
  ("The pipeline updated this draft"), or `useStreamEvents(listener)` for anything else.
- Job hooks: `useJobStatus`, `useJobSteps`, `useJobAwaiting`, `useJobError`, `useJobAttempt`,
  `useJobStepText(jobId, step)` (live draft text; `undefined` again after `draft.reset`; on
  `gap`, reload the article when the step lands), `useJobEvents`, `useJobProgress(jobId, step?)`
  (`{done, total, label, fraction}`), `useEventStreamStatus`.

## 6. Testing

- Vitest with jsdom; pure logic in Node (`// @vitest-environment node` on the first line). Tests
  sit next to the code (`*.test.ts(x)`); type-only checks with `expectTypeOf`.
- **msw for every request.** The shared server fails any request without a handler. Put your
  handlers and fixtures in `src/features/<area>/testing/` (`handlers.ts`, `fixtures.ts`), typed
  from `schema.ts` (`components['schemas'][...]`), and install them per test with
  `server.use(...)` from `@/test/msw`. Answer errors with `problem(status, code, requestId)` and
  add extension members (`current`, `findings`, `errors`) where the screen reads them.
- Render a whole page with `renderApp('/library/voice')` (real routes, shell, data layer; the
  signed-in owner is seeded) and wait with `findPageHeading`, `findByRole`. Render a component
  with `<DataProvider appData={createTestAppData()}>`. Phone layout:
  `renderApp(path, { viewport: 'mobile' })`.
- Events: `MockEventSource` (`@/test/event-source`) with the payload builders in
  `@/test/job-events` (`snapshotPayload`, `jobEventPayload`, `deltaPayload`); or apply parsed
  events to `appData.jobs` directly.
- Query by role and accessible name, never by class or test id unless there is no role.
- Timing: TanStack notifies a tick later (`await waitFor` / `findBy…`, not `act` alone). Radix
  roving focus (tabs, radio groups, toggle groups) moves focus a tick after the key: `waitFor` the
  focus; for radio groups hold the arrow (`'{ArrowDown>}'`, wait, `'{/ArrowDown}'`).
- Keep the global rules green: no `dark:` classes, no raw colours, no CDN, CSP-safe styles (a
  library that injects a `<style>` tag needs its rules copied into `index.css`; ask).
- Before you report: `timeout 600 npm run lint && timeout 600 npx tsc -b && timeout 600 npx vitest run && timeout 600 npm run build`.

## 7. Phone-first rules (Today, go-live, interview, triage, review queue, pause, snapshot form)

- Design at **375 px, one hand**, then widen. `md` (48rem, `DESKTOP_QUERY`) is the phone/desktop
  switch in CSS and JS.
- **Tap targets at least 44 px**: buttons and controls grow on `pointer-coarse:` already; give
  custom targets `min-h-11` (or `pointer-coarse:min-h-11`), and keep 8 px between targets. Rows in
  a list are whole targets, not a small link inside a row.
- **Actions at the bottom**: the page's main action within thumb reach. Use a sticky bar above the
  tab bar for flows like go-live and the interview:
  `sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] md:bottom-0` inside the page (the tab
  bar is 4 rem plus the home indicator; toasts already clear it). Sheets open from the bottom on
  phones (`side="bottom"`).
- **No hover-only affordances**: anything revealed on hover is also visible on touch and reachable
  by keyboard focus; tooltips only repeat a visible or accessible label.
- Text inputs use 16 px text (the primitives do), so iOS never zooms. Dictation uses the
  platform's own keyboard or the Web Speech API; raw text stays in component state (contract
  rule 9).
- One column; no sideways scrolling except tab lists and wide tables. Respect safe areas.
- Push links open the right page directly; a page opened from a push must make sense on its own
  (what is due, the one action).

## 8. Accessibility rules

- Every control has a name: `Field`/`Label` for form controls, `aria-label` for icon-only
  buttons. Groups have names (`Fieldset` legend, `aria-label`).
- Headings: the shell's `PageHeader` is the page's `h1`; sections start at `h2`; do not skip
  levels. One `main` landmark (the shell's).
- Focus: dialogs and sheets trap focus and give it back (the primitives do); after an action that
  removes the focused element, move focus to something sensible (the next item, the heading).
  Navigation focuses the new page's heading; tab switches keep focus on the tab list.
- Announce what changes on its own: errors with `Alert variant="error"` (`role="alert"`) or
  `ProblemAlert`; results with a toast and, when it matters on the page, `role="status"`. Keep
  live regions small and stable; never make a whole list live.
- **Never colour alone**: state colours come with an icon and words (badges, alerts, the diff's
  strike-through and underline); pillar colours always with the pillar's name.
- Tokens only: no raw colours and no `dark:` classes (tests enforce both); contrast is checked in
  `src/styles/tokens.test.ts` and `src/components/ui/ui-display.test.tsx`.
- Keyboard: everything works without a pointer; composite widgets use Radix (arrow keys).
  **Drag and drop needs a keyboard and a tap alternative** (a "Move to…" menu on each slot).
- Motion answers an action; `prefers-reduced-motion` already stops it globally.
- Server HTML (portfolio and carousel previews) only in `<iframe sandbox src=…>`, never `srcdoc`
  and never outside the iframe (contract section 10).

## 9. Contract rules the web must keep (section 10, rules for the web)

- Read-only editor whenever `ARTICLE_WRITER[status] != "owner"`; enable action buttons from
  `/api/meta/machines`; show `X-Request-Id` in error toasts (`toastError` does).
- Intake and interview raw text (dictation, typed answers before redaction) lives in component
  state only: never `localStorage`, `sessionStorage`, IndexedDB, the query cache or a service worker
  cache, and a session-expiry redirect drops it. Do not put it in a query key or a mutation's
  cached variables either: call `apiRequest` (or a mutation with `gcTime: 0`) for redaction.
- LinkedIn publishing happens only from a fresh tap on the go-live page, with its confirm token.
- The share target (`/today?share=`) carries other members' text in the URL: read it once into
  component state, replace the URL (`navigate('/today', { replace: true })`) so it leaves the
  address bar and history, and confirm before any POST.
- Labels for statuses follow the contract's glossary (Planned, Researching, Interview, Drafting,
  Checking, In review, …).
- `read_only_state` names the document's status `state` (the problem's `status` is the HTTP
  status); `readOnlyState(error)` reads it.

## 10. Where things are (Phase 1 and this preparation)

| Need | Look at |
|---|---|
| A whole page in a test | `src/app/routes.test.tsx`, `src/test/render-app.tsx` |
| Typed calls against routes the schema lacks (tests only) | `src/test/api-paths.ts` (`TestPaths`), `src/lib/api/request.test.ts`, `src/lib/query/api-query.test.tsx` |
| Primitives in use | `src/components/ui/ui*.test.tsx` |
| The editor's Markdown layer | `src/features/editor/README.md` |
| The design tokens | `src/styles/index.css`, `docs/v2/phase1/web.md` |

---

## 11. Wave results (integration, 2026-11-27)

All eight areas replaced their placeholders (same file names and exports). The integrator merged
the duplicates below, applied the shared-file requests and fixed the build. Nothing is committed.

### Checks (from `web/`)

| Check | Result |
|---|---|
| `npm run lint` | 0 problems (`--max-warnings 0`) |
| `npx tsc -b` | 0 errors |
| `npx vitest run` | 128 files, 1,568 tests passed, no unhandled requests (35.8 s). One earlier run on a machine at load 20 hit 5 s test timeouts in 7 files; all of them passed in the rerun |
| `npm run build` (+ `verify-dist`) | passes; `verify-dist: self-contained, no inline scripts or eval, PWA files present.` |

Before integration: 28 tests failed in 4 shared files and `verify-dist` found 3 external hosts.

### Bundle

- First load (entry, modulepreloads, CSS): 672.8 kB raw, 199.2 kB gzip (672.1 / 198.9 kB before integration). The entry chunks hold no editor
  (ProseMirror/TipTap) and no chart code.
- Largest lazy chunks (raw / gzip): `draft-tab` 1,027.6 / 328.4 kB, `normalize-worker` 678.7 / 219.1 kB, `week-view` 95.6 / 30.1 kB, `today` 78.5 / 23.1 kB, `posts-tab` 74.5 / 21.4 kB, `brief-tab` 50.9 / 14.6 kB, `insights-section` 50.0 / 14.9 kB.
- The editor is only in `draft-tab` and `normalize-worker`; the Insights charts only in
  `insights-section`. The Draft chunk is large because of TipTap, the highlight grammars and
  `yaml`; trimming the grammars needs `highlight.js` as a direct dependency (not added).

### Shared changes made by the integrator

- **Shell tests**: `src/test/first-install.ts` gives each area's first-install answers
  (`firstInstallHandlers(area)`, `allFirstInstallHandlers()`), built from the areas' `testing/`
  helpers. `src/app/routes.test.tsx` installs them per row and waits with `findByRole`; Library rows
  name the sections' headings; four Settings forms (`slots`, `integrations`, `policy`, `system`) are
  checked for what a first install is missing instead of an empty state. `navigation.test.tsx`
  and `command-palette.test.tsx` install every area; navigation waits for the cadence form's
  "Post slots".
- **`src/pwa/manifest.test.ts`**: the service worker listens for `install`, `activate`, `push` and
  `notificationclick` only (still no `fetch`, no caches). **`tokens.test.ts`**: the generated
  `src/lib/api/schema.ts` is exempt from the raw-colour rule (its hex values are the brand kit's
  `@default` notes).
- **Sign-in redirect** (`src/lib/api/session.ts`): `safeReturnPath` drops the share target's
  `share` and `share_url` (`SHARE_TARGET_PARAMS`), so other members' text never rides the sign-in
  round trip. Engage's `SHARE_PARAMS` is that constant.
- **Keys** (`src/lib/query/keys.ts`): `queryKeys.meta.machines`, `queryKeys.jobs.systemStatus`,
  `.schedules`, `.spend(days)`.
- **New shared helpers**: `@/lib/query/machines` (`useMachines`, `machineAllows`: one cache entry
  for every area; Draft used `['articles','machines']` before), `@/lib/query/system-status`
  (`useSystemStatus`: Today used `['today','system-status']`, so a change made in Settings did not
  reach Today's notices), `@/lib/use-now`, `@/lib/use-online`, `@/lib/urls` (`isHttpsUrl`, the
  server's rule), `@/components/ui/pillar-badge` (`PillarBadge`, `PillarThread`) and
  `pillarThreadClass`/`isPlanPillar` in `@/lib/pillars`.
- **verify-dist**: the Brief hint no longer reads as a host ("with https:// at the start"); Today
  parses its own links with `parsePath` instead of a made-up base URL; the LinkedIn authorize check
  compares host and path, so the bundle holds no LinkedIn URL.

### Duplicates merged

| Was | Now |
|---|---|
| `useNow` in Today, Calendar, Posts, Settings | `@/lib/use-now` (15 s tick, fresh time on mount and on return to the page) |
| `useOnline` in Intake, Studio piece, Settings | `@/lib/use-online` |
| Machines query in Calendar, Studio piece, Interview, Draft, Posts, Library, Settings | `@/lib/query/machines`; the areas keep their contract fallbacks |
| System status in Today and Settings | `@/lib/query/system-status` |
| `isHttpsUrl` in Engage and Settings > Sources | `@/lib/urls` |
| `PillarTag` (Library), `PillarBadge` (Settings, Studio piece), `PillarThread` (Library, Draft), `pillarThreadClass` (Calendar, Library) | `@/components/ui/pillar-badge`, `@/lib/pillars`; Calendar keeps its inline thread-and-name tag, Draft its manuscript line (`ManuscriptPillar`) |
| `useArticle`, `usePost`, `usePillars`, `useFormatPacks` in Draft and Posts | the piece layer, `@/features/studio/piece` |
| Draft's `MarkedText` span maths | the intake's `findingSpan`/`codePointOffsets` (`@/features/intake`): an emoji before a blocklisted term no longer shifts the mark |

Cross-area imports go through the owners' `index.ts` (`@/features/intake`,
`@/features/studio/piece`, `@/features/insights`, `@/features/settings`); the Studio tabs still
import the frozen `piece/piece-tab.ts`.

### Left for a follow-up (named, not merged)

- **Snapshot form twice**: Today's `snapshot-sheet.tsx` (A1) and Insights' `SnapshotForm` (A8) both
  post `/api/posts/{id}/snapshots`. They differ on a 412 (Today opens `ConflictDialog`; Insights
  re-reads and keeps the typed numbers). Pick one behaviour, then Today should render
  `SnapshotForm` from `@/features/insights` inside its sheet.
- **Sticky action bars**: `ActionBar` (Intake, page column), `ActionBar` (Go-live, page edge,
  primary/secondary/note) and `StickyActions` (Engage and Today sheets) share the offset rule but
  not the layout; merge into `src/components/ui/` after a look on a phone.
- **Status badges**: Studio piece (`StatusBadge`), Draft (`StatusBadge`) and Posts
  (`PostStatusBadge`) use the same glossary words with different icons and tones; one table in the
  piece layer should drive all three.
- **Smaller**: `SaveBar` and `NumberField` (Library, Settings); Library's `lib/spans.ts` and the
  intake's `findings.ts` (both correct, different shapes); per-area `usePillars`-style reads of kb
  singletons (same keys, so one cache entry); each area's `problemResponse` test helper versus
  `problem()` in `@/test/msw`.
- **Share target with files**: a screenshot share needs a POST `share_target` and a `fetch`
  handler in `sw.js`, which the service-worker test forbids; screenshots go through Engage's file
  picker for now. The theme resets `--animate-*`, so there is no `animate-spin` (Interview uses
  `animate-pulse-soft`).
- Nothing was checked in a real browser or phone: the only launch config needs the live Cosmos
  account.

### Server routes: missing or mismatched

| Route | Problem | Called from (`web/src/`) |
|---|---|---|
| `POST /api/render/mermaid` | Missing (`{source}` → `{svg, width?, height?, warnings?}`); live diagram previews say so | `features/studio/draft/api/render-mermaid.ts` |
| `POST /api/media` | No request body in OpenAPI (raw bytes): cast | `features/studio/draft/api/media.ts`, `features/library/lib/media.ts` |
| `POST /api/engagement/extract` | No request body (raw image): cast | `features/engagement/lib/queries.ts` |
| `POST /api/analytics/imports` | No request body (raw file): cast | `features/insights/imports-panel.tsx` |
| `GET /api/media/{name}` | Names contain `/`; the schema has one path segment | `features/studio/posts/model/media.ts`, `features/library/lib/media.ts`, `features/studio/draft/api/media.ts` |
| `GET /api/posts/{post_id}/carousel/preview` | Serves HTML, typed `application/json` | `features/studio/posts/model/media.ts`, `features/library/brand/server-preview.tsx` |
| `GET /api/articles/{article_id}/export.md` | `text/markdown` typed `unknown`: cast for `parseAs: 'text'` | `features/studio/draft/api/export.ts` |
| `GET /api/meta/machines` | Lists edges the owner cannot take, no writer table: clients mirror `ARTICLE_WRITER` and owner edges | `features/studio/piece/piece-status.ts`, `features/studio/draft/model/status.ts` |
| `POST /api/articles/{article_id}/transition` | Refuses the machine's `publish_failed → in_review` | `features/studio/draft/review/use-article-commands.ts` |
| Schemas | `Post.approvalCurrent`, `ApproveOut.postIds`, `EvalResult.errorCode`, `InsightSuggestion.value` missing; `PlannedSlot` `ideaId`/`storyIds`/`povIds`/`articleId` untyped; `Finding.field`, `CheckState.truncated` mistyped | Posts, Calendar, Settings, Insights |

Wanted by the UI, no route yet: `GET /api/engagement/items/{id}` and saving `draftText`; linking a
Brief-tab research run to `article.briefId`; research runs per post; restoring an archived post;
title search; quick takes linked to their plan slot on create; `GET /api/kb/brand/preview`; voice
preferences; a pillar filter on `/api/sources` and a format filter on `/api/posts`; measured posts
per experiment arm; per-page carousel PNGs; media on `PostEditIn`; `openLinkedInUrl` in the go-live
view.
