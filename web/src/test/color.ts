/** Colour maths for the token tests: OKLCH to sRGB, compositing and WCAG 2 contrast. */

export interface Oklch {
  l: number;
  c: number;
  h: number;
  alpha: number;
}

export type Rgb = readonly [number, number, number];

const OKLCH = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+))?\s*\)$/;

export function parseOklch(value: string): Oklch {
  const match = OKLCH.exec(value.trim());
  if (!match) throw new Error(`not an oklch() colour: ${value}`);
  const [, l, c, h, alpha] = match;
  return { l: Number(l), c: Number(c), h: Number(h), alpha: alpha === undefined ? 1 : Number(alpha) };
}

/** OKLCH to linear-light sRGB (unclamped, so out-of-gamut colours are visible). */
export function toLinearSrgb({ l, c, h }: Oklch): Rgb {
  const a = c * Math.cos((h * Math.PI) / 180);
  const b = c * Math.sin((h * Math.PI) / 180);
  const l1 = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m1 = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s1 = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l1 - 3.3077115913 * m1 + 0.2309699292 * s1,
    -1.2684380046 * l1 + 2.6097574011 * m1 - 0.3413193965 * s1,
    -0.0041960863 * l1 - 0.7034186147 * m1 + 1.707614701 * s1,
  ];
}

export function inSrgbGamut(color: Oklch, tolerance = 0.0005): boolean {
  return toLinearSrgb(color).every((channel) => channel >= -tolerance && channel <= 1 + tolerance);
}

const clamp = (value: number) => Math.min(1, Math.max(0, value));
const encode = (linear: number) => {
  const v = clamp(linear);
  return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
};
const decode = (encoded: number) => (encoded <= 0.04045 ? encoded / 12.92 : ((encoded + 0.055) / 1.055) ** 2.4);

/** Gamma-encoded sRGB in 0..1. */
export function toSrgb(color: Oklch): Rgb {
  const [r, g, b] = toLinearSrgb(color);
  return [encode(r), encode(g), encode(b)];
}

export function toHex(color: Oklch): string {
  return `#${toSrgb(color)
    .map((channel) => Math.round(channel * 255).toString(16).padStart(2, '0'))
    .join('')}`;
}

/** Paints a translucent colour over an opaque one, as browsers blend: in gamma-encoded sRGB. */
export function composite(top: Oklch, bottom: Rgb): Rgb {
  const front = toSrgb(top);
  return [0, 1, 2].map((i) => top.alpha * (front[i] ?? 0) + (1 - top.alpha) * (bottom[i] ?? 0)) as unknown as Rgb;
}

export function luminance([r, g, b]: Rgb): number {
  return 0.2126 * decode(r) + 0.7152 * decode(g) + 0.0722 * decode(b);
}

export function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}
