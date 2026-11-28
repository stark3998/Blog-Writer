import { Plus } from 'lucide-react';
import { useId, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useApiQuery } from '@/lib/query/api-query';

import { LibrarySection, Subsection } from './components/section-intro';
import { formatInteger } from './lib/format';
import type { OwnerInput } from './lib/types';
import { Composer, type ComposerMode } from './stories/composer';
import { PovsList, StoriesList } from './stories/entry-lists';
import { OWNER_INPUTS_KEY } from './stories/model';
import { ReviewQueue } from './stories/review-queue';
import { ClassifyAction, SeedPanel } from './stories/seed-panel';

const MEASURES: readonly { code: string; label: string }[] = [
  { code: 'stories_approved', label: 'Approved stories' },
  { code: 'povs_per_pillar', label: 'Pillars with three or more approved positions' },
];

/** How far the banks are from what drafting needs (`GET /api/kb/inputs`). */
function BankProgress() {
  const inputs = useApiQuery({ queryKey: OWNER_INPUTS_KEY, path: '/api/kb/inputs' });
  const baseId = useId();
  const items = inputs.data?.items ?? [];
  const shown = MEASURES.map((measure) => ({ ...measure, input: items.find((item) => item.code === measure.code) })).filter(
    (measure): measure is { code: string; label: string; input: OwnerInput } =>
      typeof measure.input?.have === 'number' && typeof measure.input.need === 'number',
  );
  if (shown.length === 0) return null;
  return (
    <dl className="grid gap-5 sm:grid-cols-2">
      {shown.map(({ code, label, input }) => {
        const have = input.have ?? 0;
        const need = input.need ?? 0;
        const id = `${baseId}-${code}`;
        return (
          <div key={code} className="space-y-2">
            <dt id={id} className="text-sm text-ink-muted">
              {label}
            </dt>
            <dd className="space-y-2">
              <span className="block text-ink">
                <span className="font-semibold tabular-nums">{`${formatInteger(have)} of ${formatInteger(need)}`}</span>
                {input.done ? ', enough for drafting' : ''}
              </span>
              <Progress
                value={Math.min(have, need)}
                max={Math.max(need, 1)}
                aria-labelledby={id}
                getValueLabel={() => `${String(have)} of ${String(need)}`}
                tone={input.done ? 'success' : 'ink'}
                size="sm"
                className="max-w-xs"
              />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/**
 * Library > Stories & POV (`/library/stories`): the review queue for proposed entries (phone
 * first), the story bank and the position bank with status, confidentiality, classifier verdict
 * and pillars, new entries through the shared intake flow, seeding from the resume or portfolio
 * and the classifier. Calls `/api/stories*`, `/api/povs*`, `POST /api/stories/seed`,
 * `POST /api/stories/classify`, `/api/stories/intake/*` (through `@/features/intake`) and
 * `/api/kb/inputs` (contract section 10). Raw intake text stays in component state (contract 1.9,
 * rule 9).
 */
export function StoriesSection() {
  const [composer, setComposer] = useState<ComposerMode | undefined>();
  const [bank, setBank] = useState('stories');
  const opener = useRef<HTMLElement | null>(null);
  const headingId = useId();

  const open = (mode: ComposerMode) => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setComposer(mode);
  };
  const close = () => {
    setComposer(undefined);
    requestAnimationFrame(() => {
      const back = opener.current;
      if (back?.isConnected) back.focus();
      else document.getElementById(headingId)?.focus();
    });
  };

  return (
    <LibrarySection
      headingId={headingId}
      title="Stories and positions"
      standfirst={
        <p>
          A story records a problem, what you did, the trade-offs, rounded metrics and what you learned. A position is your view on a pillar
          topic, in your own words. Only anonymized or public wording is stored.
        </p>
      }
      yourPart="Approve each entry before a draft can use it. You decide what is anonymized and what is public."
      actions={
        <>
          <Button
            onClick={() => {
              open({ kind: 'new-story' });
            }}
          >
            <Plus aria-hidden="true" />
            New story
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              open({ kind: 'new-pov' });
            }}
          >
            <Plus aria-hidden="true" />
            New position
          </Button>
        </>
      }
    >

      {composer ? <Composer mode={composer} onClose={close} /> : null}

      <ReviewQueue onEdit={open} />

      <BankProgress />

      <Tabs value={bank} onValueChange={setBank} className="gap-6">
        <TabsList aria-label="Banks">
          <TabsTrigger value="stories">Stories</TabsTrigger>
          <TabsTrigger value="povs">Positions</TabsTrigger>
        </TabsList>
        <TabsContent value="stories">
          <StoriesList onEdit={open} />
        </TabsContent>
        <TabsContent value="povs">
          <PovsList onEdit={open} />
        </TabsContent>
      </Tabs>

      <Subsection
        title="Propose entries from your resume or portfolio"
        description="Optional. A model reads the text and proposes stories and positions; each one waits in the review queue for your approval."
      >
        <SeedPanel />
      </Subsection>

      <Subsection
        title="Confidentiality checks"
        description="Each entry is checked when you save it. Entries saved while the AI was off, or that changed since, are checked here."
      >
        <ClassifyAction />
      </Subsection>
    </LibrarySection>
  );
}
