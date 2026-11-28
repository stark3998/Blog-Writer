import {
  AlarmClock,
  Archive,
  Check,
  ChevronDown,
  Clock,
  ExternalLink,
  FileText,
  MessageSquareQuote,
  MoreHorizontal,
  Pencil,
  Repeat2,
  RotateCcw,
  Zap,
} from 'lucide-react';
import { useId } from 'react';
import { Link } from 'react-router';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { components } from '@/lib/api/schema';
import { cn } from '@/lib/cn';
import { pillarThreadClass } from '@/lib/pillars';

import { Meter } from '../components/meter';
import { PillarTag } from '../components/pillar';
import type { CanTransition } from '../data';
import { FORMAT_LABEL, IDEA_ORIGIN, IDEA_STATUS, LANE_LABEL } from '../lib/labels';
import type { FormatOption, PillarView } from '../lib/settings';
import { localDateOf, momentLabel, parseTimestamp, relativeFuture, shortDate } from '../lib/time';

type Idea = components['schemas']['Idea'];
export type IdeaTarget = components['schemas']['IdeaTransitionIn']['to'];

export type SnoozeChoice = 'tomorrow' | 'three_days' | 'next_week';

export interface IdeaActions {
  readonly can: CanTransition;
  readonly onTransition: (idea: Idea, to: IdeaTarget) => void;
  readonly onSnooze: (idea: Idea, choice: SnoozeChoice) => void;
  readonly onTake: (idea: Idea) => void;
  readonly onQuickTake: (idea: Idea) => void;
  readonly onStartArticle: (idea: Idea) => void;
  /** A re-cut idea's start: a `repurpose`-lane post (`POST /api/posts {lane: repurpose}`). */
  readonly onStartRecut: (idea: Idea) => void;
  readonly onEdit: (idea: Idea) => void;
}

/**
 * The piece a repurpose idea (`repurposeOf`, from a post that went out) starts: an article for a
 * flagship candidate (`suggestedLane: flagship`), a `repurpose`-lane post for a re-cut
 * (`suggestedLane: repurpose`). Other ideas start with the owner's take.
 */
export function repurposeStart(idea: Pick<Idea, 'repurposeOf' | 'suggestedLane'>): 'article' | 'recut' | undefined {
  if (!idea.repurposeOf) return undefined;
  if (idea.suggestedLane === 'flagship') return 'article';
  if (idea.suggestedLane === 'repurpose') return 'recut';
  return undefined;
}

interface IdeaCardProps {
  idea: Idea;
  pillars: readonly PillarView[];
  formats: readonly FormatOption[];
  zone: string;
  nowMs: number;
  actions: IdeaActions;
  /** True while a change to this idea is saving. */
  busy: boolean;
}

/** The suggested format in words: a post format, or an article format pack. */
function formatName(id: string | null | undefined, formats: readonly FormatOption[]): string | undefined {
  if (!id) return undefined;
  if (id in FORMAT_LABEL) return FORMAT_LABEL[id as keyof typeof FORMAT_LABEL];
  return formats.find((format) => format.id === id)?.name ?? id;
}

/** "From your feeds: The Register, 25 Nov". */
function sourceLine(idea: Idea, zone: string): string {
  const origin = IDEA_ORIGIN[idea.origin];
  const published = parseTimestamp(idea.publishedAt);
  const where = [idea.publisher, published !== undefined ? shortDate(localDateOf(published, zone)) : undefined]
    .filter(Boolean)
    .join(', ');
  return where ? `${origin}: ${where}` : origin;
}

/**
 * One idea in the inbox, with its triage (pillar, relevance, novelty, lane, format, angle) and
 * the owner's choices: "What's your take?", keep, dismiss, snooze, or turn it into a piece. A
 * repurpose idea leads with its own start instead (`repurposeStart`): the article, or the re-cut.
 * A feed idea never becomes content without one of these taps.
 */
