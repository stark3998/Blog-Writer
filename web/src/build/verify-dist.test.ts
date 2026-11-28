// @vitest-environment node
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';

/** The last step of `npm run build`, run here against small hand-made builds. */
const SCRIPT = join(process.cwd(), 'scripts', 'verify-dist.mjs');

type Files = Record<string, string>;

/** A minimal build that passes every check, with the text-only hosts real bundles contain. */
function cleanBuild(): Files {
  return {
    'index.html': [
      '<!doctype html><html><head>',
      '<script src="/theme-init.js"></script>',
      '<script type="module" crossorigin src="/assets/index-a1.js"></script>',
      '<link rel="stylesheet" crossorigin href="/assets/index-a1.css">',
      '</head><body><div id="root"></div></body></html>',
    ].join(''),
    'assets/index-a1.js': [
      'const svg = "http://www.w3.org/2000/svg";',
      'const docs = "https://react.dev/errors/" + 1 + " https://reactrouter.com/en/main";',
      'const base = new URL("/", "http://localhost");',
      'const isFunction = (value) => typeof value === "function";',
      'isFunction(svg);',
    ].join('\n'),
    'assets/index-a1.css': '/*! tailwindcss | MIT License | https://tailwindcss.com */ @font-face{src:url(/assets/f-a1.woff2)}',
    'manifest.webmanifest': JSON.stringify({
      start_url: '/today',
      display: 'standalone',
      icons: [{ src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' }],
    }),
    'icons/icon-192.png': 'png',
    'sw.js': "self.addEventListener('install', function () {});",
    'theme-init.js': '(function () {})();',
    'favicon.svg': '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    'robots.txt': 'User-agent: *\nDisallow: /\n',
  };
}

const made: string[] = [];

function verify(files: Files): { ok: boolean; output: string } {
  const dist = mkdtempSync(join(tmpdir(), 'bw-dist-'));
  made.push(dist);
  for (const [path, content] of Object.entries(files)) {
    mkdirSync(dirname(join(dist, path)), { recursive: true });
    writeFileSync(join(dist, path), content);
  }
  const result = spawnSync(process.execPath, [SCRIPT, dist], { encoding: 'utf8', timeout: 30_000 });
  return { ok: result.status === 0, output: `${result.stdout}${result.stderr}` };
}

function withFile(path: string, content: string): Files {
  return { ...cleanBuild(), [path]: content };
}

afterEach(() => {
  for (const dist of made.splice(0)) rmSync(dist, { recursive: true, force: true });
});

describe('verify-dist', () => {
  it('passes a self-contained build, allowing hosts that only appear as text', () => {
    const { ok, output } = verify(cleanBuild());
    expect(output).toContain('verify-dist: self-contained, no inline scripts or eval, PWA files present.');
    expect(ok).toBe(true);
  });

  it('fails on any other external host, wherever it appears', () => {
    const js = verify(withFile('assets/index-a1.js', 'fetch("https://api.example.com/v1/data");'));
    expect(js.ok).toBe(false);
    expect(js.output).toContain('assets/index-a1.js: references the external host api.example.com');

    const socket = verify(withFile('assets/index-a1.js', 'new WebSocket("wss://events.example.net:443/live");'));
    expect(socket.output).toContain('references the external host events.example.net');

    const manifest = verify(
      withFile(
        'manifest.webmanifest',
        JSON.stringify({
          start_url: '/today',
          display: 'standalone',
          icons: [{ src: '/icons/icon-192.png' }],
          shortcuts: [{ name: 'x', url: '/today', icons: [{ src: 'https://cdn.example.org/i.png' }] }],
        }),
      ),
    );
    expect(manifest.ok).toBe(false);
    expect(manifest.output).toContain('manifest.webmanifest: references the external host cdn.example.org');
  });

  it('fails on CDN fonts and remote CSS', () => {
    const { ok, output } = verify(withFile('assets/index-a1.css', '@import url(https://fonts.googleapis.com/css2);'));
    expect(ok).toBe(false);
    expect(output).toContain('assets/index-a1.css: remote @import');
    expect(output).toContain('assets/index-a1.css: references fonts.googleapis.com');
  });

  it.each([
    ['eval', 'const run = (code) => eval(code);'],
    ['window.eval', 'window.eval("1 + 1");'],
    ['new Function', 'const make = new Function("a", "return a");'],
    ['Function()', 'const g = Function("return this")();'],
  ])('fails on %s, which the CSP refuses without unsafe-eval', (_name, code) => {
    const { ok, output } = verify(withFile('assets/index-a1.js', code));
    expect(ok).toBe(false);
    expect(output).toContain("assets/index-a1.js: eval() or Function() needs 'unsafe-eval'");
  });

  it('fails on inline script and a missing service worker', () => {
    const files = withFile(
      'index.html',
      '<!doctype html><html><head><script>window.x = 1</script></head><body></body></html>',
    );
    delete files['sw.js'];
    const { ok, output } = verify(files);
    expect(ok).toBe(false);
    expect(output).toContain('index.html: inline script content');
    expect(output).toContain('missing dist/sw.js');
  });
});
