// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { readProjectFile } from '@/test/files';

import { readSharedPayload, SHARE_PARAMS } from './lib/share';

interface ShareTarget {
  action: string;
  method?: string;
  enctype?: string;
  params: { title?: string; text?: string; url?: string; files?: unknown };
}

const manifest = JSON.parse(readProjectFile('public/manifest.webmanifest')) as { share_target?: ShareTarget; scope: string };

describe('the share target (Android shares text and links into Today)', () => {
  it('lands on /today with a GET, inside the app’s scope', () => {
    const target = manifest.share_target;
    expect(target).toBeDefined();
    expect(target?.action).toBe('/today');
    expect(target?.action.startsWith(manifest.scope)).toBe(true);
    expect(target?.method ?? 'GET').toBe('GET');
  });

  it('maps text and url to the parameters Today reads, and asks for nothing else', () => {
    expect(manifest.share_target?.params).toEqual({ text: SHARE_PARAMS.text, url: SHARE_PARAMS.url });
    // No title (other members' names stay out of the URL) and no files: a file share needs a
    // POST and a service-worker handler, which this app does not have.
    expect(manifest.share_target?.params.title).toBeUndefined();
    expect(manifest.share_target?.params.files).toBeUndefined();
  });

  it('round-trips what Android sends into what the Engage panel shows', () => {
    const query = new URLSearchParams({ [SHARE_PARAMS.text]: 'A good point', [SHARE_PARAMS.url]: 'https://www.linkedin.com/posts/1' });
    expect(readSharedPayload(query)).toEqual({ text: 'A good point', url: 'https://www.linkedin.com/posts/1' });
  });
});