export function IdeaCard({ idea, pillars, formats, zone, nowMs, actions, busy }: IdeaCardProps) {
  const titleId = useId();
  const { can } = actions;
  const status = idea.status;
  const expires = parseTimestamp(idea.expiresAt);
  const snoozedUntil = parseTimestamp(idea.snoozedUntil);
  const lane = idea.suggestedLane ?? undefined;
  const format = formatName(idea.suggestedFormat, formats);
  const reasons = idea.triage?.reasons?.trim();
  const usable = status === 'new' || status === 'accepted';
  const start = repurposeStart(idea);
  const piece = idea.articleId ? `/studio/${idea.articleId}` : idea.postId ? `/studio/${idea.postId}` : undefined;
  // Feed links are https by contract; anything else (a `javascript:` URL from a hostile feed) is not a link.
  const source = idea.url && /^https:\/\//i.test(idea.url) ? idea.url : undefined;

  return (
    <article aria-labelledby={titleId} data-idea-id={idea.id} className="flex gap-3 border-b border-line py-5">
      <span aria-hidden="true" className={cn('mt-1.5 w-1 shrink-0 self-stretch rounded-full', pillarThreadClass(idea.pillar))} />
      <div className="min-w-0 flex-1 space-y-3">
        <div className="space-y-1">
          <p className="text-xs text-ink-muted">{sourceLine(idea, zone)}</p>
          <h3 id={titleId} tabIndex={-1} className="font-serif text-xl font-medium text-ink outline-none">
            {idea.title}
          </h3>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
          <PillarTag pillarId={idea.pillar} pillars={pillars} className="text-ink-muted" />
          {idea.timeSensitive && expires !== undefined ? (
            <Badge variant={expires - nowMs < 24 * 3_600_000 ? 'danger' : 'warn'}>
              <Clock aria-hidden="true" />
              {expires > nowMs ? `Time-sensitive: expires ${relativeFuture(expires, nowMs)}` : 'Time-sensitive: expired'}
            </Badge>
          ) : idea.timeSensitive ? (
            <Badge variant="warn">
              <Clock aria-hidden="true" />
              Time-sensitive
            </Badge>
          ) : null}
          {lane ? <Badge variant="outline">{LANE_LABEL[lane]}</Badge> : null}
          {format ? <Badge variant="neutral">{format}</Badge> : null}
          {status !== 'new' ? (
            <Badge variant={status === 'accepted' ? 'success' : 'neutral'}>
              {status === 'snoozed' && snoozedUntil !== undefined
                ? `Snoozed until ${momentLabel(snoozedUntil, zone)}`
                : IDEA_STATUS[status]}
            </Badge>
          ) : null}
        </div>

        {typeof idea.relevance === 'number' || typeof idea.novelty === 'number' ? (
          <div className="max-w-sm space-y-1.5">
            {typeof idea.relevance === 'number' ? <Meter label="Relevance" value={idea.relevance} /> : null}
            {typeof idea.novelty === 'number' ? <Meter label="Novelty" value={idea.novelty} /> : null}
          </div>
        ) : null}

        {idea.angle ? (
          <p className="max-w-measure text-ink">
            <span className="font-medium">Angle: </span>
            {idea.angle}
          </p>
        ) : null}
        {idea.note ? <p className="max-w-measure whitespace-pre-wrap text-ink-muted">{idea.note}</p> : null}

        {reasons || source ? (
          <div className="flex flex-wrap items-start gap-x-5 gap-y-2 text-sm">
            {reasons ? (
              <Collapsible className="min-w-0 max-w-measure">
                <CollapsibleTrigger className="group inline-flex items-center gap-1 font-medium text-ink">
                  <ChevronDown aria-hidden="true" className="size-4 transition-transform duration-150 group-data-[state=open]:rotate-180" />
                  Why it’s here
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <p className="mt-1 text-ink-muted">{reasons}</p>
                </CollapsibleContent>
              </Collapsible>
            ) : null}
            {source ? (
              <a
                href={source}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
              >
                Read the source
                <ExternalLink aria-hidden="true" className="size-3.5" />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            ) : null}
          </div>
        ) : null}

        {status === 'used' && piece ? (
          <Link to={piece} className="inline-flex items-center gap-1 text-sm text-ink underline decoration-line-strong underline-offset-4">
            Open the piece it became
            <ExternalLink aria-hidden="true" className="size-3.5" />
          </Link>
        ) : null}

        <div className="flex flex-wrap gap-2 pt-1">
          {usable && start === 'recut' ? (
            <Button
              className="w-full sm:w-auto"
              disabled={busy}
              onClick={() => {
                actions.onStartRecut(idea);
              }}
            >
              <Repeat2 aria-hidden="true" />
              Start the re-cut…
            </Button>
          ) : usable && start === 'article' ? (
            <Button
              className="w-full sm:w-auto"
              disabled={busy}
              onClick={() => {
                actions.onStartArticle(idea);
              }}
            >
              <FileText aria-hidden="true" />
              Start the article…
            </Button>
          ) : usable ? (
            <Button
              className="w-full sm:w-auto"
              disabled={busy}
              onClick={() => {
                actions.onTake(idea);
              }}
            >
              <MessageSquareQuote aria-hidden="true" />
              What’s your take?
            </Button>
          ) : null}
          {can('idea', status, 'accepted') ? (
            <Button
              variant="secondary"
              className="flex-1 sm:flex-none"
              disabled={busy}
              onClick={() => {
                actions.onTransition(idea, 'accepted');
              }}
            >
              <Check aria-hidden="true" />
              Keep
            </Button>
          ) : null}
          {can('idea', status, 'dismissed') ? (
            <Button
              variant="secondary"
              className="flex-1 sm:flex-none"
              disabled={busy}
              onClick={() => {
                actions.onTransition(idea, 'dismissed');
              }}
            >
              <Archive aria-hidden="true" />
              Dismiss
            </Button>
          ) : null}
          {can('idea', status, 'new') && status !== 'new' ? (
            <Button
              variant="secondary"
              className="flex-1 sm:flex-none"
              disabled={busy}
              onClick={() => {
                actions.onTransition(idea, 'new');
              }}
            >
              <RotateCcw aria-hidden="true" />
              Back to new
            </Button>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" disabled={busy} aria-label={`More for “${idea.title}”`}>
                <MoreHorizontal aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              {usable ? (
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Turn it into a piece</DropdownMenuLabel>
                  <DropdownMenuItem
                    onSelect={() => {
                      actions.onQuickTake(idea);
                    }}
                  >
                    <Zap aria-hidden="true" />
                    Start a quick take…
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => {
                      actions.onStartArticle(idea);
                    }}
                  >
                    <FileText aria-hidden="true" />
                    Start a flagship article…
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                </DropdownMenuGroup>
              ) : null}
              {can('idea', status, 'snoozed') ? (
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Snooze</DropdownMenuLabel>
                  {(
                    [
                      ['tomorrow', 'Until tomorrow morning'],
                      ['three_days', 'For 3 days'],
                      ['next_week', 'Until next Monday'],
                    ] as const
                  ).map(([choice, label]) => (
                    <DropdownMenuItem
                      key={choice}
                      onSelect={() => {
                        actions.onSnooze(idea, choice);
                      }}
                    >
                      <AlarmClock aria-hidden="true" />
                      {label}
                    </DropdownMenuItem>
                  ))}
                  <DropdownMenuSeparator />
                </DropdownMenuGroup>
              ) : null}
              <DropdownMenuItem
                disabled={status === 'used'}
                onSelect={() => {
                  actions.onEdit(idea);
                }}
              >
                <Pencil aria-hidden="true" />
                Edit the triage…
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </article>
  );
}
