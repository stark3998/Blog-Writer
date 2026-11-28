import { Fingerprint } from 'lucide-react';

import { cn } from '@/lib/cn';

import { CopyButton } from './copy-button';

/** "3f9a1c2e…9c21" : enough of a SHA-256 to compare by eye. */
export function shortHash(sha256: string): string {
  return sha256.length > 16 ? `${sha256.slice(0, 8)}…${sha256.slice(-4)}` : sha256;
}

/**
 * The SHA-256 an approval is bound to (text plus media, `domain.hashing.approval_hash`), short
 * on screen with the whole value one tap away. The same hash goes with the publish tap, so what
 * was approved is exactly what goes out.
 */
export function ApprovalHash({ sha256, className }: { sha256: string; className?: string }) {
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-x-2 gap-y-1', className)}>
      <span className="inline-flex items-center gap-1.5 text-sm text-ink-muted">
        <Fingerprint aria-hidden="true" className="size-4 text-ink-subtle" />
        SHA-256
        <code className="font-mono text-ink" title={sha256}>
          {shortHash(sha256)}
        </code>
      </span>
      <CopyButton
        text={sha256}
        size="sm"
        variant="ghost"
        label="Copy the approval’s SHA-256"
        copiedMessage="SHA-256 copied."
      >
        Copy
      </CopyButton>
    </span>
  );
}
