import { Check, Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { Button, type ButtonProps } from '@/components/ui/button';
import { toastSuccess } from '@/lib/api/toast';

export interface CopyButtonProps extends Omit<ButtonProps, 'onClick' | 'children'> {
  /** Exactly what goes on the clipboard. */
  text: string;
  /** The visible words ("Copy text"). */
  children: string;
  /** The accessible name when the visible words need context ("Copy the post text"). */
  label?: string;
  /** The toast after copying ("Post text copied."). */
  copiedMessage: string;
}

/**
 * Copies `text` exactly as given (no trimming, no formatting) and says so: the icon turns into
 * a check for two seconds and a toast confirms. When the clipboard is refused, a toast says to
 * select the text instead.
 */
export function CopyButton({ text, children, label, copiedMessage, variant = 'secondary', ...props }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        setCopied(false);
      }, 2000);
      toastSuccess(copiedMessage);
    } catch {
      toast.error('Couldn’t copy.', { description: 'Select the text and copy it yourself.' });
    }
  }

  return (
    <Button
      variant={variant}
      aria-label={label}
      data-copied={copied || undefined}
      onClick={() => void copy()}
      {...props}
    >
      {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      {copied ? 'Copied' : children}
    </Button>
  );
}
