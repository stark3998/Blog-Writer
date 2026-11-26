import { STUDIO_TABS } from '@/app/nav';
import { EmptyState } from '@/components/shell/empty-state';
import { Page, PageHeader } from '@/components/shell/page';

export function Component() {
  return (
    <Page>
      <PageHeader
        title="Studio"
        standfirst="One workspace per piece, from the first brief to go-live. Articles and their LinkedIn posts are written here."
      />
      <EmptyState
        title="No pieces in progress."
        yourPart="Answer the interview, then review the draft and its posts. Research, drafting and critique run in the background."
      >
        <p>
          A piece starts when you approve an idea or the weekly plan. Each piece moves through five stages, each with
          its own tab:
        </p>
        <ol className="mt-6 border-t border-line">
          {STUDIO_TABS.map((tab) => (
            <li key={tab.id} className="border-b border-line py-3 font-medium text-ink">
              {tab.label}
            </li>
          ))}
        </ol>
      </EmptyState>
    </Page>
  );
}
