import { Lightbulb } from 'lucide-react';
import { useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { usePillarOptions } from '@/features/engagement/lib/pillars';
import { isHttpsUrl } from '@/lib/urls';
import { confidentialFindings, fieldErrors, isProblem } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

interface Errors {
  title?: string;
  note?: string;
  url?: string;
}

/** Quick capture (`POST /api/ideas`, origin `quick_capture`): a thought for later, into Calendar > Ideas. */
function IdeaForm({ onSaved }: { onSaved: () => void }) {
  const formRef = useRef<HTMLFormElement>(null);
  const pillars = usePillarOptions();
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [url, setUrl] = useState('');
  const [pillar, setPillar] = useState('none');
  const [timely, setTimely] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [blocked, setBlocked] = useState(false);
  const save = useApiMutation('post', '/api/ideas', {
    successToast: 'Idea saved to Calendar > Ideas.',
    errorToast: 'Couldn’t save the idea.',
    handles: ['validation_failed', 'confidential_text', 'blocklist_not_configured'],
    invalidates: [queryKeys.ideas.lists()],
  });

  function show(next: Errors) {
    flushSync(() => {
      setErrors(next);
    });
    focusFirstInvalid(formRef.current);
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const next: Errors = {};
    if (title.trim() === '') next.title = 'Say what the idea is, in one line.';
    else if (/[\r\n]/u.test(title)) next.title = 'Keep the idea to one line; use the note for more.';
    else if (title.trim().length > 300) next.title = 'Keep it to 300 characters; use the note for more.';
    if (note.trim().length > 2000) next.note = 'Keep the note to 2,000 characters.';
    if (url.trim() !== '' && !isHttpsUrl(url.trim())) next.url = 'Use an https link, or leave it empty.';
    if (Object.keys(next).length > 0) {
      show(next);
      return;
    }
    setErrors({});
    setBlocked(false);
    save.mutate(
      {
        body: {
          title: title.trim(),
          note: note.trim() === '' ? null : note.trim(),
          url: url.trim() === '' ? null : url.trim(),
          pillar: pillar === 'none' ? null : pillar,
          timeSensitive: timely,
        },
      },
      {
        onSuccess: onSaved,
        onError: (error) => {
          if (isProblem(error, 'blocklist_not_configured')) {
            setBlocked(true);
            return;
          }
          const fields = fieldErrors(error);
          const confidential: Errors = {};
          for (const finding of confidentialFindings(error)) {
            const { field } = finding;
            if (field === 'title' || field === 'note' || field === 'url') confidential[field] ??= finding.message;
          }
          show({
            title: confidential.title ?? fields.title,
            note: confidential.note ?? fields.note,
            url: confidential.url ?? fields.url,
          });
        },
      },
    );
  }

  return (
    <form ref={formRef} noValidate onSubmit={submit} className="mt-4 grid gap-4 rounded-lg border border-line bg-sheet p-4 sm:p-5">
      {blocked ? (
        <Alert variant="warn">
          <AlertTitle>Save your blocklist first</AlertTitle>
          <AlertDescription>
            <p>Ideas are checked against it before they are kept. An empty list is fine.</p>
          </AlertDescription>
          <AlertActions>
            <Button asChild size="sm" variant="secondary">
              <Link to="/settings/policy">Open Blocklist & policy</Link>
            </Button>
          </AlertActions>
        </Alert>
      ) : null}
      <Field label="The idea" error={errors.title} required>
        <Input
          value={title}
          autoComplete="off"
          onChange={(event) => {
            setTitle(event.target.value);
          }}
        />
      </Field>
      <Field label="Note" description="Optional. What made you think of it." error={errors.note}>
        <Textarea
          rows={3}
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
          }}
        />
      </Field>
      <Field label="Link" description="Optional. Where you saw it." error={errors.url}>
        <Input
          type="url"
          inputMode="url"
          autoComplete="off"
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
          }}
        />
      </Field>
      <Field label="Pillar" description="Optional. The planner can decide.">
        {(control) => (
          <Select value={pillar} onValueChange={setPillar}>
            <SelectTrigger {...control}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">Let the planner decide</SelectItem>
              {pillars.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  {option.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      <Field
        label="It’s news"
        description="Time-sensitive ideas go to the quick-take lane and expire if they wait too long."
        orientation="horizontal"
      >
        <Checkbox
          checked={timely}
          onCheckedChange={(value) => {
            setTimely(value === true);
          }}
        />
      </Field>
      <div className="flex justify-end">
        <Button type="submit" disabled={save.isPending} className="w-full sm:w-auto">
          {save.isPending ? 'Saving…' : 'Save the idea'}
        </Button>
      </div>
    </form>
  );
}

/**
 * "Capture an idea" on Today: one line (and an optional note, link and pillar) saved to the ideas
 * inbox for the planner, without leaving the page.
 */
export function IdeaCapture() {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  return (
    <section aria-labelledby="capture-idea" className="mt-12 max-w-measure">
      <Collapsible open={open} onOpenChange={setOpen}>
        <h2 id="capture-idea" className="sr-only">
          Capture an idea
        </h2>
        <CollapsibleTrigger asChild>
          <Button variant="secondary">
            <Lightbulb aria-hidden="true" />
            {open ? 'Close the idea form' : 'Capture an idea'}
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <IdeaForm
            key={count}
            onSaved={() => {
              setCount((value) => value + 1);
            }}
          />
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
