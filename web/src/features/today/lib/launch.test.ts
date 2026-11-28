// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { hasLaunchParams, readLaunch, withoutLaunchParams } from './launch';

const params = (query: string) => new URLSearchParams(query);

describe('readLaunch', () => {
  it('reads a share: the text and its https link', () => {
    expect(
      readLaunch(params('share=How%20do%20you%20scope%20agents%3F&share_url=https%3A%2F%2Fwww.linkedin.com%2Fposts%2Fx')),
    ).toEqual({ kind: 'share', text: 'How do you scope agents?', url: 'https://www.linkedin.com/posts/x' });
  });

  it('moves a link out of the shared text', () => {
    expect(readLaunch(params(`share=${encodeURIComponent('https://www.linkedin.com/posts/x')}`))).toEqual({
      kind: 'share',
      text: '',
      url: 'https://www.linkedin.com/posts/x',
    });
    expect(readLaunch(params(`share=${encodeURIComponent('Worth a read\nhttps://www.linkedin.com/posts/y')}`))).toEqual({
      kind: 'share',
      text: 'Worth a read',
      url: 'https://www.linkedin.com/posts/y',
    });
  });

  it('drops links the server would refuse and cuts long text at 5,000 characters', () => {
    expect(readLaunch(params('share=Hi&share_url=http%3A%2F%2Fexample.com'))).toEqual({ kind: 'share', text: 'Hi', url: null });
    const long = readLaunch(params(`share=${'é'.repeat(6000)}`));
    expect(long?.kind === 'share' ? Array.from(long.text).length : 0).toBe(5000);
  });

  it('ignores an empty share', () => {
    expect(readLaunch(params('share=%20%20'))).toBeNull();
  });

  it('reads reminders with a usable post id only', () => {
    expect(readLaunch(params('engage=post-9'))).toEqual({ kind: 'engage', postId: 'post-9' });
    expect(readLaunch(params('engage='))).toEqual({ kind: 'engage', postId: null });
    expect(readLaunch(params('engage=%3Cscript%3E'))).toEqual({ kind: 'engage', postId: null });
    expect(readLaunch(params('snapshot=post-8'))).toEqual({ kind: 'snapshot', postId: 'post-8' });
    expect(readLaunch(params('snapshot=../x'))).toBeNull();
    expect(readLaunch(params('tab=1'))).toBeNull();
  });
});

describe('cleaning the URL', () => {
  it('removes every launch parameter and keeps the others', () => {
    const query = params('share=x&share_url=y&engage=post-1&snapshot=post-2&keep=1');
    expect(hasLaunchParams(query)).toBe(true);
    expect(withoutLaunchParams(query).toString()).toBe('keep=1');
    expect(hasLaunchParams(params('keep=1'))).toBe(false);
  });
});
