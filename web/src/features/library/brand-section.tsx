import { Info } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Field, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';
import { PillarThread } from '@/components/ui/pillar-badge';

import { BrandPreview } from './brand/brand-preview';
import { ColorField, ContrastList } from './brand/colors';
import { MediaField } from './brand/media-field';
import {
  BRAND_LIMITS,
  brandFindings,
  brandToBody,
  brandToDraft,
  describeBrand,
  validateBrand,
  type BrandDraft,
} from './brand/model';
import { ServerPreview } from './brand/server-preview';
import { SaveBar } from './components/save-bar';
import { LibrarySection, LoadError, LoadingBlock, Subsection } from './components/section-intro';
import { UnsavedChangesGuard } from './components/unsaved-changes-guard';
import { usePillarDirectory } from './hooks/use-pillars';
import { useSettingsDraft } from './hooks/use-settings-draft';
import { BRAND_COLOR_TOKENS, normalizeHex, type BrandColorToken } from './lib/contrast';
import { hasErrors, mergeErrors, NO_ERRORS, serverFieldErrors } from './lib/errors';
import { plural } from './lib/format';

const HANDLED = ['etag_mismatch', 'validation_failed', 'confidential_text', 'brand_contrast', 'brand_invalid'];

const linkClass = 'text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink';

/**
 * Library > Brand kit (`/library/brand`): your name and headline, the slide colours with their
 * contrast checks, the typeface, headshot and name mark, the footer, art briefs, and previews.
 * Calls `GET/PUT /api/kb/brand`, `POST /api/media`, `GET /api/kb/policy` (the footer, edited in
 * Settings) and the carousel preview route for the server's render (contract section 10).
 */
