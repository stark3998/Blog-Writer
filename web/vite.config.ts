import { readFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defaultClientConditions, type HtmlTagDescriptor, type Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

const packageJson: unknown = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8'),
);

function readVersion(pkg: unknown): string {
  if (typeof pkg === 'object' && pkg !== null && 'version' in pkg && typeof pkg.version === 'string') {
    return pkg.version;
  }
  throw new Error('package.json has no version');
}

/**
 * Preloads the two latin font files every page needs for first paint: the UI
 * sans and the display serif used by page titles. Everything else loads on
 * demand through unicode-range.
 */
function preloadCriticalFonts(patterns: readonly RegExp[]): Plugin {
  return {
    name: 'bw:preload-critical-fonts',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        const bundle = ctx.bundle;
        if (!bundle) return [];
        const tags: HtmlTagDescriptor[] = [];
        for (const file of Object.values(bundle)) {
          if (file.type !== 'asset' || !patterns.some((pattern) => pattern.test(file.fileName))) {
            continue;
          }
          tags.push({
            tag: 'link',
            attrs: {
              rel: 'preload',
              as: 'font',
              type: 'font/woff2',
              href: `/${file.fileName}`,
              crossorigin: 'anonymous',
            },
            injectTo: 'head',
          });
        }
        return tags;
      },
    },
  };
}

// The FastAPI server listens here in local development.
const apiTarget = process.env.BW_API_ORIGIN ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    preloadCriticalFonts([
      /schibsted-grotesk-latin-wght-normal-[\w-]+\.woff2$/,
      /newsreader-latin-opsz-normal-[\w-]+\.woff2$/,
    ]),
  ],
  define: {
    __APP_VERSION__: JSON.stringify(readVersion(packageJson)),
    __BUILD_SHA__: JSON.stringify(process.env.BW_BUILD_SHA ?? 'local'),
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    // Tests resolve packages like the production build does (ESM first). Without
    // this, Vitest loads react-router's CJS build, whose own require() reaches
    // the ESM build through `module-sync`: two router contexts in one test
    // ("useRouteError must be used within a data router").
    conditions: process.env.VITEST ? ['module-sync', ...defaultClientConditions] : undefined,
  },
  build: {
    // CSP: every asset is a same-origin file. Never inline data: URIs, so the
    // server can keep font-src and img-src at 'self'.
    assetsInlineLimit: 0,
    sourcemap: false,
    rolldownOptions: {
      output: {
        codeSplitting: {
          // React and the router change rarely: keep them in their own long-cached
          // chunk so an app deploy does not make the phone download them again.
          groups: [
            {
              name: 'react',
              test: /[\\/]node_modules[\\/](react|react-dom|scheduler|react-router)[\\/]/,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
      '/.auth': { target: apiTarget, changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
