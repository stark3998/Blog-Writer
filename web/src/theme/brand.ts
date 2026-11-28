/**
 * The app mark's colours: blue-black ink and one line per pillar thread (the dark-theme pillar
 * tokens, which read well on ink). Fixed rather than themed: the mark looks the same in both
 * themes, as it does on the home screen.
 *
 * Shared with the icons drawn by scripts/generate-icons.mjs (public/favicon.svg and the PNGs);
 * brand.test.tsx keeps them in step. Components take colours from here or from the tokens, never
 * as literals.
 */
export const BRAND_INK = '#151d2b';

/** Agents (cobalt), identity (teal), governance (plum): top line to bottom line. */
export const BRAND_THREADS = ['#96b6fe', '#6ecdcc', '#e3a1dc'] as const;
