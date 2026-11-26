#!/usr/bin/env node
/**
 * Checks the production build is self-contained and CSP-ready, then prints
 * the bundle sizes. Runs as the last step of `npm run build`; any violation
 * fails the build.
 *
 * - index.html: no inline scripts or inline event handlers (CSP script-src 'self'),
 *   and every src/href is same-origin.
 * - CSS: no remote url() or @import, no data: URIs (font-src/img-src 'self').
 * - JS: no eval() or new Function() (the CSP has no 'unsafe-eval').
 * - Everything: no known CDN host, and no other host than the few that only ever
 *   appear as text (XML namespaces, links in library error messages, licence banners).
 * - PWA: manifest icons, the service worker and the theme bootstrap are present.
 *
 * Usage: node scripts/verify-dist.mjs [dist directory, default ../dist]
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

const dist = process.argv[2]
  ? resolve(process.argv[2])
  : join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const problems = [];
const fail = (message) => problems.push(message);

const CDN_HOSTS = [
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'cdn.jsdelivr.net',
  'unpkg.com',
  'cdnjs.cloudflare.com',
  'esm.sh',
  'cdn.skypack.dev',
  'use.typekit.net',
  'rsms.me',
];

/**
 * Hosts that appear in the build as plain text, never as something the page loads: XML
 * namespaces (www.w3.org), the error-decoder and docs links inside React and React Router
 * messages, Tailwind's licence banner, and React Router's base for building URLs (localhost).
 * Any other host fails the build. Before adding one, check that nothing fetches it: the CSP
 * would block the request anyway (connect-src, img-src and the rest are 'self').
 */
const TEXT_ONLY_HOSTS = new Set(['www.w3.org', 'react.dev', 'reactrouter.com', 'tailwindcss.com', 'localhost']);
const URL_HOST = /\b(?:https?|wss?):\/\/([^/\s"'`)<>\\:]+)/gi;

/** eval (also `window.eval`) and the Function constructor need 'unsafe-eval', which the CSP does not allow. */
const EVAL = /(?<![\w$])eval\s*\(|(?<![\w$.])Function\s*\(/;

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

if (!existsSync(join(dist, 'index.html'))) {
  console.error('verify-dist: dist/index.html is missing. Run vite build first.');
  process.exit(1);
}

const files = walk(dist);
const textFiles = files.filter((file) => ['.html', '.css', '.js', '.webmanifest', '.svg', '.txt'].includes(extname(file)));

// index.html
const html = readFileSync(join(dist, 'index.html'), 'utf8');
for (const [, attributes, body] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (!/\ssrc="\//.test(attributes)) fail(`index.html: script without a same-origin src: <script${attributes}>`);
  if (body.trim() !== '') fail('index.html: inline script content');
}
if (/\son[a-z]+=/i.test(html)) fail('index.html: inline event handler attribute');
for (const [, attribute, url] of html.matchAll(/\s(src|href)="([^"]*)"/g)) {
  if (!url.startsWith('/') || url.startsWith('//')) fail(`index.html: ${attribute}="${url}" is not same-origin`);
}
if (/<style\b/.test(html) || /\sstyle="/.test(html)) fail('index.html: inline style');

// CSS
for (const file of files.filter((path) => extname(path) === '.css')) {
  const css = readFileSync(file, 'utf8');
  const name = relative(dist, file);
  if (/url\(\s*['"]?(https?:)?\/\//.test(css)) fail(`${name}: remote url()`);
  if (/@import\s+(url\()?['"]?(https?:)?\/\//.test(css)) fail(`${name}: remote @import`);
  if (/url\(\s*['"]?data:/.test(css)) fail(`${name}: data: URI (needs font-src/img-src data:)`);
}

// CDN hosts, and any host that is not known to be text only
for (const file of textFiles) {
  const text = readFileSync(file, 'utf8');
  const name = relative(dist, file);
  for (const host of CDN_HOSTS) {
    if (text.includes(host)) fail(`${name}: references ${host}`);
  }
  const hosts = new Set([...text.matchAll(URL_HOST)].map(([, host]) => host.toLowerCase()));
  for (const host of hosts) {
    if (!TEXT_ONLY_HOSTS.has(host)) fail(`${name}: references the external host ${host}`);
  }
}

// JS: no eval
for (const file of files.filter((path) => extname(path) === '.js')) {
  if (EVAL.test(readFileSync(file, 'utf8'))) fail(`${relative(dist, file)}: eval() or Function() needs 'unsafe-eval'`);
}

// PWA pieces
const required = ['manifest.webmanifest', 'sw.js', 'theme-init.js', 'favicon.svg', 'robots.txt'];
for (const file of required) {
  if (!existsSync(join(dist, file))) fail(`missing dist/${file}`);
}
const manifest = JSON.parse(readFileSync(join(dist, 'manifest.webmanifest'), 'utf8'));
if (manifest.start_url !== '/today') fail('manifest: start_url must be /today');
if (manifest.display !== 'standalone') fail('manifest: display must be standalone');
for (const icon of manifest.icons ?? []) {
  if (!existsSync(join(dist, icon.src))) fail(`manifest: missing icon ${icon.src}`);
}

// Sizes
// Same unit as Vite's build report: 1 kB = 1000 bytes.
const kb = (bytes) => `${(bytes / 1000).toFixed(1)} kB`;
const rows = files
  .filter((file) => ['.js', '.css', '.woff2', '.html'].includes(extname(file)))
  .map((file) => {
    const bytes = readFileSync(file);
    const gzip = ['.woff2'].includes(extname(file)) ? bytes.length : gzipSync(bytes).length;
    return { name: relative(dist, file), size: bytes.length, gzip };
  })
  .sort((a, b) => b.size - a.size);

const initialJs = html.match(/\/assets\/[\w.-]+\.js/g) ?? [];
const initial = rows.filter((row) => initialJs.includes(`/${row.name}`) || row.name.endsWith('.css'));
const sum = (list, key) => list.reduce((total, row) => total + row[key], 0);

console.log('\nBundle (raw / gzip):');
for (const row of rows) console.log(`  ${row.name.padEnd(64)} ${kb(row.size).padStart(10)} ${kb(row.gzip).padStart(10)}`);
console.log(
  `\nFirst load (entry JS + modulepreloads + CSS): ${kb(sum(initial, 'size'))} raw, ${kb(sum(initial, 'gzip'))} gzip`,
);

if (problems.length > 0) {
  console.error(`\nverify-dist: ${problems.length} problem(s):`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log('verify-dist: self-contained, no inline scripts or eval, PWA files present.');
