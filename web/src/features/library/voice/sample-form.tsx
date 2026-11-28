import { useId, useState, type SubmitEvent } from 'react';
import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { usePillarDirectory } from '../hooks/use-pillars';
import { serverFieldErrors } from '../lib/errors';
import { formatInteger, fromDateInput } from '../lib/format';
import { OWNER_SOURCE_TYPES, SOURCE_TYPE_LABEL } from '../lib/labels';
import { charCount } from '../lib/spans';
import type { OwnerSourceType } from '../lib/types';
import { GUIDE_VIEW_KEY } from './model';

/** `voice.text.MAX_SAMPLE_CHARS`. */
export const MAX_SAMPLE_CHARS = 20_000;
const NO_PILLAR = 'none';

const linkClass = 'text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink';

/**
 * Adds a voice sample: pasted text, or the address of a page the owner wrote (fetched by the
 * server's research fetcher). The text is the owner's own published writing; it is scored for
 * AI tells on save.
 */
export function SampleForm({ onDone }: { onDone: () => void }) {
  const { pillars } = usePillarDirectory();
  const formId = useId();
  const [sourceType, setSourceType] = useState<OwnerSourceType>('linkedin_post');
  const [text, setText] = useState('');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [postedOn, setPostedOn] = useState('');
  const [pillar, setPillar] = useState(NO_PILLAR);
  const [unassisted, setUnassisted] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>();
  const add = useApiMutation('post', '/api/voice/samples', {
    invalidates: [queryKeys.voiceSamples.lists(), GUIDE_VIEW_KEY, queryKeys.today],
    successToast: 'Sample added.',
    errorToast: 'Couldn’t add the sample.',
    handles: ['validation_failed', 'confidential_text', 'text_or_url_required', 'fetch_failed', 'fetcher_unavailable', 'blocklist_not_configured'],
  });

  const errors = serverFieldErrors(add.error);
  const textError =
    localError ?? errors.text ?? (isProblem(add.error, 'text_or_url_required') ? problemMessage(add.error) : undefined);
  const urlError = errors.url ?? (isProblem(add.error, ['fetch_failed', 'fetcher_unavailable']) ? problemMessage(add.error) : undefined);
  const count = charCount(text);

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (text.trim() === '' && url.trim() === '') {
      setLocalError('Paste the text, or give the address of a page you wrote.');
      return;
    }
    if (count > MAX_SAMPLE_CHARS) {
      setLocalError(`At most ${formatInteger(MAX_SAMPLE_CHARS)} characters.`);
      return;
    }
    setLocalError(undefined);
    add.mutate(
      {
        body: {
          sourceType,
          text: text.trim() === '' ? null : text,
          url: text.trim() === '' && url.trim() !== '' ? url.trim() : null,
          title: title.trim() === '' ? null : title.trim(),
          postedAt: fromDateInput(postedOn),
          pillar: pillar === NO_PILLAR ? null : pillar,
          ownerUnassisted: unassisted,
        },
      },
      { onSuccess: onDone },
    );
  };

  return (
    <form id={formId} noValidate onSubmit={submit} className="space-y-5 rounded-lg border border-line bg-sunken p-4 sm:p-5">
      {isProblem(add.error, 'blocklist_not_configured') ? (
        <Alert variant="error">
          <AlertTitle>Save your blocklist first.</AlertTitle>
          <AlertDescription>
            <p>
              Samples are checked against it before they are stored. An empty list is fine:{' '}
              <Link to="/settings/policy" className={linkClass}>
                open Blocklist & policy
              </Link>
              .
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Source">
          {(control) => (
            <Select
              value={sourceType}
              onValueChange={(value) => {
                setSourceType(value as OwnerSourceType);
              }}
            >
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OWNER_SOURCE_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {SOURCE_TYPE_LABEL[type]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Pillar" description="Optional.">
          {(control) => (
            <Select value={pillar} onValueChange={setPillar}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_PILLAR}>No pillar</SelectItem>
                {pillars.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <Field
        label="Text"
        description={`Paste it exactly as published. ${formatInteger(count)} of ${formatInteger(MAX_SAMPLE_CHARS)} characters.`}
        error={textError}
      >
        <Textarea
          value={text}
          rows={8}
          onChange={(event) => {
            setText(event.target.value);
            if (localError) setLocalError(undefined);
          }}
        />
      </Field>
      <Field
        label="Or the page’s address"
        description="For a post or About page on your own site. The server fetches its text once."
        error={urlError}
      >
        <Input
          type="url"
          inputMode="url"
          placeholder="https://"
          value={url}
          disabled={text.trim() !== ''}
          onChange={(event) => {
            setUrl(event.target.value);
          }}
        />
      </Field>
      <div className="grid gap-5 sm:grid-cols-[minmax(0,1fr)_12rem]">
        <Field label="Title" description="Optional. Helps you find it later." error={errors.title}>
          <Input
            value={title}
            maxLength={200}
            onChange={(event) => {
              setTitle(event.target.value);
            }}
          />
        </Field>
        <Field label="Posted on" description="Optional." error={errors.postedAt}>
          <Input
            type="date"
            value={postedOn}
            onChange={(event) => {
              setPostedOn(event.target.value);
            }}
          />
        </Field>
      </div>
      <Field
        label="I wrote this without AI help"
        description="Your unassisted words count double when the guide is built."
        orientation="horizontal"
      >
        <Checkbox
          checked={unassisted}
          onCheckedChange={(checked) => {
            setUnassisted(checked === true);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={add.isPending}>
          {add.isPending ? 'Adding…' : 'Add sample'}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
