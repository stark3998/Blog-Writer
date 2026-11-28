import { Settings2 } from 'lucide-react';
import { useState, type SubmitEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { toast } from 'sonner';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { confidentialFindings, fieldErrors, isProblem } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';
import { useAppData } from '@/lib/app-data';
import { apiQueryOptions, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { DialogActions, ResponsiveDialog } from '../components/responsive-dialog';
import { FORMAT_LABEL, LANE_LABEL } from '../lib/labels';
import type { FormatOption, LanesView, PillarView } from '../lib/settings';

type Idea = components['schemas']['Idea'];
type IdeaLane = components['schemas']['IdeaLane'];

const NONE = 'none';
const IDEA_LANES: readonly IdeaLane[] = ['flagship', 'quick_take', 'native', 'derivative', 'repurpose'];
const POST_FORMATS = ['text', 'carousel', 'image_card', 'poll'] as const;

/** A blocklist hit or a validation message for one field of an idea form. */
function serverFieldError(error: unknown, field: string): string | undefined {
  if (confidentialFindings(error).some((finding) => finding.field === field)) {
    return 'This matches your blocklist. Rephrase it.';
  }
  return fieldErrors(error)[field];
}

function BlocklistNotice({ error }: { error: unknown }) {
  if (!isProblem(error, 'blocklist_not_configured')) return null;
  return (
    <Alert variant="warn">
      <AlertTitle>Save your blocklist first</AlertTitle>
      <AlertDescription>
        <p>Ideas are checked against your blocklist before they are saved. An empty list is fine.</p>
      </AlertDescription>
      <AlertActions>
        <Button asChild size="sm" variant="secondary">
          <Link to="/settings/policy">
            <Settings2 aria-hidden="true" />
            Open Blocklist & policy
          </Link>
        </Button>
      </AlertActions>
    </Alert>
  );
}

function PillarSelect({
  value,
  onChange,
  pillars,
  control,
}: {
  value: string;
  onChange: (value: string) => void;
  pillars: readonly PillarView[];
  control: object;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger {...control}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>No pillar yet</SelectItem>
        {pillars.map((pillar) => (
          <SelectItem key={pillar.id} value={pillar.id}>
            {pillar.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ------------------------------------------------------------------------------------------------
// Add an idea (quick capture)
// ------------------------------------------------------------------------------------------------

interface CaptureIdeaDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pillars: readonly PillarView[];
  lanes: LanesView;
}

/** Quick capture (`POST /api/ideas`, origin `quick_capture`). The server checks the blocklist. */
export function CaptureIdeaDialog({ open, onOpenChange, pillars, lanes }: CaptureIdeaDialogProps) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add an idea"
      description="It joins the inbox as new. Nothing is written until you pick it."
    >
      {open ? (
        <CaptureForm
          pillars={pillars}
          lanes={lanes}
          onDone={() => {
            onOpenChange(false);
          }}
        />
      ) : null}
    </ResponsiveDialog>
  );
}

function CaptureForm({ pillars, lanes, onDone }: { pillars: readonly PillarView[]; lanes: LanesView; onDone: () => void }) {
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [url, setUrl] = useState('');
  const [pillar, setPillar] = useState(NONE);
  const [lane, setLane] = useState<string>(NONE);
  const [timeSensitive, setTimeSensitive] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const create = useApiMutation('post', '/api/ideas', {
    invalidates: [queryKeys.ideas.lists()],
    successToast: 'Idea added.',
    errorToast: 'Couldn’t add the idea.',
    handles: ['confidential_text', 'validation_failed', 'blocklist_not_configured'],
  });
  const trimmedUrl = url.trim();
  const local = {
    title: title.trim() === '' ? 'Give the idea a title.' : undefined,
    url: trimmedUrl !== '' && !/^https:\/\/\S+$/i.test(trimmedUrl) ? 'Use an https:// link, or leave it empty.' : undefined,
  };
  const errors = {
    title: (submitted ? local.title : undefined) ?? serverFieldError(create.error, 'title'),
    note: serverFieldError(create.error, 'note'),
    url: (submitted ? local.url : undefined) ?? serverFieldError(create.error, 'url'),
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitted(true);
    const form = event.currentTarget;
    if (local.title || local.url) {
      setTimeout(() => focusFirstInvalid(form), 0);
      return;
    }
    create.mutate(
      {
        body: {
          title: title.trim(),
          note: note.trim() === '' ? null : note.trim(),
          url: trimmedUrl === '' ? null : trimmedUrl,
          pillar: pillar === NONE ? null : pillar,
          suggestedLane: lane === NONE ? null : (lane as IdeaLane),
          timeSensitive,
        },
      },
      {
        onSuccess: onDone,
        onError: () => {
          setTimeout(() => focusFirstInvalid(form), 0);
        },
      },
    );
  };

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <Field label="Title" error={errors.title} required>
        <Input value={title} maxLength={300} onChange={(event) => { setTitle(event.target.value); }} />
      </Field>
      <Field label="Note" description="Why it’s worth a post, in a line or two." error={errors.note}>
        <Textarea value={note} maxLength={2000} rows={3} onChange={(event) => { setNote(event.target.value); }} />
      </Field>
      <Field label="Link" description="The article or announcement, if there is one." error={errors.url}>
        <Input type="url" inputMode="url" value={url} placeholder="https://" onChange={(event) => { setUrl(event.target.value); }} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pillar">
          {(control) => <PillarSelect value={pillar} onChange={setPillar} pillars={pillars} control={control} />}
        </Field>
        <Field label="Best as">
          {(control) => (
            <Select value={lane} onValueChange={setLane}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>No preference</SelectItem>
                {(['flagship', 'quick_take', 'native'] as const).map((value) => (
                  <SelectItem key={value} value={value}>
                    {LANE_LABEL[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <Field
        label="Time-sensitive news"
        description={`It expires in ${String(lanes.quickTake.maxAgeHours)} hours unless you use it, so it fits a quick take.`}
        orientation="horizontal"
      >
        <Checkbox checked={timeSensitive} onCheckedChange={(value) => { setTimeSensitive(value === true); }} />
      </Field>
      <BlocklistNotice error={create.error} />
      <DialogActions>
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? 'Adding…' : 'Add idea'}
        </Button>
      </DialogActions>
    </form>
  );
}

// ------------------------------------------------------------------------------------------------
// Edit the triage
// ------------------------------------------------------------------------------------------------

interface IdeaFields {
  readonly title: string;
  readonly note: string | null;
  readonly pillar: string | null;
  readonly suggestedLane: IdeaLane | null;
  readonly suggestedFormat: string | null;
  readonly angle: string | null;
}

function fieldsOf(idea: Idea): IdeaFields {
  return {
    title: idea.title,
    note: idea.note ?? null,
    pillar: idea.pillar ?? null,
    suggestedLane: idea.suggestedLane ?? null,
    suggestedFormat: idea.suggestedFormat ?? null,
    angle: idea.angle ?? null,
  };
}

function describeFields(fields: IdeaFields | undefined, pillars: readonly PillarView[]): string {
  if (!fields) return '';
  const pillar = fields.pillar ? (pillars.find((option) => option.id === fields.pillar)?.name ?? fields.pillar) : 'none';
  return [
    `Title: ${fields.title}`,
    `Pillar: ${pillar}`,
    `Best as: ${fields.suggestedLane ? LANE_LABEL[fields.suggestedLane] : 'no preference'}`,
    `Format: ${fields.suggestedFormat ?? 'none'}`,
    `Angle: ${fields.angle ?? 'none'}`,
    `Note: ${fields.note ?? 'none'}`,
  ].join('\n\n');
}

interface EditIdeaDialogProps {
  idea: Idea | undefined;
  etag: string | null;
  onClose: () => void;
  pillars: readonly PillarView[];
  formats: readonly FormatOption[];
}

/** The owner's triage of an idea (`PUT /api/ideas/{id}`, If-Match): only changed fields are sent. */
export function EditIdeaDialog({ idea, etag, onClose, pillars, formats }: EditIdeaDialogProps) {
  return (
    <ResponsiveDialog
      open={idea !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Edit the triage"
      description={idea ? idea.title : undefined}
    >
      {idea ? <EditForm key={idea.id} idea={idea} etag={etag} onDone={onClose} pillars={pillars} formats={formats} /> : null}
    </ResponsiveDialog>
  );
}

function EditForm({
  idea,
  etag,
  onDone,
  pillars,
  formats,
}: {
  idea: Idea;
  etag: string | null;
  onDone: () => void;
  pillars: readonly PillarView[];
  formats: readonly FormatOption[];
}) {
  const { api, queryClient } = useAppData();
  const original = fieldsOf(idea);
  const [title, setTitle] = useState(original.title);
  const [note, setNote] = useState(original.note ?? '');
  const [pillar, setPillar] = useState(original.pillar ?? NONE);
  const [lane, setLane] = useState<string>(original.suggestedLane ?? NONE);
  const [format, setFormat] = useState(original.suggestedFormat ?? NONE);
  const [angle, setAngle] = useState(original.angle ?? '');
  const [theirs, setTheirs] = useState<{ fields: IdeaFields; etag: string | null } | undefined>();
  const conflict = useConflict();
  const save = useApiMutation('put', '/api/ideas/{idea_id}', {
    updates: () => queryKeys.ideas.detail(idea.id),
    invalidates: [queryKeys.ideas.lists()],
    successToast: 'Triage saved.',
    errorToast: 'Couldn’t save the triage.',
    handles: ['etag_mismatch', 'confidential_text', 'validation_failed', 'blocklist_not_configured'],
  });

  const mine: IdeaFields = {
    title: title.trim(),
    note: note.trim() === '' ? null : note.trim(),
    pillar: pillar === NONE ? null : pillar,
    suggestedLane: lane === NONE ? null : (lane as IdeaLane),
    suggestedFormat: format === NONE ? null : format,
    angle: angle.trim() === '' ? null : angle.trim(),
  };
  const titleError = title.trim() === '' ? 'The idea needs a title.' : serverFieldError(save.error, 'title');

  const changes = (base: IdeaFields) => {
    const body: Record<string, string | null> = {};
    for (const key of Object.keys(mine) as (keyof IdeaFields)[]) {
      if (mine[key] !== base[key]) body[key] = mine[key];
    }
    return body as components['schemas']['IdeaEditIn'];
  };

  const send = (ifMatch: string, base: IdeaFields) => {
    const body = changes(base);
    if (Object.keys(body).length === 0) {
      onDone();
      return;
    }
    save.mutate(
      { params: { path: { idea_id: idea.id } }, body, ifMatch },
      {
        onSuccess: () => {
          conflict.close();
          onDone();
        },
        onError: (error) => {
          if (!conflict.show(error)) return;
          // Load the idea as it is now, for the comparison.
          void queryClient
            .query({
              ...apiQueryOptions(api, {
                queryKey: queryKeys.ideas.detail(idea.id),
                path: '/api/ideas/{idea_id}',
                init: { params: { path: { idea_id: idea.id } } },
              }),
              staleTime: 0,
            })
            .then((current) => {
              setTheirs({ fields: fieldsOf(current.item), etag: current.etag });
            })
            .catch(() => undefined);
        },
      },
    );
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (titleError || etag === null) return;
    send(etag, original);
  };

  const formatChoices = [
    ...POST_FORMATS.map((id) => ({ id, name: FORMAT_LABEL[id], group: 'post' as const })),
    ...formats.map((option) => ({ id: option.id, name: option.name, group: 'article' as const })),
  ];

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <Field label="Title" error={titleError} required>
        <Input value={title} maxLength={300} onChange={(event) => { setTitle(event.target.value); }} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pillar" error={serverFieldError(save.error, 'pillar')}>
          {(control) => <PillarSelect value={pillar} onChange={setPillar} pillars={pillars} control={control} />}
        </Field>
        <Field label="Best as">
          {(control) => (
            <Select value={lane} onValueChange={setLane}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>No preference</SelectItem>
                {IDEA_LANES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {LANE_LABEL[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <Field label="Format" error={serverFieldError(save.error, 'suggestedFormat')}>
        {(control) => (
          <Select value={format} onValueChange={setFormat}>
            <SelectTrigger {...control}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>No format yet</SelectItem>
              <SelectGroup>
                <SelectLabel>Posts</SelectLabel>
                {formatChoices
                  .filter((choice) => choice.group === 'post')
                  .map((choice) => (
                    <SelectItem key={choice.id} value={choice.id}>
                      {choice.name}
                    </SelectItem>
                  ))}
              </SelectGroup>
              <SelectGroup>
                <SelectLabel>Articles</SelectLabel>
                {formatChoices
                  .filter((choice) => choice.group === 'article')
                  .map((choice) => (
                    <SelectItem key={choice.id} value={choice.id}>
                      {choice.name}
                    </SelectItem>
                  ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        )}
      </Field>
      <Field label="Angle" description="The point of view a post would take, in one line." error={serverFieldError(save.error, 'angle')}>
        <Input value={angle} maxLength={500} onChange={(event) => { setAngle(event.target.value); }} />
      </Field>
      <Field label="Note" error={serverFieldError(save.error, 'note')}>
        <Textarea value={note} maxLength={2000} rows={3} onChange={(event) => { setNote(event.target.value); }} />
      </Field>
      <BlocklistNotice error={save.error} />
      <DialogActions>
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending || etag === null}>
          {save.isPending ? 'Saving…' : 'Save triage'}
        </Button>
      </DialogActions>
      <ConflictDialog
        {...conflict}
        subject="This idea"
        mine={describeFields(mine, pillars)}
        theirs={theirs ? describeFields(theirs.fields, pillars) : undefined}
        saving={save.isPending}
        onKeepMine={() => {
          const current = conflict.conflict?.etag ?? theirs?.etag ?? undefined;
          if (current) send(current, theirs?.fields ?? original);
        }}
        onTakeTheirs={() => {
          conflict.close();
          void queryClient.invalidateQueries({ queryKey: queryKeys.ideas.lists() });
          onDone();
        }}
      />
    </form>
  );
}

// ------------------------------------------------------------------------------------------------
// Start a flagship article from an idea
// ------------------------------------------------------------------------------------------------

interface StartArticleDialogProps {
  idea: Idea | undefined;
  onClose: () => void;
  pillars: readonly PillarView[];
  formats: readonly FormatOption[];
}

/** `POST /api/articles {ideaId, meta}`: a planned article; the idea becomes used. Nothing is written yet. */
export function StartArticleDialog({ idea, onClose, pillars, formats }: StartArticleDialogProps) {
  return (
    <ResponsiveDialog
      open={idea !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Start a flagship article"
      description="It starts as a planned article in Studio. Research and drafting begin when you start its pipeline there."
    >
      {idea ? <StartArticleForm key={idea.id} idea={idea} onDone={onClose} pillars={pillars} formats={formats} /> : null}
    </ResponsiveDialog>
  );
}

function StartArticleForm({
  idea,
  onDone,
  pillars,
  formats,
}: {
  idea: Idea;
  onDone: () => void;
  pillars: readonly PillarView[];
  formats: readonly FormatOption[];
}) {
  const navigate = useNavigate();
  const suggested = formats.find((format) => format.id === idea.suggestedFormat)?.id;
  const [title, setTitle] = useState(idea.title.slice(0, 150));
  const [pillar, setPillar] = useState(idea.pillar ?? pillars[0]?.id ?? '');
  const [format, setFormat] = useState(suggested ?? formats[0]?.id ?? '');
  const create = useApiMutation('post', '/api/articles', {
    invalidates: [queryKeys.ideas.lists(), queryKeys.articles.lists(), queryKeys.today],
    errorToast: 'Couldn’t start the article.',
  });
  const titleError = title.trim() === '' ? 'Give the article a working title.' : undefined;

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (titleError || pillar === '' || format === '') return;
    create.mutate(
      { body: { ideaId: idea.id, meta: { title: title.trim(), pillar, format } } },
      {
        onSuccess: (article) => {
          onDone();
          toast.success('Flagship article started.', {
            description: 'It waits in Studio as planned.',
            action: {
              label: 'Open',
              onClick: () => {
                void navigate(`/studio/${article.item.id}`);
              },
            },
          });
        },
      },
    );
  };

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <Field label="Working title" error={titleError} required>
        <Input value={title} maxLength={150} onChange={(event) => { setTitle(event.target.value); }} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pillar" required>
          {(control) => (
            <Select value={pillar} onValueChange={setPillar}>
              <SelectTrigger {...control}>
                <SelectValue placeholder="Choose a pillar" />
              </SelectTrigger>
              <SelectContent>
                {pillars.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Format" required>
          {(control) => (
            <Select value={format} onValueChange={setFormat}>
              <SelectTrigger {...control}>
                <SelectValue placeholder="Choose a format" />
              </SelectTrigger>
              <SelectContent>
                {formats.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <DialogActions>
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? 'Starting…' : 'Start the article'}
        </Button>
      </DialogActions>
    </form>
  );
}

// ------------------------------------------------------------------------------------------------
// Re-cut a post that went out (a repurpose idea)
// ------------------------------------------------------------------------------------------------

type PostFormat = components['schemas']['PostFormat'];

/** The formats a re-cut can take (polls are written by hand). */
const RECUT_FORMATS: readonly PostFormat[] = ['text', 'carousel', 'image_card'];

interface StartRecutDialogProps {
  idea: Idea | undefined;
  onClose: () => void;
  pillars: readonly PillarView[];
  lanes: LanesView;
}

/**
 * `POST /api/posts {lane: repurpose, ideaId}`: a re-cut idea (`repurposeOf`) becomes a new post
 * with a new angle; the idea becomes used. The writer starts at once, and the owner reviews and
 * approves the post before anything goes out (its near-duplicate check runs against the original).
 */
export function StartRecutDialog({ idea, onClose, pillars, lanes }: StartRecutDialogProps) {
  return (
    <ResponsiveDialog
      open={idea !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Start the re-cut"
      description="A new post from one that went out, with a new angle. The writer starts now; you review and approve it before anything goes out."
    >
      {idea ? <StartRecutForm key={idea.id} idea={idea} onDone={onClose} pillars={pillars} lanes={lanes} /> : null}
    </ResponsiveDialog>
  );
}

function StartRecutForm({
  idea,
  onDone,
  pillars,
  lanes,
}: {
  idea: Idea;
  onDone: () => void;
  pillars: readonly PillarView[];
  lanes: LanesView;
}) {
  const navigate = useNavigate();
  const formats = RECUT_FORMATS.filter((value) => value !== 'image_card' || lanes.imageCardEnabled);
  const [pillar, setPillar] = useState(idea.pillar ?? pillars[0]?.id ?? '');
  const [format, setFormat] = useState<PostFormat>(formats.find((value) => value === idea.suggestedFormat) ?? 'text');
  const create = useApiMutation('post', '/api/posts', {
    invalidates: [queryKeys.ideas.lists(), queryKeys.ideas.detail(idea.id), queryKeys.posts.lists(), queryKeys.today],
    errorToast: 'Couldn’t start the re-cut.',
  });

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pillar === '') return;
    create.mutate(
      { body: { lane: 'repurpose', pillar, format, ideaId: idea.id } },
      {
        onSuccess: (created) => {
          onDone();
          toast.success('Re-cut started.', {
            description: 'The writer is on it. You approve the post before anything goes out.',
            action: {
              label: 'Open',
              onClick: () => {
                void navigate(`/studio/${created.post.item.id}/posts`);
              },
            },
          });
        },
      },
    );
  };

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pillar" required>
          {(control) => (
            <Select value={pillar} onValueChange={setPillar}>
              <SelectTrigger {...control}>
                <SelectValue placeholder="Choose a pillar" />
              </SelectTrigger>
              <SelectContent>
                {pillars.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Format" required>
          {(control) => (
            <Select
              value={format}
              onValueChange={(value) => {
                setFormat(formats.find((candidate) => candidate === value) ?? 'text');
              }}
            >
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {formats.map((value) => (
                  <SelectItem key={value} value={value}>
                    {FORMAT_LABEL[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <DialogActions>
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={create.isPending || pillar === ''}>
          {create.isPending ? 'Starting…' : 'Start the re-cut'}
        </Button>
      </DialogActions>
    </form>
  );
}
