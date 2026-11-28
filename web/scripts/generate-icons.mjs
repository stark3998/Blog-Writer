#!/usr/bin/env node
/**
 * Generates the app mark as SVG and PNG with no dependencies.
 *
 * The mark is three lines of writing, one per content pillar, on blue-black ink:
 * AI agents & security, identity modernization and AI governance. Colours are
 * the dark-theme pillar tokens from src/styles/index.css.
 *
 * Usage: npm run icons (writes into public/). The outputs are committed.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { crc32, deflateSync } from 'node:zlib';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
const INK = '#151d2b';
const BARS = [
  { x: 112, y: 148, w: 288, h: 56, color: '#96b6fe' }, // AI agents & security
  { x: 112, y: 228, w: 224, h: 56, color: '#6ecdcc' }, // Identity modernization
  { x: 112, y: 308, w: 160, h: 56, color: '#e3a1dc' }, // AI governance
];

function svg({ rounded }) {
  const bg = rounded
    ? `<rect width="512" height="512" rx="112" fill="${INK}"/>`
    : `<rect width="512" height="512" fill="${INK}"/>`;
  const bars = BARS.map(
    (b) => `<rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="${b.h / 2}" fill="${b.color}"/>`,
  ).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">${bg}${bars}</svg>\n`;
}

function hexToRgb(hex) {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function insideRoundedRect(px, py, { x, y, w, h, r }) {
  if (px < x || py < y || px > x + w || py > y + h) return false;
  const dx = Math.max(x + r - px, 0, px - (x + w - r));
  const dy = Math.max(y + r - py, 0, py - (y + h - r));
  return dx * dx + dy * dy <= r * r;
}

/** Rasterises the mark at `size` px with 4x4 supersampling. `scale` shrinks the artwork (maskable safe zone). */
function rasterise(size, { rounded, scale = 1 }) {
  const shapes = [
    { x: 0, y: 0, w: 512, h: 512, r: rounded ? 112 : 0, rgb: hexToRgb(INK), art: false },
    ...BARS.map((b) => ({ ...b, r: b.h / 2, rgb: hexToRgb(b.color), art: true })),
  ];
  const pixels = Buffer.alloc(size * size * 4);
  const unit = 512 / size;
  const samples = 4;
  for (let py = 0; py < size; py += 1) {
    for (let px = 0; px < size; px += 1) {
      let [r, g, b, a] = [0, 0, 0, 0];
      for (const shape of shapes) {
        let hits = 0;
        for (let sy = 0; sy < samples; sy += 1) {
          for (let sx = 0; sx < samples; sx += 1) {
            let ux = (px + (sx + 0.5) / samples) * unit;
            let uy = (py + (sy + 0.5) / samples) * unit;
            if (shape.art) {
              ux = 256 + (ux - 256) / scale;
              uy = 256 + (uy - 256) / scale;
            }
            if (insideRoundedRect(ux, uy, shape)) hits += 1;
          }
        }
        const coverage = hits / (samples * samples);
        if (coverage === 0) continue;
        // Porter-Duff "over" in straight alpha.
        const outA = coverage + a * (1 - coverage);
        r = (shape.rgb[0] * coverage + r * a * (1 - coverage)) / outA;
        g = (shape.rgb[1] * coverage + g * a * (1 - coverage)) / outA;
        b = (shape.rgb[2] * coverage + b * a * (1 - coverage)) / outA;
        a = outA;
      }
      const i = (py * size + px) * 4;
      pixels[i] = Math.round(r);
      pixels[i + 1] = Math.round(g);
      pixels[i + 2] = Math.round(b);
      pixels[i + 3] = Math.round(a * 255);
    }
  }
  return pixels;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

function encodePng(size, rgba) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8; // bit depth
  header[9] = 6; // RGBA
  const stride = size * 4;
  const raw = Buffer.alloc((stride + 1) * size);
  for (let y = 0; y < size; y += 1) {
    raw[y * (stride + 1)] = 0; // filter: none
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const outputs = [
  { file: 'icons/icon-192.png', size: 192, rounded: true },
  { file: 'icons/icon-512.png', size: 512, rounded: true },
  // Maskable: full-bleed background, artwork inside the 80% safe zone.
  { file: 'icons/icon-maskable-512.png', size: 512, rounded: false, scale: 0.8 },
  // iOS masks the icon itself and fills transparent corners with black.
  { file: 'icons/apple-touch-icon.png', size: 180, rounded: false },
  { file: 'icons/favicon-32.png', size: 32, rounded: true },
];

mkdirSync(join(root, 'icons'), { recursive: true });
writeFileSync(join(root, 'favicon.svg'), svg({ rounded: true }));
writeFileSync(join(root, 'icons', 'icon.svg'), svg({ rounded: true }));
for (const { file, size, rounded, scale } of outputs) {
  writeFileSync(join(root, file), encodePng(size, rasterise(size, { rounded, scale })));
  console.log(`wrote public/${file}`);
}
console.log('wrote public/favicon.svg and public/icons/icon.svg');
