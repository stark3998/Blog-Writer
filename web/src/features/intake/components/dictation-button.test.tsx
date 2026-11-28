import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  DictationButton,
  onDeviceRecognition,
  type SpeechRecognitionConstructorLike,
  type SpeechRecognitionErrorLike,
  type SpeechRecognitionEventLike,
} from './dictation-button';

/** A recogniser shaped like the browser's on-device one: `processLocally` is an accessor on the prototype. */
function fakeRecognition({ availability = 'available', installs = true } = {}) {
  const sessions: FakeRecognition[] = [];
  class FakeRecognition {
    static available = vi.fn(() => Promise.resolve(availability));
    static install = vi.fn(() => Promise.resolve(installs));
    lang = '';
    continuous = false;
    interimResults = true;
    onresult: ((event: SpeechRecognitionEventLike) => void) | null = null;
    onerror: ((event: SpeechRecognitionErrorLike) => void) | null = null;
    onend: (() => void) | null = null;
    #local = false;
    started = false;
    get processLocally() {
      return this.#local;
    }
    set processLocally(value: boolean) {
      this.#local = value;
    }
    constructor() {
      sessions.push(this);
    }
    start() {
      this.started = true;
    }
    stop() {
      this.onend?.();
    }
    abort() {
      this.onend?.();
    }
    /** The browser heard a phrase. */
    hear(text: string, isFinal = true) {
      const result = Object.assign([{ transcript: text }], { isFinal });
      this.onresult?.({ resultIndex: 0, results: Object.assign([result], { length: 1 }) });
    }
  }
  return { ctor: FakeRecognition as unknown as SpeechRecognitionConstructorLike & typeof FakeRecognition, sessions };
}

describe('onDeviceRecognition', () => {
  it('offers only a recogniser that can be kept on the device', () => {
    expect(onDeviceRecognition({})).toBeUndefined();
    // A cloud-only recogniser (no processLocally, no availability check) is never used.
    class CloudOnly {
      lang = '';
    }
    expect(onDeviceRecognition({ webkitSpeechRecognition: CloudOnly })).toBeUndefined();
    const { ctor } = fakeRecognition();
    expect(onDeviceRecognition({ SpeechRecognition: ctor })).toBe(ctor);
  });
});

describe('DictationButton', () => {
  it('renders nothing where on-device dictation is not offered', async () => {
    const { ctor } = fakeRecognition({ availability: 'unavailable' });
    const { container } = render(<DictationButton onTranscript={vi.fn()} recognition={onDeviceRecognition({ SpeechRecognition: ctor })} />);
    await waitFor(() => {
      expect(ctor.available).toHaveBeenCalledWith({ langs: [navigator.language], processLocally: true });
    });
    expect(container).toBeEmptyDOMElement();
  });

  it('dictates on the device and hands each finished phrase over', async () => {
    const { ctor, sessions } = fakeRecognition();
    const onTranscript = vi.fn();
    const user = userEvent.setup();
    render(<DictationButton onTranscript={onTranscript} recognition={onDeviceRecognition({ SpeechRecognition: ctor })} />);
    await user.click(await screen.findByRole('button', { name: 'Dictate' }));
    const session = sessions[0];
    if (!session) throw new Error('no recognition session started');
    expect(session.processLocally).toBe(true);
    expect(session.started).toBe(true);
    expect(session.continuous).toBe(true);
    expect(screen.getByRole('status')).toHaveTextContent('Listening on this device.');
    act(() => {
      session.hear('We moved the apps in waves.', false);
      session.hear('We moved the apps in waves.');
    });
    expect(onTranscript).toHaveBeenCalledTimes(1);
    expect(onTranscript).toHaveBeenCalledWith('We moved the apps in waves.');
    await user.click(screen.getByRole('button', { name: 'Stop dictating' }));
    expect(screen.getByRole('button', { name: 'Dictate' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('installs the language pack on a tap, and gives up without falling back to the cloud', async () => {
    const { ctor, sessions } = fakeRecognition({ availability: 'downloadable', installs: false });
    const user = userEvent.setup();
    render(<DictationButton onTranscript={vi.fn()} recognition={onDeviceRecognition({ SpeechRecognition: ctor })} />);
    await user.click(await screen.findByRole('button', { name: 'Dictate' }));
    expect(ctor.install).toHaveBeenCalledWith({ langs: [navigator.language], processLocally: true });
    expect(sessions).toHaveLength(0);
    expect(await screen.findByText('Dictation on this device isn’t available. Use your keyboard’s microphone instead.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Dictate' })).not.toBeInTheDocument();
  });

  it('explains a blocked microphone', async () => {
    const { ctor, sessions } = fakeRecognition();
    const user = userEvent.setup();
    render(<DictationButton onTranscript={vi.fn()} recognition={onDeviceRecognition({ SpeechRecognition: ctor })} />);
    await user.click(await screen.findByRole('button', { name: 'Dictate' }));
    act(() => {
      sessions[0]?.onerror?.({ error: 'not-allowed' });
      sessions[0]?.onend?.();
    });
    expect(screen.getByRole('status')).toHaveTextContent('Microphone access is blocked.');
  });
});
