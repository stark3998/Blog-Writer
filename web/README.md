# Blog Writer v2: web

The owner's workspace: Today, Calendar, Studio, Library and Settings. Vite, React 19, TypeScript
(strict), React Router 7 (data mode, lazy routes), Tailwind v4 tokens, Radix primitives with
shadcn-style components, Sonner, cmdk and self-hosted fonts. An installable PWA.

Design notes, tokens, routes and decisions: [`docs/v2/phase1/web.md`](../docs/v2/phase1/web.md).

## Scripts

| Command | What it does |
|---|---|
| `npm ci` | Install the locked dependencies (Node 24) |
| `npm run dev` | Vite dev server; `/api` and `/.auth` proxy to `BW_API_ORIGIN` (default `http://127.0.0.1:8000`) |
| `npm run build` | Type-check, build to `dist/`, then `scripts/verify-dist.mjs` (CSP and self-containment checks: no inline script, `eval` or external host beyond a short text-only allowlist; bundle sizes) |
| `npm run preview` | Serve `dist/` locally |
| `npm run lint` | ESLint (typescript-eslint strict-type-checked, React Hooks), no warnings allowed |
| `npm run typecheck` | `tsc -b` over the app, test and config projects |
| `npm test` | Vitest with jsdom and Testing Library |
| `npm run icons` | Regenerate the app icons in `public/` (no dependencies) |

The server serves `dist/` when `BW_SPA_DIR` points at it. Set `BW_BUILD_SHA` at build time to show
the commit in Settings, System.
