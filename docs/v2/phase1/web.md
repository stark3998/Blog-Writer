# Phase 1: web shell and design system

Date: 2026-11-26. Plan sections: "Frontend (`web/`)" and "Owner time budget". Everything lives in
`web/`; nothing outside it was changed.

## What was built

| Path (`web/`) | What |
|---|---|
| `src/styles/index.css` | Every design token in `@theme`; the dark theme re-points the same tokens under `.dark` |
| `src/components/ui/` | Button, Card, Dialog, DropdownMenu, Tabs, Tooltip, Sheet, Badge, Input, Textarea, Switch, Separator, ScrollArea, Skeleton, plus Command (cmdk), Toaster (Sonner) and Kbd |
| `src/components/shell/` | App shell, sidebar (collapsible rail), phone top bar and bottom tab bar, command palette, theme menu, skip link, route progress, page header, empty state, URL-backed section tabs, detail list and timeline, error and not-found pages |
| `src/routes/` | The five areas, one lazy chunk each: `today`, `calendar`, `studio-list`, `studio-piece`, `library`, `settings` |
| `src/app/` | Route table (`routes.tsx`) and navigation config (`nav.ts`: areas, sections, keywords) |
| `src/theme/` | Theme store (system / light / dark, saved per device, synced across tabs) and provider |
| `src/pwa/register-sw.ts` | Service worker registration after `load`, production only |
| `public/` | `manifest.webmanifest`, `sw.js` (stub), `theme-init.js` (pre-paint theme), icons, `robots.txt` |
| `scripts/generate-icons.mjs` | Draws the mark as SVG and PNG (192, 512, maskable 512, Apple 180, favicon 32) with no dependencies |
| `scripts/verify-dist.mjs` | Last step of `npm run build`: fails on inline scripts, remote URLs, CDN hosts, `data:` URIs in CSS or missing PWA files; prints bundle sizes |
| `eslint.config.js`, `tsconfig*.json`, `vite.config.ts` | Tooling (below) |

Scripts: `dev`, `build`, `preview`, `lint`, `typecheck`, `test`, plus `icons`.

## Design direction: ink on paper, three threads

A calm, high-contrast desk for one expert writer. Each page is one sheet of white paper on a cool
neutral desk; text and primary actions are blue-black ink; colour appears only where it means
something: the three pillar threads and three states.

- **Type does the hierarchy.** Page titles and empty-state headlines are set in Newsreader (an
  optical-size reading serif); the tool is set in Schibsted Grotesk (an editorial grotesk). Every
  page opens with a serif title and a one-sentence standfirst.
- **The owner's part is a margin note.** Each empty state says what will appear (main column) and
  what the owner does (a "Your part" note under an ink rule, beside the text on wide screens, after
  it on phones), like an editor's note on a proof.
- **Today leads with a sentence and the time budget**: "Nothing needs you right now.", the four kinds
  of inbox item (approve, answer, publish, reply) and "Your week" (Sunday 30 min, mid-week 30 min,
  about 1 min per post slot, 15 min daily: about 2.5 to 3 hours).
- **Unlike v1**: no glass, gradients, blobs or shadows on content. Solid surfaces, one sheet per page,
  shadows only on things that float (menus, dialogs, toasts). Dark mode is a token remap, and a test
  forbids `dark:` classes in components (v1 had 115 dark overrides).

### Tokens (`@theme`, light / dark)

| Group | Tokens |
|---|---|
| Surfaces | `desk` #eef0f3 / #0b0e14, `sheet` #ffffff / #14171e, `raised`, `sunken`, `wash` and `wash-strong` (translucent ink, for hover and selection on any surface), `line`, `line-strong` (hairlines), `control` (input borders, 3:1) |
| Ink | `ink` #151d2b / #eef0f4, `ink-muted` #4c535f / #b6bbc4, `ink-subtle` #636974 / #9a9fa7, `ink-inverse`, `focus` (= ink), `highlight` (text selection, a highlighter yellow), `backdrop` |
| Pillars | `agents` cobalt #3559c3 / #96b6fe, `identity` teal #017272 / #6ecdcc, `governance` plum #93398e / #e3a1dc, each with `-soft`. Equal OKLCH lightness, so no pillar shouts louder. Always shown with the pillar name. |
| States | `success` #21763c, `warn` #985b0b, `danger` #be2323 (light), each with `-soft` |
| Type | `font-sans` Schibsted Grotesk Variable, `font-serif` Newsreader Variable (opsz 6–72), `font-mono` system stack. Scale from *The Elements of Typographic Style*: 12, 14, 16, 18, 21, 24, 36, 48 px (`text-xs` … `text-4xl`), with line heights; `leading-prose` 1.7 for serif long-form |
| Shape and depth | `radius-sm/md/lg/xl` 6/8/12/16 px; `shadow-overlay` only |
| Layout | `w-sidebar` 15.5rem, `w-rail` 4.25rem, `max-w-measure` 38rem, `max-w-page` 72rem; `md` (48rem) is the phone/desktop switch in CSS and JS |
| Motion | `ease-standard`, `animate-fade-in`, `pop-in`, `slide-in-*`, `progress`, `pulse-soft`; motion answers an action, nothing moves on its own; `prefers-reduced-motion` stops all of it globally |

