// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { decisionReason } from './policy/autonomy-panel';
import { describeRule, formatHours, formatMinutes, formatRelative, formatUsd, jobTypeLabel } from './format';
import { connectionErrorSentence } from './integrations/linkedin-panel';
import { isLinkedInAuthorizeUrl } from './integrations/navigation';
import { tokenAgeText } from './integrations/token-ruler';
import { isHttpsUrl } from '@/lib/urls';
import { fetchErrorSentence } from './sources-section';
import { autonomyView } from './testing/fixtures';

const now = Date.parse('2026-11-27T15:00:00Z');

describe('words for numbers and times', () => {
  it('formats money, keeping tiny amounts honest', () => {
    expect(formatUsd(4.12)).toBe('$4.12');
    expect(formatUsd(0)).toBe('$0.00');
    expect(formatUsd(0.001)).toBe('under $0.01');
  });

  it('says how long ago or how soon', () => {
    expect(formatRelative('2026-11-27T12:00:00Z', now)).toBe('3 hours ago');
    expect(formatRelative('2026-11-29T15:00:00Z', now)).toBe('in 2 days');
    expect(formatRelative('2026-11-27T14:59:50Z', now)).toBe('now');
    expect(formatRelative(null, now)).toBe('–');
  });

  it('writes durations the way people say them', () => {
    expect(formatMinutes(15)).toBe('15 min');
    expect(formatMinutes(120)).toBe('2 h');
    expect(formatMinutes(90)).toBe('1 h 30 min');
    expect(formatHours(48)).toBe('2 days');
    expect(formatHours(168)).toBe('7 days');
    expect(formatHours(36)).toBe('36 h');
  });

  it('describes schedule rules', () => {
    expect(describeRule({ kind: 'weekly', days: ['sun'], at: '15:00' })).toBe('Every Sunday at 15:00');
    expect(describeRule({ kind: 'weekly', days: ['mon', 'wed', 'fri'], at: '08:00' })).toBe(
      'Every Monday, Wednesday and Friday at 08:00',
    );
    expect(describeRule({ kind: 'daily', at: '03:30' })).toBe('Every day at 03:30');
    expect(describeRule({ kind: 'interval', seconds: 21_600 })).toBe('Every 6 hours');
    expect(describeRule({ kind: 'interval', seconds: 3600 })).toBe('Every hour');
  });

  it('names job types, and reads unknown ones as words', () => {
    expect(jobTypeLabel('signals.crawl')).toBe('Feed crawl and triage');
    expect(jobTypeLabel('future.thing-new')).toBe('Future thing new');
  });
});

describe('owner-facing sentences for codes', () => {
  it('explains LinkedIn connection errors without echoing anything', () => {
    expect(connectionErrorSentence('oauth_denied')).toMatch(/You cancelled/);
    expect(connectionErrorSentence('token_expired')).toMatch(/60 days/);
    expect(connectionErrorSentence('weird_code')).toBe('The last attempt failed (weird_code).');
  });

  it('explains feed read failures', () => {
    expect(fetchErrorSentence('http_404')).toBe('The feed’s address answered “not found”.');
    expect(fetchErrorSentence('http_503')).toBe('The feed answered HTTP 503.');
    expect(fetchErrorSentence('not_a_feed')).toBe('The address didn’t return a feed.');
  });

  it('explains why auto-publish is held', () => {
    const view = autonomyView();
    expect(decisionReason('calibrating', view)).toBe('4 more clean reviews in a row needed.');
    expect(decisionReason('paused', view)).toBe('Autonomy is paused.');
  });

  it('counts the token’s days', () => {
    expect(tokenAgeText(12)).toBe('Day 12 of 60. 48 days left.');
    expect(tokenAgeText(59)).toBe('Day 59 of 60. 1 day left: reconnect before then.');
    expect(tokenAgeText(60)).toBe('Day 60 of 60. The token has run out.');
  });
});

describe('addresses', () => {
  it('only sends the tab to LinkedIn’s own authorize page', () => {
    expect(isLinkedInAuthorizeUrl('https://www.linkedin.com/oauth/v2/authorization?client_id=x&state=y')).toBe(true);
    expect(isLinkedInAuthorizeUrl('https://www.linkedin.com.evil.example/oauth/v2/authorization')).toBe(false);
    expect(isLinkedInAuthorizeUrl('http://www.linkedin.com/oauth/v2/authorization')).toBe(false);
    expect(isLinkedInAuthorizeUrl('https://www.linkedin.com/feed/')).toBe(false);
    expect(isLinkedInAuthorizeUrl('javascript:alert(1)')).toBe(false);
  });

  it('accepts only https feed addresses', () => {
    expect(isHttpsUrl('https://example.com/feed')).toBe(true);
    expect(isHttpsUrl('http://example.com/feed')).toBe(false);
    expect(isHttpsUrl('example.com')).toBe(false);
  });
});
