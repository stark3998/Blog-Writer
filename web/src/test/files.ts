import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/** Reads a file relative to the web/ project root (Vitest runs from there). */
export function readProjectFile(path: string): string {
  return readFileSync(join(process.cwd(), path), 'utf8');
}

export function readProjectBytes(path: string): Buffer {
  return readFileSync(join(process.cwd(), path));
}