Colours are OKLCH. `src/styles/tokens.test.ts` parses `index.css` and checks, in both themes: every
token is in sRGB; text pairs meet AA (ink ≥7:1; muted, subtle, pillar and state text ≥4.5:1 on
sheet, desk, raised, sunken and their soft backgrounds; hover rows too); controls and the focus ring
≥3:1; the dark theme re-points every colour token; the type scale; and that the browser chrome colour
equals the sheet. Tightest measured pairs: `ink-subtle` on desk 4.83:1, `warn` on `warn-soft` 4.84:1.

### Components and shell

- `ui/` follows shadcn conventions (`data-slot`, `cn()`, cva variants, `asChild`) on the `radix-ui`
  package. Buttons are solid ink (primary), bordered, ghost, danger or link; on touch screens
  (`pointer-coarse`) controls grow to 44 px. Inputs use 16 px text so iOS never zooms. Badges have
  pillar and state variants.
- Desktop: sticky sidebar on the desk (the current page looks like a sheet of paper); collapses to an
  icon rail with tooltips; the choice is saved per device. Search (⌘K / Ctrl K) and the theme menu
  sit at the bottom.
- Phone: a slim header (mark, search, theme) and a bottom tab bar of five 64 px targets above the
  home indicator, so Today works one-handed. Toasts sit above the tab bar.
- Command palette (cmdk in a Radix Dialog): every area and section (with keywords such as
  "linkedin" for Integrations), the three theme choices, and collapse/expand on desktop. It is a
  lazy chunk, prefetched when the page is idle.
- Accessibility: skip link first in tab order; one `main` landmark; the page heading takes focus after
  navigation (not on tab switches, so arrow keys keep working); `aria-current` on navigation; a
  visible 2 px ink focus ring with offset; document titles per page and tab ("Insights – Settings –
  Blog Writer").
- Theme: system by default, saved as `bw:theme`. `public/theme-init.js` applies it before first paint
  (a same-origin file, so no inline script) and sets the `theme-color` metas.
- Errors: a page error keeps the shell; a failed chunk download (old tab after a deploy) says so and
  offers Reload. Unknown paths and sections show "Page not found" inside the shell.
- Connectivity: a toast when the device goes offline and when it is back.

## Routes (React Router 7.18, data mode)

| Path | Chunk | Content |
|---|---|---|
| `/` | — | Redirects to `/today` (also the PWA `start_url`) |
| `/today` | `today` | Inbox empty state, the four kinds of item, "Your week" time budget |
| `/calendar/:view?` | `calendar` | Tabs: Plan (plus "How a week runs", New York times), Ideas, Availability (Normal, Light, Away) |
| `/studio` | `studio-list` | Pieces empty state and the five stages |
| `/studio/:pieceId/:tab?` | `studio-piece` | Tabs: Brief & research, Interview, Draft, Posts & carousel, Go-live |
| `/library/:section?` | `library` | Tabs: Pillars (the three pillars and topics), Voice guide, Stories & POV, Brand kit, Format packs (lengths) |
| `/settings/:section?` | `settings` | Tabs: Models, Slots (defaults), Sources, Integrations, Blocklist & policy (auto-publish Off), Insights, System (jobs, plus app version, install and service worker state for this device) |
| `*` | eager | Not found |

Tabs live in the URL (`replace`, scroll kept), so sections can be bookmarked and the palette can
open them. Unknown sections are "Page not found". A pathless `pages` route holds the
`HydrateFallback` (a skeleton inside the shell) and the page error boundary.

## PWA

`manifest.webmanifest`: name Blog Writer, short name Writer, `start_url` `/today`, scope `/`,
`display` standalone, white theme and background, SVG, 192, 512 and maskable icons, shortcuts to
Today and Calendar. `index.html` links it with `crossorigin="use-credentials"` (Easy Auth). `sw.js`
installs and claims clients only: no caching, no fetch handler, no push yet. The mark is three lines
of writing in the pillar colours on blue-black ink.

## CSP and serving (checked against `core.md` and `core/security_headers.py`)

- Verified in headless Chromium with the server's exact `SPA_CSP` (`script-src 'self'; style-src 'self'`
  and the rest): every page renders, fonts load, dialogs, menus, tooltips and toasts work.
- No inline script or style in `index.html`; no CDN; no `data:` URIs (`assetsInlineLimit: 0`), so
  `font-src` and `img-src` can stay at `'self'`. `verify-dist.mjs` enforces this on every build.
