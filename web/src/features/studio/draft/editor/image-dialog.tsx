import type { Editor } from '@tiptap/core';
import { useEffect, useMemo, useState, type SyntheticEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAppData } from '@/lib/app-data';

import { mediaSrc, uploadImage, uploadProblem, UPLOAD_TYPES } from '../api/media';
import { ActionProblem } from '../review/action-problem';

import { ALT_TEXT_MAX, altTextProblem } from './alt-text-popover';
import { insertBlock } from './commands';

export interface ImageDialogProps {
  editor: Editor;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** A file pasted or dropped into the text. */
  initialFile?: File;
}

/**
 * Upload an image (`POST /api/media`, re-encoded by the server, metadata stripped) and put it in
 * its own paragraph with its alt text, which the dialect requires.
 */
export function ImageDialog({ editor, open, onOpenChange, initialFile }: ImageDialogProps) {
  const { api } = useAppData();
  const [file, setFile] = useState<File | undefined>(initialFile);
  const [alt, setAlt] = useState('');
  const [title, setTitle] = useState('');
  const [errors, setErrors] = useState<{ file?: string; alt?: string }>({});
  const [problem, setProblem] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const preview = useMemo(
    () => (file && !uploadProblem(file) && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : undefined),
    [file],
  );
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    const text = alt.replace(/\s+/g, ' ').trim();
    const next: typeof errors = {};
    if (!file) next.file = 'Choose an image.';
    else next.file = uploadProblem(file);
    next.alt = altTextProblem(text);
    setErrors(next);
    if (next.file || next.alt || !file) return;
    setPending(true);
    setProblem(undefined);
    try {
      const media = await uploadImage(api, file);
      if (editor.isDestroyed) return;
      insertBlock(editor, {
        type: 'paragraph',
        content: [{ type: 'image', attrs: { src: mediaSrc(media), alt: text, title: title.trim() || null } }],
      });
      onOpenChange(false);
    } catch (error) {
      setProblem(error);
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!pending) onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add an image</DialogTitle>
          <DialogDescription>
            PNG, JPEG or WebP, up to 10 MB. It is stored with the article and published with it.
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(event) => void submit(event)} noValidate>
          <Field label="Image" error={errors.file} required>
            <Input
              type="file"
              accept={UPLOAD_TYPES.join(',')}
              className="py-2"
              onChange={(event) => {
                setFile(event.target.files?.[0]);
                setErrors((current) => ({ ...current, file: undefined }));
              }}
            />
          </Field>
          {preview ? (
            <img src={preview} alt="" className="max-h-48 justify-self-start rounded-md border border-line object-contain" />
          ) : file ? (
            <p className="text-sm text-ink-muted">{file.name}</p>
          ) : null}
          <Field label="Alt text" description="What the image shows, for readers who can’t see it." error={errors.alt} required>
            <Textarea
              rows={2}
              maxLength={ALT_TEXT_MAX}
              value={alt}
              onChange={(event) => {
                setAlt(event.target.value);
                setErrors((current) => ({ ...current, alt: undefined }));
              }}
            />
          </Field>
          <Field label="Title" description="Optional: shown when the pointer rests on the image.">
            <Input
              value={title}
              onChange={(event) => {
                setTitle(event.target.value);
              }}
            />
          </Field>
          {problem ? <ActionProblem error={problem} title="Couldn’t upload the image." /> : null}
          <DialogFooter>
            <Button
              variant="secondary"
              disabled={pending}
              onClick={() => {
                onOpenChange(false);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? 'Uploading…' : 'Add the image'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
