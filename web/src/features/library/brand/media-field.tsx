import { ImageOff, Upload } from 'lucide-react';
import { useId, useRef, useState, type ChangeEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Fieldset } from '@/components/ui/field';
import { problemMessage } from '@/lib/api/problems';
import { useAppData } from '@/lib/app-data';
import { cn } from '@/lib/cn';

import { mediaUrl, uploadMedia, uploadProblem, UPLOAD_TYPES, type UploadRole } from '../lib/media';
import type { MediaRef } from '../lib/types';

export interface MediaFieldProps {
  legend: string;
  description: string;
  role: UploadRole;
  value: MediaRef | null;
  onChange: (value: MediaRef | null) => void;
  /** What the image shows, for its alt text ("Your headshot"). */
  alt: string;
  /** The upload button's words ("Upload a photo"). */
  uploadLabel: string;
  /** A problem the last save reported for this image. */
  error?: string | undefined;
  /** Round preview (the headshot is cropped to a circle on slides). */
  round?: boolean;
}

/**
 * An image for the kit (headshot, name mark): what is there now, Upload or Replace, Remove. The
 * file goes to `POST /api/media` at once (re-encoded on the server, metadata dropped); the kit
 * points at it once the owner saves.
 */
export function MediaField({ legend, description, role, value, onChange, alt, uploadLabel, error, round = false }: MediaFieldProps) {
  const { api } = useAppData();
  const inputRef = useRef<HTMLInputElement>(null);
  const statusId = useId();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | undefined>();

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const problem = uploadProblem(file);
    if (problem) {
      setUploadError(problem);
      return;
    }
    setUploadError(undefined);
    setUploading(true);
    try {
      onChange(await uploadMedia(api, file, role));
    } catch (failure) {
      setUploadError(problemMessage(failure));
    } finally {
      setUploading(false);
    }
  };

  const shown = uploadError ?? error;
  return (
    <Fieldset legend={legend} description={description} error={shown}>
      <div className="flex flex-wrap items-center gap-4">
        <div
          className={cn(
            'flex size-24 shrink-0 items-center justify-center overflow-hidden border border-line bg-sunken',
            round ? 'rounded-full' : 'rounded-md',
          )}
        >
          {value ? (
            <img src={mediaUrl(value.name)} alt={alt} className="size-full object-cover" />
          ) : (
            <ImageOff aria-hidden="true" className="size-6 text-ink-subtle" />
          )}
        </div>
        <div className="flex flex-col items-start gap-2">
          <p id={statusId} role="status" className="text-sm text-ink-muted">
            {uploading ? 'Uploading…' : value ? `${value.width && value.height ? `${String(value.width)} × ${String(value.height)} px, ` : ''}uploaded.` : 'None yet.'}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={uploading}
              aria-describedby={statusId}
              onClick={() => {
                inputRef.current?.click();
              }}
            >
              <Upload aria-hidden="true" />
              {value ? `Replace ${legend.toLowerCase()}` : uploadLabel}
            </Button>
            {value ? (
              <Button
                variant="ghost"
                size="sm"
                disabled={uploading}
                onClick={() => {
                  onChange(null);
                }}
              >
                {`Remove ${legend.toLowerCase()}`}
              </Button>
            ) : null}
          </div>
          {/* Out of the page and the accessibility tree; the button above opens it. */}
          <input
            ref={inputRef}
            type="file"
            accept={UPLOAD_TYPES.join(',')}
            aria-label={`${legend} file`}
            className="hidden"
            onChange={(event) => void onFile(event)}
          />
        </div>
      </div>
    </Fieldset>
  );
}
