import { Lock, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

import { ConfirmAction } from '../components/confirm-action';
import { PairsEditor } from '../components/pairs-editor';
import { StringListEditor } from '../components/string-list-editor';
import { errorsUnder, itemErrors, type ErrorMap } from '../lib/errors';
import { formatInteger } from '../lib/format';
import { hashtagError, hostError, MAX_HASHTAGS, normalizeHashtag, normalizeHost, SLUG_HINT, slugify } from '../lib/rules';
import { LIMITS, type PillarDraft } from './model';
import { PillarFeeds } from './pillar-feeds';

export interface PillarFormProps {
  pillar: PillarDraft;
  /** Errors under `pillars.<index>` (paths relative to the pillar: `name`, `hashtags.1`). */
  errors: ErrorMap;
  onChange: (change: (pillar: PillarDraft) => PillarDraft) => void;
  onRemove: () => void;
  canRemove: boolean;
}

function limitHint(max: number): string {
  return `Up to ${formatInteger(max)} characters.`;
}

/** Every setting of one pillar. Changes go to the section's draft; the save bar saves them all. */
export function PillarForm({ pillar, errors, onChange, onRemove, canRemove }: PillarFormProps) {
  const set = <K extends keyof PillarDraft>(field: K, value: PillarDraft[K]) => {
    onChange((current) => ({ ...current, [field]: value }));
  };
  const displayName = pillar.name.trim() || 'this pillar';
  const renameErrors = itemErrors(errors, 'renames');

  return (
    <div className="space-y-8">
      <div className="grid gap-5 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <Field label="Name" error={errors.name} required description="How the pillar reads in the app and to the planner.">
          <Input
            value={pillar.name}
            maxLength={LIMITS.name}
            onChange={(event) => {
              const name = event.target.value;
              onChange((current) => {
                // A new pillar's id follows its name until the owner types an id of their own.
                const follows = !current.saved && (current.id === '' || current.id === slugify(current.name, LIMITS.id));
                return { ...current, name, id: follows ? slugify(name, LIMITS.id) : current.id };
              });
            }}
          />
        </Field>
        <Field label="Target share" error={errors.targetShare} description="Of each week’s posts.">
          {(control) => (
            <div className="flex items-center gap-2">
              <Input
                {...control}
                inputMode="decimal"
                value={pillar.share}
                className="w-24 tabular-nums"
                onChange={(event) => {
                  set('share', event.target.value);
                }}
              />
              <span aria-hidden="true" className="text-ink-muted">
                %
              </span>
            </div>
          )}
        </Field>
      </div>

      {pillar.saved ? (
        <p className="flex items-start gap-2 text-sm text-ink-muted">
          <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
          <span>
            Id <code className="font-mono text-ink">{pillar.id}</code>. It stays fixed: ideas, articles, posts and stories point at it.
          </span>
        </p>
      ) : (
        <Field label="Id" error={errors.id} required description={`${SLUG_HINT} It cannot change once saved.`}>
          <Input
            value={pillar.id}
            maxLength={LIMITS.id}
            className="font-mono"
            autoCapitalize="none"
            spellCheck={false}
            onChange={(event) => {
              set('id', event.target.value.toLowerCase());
            }}
          />
        </Field>
      )}

      <Field label="Topics" error={errors.topics} description={`A one-line summary, shown in lists and used to sort ideas. ${limitHint(LIMITS.topics)}`}>
        <Input
          value={pillar.topics}
          maxLength={LIMITS.topics}
          onChange={(event) => {
            set('topics', event.target.value);
          }}
        />
      </Field>

      <Field label="Description" error={errors.description} description={`What the pillar covers and what it leaves out. ${limitHint(LIMITS.description)}`}>
        <Textarea
          value={pillar.description}
          maxLength={LIMITS.description}
          rows={4}
          onChange={(event) => {
            set('description', event.target.value);
          }}
        />
      </Field>

      <Field label="Audience" error={errors.audience} description={`Who reads it, by role. Drafts and hooks are written for them. ${limitHint(LIMITS.audience)}`}>
        <Textarea
          value={pillar.audience}
          maxLength={LIMITS.audience}
          rows={3}
          onChange={(event) => {
            set('audience', event.target.value);
          }}
        />
      </Field>

      <StringListEditor
        legend="Vocabulary"
        description="The canonical terms drafts must use, spelled exactly as here. Paste a list separated by commas or new lines."
        items={pillar.vocabulary}
        onChange={(vocabulary) => {
          set('vocabulary', vocabulary);
        }}
        addLabel={`Add a term to ${displayName}`}
        noun="term"
        placeholder="Model Context Protocol (MCP)"
        max={LIMITS.vocabulary}
        maxLength={LIMITS.term}
        splitPaste
        itemErrors={itemErrors(errors, 'vocabulary')}
        error={errors.vocabulary}
      />

      <PairsEditor
        legend="Renamed products and terms"
        description="Old names drafts must replace with the current one, such as Azure AD with Microsoft Entra ID."
        rows={pillar.renames}
        onChange={(renames) => {
          set('renames', renames);
        }}
        fromLabel="Old name"
        toLabel="Use instead"
        addLabel="Add a rename"
        noun="rename"
        rowErrors={renameErrors}
        error={errors.renames}
        max={LIMITS.renames}
        fromMaxLength={LIMITS.renameName}
        toMaxLength={LIMITS.renameName}
      />

      <StringListEditor
        legend="Hashtags"
        description={`Up to ${String(MAX_HASHTAGS)}. Posts on this pillar may use only these.`}
        items={pillar.hashtags}
        onChange={(hashtags) => {
          set('hashtags', hashtags);
        }}
        addLabel={`Add a hashtag to ${displayName}`}
        noun="hashtag"
        placeholder="#ZeroTrust"
        max={MAX_HASHTAGS}
        maxLength={51}
        normalize={normalizeHashtag}
        validate={hashtagError}
        display={(tag) => `#${tag}`}
        itemErrors={itemErrors(errors, 'hashtags')}
        error={errors.hashtags}
      />

      <StringListEditor
        legend="Trusted research sites"
        description="Research searches these sites first, then the open web. Paste addresses or site names; only the site is kept."
        items={pillar.researchDomains}
        onChange={(researchDomains) => {
          set('researchDomains', researchDomains);
        }}
        addLabel={`Add a research site to ${displayName}`}
        noun="site"
        placeholder="learn.microsoft.com"
        max={LIMITS.domains}
        normalize={normalizeHost}
        validate={hostError}
        splitPaste
        itemErrors={itemErrors(errors, 'researchDomains')}
        error={errors.researchDomains}
      />

      <PillarFeeds pillarId={pillar.id} pillarName={pillar.name} saved={pillar.saved} />

      <div className="border-t border-line pt-5">
        {canRemove ? (
          <ConfirmAction
            title={`Remove ${displayName}?`}
            description={
              pillar.saved
                ? 'The pillar goes when you save. If any idea, article, post, story, position, feed or series still uses it, the save is refused and nothing changes.'
                : 'This new pillar has not been saved yet.'
            }
            confirmLabel="Remove pillar"
            cancelLabel="Keep it"
            onConfirm={onRemove}
          >
            <Button variant="ghost" className="text-danger hover:text-danger">
              <Trash2 aria-hidden="true" />
              {`Remove ${displayName}`}
            </Button>
          </ConfirmAction>
        ) : (
          <p className="text-sm text-ink-muted">You need at least one pillar.</p>
        )}
      </div>
    </div>
  );
}

/** The errors of pillar `index` from the section's map. */
export function pillarErrors(errors: ErrorMap, index: number): ErrorMap {
  return errorsUnder(errors, `pillars.${String(index)}`);
}
