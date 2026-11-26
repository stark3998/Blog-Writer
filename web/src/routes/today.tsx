import { MessageSquareReply, Mic, Send, Stamp } from 'lucide-react';

import { DetailList, type DetailItem } from '@/components/shell/detail-list';
import { EmptyState } from '@/components/shell/empty-state';
import { Page, PageHeader } from '@/components/shell/page';
import { DataProvider } from '@/features/data/data-provider';
import { TodayStatus } from '@/features/today/today-status';

/** The four kinds of item that land in the inbox. */
const ACTIONS: readonly DetailItem[] = [
  {
    id: 'approve',
    term: 'Approve',
    icon: Stamp,
    detail: 'Next week’s plan on Sunday evening, and each draft when it is ready for review.',
  },
  {
    id: 'answer',
    term: 'Answer',
    icon: Mic,
    detail: 'Micro-interviews: two short questions per piece, under three minutes. Dictation works.',
  },
  {
    id: 'publish',
    term: 'Publish',
    icon: Send,
    detail: 'One tap at each post slot. Nothing reaches LinkedIn until you tap.',
  },
  {
    id: 'reply',
    term: 'Reply',
    icon: MessageSquareReply,
    detail: 'Reminders 15 minutes, 1 hour and 3 hours after a post goes live.',
  },
];

/** The owner time budget from the plan: about 2.5 to 3 hours a week. */
const WEEK: readonly DetailItem[] = [
  {
    id: 'sunday',
    term: 'Sunday',
    meta: '30 min',
    detail: 'Approve next week’s plan and answer the micro-interviews.',
  },
  { id: 'mid-week', term: 'Mid-week', meta: '30 min', detail: 'Review the flagship article and its posts.' },
  { id: 'slots', term: 'Each post slot', meta: 'About 1 min', detail: 'Publish with one tap.' },
  { id: 'daily', term: 'Daily', meta: '15 min', detail: 'Engage on LinkedIn: replies and comments.' },
];

export function Component() {
  // DataProvider passes through when the app already provides one around the router.
  return (
    <DataProvider>
      <TodayPage />
    </DataProvider>
  );
}

function TodayPage() {
  return (
    <Page>
      <PageHeader
        title="Today"
        standfirst="Approvals, answers, posts to publish and replies. Anything that needs you lands here first."
      >
        <TodayStatus />
      </PageHeader>

      <EmptyState
        size="lg"
        title="Nothing needs you right now."
        yourPart="Clear this list when it fills up. Most items take a minute or two, and they work one-handed on your phone."
      >
        <p>When the engine needs a decision from you, it appears here with one clear action. There are four kinds:</p>
        <DetailList items={ACTIONS} className="mt-6" />
      </EmptyState>

      <section aria-labelledby="your-week" className="mt-16 max-w-measure md:mt-20">
        <h2 id="your-week" className="font-serif text-2xl font-medium text-ink">
          Your week
        </h2>
        <p className="mt-2 text-ink-muted">Where your time goes in a default week.</p>
        <DetailList items={WEEK} className="mt-6" />
        <p className="mt-4 font-semibold text-ink">About 2½ to 3 hours a week in total.</p>
      </section>
    </Page>
  );
}