export function BrandSection() {
  const query = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.brand), path: '/api/kb/brand' });
  const policy = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.policy), path: '/api/kb/policy' });
  const { pillars } = usePillarDirectory();
  const brand = useSettingsDraft(query.data, brandToDraft);
  const conflict = useConflict();
  const formRef = useRef<HTMLDivElement>(null);
  const contrastRef = useRef<HTMLHeadingElement>(null);
  const [showErrors, setShowErrors] = useState(false);
  const save = useApiMutation('put', '/api/kb/brand', {
    updates: () => queryKeys.kb.detail(KB_SINGLETONS.brand),
    invalidates: [queryKeys.today],
    successToast: 'Brand kit saved.',
    errorToast: 'Couldn’t save the brand kit.',
    handles: HANDLED,
  });

  const draft = brand.draft;
  const clientErrors = useMemo(() => (draft ? validateBrand(draft) : NO_ERRORS), [draft]);
  const findings = brandFindings(save.error);
  const errors = mergeErrors(showErrors ? clientErrors : NO_ERRORS, serverFieldErrors(save.error), findings);
  const total = Object.keys(errors).filter((path) => !path.includes(':')).length;
  const genericError = save.error && !isProblem(save.error, HANDLED) ? save.error : undefined;
  const disclaimer = policy.data?.item.disclaimerShort;

  const previewColors = useMemo(() => {
    const result: Partial<Record<BrandColorToken, string>> = {};
    for (const token of BRAND_COLOR_TOKENS) {
      const value = normalizeHex(draft?.colors[token] ?? '') ?? normalizeHex(brand.baseline?.colors[token] ?? '');
      if (value) result[token] = value;
    }
    return result;
  }, [draft, brand.baseline]);

  const set = <K extends keyof BrandDraft>(field: K, value: BrandDraft[K]) => {
    brand.update((current) => ({ ...current, [field]: value }));
  };
  const setColor = (token: BrandColorToken, value: string) => {
    brand.update((current) => ({ ...current, colors: { ...current.colors, [token]: value } }));
  };

  const send = (sent: BrandDraft, ifMatch: string | null) => {
    save.mutate(
      { body: brandToBody(sent, pillars.length > 0 ? pillars.map((pillar) => pillar.id) : undefined), ifMatch },
      {
        onSuccess: (data) => {
          brand.saved(sent, data);
          conflict.close();
          setShowErrors(false);
        },
        onError: (error) => {
          if (conflict.show(error)) {
            void query.refetch();
            return;
          }
          requestAnimationFrame(() => {
            focusFirstInvalid(formRef.current);
          });
        },
      },
    );
  };

  const onSave = () => {
    if (!draft) return;
    setShowErrors(true);
    if (hasErrors(clientErrors)) {
      requestAnimationFrame(() => {
        // Contrast failures have no single control to point at: go to the contrast checks.
        if (!focusFirstInvalid(formRef.current)) contrastRef.current?.focus();
      });
      return;
    }
    send(draft, brand.baseEtag ?? null);
  };

  return (
    <LibrarySection
      title="Brand kit"
      standfirst={
        <p>
          Carousels, image cards and link previews are built from your kit, with readable type and AA contrast on every slide. They never carry
          employer branding.
        </p>
      }
      yourPart="Set your name, colours, headshot and name mark once. Every slide template uses them."
    >

      {query.isPending ? (
        <LoadingBlock label="Loading your brand kit" rows={5} />
      ) : query.isError ? (
        <LoadError error={query.error} title="Couldn’t load the brand kit." retryLabel="Try loading the brand kit again" onRetry={() => void query.refetch()} />
      ) : draft ? (
        <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div ref={formRef} className="min-w-0 space-y-10">
            {draft.authorName.trim() === '' ? (
              <Alert variant="warn">
                <AlertTitle>Add your name to render carousels and cards.</AlertTitle>
                <AlertDescription>
                  <p>Until then text posts and the ready-to-post kit still work, but nothing with slides or images can be made.</p>
                </AlertDescription>
              </Alert>
            ) : null}
            {showErrors && total > 0 ? (
              <Alert variant="error">
                <AlertTitle>{`${plural(total, 'field needs', 'fields need')} a fix before saving.`}</AlertTitle>
                <AlertDescription>
                  <p>Each one is marked where it is.</p>
                </AlertDescription>
              </Alert>
            ) : null}
            {genericError ? <ProblemAlert error={genericError} title="Couldn’t save the brand kit." /> : null}

            <Subsection title="You on the slides" className="border-t-0 pt-0">
              <div className="grid gap-5">
                <Field
                  label="Your name"
                  description="On the cover byline, every slide’s footer and link previews. Needed for anything with slides."
                  error={errors.authorName}
                >
                  <Input
                    value={draft.authorName}
                    maxLength={BRAND_LIMITS.authorName}
                    autoComplete="name"
                    onChange={(event) => {
                      set('authorName', event.target.value);
                    }}
                  />
                </Field>
                <Field label="Headline" description="Under your name on the cover, such as “Identity and AI security”." error={errors.authorHeadline}>
                  <Input
                    value={draft.authorHeadline}
                    maxLength={BRAND_LIMITS.authorHeadline}
                    onChange={(event) => {
                      set('authorHeadline', event.target.value);
                    }}
                  />
                </Field>
                <Field
                  label="Site name"
                  description="On link preview images only. Carousels and cards carry no web addresses."
                  error={errors.siteName}
                >
                  <Input
                    value={draft.siteName}
                    maxLength={BRAND_LIMITS.siteName}
                    onChange={(event) => {
                      set('siteName', event.target.value);
                    }}
                  />
                </Field>
              </div>
            </Subsection>

            <Subsection
              title="Colours"
              description="Eight colours the slide templates use. Every pair that carries text must reach a contrast of 4.5:1 (WCAG AA), even though slide text is large."
            >
              <div className="space-y-8">
                <div className="grid gap-5 sm:grid-cols-2">
                  {BRAND_COLOR_TOKENS.map((token) => (
                    <ColorField
                      key={token}
                      token={token}
                      value={draft.colors[token]}
                      fallback={brand.baseline?.colors[token]}
                      error={errors[`colors.${token}`]}
                      onChange={(value) => {
                        setColor(token, value);
                      }}
                    />
                  ))}
                </div>
                <div className="space-y-2">
                  <h4 ref={contrastRef} tabIndex={-1} className="text-sm font-semibold text-ink outline-none">
                    Contrast
                  </h4>
                  <ContrastList colors={draft.colors} serverFindings={findings} />
                  {errors.colors ? <p className="text-sm font-medium text-danger">{errors.colors}</p> : null}
                </div>
              </div>
            </Subsection>

            <Subsection title="Typeface">
              <p className="max-w-measure text-ink-muted">
                Slides are set in Inter: Inter Display for headings, Inter for text. Another typeface has to be added to the renderer, with its
                licence, before the kit can use it.
              </p>
            </Subsection>

            <Subsection title="Headshot and name mark">
              <div className="grid gap-8 sm:grid-cols-2">
                <MediaField
                  legend="Headshot"
                  description="A square photo works best. It is cropped to a circle on the cover and on link previews."
                  role="headshot"
                  value={draft.headshot}
                  alt="Your headshot"
                  uploadLabel="Upload a photo"
                  round
                  error={errors.headshot}
                  onChange={(value) => {
                    set('headshot', value);
                  }}
                />
                <MediaField
                  legend="Name mark"
                  description="Your signature or monogram as an image, for slide footers. Leave it empty to use your name in text."
                  role="name_mark"
                  value={draft.nameMark}
                  alt="Your name mark"
                  uploadLabel="Upload a name mark"
                  error={errors.nameMark}
                  onChange={(value) => {
                    set('nameMark', value);
                  }}
                />
              </div>
            </Subsection>

            <Subsection title="Footer">
              <div className="flex max-w-measure items-start gap-3 text-ink-muted">
                <Info aria-hidden="true" className="mt-1 size-4 shrink-0 text-ink-subtle" />
                <div className="space-y-1">
                  {policy.isPending ? (
                    <p>Loading the footer…</p>
                  ) : disclaimer ? (
                    <p>
                      Every slide and card ends with <q className="text-ink">{disclaimer}</q>
                      {errors.disclaimerShort ? <span className="block font-medium text-danger">{errors.disclaimerShort}</span> : null}
                    </p>
                  ) : (
                    <p>Slides have no footer yet.</p>
                  )}
                  <p className="text-sm">
                    The footer is your employer’s disclaimer, kept with the policy in{' '}
                    <Link to="/settings/policy" className={linkClass}>
                      Settings, Blocklist & policy
                    </Link>
                    .
                  </p>
                </div>
              </div>
            </Subsection>

            {pillars.length > 0 ? (
              <Subsection
                title="Art briefs"
                description={
                  <>
                    <p>
                      A short brief per pillar steers the AI illustrations: their style, mood and palette. Describe a
                      look, never a client or a product logo. Leave one empty for the default look: editorial and
                      abstract, in your brand colours, with no text.
                    </p>
                    <p>
                      Illustrations are made from an article’s Draft tab once you switch them on in{' '}
                      <Link to="/settings/models" className={linkClass}>
                        Settings, Models
                      </Link>
                      .
                    </p>
                  </>
                }
              >
                <div className="grid gap-5">
                  {pillars.map((pillar) => (
                    <Field
                      key={pillar.id}
                      label={
                        <span className="inline-flex items-center gap-2">
                          <PillarThread id={pillar.id} />
                          {pillar.name}
                        </span>
                      }
                      description={`Up to ${String(BRAND_LIMITS.artBrief)} characters.`}
                      error={errors[`artBriefs.${pillar.id}`]}
                    >
                      <Textarea
                        rows={2}
                        maxLength={BRAND_LIMITS.artBrief}
                        value={draft.artBriefs[pillar.id] ?? ''}
                        onChange={(event) => {
                          const brief = event.target.value;
                          brand.update((current) => ({ ...current, artBriefs: { ...current.artBriefs, [pillar.id]: brief } }));
                        }}
                      />
                    </Field>
                  ))}
                  {errors.artBriefs ? <p className="text-sm font-medium text-danger">{errors.artBriefs}</p> : null}
                </div>
              </Subsection>
            ) : null}
          </div>

          <aside aria-label="Previews" className="min-w-0 space-y-8 lg:sticky lg:top-6 lg:self-start">
            <div className="space-y-3">
              <h3 className="text-lg font-semibold text-ink">Preview</h3>
              <BrandPreview
                colors={previewColors}
                authorName={draft.authorName}
                authorHeadline={draft.authorHeadline}
                headshot={draft.headshot}
                nameMark={draft.nameMark}
                disclaimer={disclaimer}
              />
            </div>
            <div className="space-y-3">
              <h3 className="text-lg font-semibold text-ink">Rendered by the server</h3>
              <ServerPreview dirty={brand.dirty} />
            </div>
          </aside>
        </div>
      ) : null}

      <SaveBar
        dirty={brand.dirty}
        saving={save.isPending}
        saveLabel="Save brand kit"
        onSave={onSave}
        onDiscard={() => {
          brand.discard();
          save.reset();
          setShowErrors(false);
        }}
        blockedReason={showErrors && total > 0 ? `${plural(total, 'field needs', 'fields need')} a fix.` : undefined}
      />

      <ConflictDialog
        {...conflict}
        subject="The brand kit"
        mine={draft ? describeBrand(draft) : undefined}
        theirs={query.data && query.data.etag !== brand.baseEtag ? describeBrand(brandToDraft(query.data.item)) : undefined}
        saving={save.isPending}
        onKeepMine={() => {
          if (draft) send(draft, conflict.conflict?.etag ?? query.data?.etag ?? null);
        }}
        onTakeTheirs={() => {
          void query.refetch().then(() => {
            brand.takeServer();
          });
          save.reset();
          conflict.close();
        }}
      />

      <UnsavedChangesGuard when={brand.dirty} subject="your brand kit" />
    </LibrarySection>
  );
}