- Two libraries still try to inject a `<style>` tag at runtime, and `style-src 'self'` blocks it, which
  logs a CSP error in the console: Sonner once per page load (its CSS is bundled from
  `sonner/dist/styles.css`, so toasts are styled anyway) and Radix's scroll lock once per open dialog
  or menu (a static `body[data-scroll-locked]` rule plus `scrollbar-gutter: stable` replaces it;
  react-remove-scroll's touch and wheel handlers still run). Nothing breaks. If CSP reporting is
  turned on later, expect these reports; adding `'unsafe-inline'` to `style-src` would silence them.
- Files from `public/` stay at the root (`/sw.js`, `/theme-init.js`, `/manifest.webmanifest`,
  `/icons/*`), never under `/assets/`, so the server serves them `no-cache`; hashed output in
  `/assets/` gets the immutable header.

## Tooling and decisions

- **Versions** (exact pins, `package-lock.json`): React 19.3.1, React Router 7.18.7 (the plan says 7;
  8.5 exists and 7.x is still maintained), Vite 8.4 (Rolldown), Tailwind 4.3.5, radix-ui 1.8.1, cmdk
  1.1.1, Sonner 2.0.8, lucide-react 1.81, Vitest 5.1.2, ESLint 10.15, typescript-eslint 8.74.
- **TypeScript 6.0.3, not 7**: typescript-eslint supports `<6.1`. Strict, plus
  `noUncheckedIndexedAccess`, `noImplicitOverride`, `noUncheckedSideEffectImports`,
  `erasableSyntaxOnly`. Three projects: app (`src`, no Node types), tests (Node types allowed),
  config.
- **jsdom 29.1.1, not 31**: jsdom 30+ needs Node ≥24.15 and this machine runs 24.14.1. `engines`
  stays `>=24`; CI's Node 24.22.3 works with either.
- **Test resolution**: under Vitest, `resolve.conditions` is `['module-sync', ...client defaults]`.
  Without it Vitest loads react-router's CJS build while that build's own `require` reaches the ESM
  build, giving two router contexts ("useRouteError must be used within a data router").
- **Lint**: flat config with `strictTypeChecked`, `stylisticTypeChecked` and React Hooks
  `recommended-latest` (Compiler rules), `--max-warnings 0`. `eslint-plugin-jsx-a11y` was left out: it
  does not support ESLint 10. Accessibility is covered by role-based tests instead.
- **No server data yet**, so TanStack Query, Zustand, the OpenAPI client and the SSE client are not
  installed; UI state is two tiny persistent stores (theme, sidebar) on `useSyncExternalStore`. Add
  them with the first API call.
- **Chunks**: React, React DOM and the router are one long-cached `react` chunk, so app deploys don't
  make the phone download them again. Fonts: all subsets ship (unicode-range loads only what a page
  uses); the two latin files needed for first paint are preloaded by a small Vite plugin.
- **Pitfall fixed**: Radix `Slot` (used by `TooltipTrigger asChild`) joins class names as strings, so
  a NavLink function `className` came out as its source text in the collapsed rail. Navigation links
  use plain class strings and `aria-[current=page]:` variants; a test guards it.

## Checks (run in `web/`)

- `npm run lint`: clean (0 warnings).
- `npm run typecheck`: clean.
- `npm test`: 125 tests in 12 files pass: lazy routes and fallbacks (every route and tab, redirects,
  not found, chunk failure), layout and navigation on desktop and phone (landmarks, skip link,
  `aria-current`, rail collapse saved and restored, tooltips, URL tabs with replace and arrow keys,
  focus after navigation, viewport changes), theme (provider, system changes, saved choice, other
  tabs, menus, `theme-init.js` itself), command palette (shortcuts, search, keywords, navigation and
  focus, theme, sidebar, phone), every UI component, token contrast and rules, PWA manifest and
  icons, service worker registration, the persistent store.
- `npm run build`: passes, `verify-dist` clean.

Bundle, from Vite's report (minified / gzip, 1 kB = 1000 bytes): `react` 312.7 / 98.8 kB, `index`
(shell) 189.1 / 60.7 kB, CSS 55.7 / 11.5 kB, runtime 0.6 / 0.4 kB. First load (those four) is 558 kB
minified, about 170 kB gzip. Lazy: command palette 21.6 / 7.7 kB; pages 3.1–5.9 kB each (shared
`section-tabs` 4.1, `detail-list` 1.6, `empty-state` 0.9). Fonts: 10 woff2 files; the latin pair
preloaded for first paint is 179 kB (Newsreader 132.0, Schibsted Grotesk 46.8). `dist/` is 1.3 MB.

## Not verified

- Install and behaviour on a real iPhone or Android phone, and Safari/WebKit in general (screenshots
  were Chromium only). The dev server and `npm run preview` were not started (no long-running
  processes in this session).
- Whether Easy Auth lets the browser fetch manifest icons during install (they are behind auth like
  everything except `/api/health`). Worth checking with the Phase 0 phone spike.

## Follow-ups

- When the first API lands: TanStack Query, the generated `openapi-fetch` client, the SSE client, the
  401/403 handling from `core.md`, and `X-Request-Id` in error toasts.
- Studio: TipTap editor, a serif long-form preview style (`leading-prose`, `max-w-measure`), Monaco
  for prompts. Insights charts with the dataviz skill.
- Push: `sw.js` gets the `push` and `notificationclick` handlers (title and link only).
