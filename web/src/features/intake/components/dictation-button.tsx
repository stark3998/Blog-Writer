import { Mic, Square } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';

/**
 * Dictation with the Web Speech API, **on this device only**. Private words must not leave the
 * browser except in the redact request (contract 1.9 rule 9), and a browser's default speech
 * recognition may send the audio to a cloud service. So the button exists only where the
 * browser offers on-device recognition (`processLocally`, `SpeechRecognition.available()` and
 * `install()`), and every session sets `processLocally = true`. Everywhere else the owner uses
 * the keyboard's own microphone, which the field's hint points to.
 */

interface SpeechAlternativeLike {
  readonly transcript: string;
}

interface SpeechResultLike {
  readonly isFinal: boolean;
  readonly [index: number]: SpeechAlternativeLike | undefined;
}

interface SpeechResultListLike {
  readonly length: number;
  readonly [index: number]: SpeechResultLike | undefined;
}

export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: SpeechResultListLike;
}

export interface SpeechRecognitionErrorLike {
  readonly error: string;
}

/** The part of `SpeechRecognition` this button uses (TypeScript's DOM types do not include it). */
export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  processLocally: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

interface RecognitionOptions {
  langs: string[];
  processLocally: boolean;
}

export interface SpeechRecognitionConstructorLike {
  new (): SpeechRecognitionLike;
  readonly prototype: object;
  readonly available?: (options: RecognitionOptions) => Promise<string>;
  readonly install?: (options: RecognitionOptions) => Promise<boolean>;
}

type OnDeviceRecognition = SpeechRecognitionConstructorLike &
  Required<Pick<SpeechRecognitionConstructorLike, 'available' | 'install'>>;

/** The browser's `SpeechRecognition`, but only when it can be told to stay on the device. */
export function onDeviceRecognition(scope: object = window): OnDeviceRecognition | undefined {
  const { SpeechRecognition, webkitSpeechRecognition } = scope as {
    SpeechRecognition?: unknown;
    webkitSpeechRecognition?: unknown;
  };
  const candidate = SpeechRecognition ?? webkitSpeechRecognition;
  if (typeof candidate !== 'function') return undefined;
  const ctor = candidate as SpeechRecognitionConstructorLike;
  if (typeof ctor.available !== 'function' || typeof ctor.install !== 'function') return undefined;
  if (!('processLocally' in ctor.prototype)) return undefined;
  return ctor as OnDeviceRecognition;
}

type Availability = 'unknown' | 'available' | 'downloadable' | 'downloading' | 'unavailable';

function availabilityOf(value: unknown): Availability {
  return value === 'available' || value === 'downloadable' || value === 'downloading' ? value : 'unavailable';
}

const UNAVAILABLE = 'Dictation on this device isn’t available. Use your keyboard’s microphone instead.';
const LISTENING = 'Listening on this device. Tap Stop dictating when you’re done.';

function errorMessage(code: string): string | undefined {
  switch (code) {
    case 'aborted':
      return undefined;
    case 'not-allowed':
      return 'Microphone access is blocked. Allow it for this site, or use your keyboard’s microphone.';
    case 'audio-capture':
      return 'No microphone was found. Use your keyboard’s microphone instead.';
    case 'no-speech':
      return 'Didn’t hear anything. Tap Dictate and try again.';
    case 'service-not-allowed':
    case 'language-not-supported':
      return UNAVAILABLE;
    default:
      return 'Dictation stopped. Try again, or use your keyboard’s microphone.';
  }
}

function speechLanguage(): string {
  return navigator.language || document.documentElement.lang || 'en-US';
}

export interface DictationButtonProps {
  /** Each finished phrase, to add to the raw text (component state only). */
  onTranscript: (text: string) => void;
  disabled?: boolean;
  className?: string;
  /** For tests; defaults to the window's `SpeechRecognition`. */
  recognition?: OnDeviceRecognition;
}

/**
 * "Dictate", then "Stop dictating" while it listens, with a one-line status. Renders nothing
 * unless on-device recognition is offered for the owner's language.
 */
export function DictationButton({ onTranscript, disabled = false, className, recognition }: DictationButtonProps) {
  const [ctor] = useState(() => recognition ?? onDeviceRecognition());
  const [lang] = useState(speechLanguage);
  const [availability, setAvailability] = useState<Availability>('unknown');
  const [listening, setListening] = useState(false);
  const [message, setMessage] = useState<string | undefined>();
  const session = useRef<SpeechRecognitionLike | null>(null);
  const transcript = useRef(onTranscript);

  useEffect(() => {
    transcript.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    if (!ctor) return;
    let cancelled = false;
    ctor.available({ langs: [lang], processLocally: true }).then(
      (value) => {
        if (!cancelled) setAvailability(availabilityOf(value));
      },
      () => {
        if (!cancelled) setAvailability('unavailable');
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ctor, lang]);

  // Leaving the screen ends dictation; nothing heard after that goes anywhere.
  useEffect(
    () => () => {
      session.current?.abort();
      session.current = null;
    },
    [],
  );

  const offered = ctor !== undefined && availability !== 'unknown' && availability !== 'unavailable';
  // Once it turns out not to be offered after all, the reason stays; the button goes.
  if (!ctor || (!offered && message === undefined)) return null;

  const stop = () => {
    session.current?.stop();
  };

  const start = async () => {
    setMessage(undefined);
    if (availability !== 'available') {
      setMessage('Getting dictation ready on this device…');
      const installed = await ctor.install({ langs: [lang], processLocally: true }).catch(() => false);
      if (!installed) {
        setAvailability('unavailable');
        setMessage(UNAVAILABLE);
        return;
      }
      setAvailability('available');
    }
    const next = new ctor();
    next.processLocally = true;
    // Never fall back to a service that may send the audio elsewhere: read the flag back.
    if ((next as { readonly processLocally?: unknown }).processLocally !== true) {
      setMessage(UNAVAILABLE);
      return;
    }
    next.lang = lang;
    next.continuous = true;
    next.interimResults = false;
    next.onresult = (event) => {
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const text = result?.isFinal ? result[0]?.transcript.trim() : undefined;
        if (text) transcript.current(text);
      }
    };
    next.onerror = (event) => {
      setMessage(errorMessage(event.error));
    };
    next.onend = () => {
      if (session.current === next) session.current = null;
      setListening(false);
      setMessage((current) => (current === LISTENING ? undefined : current));
    };
    session.current = next;
    try {
      next.start();
      setListening(true);
      setMessage(LISTENING);
    } catch {
      session.current = null;
      setMessage(UNAVAILABLE);
    }
  };

  return (
    <div className={cn('flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1', className)}>
      {offered ? (
        <Button
          variant="secondary"
          size="sm"
          className="pointer-coarse:min-h-11"
          disabled={disabled}
          onClick={() => {
            if (listening) stop();
            else void start();
          }}
        >
          {listening ? <Square aria-hidden="true" /> : <Mic aria-hidden="true" />}
          {listening ? 'Stop dictating' : 'Dictate'}
        </Button>
      ) : null}
      <p role="status" className="min-w-0 text-sm text-ink-muted">
        {message}
      </p>
    </div>
  );
}
