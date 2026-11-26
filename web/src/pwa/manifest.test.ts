import { describe, expect, it } from 'vitest';

import { THEME_COLOR } from '@/theme/theme';
import { readProjectBytes, readProjectFile } from '@/test/files';

interface ManifestIcon {
  src: string;
  sizes: string;
  type: string;
  purpose?: string;
}

interface Manifest {
  name: string;
  short_name: string;
  start_url: string;
  scope: string;
  display: string;
  theme_color: string;
  background_color: string;
  icons: ManifestIcon[];
}

const manifest = JSON.parse(readProjectFile('public/manifest.webmanifest')) as Manifest;

/** Width and height from a PNG's IHDR chunk. */
function pngSize(path: string): [number, number] {
  const bytes = readProjectBytes(path);
  expect(bytes.subarray(1, 4).toString('ascii')).toBe('PNG');
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

describe('web app manifest', () => {
  it('installs as a standalone app that opens on Today', () => {
    expect(manifest.name).toBe('Blog Writer');
    expect(manifest.short_name.length).toBeLessThanOrEqual(12);
    expect(manifest.start_url).toBe('/today');
    expect(manifest.scope).toBe('/');
    expect(manifest.display).toBe('standalone');
    expect(manifest.theme_color).toBe(THEME_COLOR.light);
    expect(manifest.background_color).toBe(THEME_COLOR.light);
  });

  it('ships every icon it lists, at the size it claims, from this origin', () => {
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith('/'), icon.src).toBe(true);
      const path = `public${icon.src}`;
      if (icon.type === 'image/svg+xml') {
        expect(readProjectFile(path)).toContain('<svg');
        continue;
      }
      const [width, height] = pngSize(path);
      expect(`${width}x${height}`, icon.src).toBe(icon.sizes);
    }
  });

  it('includes the 192 and 512 px icons and a maskable icon', () => {
    const sizes = manifest.icons.map((icon) => icon.sizes);
    expect(sizes).toEqual(expect.arrayContaining(['192x192', '512x512']));
    expect(manifest.icons.some((icon) => icon.purpose === 'maskable')).toBe(true);
  });

  it('is linked from index.html with credentials (Easy Auth) and an Apple touch icon', () => {
    const html = readProjectFile('index.html');
    expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials" />');
    expect(html).toContain('<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />');
    expect(pngSize('public/icons/apple-touch-icon.png')).toEqual([180, 180]);
  });
});

describe('service worker stub', () => {
  const source = readProjectFile('public/sw.js');

  it('takes control without caching or intercepting requests', () => {
    expect(source).toContain("addEventListener('install'");
    expect(source).toContain("addEventListener('activate'");
    expect(source).not.toMatch(/addEventListener\('fetch'/);
    expect(source).not.toMatch(/caches\./);
  });

  it('has no push handler yet', () => {
    expect(source).not.toMatch(/addEventListener\('push'/);
  });
});

describe('index.html under CSP script-src self', () => {
  const html = readProjectFile('index.html');

  it('has no inline scripts or inline event handlers', () => {
    const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
    expect(scripts.length).toBeGreaterThan(0);
    for (const [, attributes = '', body = ''] of scripts) {
      expect(attributes).toMatch(/\ssrc="\//);
      expect(body.trim()).toBe('');
    }
    expect(html).not.toMatch(/\son[a-z]+="/i);
  });
});
