import { Page, PageHeader } from '@/components/shell/page';
import { EngagementPanel } from '@/features/engagement/engagement-panel';

import { IdeaCapture } from './idea-capture';
import { splitInbox } from './lib/items';
import { useAutonomy, useScorecards, useSystemStatus, useTodayInbox } from './lib/queries';
import { useTodaySheets } from './lib/use-today-sheets';
import { SnapshotSheet } from './snapshot-sheet';
import { TodayInbox } from './today-inbox';
import { TodayNotices } from './today-notices';
import { TodayStatus } from './today-status';
import { WeekSummary } from './week-summary';

/**
 * Today (`/today`, phone-first): everything that waits on the owner, most urgent first, each with
 * one action (approve the week's plan, answer interviews, review drafts and posts, go live at a
 * slot, reply, log numbers); notices that change how the engine behaves (safe mode, the spending
 * brake, the pause switch, LinkedIn and portfolio tokens); the week and its scorecard; and the
 * Engage panel. Reminders and the share target open the Engage panel or the snapshot form in place
 * (`?engage=`, `?snapshot=`, `?share=`). Calls `GET /api/today`, `/api/system/status`,
 * `/api/kb/autonomy`, `/api/plan/scorecard`, `/api/me`, `/api/health`,
 * `POST /api/reminders/{id}/dismiss`, `POST /api/posts/{id}/snapshots` and `POST /api/ideas` (quick
 * capture) (contract section 10).
 * Today's queries refetch when the owner comes back to the app.
 */
export function TodayPage() {
  const inbox = useTodayInbox();
  const status = useSystemStatus();
  const autonomy = useAutonomy();
  const scorecards = useScorecards();
  const sheets = useTodaySheets();

  const notices = inbox.data ? splitInbox(inbox.data.items).notices : [];

  return (
    <Page>
      <PageHeader title="Today" standfirst="Approvals, answers, posts to publish and replies. Anything that needs you lands here first.">
        <TodayStatus />
      </PageHeader>

      <TodayNotices
        status={status.data}
        statusFailed={status.isError}
        onRetryStatus={() => void status.refetch()}
        autonomy={autonomy.data}
        items={notices}
      />

      <TodayInbox
        today={inbox.data}
        isPending={inbox.isPending}
        error={inbox.error}
        onRetry={() => void inbox.refetch()}
        onReply={sheets.openReply}
        onSnapshot={sheets.openSnapshot}
      />

      <IdeaCapture />

      <WeekSummary week={inbox.data?.week} history={scorecards.data} failed={inbox.isError && !inbox.data} />

      <EngagementPanel open={sheets.engageOpen} onOpenChange={sheets.setEngageOpen} request={sheets.engageRequest} />

      <SnapshotSheet postId={sheets.snapshotPostId} onClose={sheets.closeSnapshot} />
    </Page>
  );
}
